"""
Syslog handler management.

Reads SYSLOG_* settings from app.config and (re)attaches an appropriate
SysLogHandler to the 'leash' logger:

  - SYSLOG_ENABLED=False   → no handler attached
  - SYSLOG_HOST=""         → Unix domain socket (/dev/log or /var/run/syslog)
  - SYSLOG_HOST set        → UDP to that host:port

Safe to call repeatedly: removes any previous SysLogHandler first.
"""
import logging
import logging.handlers
import socket
from typing import Optional

logger = logging.getLogger(__name__)

_FACILITIES = {
    "local0": logging.handlers.SysLogHandler.LOG_LOCAL0,
    "local1": logging.handlers.SysLogHandler.LOG_LOCAL1,
    "local2": logging.handlers.SysLogHandler.LOG_LOCAL2,
    "local3": logging.handlers.SysLogHandler.LOG_LOCAL3,
    "local4": logging.handlers.SysLogHandler.LOG_LOCAL4,
    "local5": logging.handlers.SysLogHandler.LOG_LOCAL5,
    "local6": logging.handlers.SysLogHandler.LOG_LOCAL6,
    "local7": logging.handlers.SysLogHandler.LOG_LOCAL7,
    "user":   logging.handlers.SysLogHandler.LOG_USER,
    "daemon": logging.handlers.SysLogHandler.LOG_DAEMON,
}


def _resolve_facility(name: str) -> int:
    return _FACILITIES.get((name or "").strip().lower(), logging.handlers.SysLogHandler.LOG_LOCAL0)


def _remove_existing_handler(target_logger: logging.Logger) -> None:
    for h in list(target_logger.handlers):
        if isinstance(h, logging.handlers.SysLogHandler):
            try:
                h.close()
            except Exception:
                pass
            target_logger.removeHandler(h)


def apply_syslog_config(app) -> Optional[logging.handlers.SysLogHandler]:
    """(Re)configure the syslog handler from app.config. Returns the new handler or None."""
    cfg = app.config
    leash_log = logging.getLogger("leash")
    _remove_existing_handler(leash_log)

    if not cfg.get("SYSLOG_ENABLED", True):
        app.logger.info("Syslog: disabled by configuration")
        return None

    level = logging.DEBUG if app.debug else logging.INFO
    fmt = logging.Formatter("leash %(name)s %(levelname)s: %(message)s")
    facility = _resolve_facility(cfg.get("SYSLOG_FACILITY", "local0"))

    host = (cfg.get("SYSLOG_HOST") or "").strip()
    if host:
        port = int(cfg.get("SYSLOG_PORT", 514) or 514)
        try:
            handler = logging.handlers.SysLogHandler(
                address=(host, port),
                facility=facility,
                socktype=socket.SOCK_DGRAM,
            )
        except OSError as exc:
            app.logger.warning("Syslog: could not connect to %s:%d — %s", host, port, exc)
            return None
        handler.setFormatter(fmt)
        handler.setLevel(level)
        leash_log.setLevel(level)
        leash_log.addHandler(handler)
        app.logger.info("Syslog: handler attached → %s:%d (facility=%s)",
                        host, port, cfg.get("SYSLOG_FACILITY", "local0"))
        return handler

    # Local Unix socket fallback
    for addr in ("/dev/log", "/var/run/syslog"):
        try:
            handler = logging.handlers.SysLogHandler(address=addr, facility=facility)
            handler.setFormatter(fmt)
            handler.setLevel(level)
            leash_log.setLevel(level)
            leash_log.addHandler(handler)
            app.logger.info("Syslog: handler attached → %s (facility=%s)",
                            addr, cfg.get("SYSLOG_FACILITY", "local0"))
            return handler
        except OSError:
            continue

    app.logger.warning("Syslog: no Unix socket found and no remote host configured")
    return None


def send_test_message(app, message: str = "Leash syslog test") -> dict:
    """Emit a test record on the leash.audit logger and report what happened."""
    leash_log = logging.getLogger("leash")
    has_handler = any(
        isinstance(h, logging.handlers.SysLogHandler) for h in leash_log.handlers
    )
    if not has_handler:
        return {"sent": False, "reason": "No syslog handler is attached. Check SYSLOG_ENABLED, SYSLOG_HOST, and the local /dev/log socket."}

    audit = logging.getLogger("leash.audit")
    audit.info("SYSLOG_TEST message=%r", message)

    cfg = app.config
    target = (cfg.get("SYSLOG_HOST") or "").strip()
    return {
        "sent": True,
        "target": f"{target}:{cfg.get('SYSLOG_PORT', 514)}" if target else "local Unix socket",
        "facility": cfg.get("SYSLOG_FACILITY", "local0"),
        "message": message,
    }
