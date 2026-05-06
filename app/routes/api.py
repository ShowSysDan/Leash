"""
REST API blueprint — all responses are JSON.

Receiver endpoints
  GET    /api/receivers                   list all
  POST   /api/receivers                   create
  GET    /api/receivers/<id>              get one
  PUT    /api/receivers/<id>              update label / ip_last_octet
  DELETE /api/receivers/<id>              delete
  POST   /api/receivers/<id>/source       set NDI source
  POST   /api/receivers/<id>/reboot       reboot device
  POST   /api/receivers/bulk-reboot       reboot all online devices
  POST   /api/receivers/<id>/restart      restart video subsystem
  GET    /api/receivers/<id>/status       poll live status from device
  GET    /api/receivers/bulk-reload       refresh status for every receiver
  GET    /api/receivers/<id>/settings/<group>   get a settings group
  POST   /api/receivers/<id>/settings/<group>   set a settings group

Scan endpoint
  POST   /api/scan                        subnet scan — discovers decoders AND cameras

Source endpoints
  GET    /api/sources                     list cached sources
  POST   /api/sources/discover            poll Tractus MV API and sync NDISource table
  DELETE /api/sources/<id>                remove cached source
"""
import asyncio
import logging
from datetime import datetime

import aiohttp
from flask import Blueprint, current_app, jsonify, request

from app import db
from app.models import DeviceEvent, NDIReceiver, NDISource, PTZCamera
from app.services.audit_log import (
    device_error,
    receiver_added,
    receiver_went_offline,
    scan_complete,
    source_change_failed,
    source_changed,
    sources_discovered,
)
from app.routes._helpers import err as _err, valid_octet, MAX_LABEL
from app.services.birddog_client import (
    BirdDogClient,
    bulk_fetch_status,
    client_config,
    client_from_receiver,
    run_async,
)
from app.services.scanner import scan_subnet
from app.services.tractus_client import fetch_sources

logger = logging.getLogger(__name__)

api_bp = Blueprint("api", __name__)


@api_bp.route("/version", methods=["GET"])
def version():
    from app.__version__ import __version__
    return jsonify({"version": __version__})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SETTINGS_GROUPS = {
    "analog_audio": ("get_analog_audio", "set_analog_audio"),
    "operation_mode": ("get_operation_mode", "set_operation_mode"),
    "video_output": ("get_video_output_interface", "set_video_output_interface"),
    "encode_transport": ("get_encode_transport", "set_encode_transport"),
    "encode_setup": ("get_encode_setup", "set_encode_setup"),
    "decode_transport": ("get_decode_transport", "set_decode_transport"),
    "decode_setup": ("get_decode_setup", "set_decode_setup"),
    "decode_status": ("get_decode_status", None),
    "ptz": ("get_ptz_setup", "set_ptz_setup"),
    "exposure": ("get_exposure", "set_exposure"),
    "white_balance": ("get_white_balance", "set_white_balance"),
    "picture": ("get_picture", "set_picture"),
    "colour_matrix": ("get_colour_matrix", "set_colour_matrix"),
    "advanced": ("get_advanced", "set_advanced"),
    "external": ("get_external", "set_external"),
    "detail": ("get_detail", "set_detail"),
    "gamma": ("get_gamma", "set_gamma"),
    "sil2_codec": ("get_sil2_codec", "set_sil2_codec"),
    "sil2_enc": ("get_sil2_enc", "set_sil2_enc"),
    "ndi_discovery": ("get_ndi_discovery_server", "set_ndi_discovery_server"),
    "ndi_group": ("get_ndi_group_name", "set_ndi_group_name"),
    "ndi_offsubnet": ("get_ndi_off_subnet", "set_ndi_off_subnet"),
}


# ---------------------------------------------------------------------------
# Receivers — CRUD
# ---------------------------------------------------------------------------


@api_bp.route("/receivers", methods=["GET"])
def list_receivers():
    receivers = NDIReceiver.query.order_by(NDIReceiver.index).all()
    return jsonify([r.to_dict() for r in receivers])


@api_bp.route("/receivers", methods=["POST"])
def create_receiver():
    body = request.get_json(silent=True) or {}
    ok, octet = valid_octet(body.get("ip_last_octet"))
    if not ok:
        return _err("ip_last_octet must be an integer from 1 to 254")
    if NDIReceiver.query.filter_by(ip_last_octet=octet).first():
        return _err(f"Receiver with IP octet {octet} already exists", 409)

    # Use provided index, or last octet as integer, or next sequential
    index = int(body["index"]) if "index" in body else int(octet)
    if NDIReceiver.query.filter_by(index=index).first():
        max_idx = db.session.query(db.func.max(NDIReceiver.index)).scalar() or 0
        index = max_idx + 1

    label = (body.get("label") or None)
    if label is not None:
        label = str(label).strip()[:MAX_LABEL] or None

    receiver = NDIReceiver(index=index, ip_last_octet=octet, label=label)
    db.session.add(receiver)
    db.session.commit()
    return jsonify(receiver.to_dict()), 201


@api_bp.route("/receivers/<int:receiver_id>", methods=["GET"])
def get_receiver(receiver_id: int):
    receiver = NDIReceiver.query.get_or_404(receiver_id)
    return jsonify(receiver.to_dict())


@api_bp.route("/receivers/<int:receiver_id>", methods=["PUT"])
def update_receiver(receiver_id: int):
    receiver = NDIReceiver.query.get_or_404(receiver_id)
    body = request.get_json(silent=True) or {}

    if "label" in body:
        raw = body["label"]
        receiver.label = (str(raw).strip()[:MAX_LABEL] or None) if raw else None
    if "ip_last_octet" in body:
        ok, octet = valid_octet(body["ip_last_octet"])
        if not ok:
            return _err("ip_last_octet must be an integer from 1 to 254")
        receiver.ip_last_octet = octet

    receiver.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify(receiver.to_dict())


@api_bp.route("/receivers/<int:receiver_id>", methods=["DELETE"])
def delete_receiver(receiver_id: int):
    receiver = NDIReceiver.query.get_or_404(receiver_id)
    db.session.delete(receiver)
    db.session.commit()
    return jsonify({"deleted": receiver_id})


# ---------------------------------------------------------------------------
# Subnet scan — auto-detect decoders AND cameras
# ---------------------------------------------------------------------------


def _upsert_decoder(device: dict, now: datetime) -> tuple[str, object]:
    """Upsert one decoder dict. Returns ('added'|'updated', obj)."""
    octet = device["ip_last_octet"]
    existing = NDIReceiver.query.filter_by(ip_last_octet=octet).first()
    if existing:
        for field in ("hostname", "hardware_version", "firmware_version",
                      "serial_number", "mcu_version", "video_format",
                      "network_config_method", "gateway", "network_mask", "fallback_ip"):
            val = device.get(field)
            if val:
                setattr(existing, field, val)
        existing.status = "online"
        existing.last_seen = now
        existing.updated_at = now
        return "updated", existing
    else:
        index = int(octet) if octet.isdigit() else None
        if index is None or NDIReceiver.query.filter_by(index=index).first():
            index = (db.session.query(db.func.max(NDIReceiver.index)).scalar() or 0) + 1
        recv = NDIReceiver(
            index=index, ip_last_octet=octet, hostname=device["hostname"],
            status="online", first_seen=now, last_seen=now,
            hardware_version=device.get("hardware_version"),
            firmware_version=device.get("firmware_version"),
            serial_number=device.get("serial_number"),
            mcu_version=device.get("mcu_version"),
            video_format=device.get("video_format"),
            network_config_method=device.get("network_config_method"),
            gateway=device.get("gateway"),
            network_mask=device.get("network_mask"),
            fallback_ip=device.get("fallback_ip"),
        )
        db.session.add(recv)
        db.session.flush()
        return "added", recv


def _upsert_camera(device: dict, now: datetime) -> tuple[str, object]:
    """Upsert one camera dict. Returns ('added'|'updated', obj)."""
    octet = device["ip_last_octet"]
    existing = PTZCamera.query.filter_by(ip_last_octet=octet).first()
    if existing:
        for field in ("hostname", "model", "hardware_version", "firmware_version",
                      "serial_number", "mcu_version", "network_config_method",
                      "gateway", "network_mask"):
            val = device.get(field)
            if val:
                setattr(existing, field, val)
        existing.status = "online"
        existing.last_seen = now
        existing.updated_at = now
        return "updated", existing
    else:
        index = int(octet) if octet.isdigit() else None
        if index is None or PTZCamera.query.filter_by(index=index).first():
            index = (db.session.query(db.func.max(PTZCamera.index)).scalar() or 0) + 1
        cam = PTZCamera(
            index=index, ip_last_octet=octet, hostname=device["hostname"],
            model=device.get("model"), status="online", first_seen=now, last_seen=now,
            hardware_version=device.get("hardware_version"),
            firmware_version=device.get("firmware_version"),
            serial_number=device.get("serial_number"),
            mcu_version=device.get("mcu_version"),
            network_config_method=device.get("network_config_method"),
            gateway=device.get("gateway"),
            network_mask=device.get("network_mask"),
        )
        db.session.add(cam)
        db.session.flush()
        return "added", cam


@api_bp.route("/scan", methods=["POST"])
def scan():
    """
    Concurrently probe the configured subnet.
    Discovers BirdDog PLAY decoders AND PTZ cameras in one pass.
    Both types are upserted; known devices not found are marked offline.
    """
    body = request.get_json(silent=True) or {}
    try:
        start = int(body.get("start", 1))
        end = int(body.get("end", 254))
    except (TypeError, ValueError):
        return _err("start and end must be integers")

    # Clamp to the legal IPv4 last-octet range and reject inverted bounds so
    # a hostile or buggy client cannot ask us to fire 4-billion HTTP probes.
    start = max(1, min(start, 254))
    end = max(1, min(end, 254))
    if start > end:
        return _err("start must be ≤ end")

    cfg = current_app.config
    cameras_enabled = bool(cfg.get("CAMERAS_ENABLED", False))
    decoders, cameras = run_async(scan_subnet(
        prefix=cfg["NDI_SUBNET_PREFIX"],
        port=cfg["NDI_DEVICE_PORT"],
        password=cfg["NDI_DEVICE_PASSWORD"],
        timeout=2,
        start=start,
        end=end,
    ))
    # Discard discovered cameras while the feature is paused so the table
    # neither gets new rows nor has its existing rows toggled offline.
    if not cameras_enabled:
        cameras = []

    now = datetime.utcnow()
    recv_added, recv_updated = [], []
    cam_added = 0

    for d in decoders:
        action, obj = _upsert_decoder(d, now)
        (recv_added if action == "added" else recv_updated).append(obj)
    for c in cameras:
        action, _ = _upsert_camera(c, now)
        if action == "added":
            cam_added += 1

    # Mark decoders not found as offline
    decoder_octets = {d["ip_last_octet"] for d in decoders}
    going_offline = NDIReceiver.query.filter(
        NDIReceiver.ip_last_octet.notin_(decoder_octets),
        NDIReceiver.status == "online",
    ).all()
    NDIReceiver.query.filter(
        NDIReceiver.ip_last_octet.notin_(decoder_octets)
    ).update({"status": "offline", "updated_at": now}, synchronize_session=False)

    if cameras_enabled:
        # Only sweep camera state when the feature is on. Otherwise leave
        # whatever rows pre-exist alone so flipping the flag back later
        # doesn't surprise the operator with mass status changes.
        camera_octets = {c["ip_last_octet"] for c in cameras}
        PTZCamera.query.filter(
            PTZCamera.ip_last_octet.notin_(camera_octets)
        ).update({"status": "offline", "updated_at": now}, synchronize_session=False)

    db.session.commit()

    for r in recv_added:
        receiver_added(r.ip_address, r.hostname or "")
    for r in going_offline:
        receiver_went_offline(r.ip_address, r.hostname or "")
    scan_complete(
        scanned=end - start + 1,
        found=len(decoders) + len(cameras),
        added=len(recv_added),
        updated=len(recv_updated),
        offline=len(going_offline),
    )

    all_receivers = NDIReceiver.query.order_by(NDIReceiver.index).all()
    return jsonify({
        "scanned": end - start + 1,
        "decoders_found": len(decoders),
        "cameras_found": len(cameras),
        "cameras_added": cam_added,
        "added": len(recv_added),
        "updated": len(recv_updated),
        "receivers": [r.to_dict() for r in all_receivers],
    })


# ---------------------------------------------------------------------------
# Receivers — live actions
# ---------------------------------------------------------------------------


@api_bp.route("/receivers/<int:receiver_id>/source", methods=["POST"])
def set_source(receiver_id: int):
    receiver = NDIReceiver.query.get_or_404(receiver_id)
    body = request.get_json(silent=True) or {}
    source_name = body.get("source_name", "").strip()

    if not source_name:
        return _err("source_name is required")

    client = client_from_receiver(receiver, current_app.config)
    label = receiver.display_name

    if source_name == "Reboot":
        code, data = run_async(client.reboot())
        return jsonify({"action": "reboot", "status": code, "response": data})

    old_source = receiver.current_source
    code, data = run_async(client.set_connect_to(source_name))
    if code == 200:
        receiver.current_source = source_name
        receiver.status = "online"
        receiver.updated_at = datetime.utcnow()
        db.session.commit()
        source_changed(label, receiver.ip_address, old_source, source_name, via="ui")
    else:
        device_error(receiver.ip_address, "set_source", code)
        source_change_failed(label, receiver.ip_address, source_name, code, via="ui")

    return jsonify({"status": code, "response": data})


@api_bp.route("/receivers/<int:receiver_id>/reboot", methods=["POST"])
def reboot_receiver(receiver_id: int):
    receiver = NDIReceiver.query.get_or_404(receiver_id)
    code, data = run_async(client_from_receiver(receiver, current_app.config).reboot())
    return jsonify({"status": code, "response": data})


@api_bp.route("/receivers/bulk-reboot", methods=["POST"])
def bulk_reboot():
    """Reboot every online receiver concurrently."""
    cfg = current_app.config
    receivers = NDIReceiver.query.filter_by(status="online").order_by(NDIReceiver.index).all()
    if not receivers:
        return jsonify({"rebooted": 0, "failed": 0, "results": []})

    concurrency = cfg.get("RECALL_CONCURRENCY", 10)
    prefix   = cfg["NDI_SUBNET_PREFIX"]
    port     = cfg["NDI_DEVICE_PORT"]
    password = cfg["NDI_DEVICE_PASSWORD"]
    timeout  = cfg["HTTP_TIMEOUT"]

    async def _reboot_all():
        sem = asyncio.Semaphore(concurrency)
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout)
        ) as session:
            async def _one(recv):
                async with sem:
                    try:
                        client = BirdDogClient(
                            recv.ip_address, port=port, password=password,
                            timeout=timeout, session=session,
                        )
                        code, _ = await client.reboot()
                    except Exception as exc:
                        logger.warning("Bulk reboot %s raised: %s", recv.ip_address, exc)
                        code = 0
                    return {"id": recv.id, "ip": recv.ip_address,
                            "name": recv.display_name, "ok": code == 200, "status": code}
            return await asyncio.gather(*[_one(r) for r in receivers], return_exceptions=False)

    results = run_async(_reboot_all())
    ok_count = sum(1 for r in results if r["ok"])
    failed_count = len(results) - ok_count

    logger.info("Bulk reboot: %d/%d receivers responded OK", ok_count, len(receivers))
    return jsonify({"rebooted": ok_count, "failed": failed_count, "results": results})


@api_bp.route("/receivers/<int:receiver_id>/restart", methods=["POST"])
def restart_receiver(receiver_id: int):
    receiver = NDIReceiver.query.get_or_404(receiver_id)
    code, data = run_async(client_from_receiver(receiver, current_app.config).restart())
    return jsonify({"status": code, "response": data})


@api_bp.route("/receivers/<int:receiver_id>/status", methods=["GET"])
def poll_receiver_status(receiver_id: int):
    receiver = NDIReceiver.query.get_or_404(receiver_id)
    result = run_async(client_from_receiver(receiver, current_app.config).fetch_status())

    # Persist to DB
    receiver.hostname = result.get("hostname") or receiver.hostname
    receiver.current_source = result.get("current_source") or receiver.current_source
    receiver.status = "online" if result["online"] else "offline"
    if result.get("firmware_version"):
        receiver.firmware_version = result["firmware_version"]
    if result.get("serial_number"):
        receiver.serial_number = result["serial_number"]
    if result.get("video_format"):
        receiver.video_format = result["video_format"]
    receiver.updated_at = datetime.utcnow()
    db.session.commit()

    return jsonify({**receiver.to_dict(), **result})


@api_bp.route("/receivers/bulk-reload", methods=["GET"])
def bulk_reload():
    """Concurrently refresh status for every receiver and persist to DB."""
    receivers = NDIReceiver.query.order_by(NDIReceiver.index).all()
    if not receivers:
        return jsonify([])

    recv_dicts = [{"id": r.id, "ip_last_octet": r.ip_last_octet} for r in receivers]
    results = run_async(bulk_fetch_status(recv_dicts, client_config(current_app.config)))

    # Build a lookup and persist
    result_map = {r["id"]: r for r in results}
    for recv in receivers:
        r = result_map.get(recv.id, {})
        recv.status = "online" if r.get("online") else "offline"
        if r.get("hostname"):
            recv.hostname = r["hostname"]
        if r.get("current_source"):
            recv.current_source = r["current_source"]
        if r.get("firmware_version"):
            recv.firmware_version = r["firmware_version"]
        if r.get("serial_number"):
            recv.serial_number = r["serial_number"]
        if r.get("video_format"):
            recv.video_format = r["video_format"]
        recv.updated_at = datetime.utcnow()

    db.session.commit()

    return jsonify([r.to_dict() for r in receivers])


# ---------------------------------------------------------------------------
# Receivers — settings groups (generic pass-through)
# ---------------------------------------------------------------------------


@api_bp.route("/receivers/<int:receiver_id>/history", methods=["GET"])
def receiver_history(receiver_id: int):
    """Return up to ?limit=N (default 100, max 500) most recent device events.

    Includes any rows that match the receiver's current IP even if the
    receiver_id FK was nulled out by a previous delete.
    """
    receiver = NDIReceiver.query.get_or_404(receiver_id)
    limit = max(1, min(int(request.args.get("limit", 100)), 500))

    events = (
        DeviceEvent.query
        .filter(
            db.or_(
                DeviceEvent.receiver_id == receiver_id,
                DeviceEvent.ip_address == receiver.ip_address,
            )
        )
        .order_by(DeviceEvent.timestamp.desc())
        .limit(limit)
        .all()
    )
    return jsonify([e.to_dict() for e in events])


@api_bp.route("/receivers/<int:receiver_id>/settings/<group>", methods=["GET"])
def get_settings(receiver_id: int, group: str):
    if group not in SETTINGS_GROUPS:
        return _err(f"Unknown settings group '{group}'", 404)

    receiver = NDIReceiver.query.get_or_404(receiver_id)
    getter_name, _ = SETTINGS_GROUPS[group]
    client = client_from_receiver(receiver, current_app.config)
    code, data = run_async(getattr(client, getter_name)())
    return jsonify({"status": code, "group": group, "data": data})


@api_bp.route("/receivers/<int:receiver_id>/settings/<group>", methods=["POST"])
def set_settings(receiver_id: int, group: str):
    if group not in SETTINGS_GROUPS:
        return _err(f"Unknown settings group '{group}'", 404)

    _, setter_name = SETTINGS_GROUPS[group]
    if not setter_name:
        return _err(f"Settings group '{group}' is read-only", 405)

    receiver = NDIReceiver.query.get_or_404(receiver_id)
    body = request.get_json(silent=True) or {}
    client = client_from_receiver(receiver, current_app.config)

    # Plain-text setters expect a string value under key "value"
    if group in ("operation_mode", "video_output", "ndi_group", "ndi_offsubnet"):
        payload = body.get("value", body)
    else:
        payload = body

    code, data = run_async(getattr(client, setter_name)(payload))
    return jsonify({"status": code, "group": group, "data": data})


# ---------------------------------------------------------------------------
# Sources — cached list
# ---------------------------------------------------------------------------


@api_bp.route("/sources", methods=["GET"])
def list_sources():
    sources = NDISource.query.order_by(NDISource.name).all()
    return jsonify([s.to_dict() for s in sources])


def _sync_sources(names: list[str]) -> tuple[list[str], list[str], int]:
    """Upsert NDISource rows from a list of source names.

    Returns (added, updated, going_offline_count).
    """
    now = datetime.utcnow()
    added: list[str] = []
    updated: list[str] = []
    seen: set[str] = set()

    for name in names:
        clean = name.strip()
        if not clean:
            continue
        seen.add(clean)
        existing = NDISource.query.filter_by(name=clean).first()
        if existing:
            existing.discovered = True
            existing.last_seen = now
            if existing.source_index is None:
                existing.source_index = NDISource.next_index()
                db.session.flush()
            updated.append(clean)
        else:
            new_src = NDISource(
                name=clean,
                discovered=True,
                last_seen=now,
                source_index=NDISource.next_index(),
            )
            db.session.add(new_src)
            db.session.flush()
            added.append(clean)

    going_offline_count = NDISource.query.filter(
        NDISource.discovered == True,   # noqa: E712
        NDISource.name.notin_(seen),
    ).count()
    NDISource.query.filter(
        NDISource.discovered == True,   # noqa: E712
        NDISource.name.notin_(seen),
    ).update({"discovered": False}, synchronize_session=False)

    db.session.commit()
    return added, updated, going_offline_count


@api_bp.route("/sources/discover", methods=["POST"])
def discover_sources():
    """
    Manually trigger a Tractus MV API poll and sync the NDISource table.
    Primary host is tried first; the fallback host is used if primary is unreachable.
    """
    cfg = current_app.config
    names = run_async(fetch_sources(
        hosts=cfg["TRACTUS_MV_HOSTS"],
        port=cfg["TRACTUS_MV_PORT"],
        timeout=cfg["HTTP_TIMEOUT"],
    ))

    if names is None:
        return jsonify({"error": "Tractus MV API unreachable"}), 502

    added, updated, going_offline_count = _sync_sources(names)

    sources_discovered(added, updated, going_offline_count, via="tractus-mv")

    all_sources = NDISource.query.order_by(NDISource.source_index).all()
    return jsonify({
        "added": added,
        "updated": updated,
        "total": len(all_sources),
        "sources": [s.to_dict() for s in all_sources],
    })


@api_bp.route("/sources/<int:source_id>", methods=["DELETE"])
def delete_source(source_id: int):
    source = NDISource.query.get_or_404(source_id)
    db.session.delete(source)
    db.session.commit()
    return jsonify({"deleted": source_id})
