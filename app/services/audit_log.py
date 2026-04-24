"""
Structured audit logging for Leash.

All events are emitted on the 'leash.audit' logger, which inherits the
SysLogHandler attached to the parent 'leash' logger in create_app().

Log levels:
  INFO    — normal operational events (source change, scan complete, discovery)
  WARNING — expected-but-notable events (device offline, source change rejected)
  ERROR   — device communication failures
"""
import logging

_log = logging.getLogger("leash.audit")


def source_changed(label: str, ip: str, old: str | None, new: str, via: str = "ui") -> None:
    _log.info("SOURCE_CHANGE receiver=%r ip=%s from=%r to=%r via=%s", label, ip, old or "none", new, via)


def source_change_failed(label: str, ip: str, source: str, http_status: int, via: str = "ui") -> None:
    _log.warning(
        "SOURCE_CHANGE_FAILED receiver=%r ip=%s source=%r http_status=%d via=%s",
        label, ip, source, http_status, via,
    )


def scan_complete(scanned: int, found: int, added: int, updated: int, offline: int) -> None:
    _log.info(
        "SCAN_COMPLETE scanned=%d found=%d added=%d updated=%d went_offline=%d",
        scanned, found, added, updated, offline,
    )


def receiver_added(ip: str, hostname: str) -> None:
    _log.info("RECEIVER_ADDED ip=%s hostname=%r", ip, hostname)


def receiver_went_offline(ip: str, hostname: str) -> None:
    _log.warning("RECEIVER_OFFLINE ip=%s hostname=%r", ip, hostname)


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
