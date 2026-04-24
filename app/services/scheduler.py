"""
Background scheduler for Leash.

Runs a single APScheduler BackgroundScheduler (one background thread).
Every minute it checks which ScheduledRecall entries are due and fires them.

Concurrency is rate-limited per recall: at most RECALL_CONCURRENCY devices
are contacted in parallel, so 100 receivers get batched rather than all
hammered at once.

IMPORTANT: Gunicorn must be run with --workers 1 (plus --threads N for
concurrent HTTP) so only one scheduler instance exists. See leash.service.
"""
import asyncio
import logging
import os
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def init_scheduler(app) -> BackgroundScheduler:
    """Create and start the background scheduler. Safe to call once per process."""
    global _scheduler
    _scheduler = BackgroundScheduler(daemon=True)

    _scheduler.add_job(
        _check_schedules,
        trigger="interval",
        minutes=1,
        id="leash_schedule_checker",
        args=[app],
        max_instances=1,       # never queue up more than one check at a time
        coalesce=True,         # if a check was missed, run it once rather than catching up
    )
    _scheduler.start()
    logger.info("Leash scheduler: started (pid=%d)", os.getpid())

    import atexit
    atexit.register(lambda: _scheduler.shutdown(wait=False))

    return _scheduler


def get_scheduler() -> BackgroundScheduler | None:
    return _scheduler


# ---------------------------------------------------------------------------
# Internal: schedule check
# ---------------------------------------------------------------------------

def _check_schedules(app) -> None:
    """Called every minute. Fires any enabled schedule whose day+time match now."""
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
# Internal: recall execution with concurrency gate
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
            logger.warning(
                "Leash scheduler: schedule %r has no snapshot — skipping", sched.name
            )
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

        if not to_apply:
            sched.last_run = datetime.utcnow()
            sched.last_result = "SKIPPED: no online receivers in snapshot"
            db.session.commit()
            return

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
        sched.last_result = f"OK: {len(ok_map)}/{len(to_apply)} succeeded"
        db.session.commit()

        snapshot_recalled(snap.name, len(to_apply), len(ok_map))
        logger.info(
            "Leash scheduler: %r complete — %d/%d succeeded (concurrency=%d)",
            sched.name, len(ok_map), len(to_apply), concurrency,
        )
