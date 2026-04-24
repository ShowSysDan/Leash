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
  POST   /api/receivers/<id>/restart      restart video subsystem
  GET    /api/receivers/<id>/status       poll live status from device
  GET    /api/receivers/bulk-reload       refresh status for every receiver
  GET    /api/receivers/<id>/settings/<group>   get a settings group
  POST   /api/receivers/<id>/settings/<group>   set a settings group

Source endpoints
  GET    /api/sources                     list cached sources
  POST   /api/sources/discover            reset + list from a reference device
  DELETE /api/sources/<id>                remove cached source
"""
import asyncio
from datetime import datetime

from flask import Blueprint, current_app, jsonify, request

from app import db
from app.models import NDIReceiver, NDISource
from app.services.birddog_client import BirdDogClient, bulk_fetch_status, run_async

api_bp = Blueprint("api", __name__)

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


def _client(receiver: NDIReceiver) -> BirdDogClient:
    cfg = current_app.config
    return BirdDogClient(
        ip=receiver.ip_address,
        port=cfg["NDI_DEVICE_PORT"],
        password=cfg["NDI_DEVICE_PASSWORD"],
        timeout=cfg["HTTP_TIMEOUT"],
    )


def _err(msg: str, code: int = 400):
    return jsonify({"error": msg}), code


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
    if "index" not in body or "ip_last_octet" not in body:
        return _err("index and ip_last_octet are required")

    if NDIReceiver.query.filter_by(index=body["index"]).first():
        return _err(f"Receiver with index {body['index']} already exists", 409)

    receiver = NDIReceiver(
        index=int(body["index"]),
        ip_last_octet=str(body["ip_last_octet"]),
        label=body.get("label"),
    )
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
        receiver.label = body["label"]
    if "ip_last_octet" in body:
        receiver.ip_last_octet = str(body["ip_last_octet"])

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
# Receivers — live actions
# ---------------------------------------------------------------------------


@api_bp.route("/receivers/<int:receiver_id>/source", methods=["POST"])
def set_source(receiver_id: int):
    receiver = NDIReceiver.query.get_or_404(receiver_id)
    body = request.get_json(silent=True) or {}
    source_name = body.get("source_name", "").strip()

    if not source_name:
        return _err("source_name is required")

    client = _client(receiver)

    if source_name == "Reboot":
        code, data = run_async(client.reboot())
        return jsonify({"action": "reboot", "status": code, "response": data})

    code, data = run_async(client.set_connect_to(source_name))
    if code == 200:
        receiver.current_source = source_name
        receiver.status = "online"
        receiver.updated_at = datetime.utcnow()
        db.session.commit()

    return jsonify({"status": code, "response": data})


@api_bp.route("/receivers/<int:receiver_id>/reboot", methods=["POST"])
def reboot_receiver(receiver_id: int):
    receiver = NDIReceiver.query.get_or_404(receiver_id)
    code, data = run_async(_client(receiver).reboot())
    return jsonify({"status": code, "response": data})


@api_bp.route("/receivers/<int:receiver_id>/restart", methods=["POST"])
def restart_receiver(receiver_id: int):
    receiver = NDIReceiver.query.get_or_404(receiver_id)
    code, data = run_async(_client(receiver).restart())
    return jsonify({"status": code, "response": data})


@api_bp.route("/receivers/<int:receiver_id>/status", methods=["GET"])
def poll_receiver_status(receiver_id: int):
    receiver = NDIReceiver.query.get_or_404(receiver_id)
    result = run_async(_client(receiver).fetch_status())

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
    cfg = {
        "NDI_SUBNET_PREFIX": current_app.config["NDI_SUBNET_PREFIX"],
        "NDI_DEVICE_PORT": current_app.config["NDI_DEVICE_PORT"],
        "NDI_DEVICE_PASSWORD": current_app.config["NDI_DEVICE_PASSWORD"],
        "HTTP_TIMEOUT": current_app.config["HTTP_TIMEOUT"],
    }

    results = run_async(bulk_fetch_status(recv_dicts, cfg))

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


@api_bp.route("/receivers/<int:receiver_id>/settings/<group>", methods=["GET"])
def get_settings(receiver_id: int, group: str):
    if group not in SETTINGS_GROUPS:
        return _err(f"Unknown settings group '{group}'", 404)

    receiver = NDIReceiver.query.get_or_404(receiver_id)
    getter_name, _ = SETTINGS_GROUPS[group]
    client = _client(receiver)
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
    client = _client(receiver)

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


@api_bp.route("/sources/discover", methods=["POST"])
def discover_sources():
    """
    Trigger NDI source discovery on a reference device (first online receiver
    by default, or pass receiver_id in the JSON body).  Runs /reset then waits
    3 s before calling /List, then caches all returned sources.
    """
    body = request.get_json(silent=True) or {}
    receiver_id = body.get("receiver_id")

    if receiver_id:
        receiver = NDIReceiver.query.get_or_404(receiver_id)
    else:
        receiver = NDIReceiver.query.filter_by(status="online").order_by(NDIReceiver.index).first()
        if not receiver:
            receiver = NDIReceiver.query.order_by(NDIReceiver.index).first()
        if not receiver:
            return _err("No receivers configured", 404)

    async def _discover():
        client = BirdDogClient(
            ip=receiver.ip_address,
            port=current_app.config["NDI_DEVICE_PORT"],
            password=current_app.config["NDI_DEVICE_PASSWORD"],
            timeout=current_app.config["HTTP_TIMEOUT"],
        )
        await client.reset_ndi()
        await asyncio.sleep(3)
        code, data = await client.get_ndi_list()
        return code, data

    code, data = run_async(_discover())

    if code != 200:
        return jsonify({"error": "Discovery failed", "status": code, "response": data}), 502

    if not isinstance(data, dict):
        return jsonify({"error": "Unexpected response format", "raw": data}), 502

    now = datetime.utcnow()
    added = []
    updated = []

    for name in data:
        clean = name.strip()
        if not clean or clean == "None":
            continue
        existing = NDISource.query.filter_by(name=clean).first()
        if existing:
            existing.discovered = True
            existing.last_seen = now
            updated.append(clean)
        else:
            db.session.add(NDISource(name=clean, discovered=True, last_seen=now))
            added.append(clean)

    # Mark previously-discovered sources that are no longer visible as stale
    seen_names = {n.strip() for n in data if n.strip() and n.strip() != "None"}
    NDISource.query.filter(
        NDISource.discovered == True,
        NDISource.name.notin_(seen_names),
    ).update({"discovered": False}, synchronize_session=False)

    db.session.commit()

    all_sources = NDISource.query.order_by(NDISource.name).all()
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
