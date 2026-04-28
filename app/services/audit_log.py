"""
Structured audit logging for Leash.

All events are emitted on the 'leash.audit' logger, which inherits the
SysLogHandler attached to the parent 'leash' logger in create_app().

In addition to syslog, notable receiver events are persisted to the
device_events table so the history is browsable per-receiver in the UI.

Log levels:
  INFO    — normal operational events (source change, scan complete, discovery)
  WARNING — expected-but-notable events (device offline, source change rejected)
  ERROR   — device communication failures
"""
import logging

_log = logging.getLogger("leash.audit")


def _persist_event(ip: str, event_type: str, detail: str) -> None:
    """Best-effort write to device_events. Never raises into the caller.

    Looks up the receiver by IP if possible so deleted receivers still get a
    historical row keyed by IP.
    """
    try:
        from app import db
        from app.models import DeviceEvent, NDIReceiver
        from flask import current_app

        if not current_app:
            return

        recv_id = None
        if ip:
            octet = ip.rsplit(".", 1)[-1]
            r = NDIReceiver.query.filter_by(ip_last_octet=octet).first()
            if r:
                recv_id = r.id

        evt = DeviceEvent(
            receiver_id=recv_id,
            ip_address=ip,
            event_type=event_type,
            detail=detail[:500] if detail else None,
        )
        db.session.add(evt)
        db.session.commit()
    except Exception:
        # Never let history persistence break a control flow.
        try:
            from app import db
            db.session.rollback()
        except Exception:
            pass
        _log.exception("audit_log: failed to persist DeviceEvent (%s)", event_type)


def source_changed(label: str, ip: str, old: str | None, new: str, via: str = "ui") -> None:
    _log.info("SOURCE_CHANGE receiver=%r ip=%s from=%r to=%r via=%s", label, ip, old or "none", new, via)
    _persist_event(ip, "SOURCE_CHANGE", f"{label}: {old or 'none'} → {new} (via {via})")


def source_change_failed(label: str, ip: str, source: str, http_status: int, via: str = "ui") -> None:
    _log.warning(
        "SOURCE_CHANGE_FAILED receiver=%r ip=%s source=%r http_status=%d via=%s",
        label, ip, source, http_status, via,
    )
    _persist_event(ip, "SOURCE_CHANGE_FAILED", f"{label}: tried {source} (HTTP {http_status}, via {via})")


def scan_complete(scanned: int, found: int, added: int, updated: int, offline: int) -> None:
    _log.info(
        "SCAN_COMPLETE scanned=%d found=%d added=%d updated=%d went_offline=%d",
        scanned, found, added, updated, offline,
    )


def receiver_added(ip: str, hostname: str) -> None:
    _log.info("RECEIVER_ADDED ip=%s hostname=%r", ip, hostname)
    _persist_event(ip, "RECEIVER_ADDED", hostname or ip)


def receiver_went_offline(ip: str, hostname: str) -> None:
    _log.warning("RECEIVER_OFFLINE ip=%s hostname=%r", ip, hostname)
    _persist_event(ip, "RECEIVER_OFFLINE", hostname or ip)


def receiver_came_online(ip: str, hostname: str) -> None:
    _log.info("RECEIVER_ONLINE ip=%s hostname=%r", ip, hostname)
    _persist_event(ip, "RECEIVER_ONLINE", hostname or ip)


def sources_discovered(added: list, updated: list, offline_count: int, via: str) -> None:
    _log.info(
        "SOURCES_DISCOVERED new=%d seen_again=%d went_offline=%d via=%s",
        len(added), len(updated), offline_count, via,
    )
    for name in added:
        _log.info("SOURCE_NEW name=%r", name)


def snapshot_recalled(snap_name: str, attempted: int, succeeded: int) -> None:
    _log.info("SNAPSHOT_RECALL name=%r attempted=%d succeeded=%d", snap_name, attempted, succeeded)


def snapshot_source_changed(label: str, ip: str, old: str | None, new: str, snap_name: str) -> None:
    _log.info(
        "SOURCE_CHANGE receiver=%r ip=%s from=%r to=%r via=snapshot:%r",
        label, ip, old or "none", new, snap_name,
    )
    _persist_event(ip, "SOURCE_CHANGE", f"{label}: {old or 'none'} → {new} (via snapshot:{snap_name})")


def group_source_sent(group_name: str, source: str, attempted: int, succeeded: int) -> None:
    _log.info(
        "GROUP_SOURCE group=%r source=%r attempted=%d succeeded=%d",
        group_name, source, attempted, succeeded,
    )


def device_error(ip: str, operation: str, http_status: int, detail: str = "") -> None:
    _log.error(
        "DEVICE_ERROR ip=%s op=%s http_status=%d detail=%s",
        ip, operation, http_status, detail or "none",
    )
    _persist_event(ip, "DEVICE_ERROR", f"{operation} (HTTP {http_status}) {detail}".strip())
