"""
Tractus projector engine — cycles / holds ProjectCues on every Tractus host.

Runs as one daemon thread (started from init_scheduler, so only in the
single real worker process).  It wakes at least once a second — sooner when
a phase boundary is due or kick() is called after an activation — and:

  1. Releases cues whose active_until has passed.
  2. Advances the playlist timeline:
        idle → dwell(cue A) → gap → dwell(cue B) → gap → …
     over the active mode="cycle" cues in sort_order.  The rotation is a
     single global timeline so every viewer and every host flips together.
  3. Resolves the desired projector state for each (host, viewer) it
     manages:  hold cue (highest priority, then latest activated)
                → current dwell cue → None (normal view).
  4. Sends only the differences, to every host.  A command is recorded as
     applied only after that host returns 200, so failed hosts are retried
     (with a short backoff) until they converge.

A viewer is "managed" only while at least one active cue targets it.  When
the last cue releases it, the engine sends a single project/stop and then
leaves it alone — manual projections on unmanaged viewers are never touched.

Every VIEWER_REFRESH_SECONDS the engine re-reads /viewers from each host,
both to learn which viewers exist (for cues targeting "all viewers") and to
self-heal: if a managed viewer's projector on/off state disagrees with what
we last applied (e.g. a Tractus restart), the applied entry is dropped so
the next pass re-sends it.
"""
import logging
import threading
import time
from datetime import datetime, timedelta

from app.services import tractus_client

logger = logging.getLogger(__name__)

VIEWER_REFRESH_SECONDS = 30
# Back-off after a failed command to one (host, viewer)
KEY_RETRY_SECONDS = 5
# Back-off after a host could not be reached at all
HOST_RETRY_SECONDS = 10
# Projector commands are time-sensitive; never wait the full HTTP_TIMEOUT
MAX_COMMAND_TIMEOUT = 3

_engine: "ProjectorEngine | None" = None


class ProjectorEngine:
    def __init__(self, app):
        self.app = app
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        # Playlist timeline
        self._phase = "idle"            # idle | dwell | gap
        self._phase_until = 0.0         # time.monotonic() deadline
        self._cur_id: int | None = None
        self._cur_key: tuple | None = None
        self._cur_gap = 0
        self._jump_to: int | None = None

        # (host, viewer) → source name projected, or None (stopped)
        self._applied: dict[tuple[str, int], str | None] = {}
        self._key_retry_at: dict[tuple[str, int], float] = {}
        self._host_retry_at: dict[str, float] = {}
        self._host_state: dict[str, dict] = {}
        # host → list of viewer dicts from the last successful /viewers
        self._viewers: dict[str, list[dict]] = {}
        self._viewers_at = 0.0
        self._last_desired: dict[tuple[str, int], str | None] = {}

    # ── lifecycle ──────────────────────────────────────────────────────────

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="leash-projector", daemon=True)
        self._thread.start()
        logger.info("Projector engine: started")

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def kick(self, jump_to: int | None = None) -> None:
        """Wake the engine now.  jump_to=cue id restarts the rotation on that cue."""
        if jump_to is not None:
            with self._lock:
                self._jump_to = jump_to
        self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            wait = 1.0
            try:
                wait = self._tick()
            except Exception:
                logger.exception("Projector engine: tick failed")
            self._wake.wait(timeout=max(0.05, min(1.0, wait)))
            self._wake.clear()

    # ── one pass ───────────────────────────────────────────────────────────

    def _tick(self) -> float:
        from app import db
        from app.models import ProjectCue

        with self.app.app_context():
            cfg = self.app.config
            hosts = list(cfg.get("TRACTUS_MV_HOSTS") or [])
            port = int(cfg.get("TRACTUS_MV_PORT", 8901) or 8901)
            timeout = min(int(cfg.get("HTTP_TIMEOUT", 5) or 5), MAX_COMMAND_TIMEOUT)

            # 1. Expire timed activations
            now_utc = datetime.utcnow()
            expired = ProjectCue.query.filter(
                ProjectCue.active == True,   # noqa: E712
                ProjectCue.active_until.isnot(None),
                ProjectCue.active_until <= now_utc,
            ).all()
            for cue in expired:
                _set_inactive(cue)
                _audit(cue, False, via="expired")
            if expired:
                db.session.commit()

            cues = ProjectCue.query.filter_by(active=True).all()
            cue_data = [_snapshot(c) for c in cues]
            db.session.remove()

        if hosts and (time.monotonic() - self._viewers_at >= VIEWER_REFRESH_SECONDS):
            self._refresh_viewers(hosts, port, timeout)

        with self._lock:
            mono = time.monotonic()
            self._advance(cue_data, mono)
            desired = self._resolve(cue_data, hosts)
            self._last_desired = dict(desired)
            to_send = self._diff(desired, mono)
            next_wait = (self._phase_until - mono) if self._phase != "idle" else 1.0

        if to_send:
            self._send(to_send, port, timeout)
        return next_wait

    def _advance(self, cues: list[dict], mono: float) -> None:
        """Move the playlist timeline forward to `mono`."""
        cycle = sorted((c for c in cues if c["mode"] == "cycle"), key=lambda c: c["key"])
        by_id = {c["id"]: c for c in cycle}

        if not cycle:
            self._phase, self._cur_id, self._cur_key = "idle", None, None
            self._jump_to = None
            return

        if self._jump_to is not None:
            target = by_id.get(self._jump_to)
            self._jump_to = None
            if target:
                self._start_dwell(target, mono)
                return

        if self._phase == "idle":
            self._start_dwell(cycle[0], mono)
            return

        # A dwelling cue that was released clears immediately into its gap.
        if self._phase == "dwell" and self._cur_id not in by_id:
            self._phase, self._phase_until = "gap", mono + self._cur_gap

        for _ in range(len(cycle) * 2 + 2):   # bounded: gap/dwell of 0 s chain
            if mono < self._phase_until:
                return
            # Chain from the scheduled boundary so timing doesn't drift, but
            # after a long stall restart from now instead of replaying history.
            boundary = self._phase_until if self._phase_until >= mono - 1 else mono
            if self._phase == "dwell":
                self._phase, self._phase_until = "gap", boundary + self._cur_gap
            else:
                nxt = next((c for c in cycle if c["key"] > self._cur_key), cycle[0])
                self._start_dwell(nxt, boundary)

    def _start_dwell(self, cue: dict, start: float) -> None:
        self._phase = "dwell"
        self._cur_id, self._cur_key = cue["id"], cue["key"]
        self._cur_gap = cue["gap_seconds"]
        self._phase_until = start + cue["dwell_seconds"]

    def _resolve(self, cues: list[dict], hosts: list[str]) -> dict[tuple[str, int], str | None]:
        """Desired projector source for every managed (host, viewer)."""
        dwell = None
        if self._phase == "dwell":
            dwell = next((c for c in cues if c["id"] == self._cur_id), None)
        holds = sorted(
            (c for c in cues if c["mode"] == "hold"),
            key=lambda c: (c["priority"], c["activated_ts"]),
            reverse=True,
        )

        desired: dict[tuple[str, int], str | None] = {}
        for host in hosts:
            known = [v["index"] for v in self._viewers.get(host, [])]

            def targets(cue, _known=known):
                return cue["viewers"] if cue["viewers"] is not None else _known

            for cue in cues:
                for v in targets(cue):
                    desired.setdefault((host, v), None)
            if dwell:
                for v in targets(dwell):
                    desired[(host, v)] = dwell["source"]
            for cue in reversed(holds):         # lowest first, highest wins last
                for v in targets(cue):
                    desired[(host, v)] = cue["source"]
        return desired

    def _diff(self, desired: dict, mono: float) -> list[tuple[str, int, str | None]]:
        """Commands needed to bring hosts in line, honouring back-offs."""
        to_send = []
        for key in set(desired) | set(self._applied):
            want = desired.get(key)
            managed = key in desired
            if key in self._applied and self._applied[key] == want:
                if not managed:
                    del self._applied[key]      # released and already stopped
                continue
            if not managed and key not in self._applied:
                continue
            host = key[0]
            if self._host_retry_at.get(host, 0) > mono or self._key_retry_at.get(key, 0) > mono:
                continue
            to_send.append((host, key[1], want))
        return to_send

    def _send(self, commands: list[tuple[str, int, str | None]], port: int, timeout: int) -> None:
        from app.services.birddog_client import run_async

        async def _all():
            import aiohttp
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as s:
                import asyncio
                return await asyncio.gather(*(
                    tractus_client.send_command(
                        s, host, port,
                        tractus_client.project_source_path(v, src) if src
                        else tractus_client.project_stop_path(v),
                    )
                    for host, v, src in commands
                ))

        results = run_async(_all())
        mono = time.monotonic()
        with self._lock:
            for (host, v, src), (ok, code, error) in zip(commands, results):
                key = (host, v)
                if ok:
                    self._key_retry_at.pop(key, None)
                    if key in self._last_desired:
                        self._applied[key] = src
                    else:
                        self._applied.pop(key, None)
                    self._host_ok(host)
                    logger.info("Projector: %s viewer %d → %s", host, v, src or "stop")
                else:
                    self._key_retry_at[key] = mono + KEY_RETRY_SECONDS
                    if code == 0:
                        self._host_retry_at[host] = mono + HOST_RETRY_SECONDS
                    self._host_fail(host, error)
                    logger.warning("Projector: %s viewer %d → %s failed (%s)",
                                   host, v, src or "stop", error)

    def _refresh_viewers(self, hosts: list[str], port: int, timeout: int) -> None:
        from app.services.birddog_client import run_async

        results = run_async(tractus_client.fetch_viewers_all(hosts, port, timeout))
        with self._lock:
            self._viewers_at = time.monotonic()
            if not all(r["ok"] for r in results.values()):
                # Retry an unreachable host sooner than the normal refresh
                self._viewers_at -= VIEWER_REFRESH_SECONDS - HOST_RETRY_SECONDS
            for host, r in results.items():
                if not r["ok"]:
                    self._host_fail(host, r["error"])
                    continue
                self._host_ok(host)
                self._viewers[host] = r["viewers"]
                self._host_retry_at.pop(host, None)
                # Self-heal: drop applied state that no longer matches reality
                for v in r["viewers"]:
                    key = (host, v["index"])
                    if key in self._applied and (self._applied[key] is None) != (v["projected_source"] is None):
                        logger.info("Projector: %s viewer %d drifted — re-sending", host, v["index"])
                        del self._applied[key]

    def _host_ok(self, host: str) -> None:
        st = self._host_state.setdefault(host, {})
        st.update(ok=True, last_ok=datetime.utcnow().isoformat())

    def _host_fail(self, host: str, error: str | None) -> None:
        st = self._host_state.setdefault(host, {})
        st.update(ok=False, last_error=error, last_error_at=datetime.utcnow().isoformat())

    # ── introspection ──────────────────────────────────────────────────────

    def status(self) -> dict:
        with self._lock:
            mono = time.monotonic()
            remaining = max(0.0, self._phase_until - mono) if self._phase != "idle" else None
            hosts = list(self.app.config.get("TRACTUS_MV_HOSTS") or [])
            return {
                "running": bool(self._thread and self._thread.is_alive()),
                "phase": self._phase,
                "current_cue_id": self._cur_id if self._phase != "idle" else None,
                "phase_seconds_remaining": round(remaining, 1) if remaining is not None else None,
                "hosts": [
                    {"host": h, **self._host_state.get(h, {"ok": None})} for h in hosts
                ],
                "viewers": [
                    {
                        "host": h, "viewer": v,
                        "desired": want,
                        "applied": self._applied.get((h, v), "unknown"),
                        "in_sync": (h, v) in self._applied and self._applied[(h, v)] == want,
                    }
                    for (h, v), want in sorted(self._last_desired.items())
                ],
            }


# ---------------------------------------------------------------------------
# Helpers shared with the route modules
# ---------------------------------------------------------------------------

def _snapshot(cue) -> dict:
    """Plain-data copy of a cue so the engine never holds ORM objects."""
    return {
        "id": cue.id,
        "source": cue.source_name,
        "viewers": cue.viewer_list(),
        "mode": cue.mode,
        "dwell_seconds": max(1, cue.dwell_seconds or 1),
        "gap_seconds": max(0, cue.gap_seconds or 0),
        "priority": cue.priority or 0,
        "activated_ts": cue.activated_at.timestamp() if cue.activated_at else 0,
        "key": (cue.sort_order or 0, cue.name.lower(), cue.id),
    }


def _set_inactive(cue) -> None:
    cue.active = False
    cue.active_until = None


def _audit(cue, active: bool, via: str) -> None:
    from app.services.audit_log import project_cue_changed
    project_cue_changed(cue.name, cue.code, active, via=via)


def init_engine(app) -> ProjectorEngine:
    global _engine
    if _engine is None:
        _engine = ProjectorEngine(app)
        _engine.start()
    return _engine


def get_engine() -> ProjectorEngine | None:
    return _engine


def activate(cue, via: str, duration: int | None = None, jump: bool = False) -> None:
    """Mark a cue active (caller commits) and wake the engine."""
    cue.active = True
    cue.activated_at = datetime.utcnow()
    cue.active_until = cue.activated_at + timedelta(seconds=duration) if duration else None
    _audit(cue, True, via=via)
    _after_commit_kick(cue.id if jump else None)


def deactivate(cue, via: str) -> None:
    """Mark a cue inactive (caller commits) and wake the engine."""
    if cue.active:
        _audit(cue, False, via=via)
    _set_inactive(cue)
    _after_commit_kick(None)


def _after_commit_kick(jump_to: int | None) -> None:
    # The engine reads the DB, so wake it only once the caller's transaction
    # is visible.  A one-shot listener keeps the call sites simple.
    from sqlalchemy import event
    from app import db

    sess = db.session()

    def _kick(_session):
        if _engine:
            _engine.kick(jump_to)

    event.listen(sess, "after_commit", _kick, once=True)
