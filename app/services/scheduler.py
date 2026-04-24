"""
Background scheduler for Leash.

Two APScheduler jobs run in a single BackgroundScheduler thread:

  leash_schedule_checker  (every 1 minute)
      Looks for enabled ScheduledRecall entries whose day+time match the
      current local time and fires them.  If a schedule is marked persistent,
      sets enforcing_until = now + persist_minutes after the recall completes.

  leash_enforcement       (every ENFORCEMENT_INTERVAL seconds, default 60)
      For each active enforcement window (enforcing_until > utcnow), polls
      every affected receiver's live current source via a lightweight
      /connectTo call and corrects any drift, including receivers that were
      offline when the recall fired and have since come back online.
      Multiple concurrent windows on the same receiver → most-recently-fired
      schedule wins.

Concurrency gate: asyncio.Semaphore(RECALL_CONCURRENCY) inside every recall
and correction pass so devices are batched rather than all hit at once.

IMPORTANT: Gunicorn must run with --workers 1 (plus --threads for HTTP
concurrency) so only one scheduler instance exists.  See leash.service.
"""
import asyncio
import logging
import os
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def init_scheduler(app) -> BackgroundScheduler:
    """Create and start the background scheduler. Call once per process."""
    global _scheduler
    _scheduler = BackgroundScheduler(daemon=True)

    _scheduler.add_job(
        _check_schedules,
        trigger="interval",
        minutes=1,
        id="leash_schedule_checker",
        args=[app],
        max_instances=1,
        coalesce=True,
    )

    interval = app.config.get("ENFORCEMENT_INTERVAL", 60)
    _scheduler.add_job(
        _enforce_persistent,
        trigger="interval",
        seconds=interval,
        id="leash_enforcement",
        args=[app],
        max_instances=1,
        coalesce=True,
    )

    _scheduler.start()
    logger.info("Leash scheduler: started (pid=%d, enforcement_interval=%ds)", os.getpid(), interval)

    import atexit
    atexit.register(lambda: _scheduler.shutdown(wait=False))

    return _scheduler


def get_scheduler() -> BackgroundScheduler | None:
    return _scheduler


# ---------------------------------------------------------------------------
# Job 1: fire scheduled recalls
# ---------------------------------------------------------------------------

def _check_schedules(app) -> None:
    """Called every minute. Fires enabled schedules whose day+time match now."""
    with app.app_context():
        from app.models import ScheduledRecall

        now_local = datetime.now()
        current_dow = str(now_local.weekday())   # "0"=Mon … "6"=Sun
        current_hhmm = now_local.strftime("%H:%M")

        schedules = ScheduledRecall.query.filter_by(enabled=True).all()
        for sched in schedules:
            # Guard against double-fire if app restarted within the same minute
            if sched.last_run:
                elapsed = (datetime.utcnow() - sched.last_run).total_seconds()
                if elapsed < 55:
                    continue

            days = [d.strip() for d in sched.days_of_week.split(",")]
            if current_dow in days and current_hhmm == sched.time_of_day:
                logger.info(
                    "Leash scheduler: firing schedule %r (id=%d) → snapshot_id=%s",
                    sched.name, sched.id, sched.snapshot_id,
                )
                _do_recall(app, sched.id)


# ---------------------------------------------------------------------------
# Recall execution
# ---------------------------------------------------------------------------

def _do_recall(app, schedule_id: int) -> None:
    """Execute a scheduled snapshot recall inside an app context."""
    from app import db
    from app.models import NDIReceiver, ScheduledRecall
    from app.services.audit_log import device_error, snapshot_recalled, snapshot_source_changed
    from app.services.birddog_client import BirdDogClient, run_async

    with app.app_context():
        sched = db.session.get(ScheduledRecall, schedule_id)
        if not sched:
            logger.warning("Leash scheduler: schedule id=%d disappeared", schedule_id)
            return

        snap = sched.snapshot
        if not snap:
            logger.warning("Leash scheduler: schedule %r has no snapshot — skipping", sched.name)
            sched.last_run = datetime.utcnow()
            sched.last_result = "SKIPPED: snapshot was deleted"
            db.session.commit()
            return

        cfg = app.config
        concurrency = cfg.get("RECALL_CONCURRENCY", 10)

        to_apply = [
            e for e in snap.entries
            if e.source_name and e.receiver and e.receiver.status != "offline"
        ]

        async def _recall_all():
            sem = asyncio.Semaphore(concurrency)

            async def _one(entry):
                async with sem:
                    recv = entry.receiver
                    client = BirdDogClient(
                        ip=recv.ip_address,
                        port=cfg["NDI_DEVICE_PORT"],
                        password=cfg["NDI_DEVICE_PASSWORD"],
                        timeout=cfg["HTTP_TIMEOUT"],
                    )
                    code, _ = await client.set_connect_to(entry.source_name)
                    return {
                        "receiver_id": recv.id,
                        "source_name": entry.source_name,
                        "status": code,
                        "ok": code == 200,
                    }

            return await asyncio.gather(*[_one(e) for e in to_apply], return_exceptions=False)

        results = []
        if to_apply:
            try:
                results = run_async(_recall_all())
            except Exception as exc:
                logger.exception("Leash scheduler: recall failed for schedule %r", sched.name)
                sched.last_run = datetime.utcnow()
                sched.last_result = f"ERROR: {exc}"
                db.session.commit()
                return

        ok_map = {r["receiver_id"]: r["source_name"] for r in results if r.get("ok")}
        failed = [r for r in results if not r.get("ok")]
        recv_by_id = {e.receiver_id: e.receiver for e in to_apply}

        now = datetime.utcnow()
        for recv in NDIReceiver.query.filter(NDIReceiver.id.in_(ok_map)).all():
            old_source = recv.current_source
            recv.current_source = ok_map[recv.id]
            recv.updated_at = now
            snapshot_source_changed(
                recv.label or recv.hostname or recv.ip_last_octet,
                recv.ip_address, old_source, ok_map[recv.id], snap.name,
            )
        for r in failed:
            recv = recv_by_id.get(r["receiver_id"])
            if recv:
                device_error(recv.ip_address, "scheduled_recall", r.get("status", 0))

        sched.last_run = now
        if to_apply:
            sched.last_result = f"OK: {len(ok_map)}/{len(to_apply)} succeeded"
        else:
            sched.last_result = "SKIPPED: no online receivers (enforcement will catch them)"

        # Arm enforcement window if persistent
        if sched.persistent and sched.persist_minutes:
            sched.enforcing_until = now + timedelta(minutes=sched.persist_minutes)
            logger.info(
                "Leash scheduler: enforcement armed for %r — %d min window until %s",
                sched.name, sched.persist_minutes,
                sched.enforcing_until.strftime("%H:%M UTC"),
            )

        db.session.commit()

        snapshot_recalled(snap.name, len(to_apply), len(ok_map))
        logger.info(
            "Leash scheduler: %r complete — %d/%d succeeded (concurrency=%d)",
            sched.name, len(ok_map), len(to_apply), concurrency,
        )


# ---------------------------------------------------------------------------
# Job 2: enforcement poller
# ---------------------------------------------------------------------------

def _enforce_persistent(app) -> None:
    """Poll receivers in active enforcement windows and correct any source drift."""
    with app.app_context():
        from app import db
        from app.models import NDIReceiver, ScheduledRecall
        from app.services.audit_log import source_changed
        from app.services.birddog_client import BirdDogClient, bulk_fetch_source, run_async

        now = datetime.utcnow()
        active = ScheduledRecall.query.filter(
            ScheduledRecall.persistent == True,      # noqa: E712
            ScheduledRecall.enforcing_until > now,
            ScheduledRecall.enabled == True,         # noqa: E712
        ).all()

        if not active:
            return

        logger.debug("Enforcement: %d active window(s)", len(active))

        # Build receiver → expected_source map.
        # Sort by last_run ascending so the most-recently-fired schedule overwrites earlier ones.
        active_sorted = sorted(active, key=lambda s: s.last_run or datetime.min)
        receiver_targets: dict[int, str] = {}
        for sched in active_sorted:
            if not sched.snapshot:
                continue
            for entry in sched.snapshot.entries:
                if entry.source_name:
                    receiver_targets[entry.receiver_id] = entry.source_name

        if not receiver_targets:
            return

        recv_ids = list(receiver_targets.keys())
        receivers = NDIReceiver.query.filter(NDIReceiver.id.in_(recv_ids)).all()
        recv_by_id = {r.id: r for r in receivers}

        cfg = app.config
        cfg_dict = {
            "NDI_SUBNET_PREFIX": cfg["NDI_SUBNET_PREFIX"],
            "NDI_DEVICE_PORT": cfg["NDI_DEVICE_PORT"],
            "NDI_DEVICE_PASSWORD": cfg["NDI_DEVICE_PASSWORD"],
            "HTTP_TIMEOUT": cfg["HTTP_TIMEOUT"],
            "RECALL_CONCURRENCY": cfg.get("RECALL_CONCURRENCY", 10),
        }
        recv_dicts = [{"id": r.id, "ip_last_octet": r.ip_last_octet} for r in receivers]

        # Lightweight poll — one /connectTo call per device
        try:
            poll_results = run_async(bulk_fetch_source(recv_dicts, cfg_dict))
        except Exception:
            logger.exception("Enforcement: source poll failed")
            return

        needs_correction: list[tuple[NDIReceiver, str]] = []
        poll_now = datetime.utcnow()

        for result in poll_results:
            recv = recv_by_id.get(result["id"])
            if not recv:
                continue

            was_offline = recv.status != "online"
            recv.status = "online" if result["online"] else "offline"
            if result["current_source"] is not None:
                recv.current_source = result["current_source"]
            recv.updated_at = poll_now

            expected = receiver_targets[recv.id]
            if result["online"] and result["current_source"] != expected:
                if was_offline:
                    logger.info(
                        "Enforcement: %s came back online — applying %r",
                        recv.ip_address, expected,
                    )
                else:
                    logger.warning(
                        "Enforcement: %s drift detected — live=%r expected=%r",
                        recv.ip_address, result["current_source"], expected,
                    )
                needs_correction.append((recv, expected))

        db.session.commit()

        if not needs_correction:
            return

        logger.info("Enforcement: correcting %d receiver(s)", len(needs_correction))
        concurrency = cfg.get("RECALL_CONCURRENCY", 10)

        async def _correct_all():
            sem = asyncio.Semaphore(concurrency)

            async def _one(recv, expected):
                async with sem:
                    client = BirdDogClient(
                        ip=recv.ip_address,
                        port=cfg["NDI_DEVICE_PORT"],
                        password=cfg["NDI_DEVICE_PASSWORD"],
                        timeout=cfg["HTTP_TIMEOUT"],
                    )
                    code, _ = await client.set_connect_to(expected)
                    return {"receiver_id": recv.id, "ok": code == 200, "expected": expected}

            return await asyncio.gather(
                *[_one(r, e) for r, e in needs_correction],
                return_exceptions=False,
            )

        try:
            corrections = run_async(_correct_all())
        except Exception:
            logger.exception("Enforcement: correction pass failed")
            return

        commit_now = datetime.utcnow()
        ok_count = 0
        for c in corrections:
            recv = recv_by_id.get(c["receiver_id"])
            if recv and c["ok"]:
                old_source = recv.current_source
                recv.current_source = c["expected"]
                recv.updated_at = commit_now
                source_changed(
                    recv.label or recv.hostname or recv.ip_last_octet,
                    recv.ip_address, old_source, c["expected"], via="enforcement",
                )
                ok_count += 1

        db.session.commit()
        logger.info("Enforcement: corrected %d/%d receivers", ok_count, len(corrections))
