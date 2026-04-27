"""
PTZ Camera API blueprint.

Cameras
  GET    /api/cameras                       list all
  POST   /api/cameras                       create manually
  GET    /api/cameras/<id>                  get one
  PUT    /api/cameras/<id>                  update label
  DELETE /api/cameras/<id>                  delete
  GET    /api/cameras/<id>/status           poll live /about from device

PTZ control (velocity — send move, send stop separately)
  POST   /api/cameras/<id>/ptz              move or stop (pan/tilt/zoom)
  POST   /api/cameras/<id>/focus            focus control (near/far/stop/auto)

Presets (numbers stored on device; labels stored in DB)
  GET    /api/cameras/<id>/presets          list labeled presets
  POST   /api/cameras/<id>/presets          add / update a preset label
  DELETE /api/cameras/<id>/presets/<num>    remove a preset label
  POST   /api/cameras/<id>/presets/<num>/recall   recall preset on device
  POST   /api/cameras/<id>/presets/<num>/save     save current position as preset
"""
import logging
from datetime import datetime

from flask import Blueprint, current_app, jsonify, request

from app import db
from app.models import CameraPreset, PTZCamera
from app.routes._helpers import MAX_LABEL, err as _err, valid_octet
from app.services.birddog_client import client_from_camera, ptz_client_from_camera, run_async

logger = logging.getLogger(__name__)

cameras_api_bp = Blueprint("cameras_api", __name__)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

@cameras_api_bp.route("/cameras", methods=["GET"])
def list_cameras():
    cameras = PTZCamera.query.order_by(PTZCamera.index).all()
    return jsonify([c.to_dict() for c in cameras])


@cameras_api_bp.route("/cameras", methods=["POST"])
def create_camera():
    body = request.get_json(silent=True) or {}
    ok, octet = valid_octet(body.get("ip_last_octet"))
    if not ok:
        return _err("ip_last_octet must be an integer from 1 to 254")
    if PTZCamera.query.filter_by(ip_last_octet=octet).first():
        return _err(f"Camera with IP octet {octet} already exists", 409)

    index = int(body["index"]) if "index" in body else int(octet)
    if PTZCamera.query.filter_by(index=index).first():
        index = (db.session.query(db.func.max(PTZCamera.index)).scalar() or 0) + 1

    label = (str(body["label"]).strip()[:MAX_LABEL] or None) if body.get("label") else None
    cam = PTZCamera(index=index, ip_last_octet=octet, label=label)
    db.session.add(cam)
    db.session.commit()
    return jsonify(cam.to_dict()), 201


@cameras_api_bp.route("/cameras/<int:camera_id>", methods=["GET"])
def get_camera(camera_id: int):
    return jsonify(PTZCamera.query.get_or_404(camera_id).to_dict())


@cameras_api_bp.route("/cameras/<int:camera_id>", methods=["PUT"])
def update_camera(camera_id: int):
    cam = PTZCamera.query.get_or_404(camera_id)
    body = request.get_json(silent=True) or {}
    if "label" in body:
        raw = body["label"]
        cam.label = (str(raw).strip()[:MAX_LABEL] or None) if raw else None
    cam.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify(cam.to_dict())


@cameras_api_bp.route("/cameras/<int:camera_id>", methods=["DELETE"])
def delete_camera(camera_id: int):
    cam = PTZCamera.query.get_or_404(camera_id)
    db.session.delete(cam)
    db.session.commit()
    return jsonify({"deleted": camera_id})


@cameras_api_bp.route("/cameras/<int:camera_id>/status", methods=["GET"])
def camera_status(camera_id: int):
    cam = PTZCamera.query.get_or_404(camera_id)
    client = client_from_camera(cam, current_app.config)
    code, data = run_async(client.get_about())
    if code == 200 and isinstance(data, dict):
        cam.hostname = (data.get("HostName") or cam.hostname or "").strip()
        cam.firmware_version = data.get("FirmwareVersion") or cam.firmware_version
        cam.status = "online"
        cam.last_seen = datetime.utcnow()
        cam.updated_at = cam.last_seen
        db.session.commit()
    else:
        cam.status = "offline"
        cam.updated_at = datetime.utcnow()
        db.session.commit()
    return jsonify({"status": code, "camera": cam.to_dict(), "raw": data})


@cameras_api_bp.route("/cameras/<int:camera_id>/probe", methods=["GET"])
def probe_camera(camera_id: int):
    """Probe which PTZ/focus endpoints the device responds to (for diagnostics)."""
    cam = PTZCamera.query.get_or_404(camera_id)
    client = client_from_camera(cam, current_app.config)

    ptz_client = ptz_client_from_camera(cam, current_app.config)
    ptz_port   = current_app.config.get("CAMERA_PTZ_PORT", 6791)

    async def _probe_all():
        about_code, about_data = await client._get("/about")
        port8080 = {"GET /about": about_code}
        port6791 = {}
        for path in ["/birddogptz", "/birddogfocus", "/birddogRecallPreset",
                     "/birddogSavePreset", "/birddogptzcontrol"]:
            code, _ = await ptz_client._post(path, {})
            port6791[f"POST {path}"] = code
        return about_data if about_code == 200 else None, port8080, port6791

    about_data, p8080, p6791 = run_async(_probe_all())
    return jsonify({
        "camera_ip": cam.ip_address,
        "port_8080": p8080,
        f"port_{ptz_port}": p6791,
        "about": about_data,
    })


# ---------------------------------------------------------------------------
# PTZ control
# ---------------------------------------------------------------------------

@cameras_api_bp.route("/cameras/<int:camera_id>/ptz", methods=["POST"])
def ptz_command(camera_id: int):
    """
    Send a velocity PTZ command.
    Body: {"pan":"LEFT|RIGHT|STOP", "tilt":"UP|DOWN|STOP",
           "zoom":"TELE|WIDE|STOP", "speed": 1-24}
    Omitted axes default to STOP.
    """
    cam = PTZCamera.query.get_or_404(camera_id)
    body = request.get_json(silent=True) or {}
    pan   = str(body.get("pan",  "STOP")).upper()
    tilt  = str(body.get("tilt", "STOP")).upper()
    zoom  = str(body.get("zoom", "STOP")).upper()
    speed = int(body.get("speed", 5))

    valid_pan  = {"LEFT", "RIGHT", "STOP"}
    valid_tilt = {"UP", "DOWN", "STOP"}
    valid_zoom = {"TELE", "WIDE", "STOP"}
    if pan not in valid_pan or tilt not in valid_tilt or zoom not in valid_zoom:
        return _err("Invalid pan/tilt/zoom value")

    client = ptz_client_from_camera(cam, current_app.config)
    current_app.logger.warning(
        "PTZ %s: pan=%s tilt=%s zoom=%s → %s:%d",
        cam.display_name, pan, tilt, zoom,
        cam.ip_address, current_app.config.get("CAMERA_PTZ_PORT", 6791),
    )
    code, data = run_async(client.ptz_move(pan=pan, tilt=tilt, zoom=zoom, speed=speed))
    current_app.logger.warning("PTZ %s response: HTTP %d %s", cam.display_name, code, data)
    return jsonify({"status": code, "response": data})


@cameras_api_bp.route("/cameras/<int:camera_id>/focus", methods=["POST"])
def focus_command(camera_id: int):
    """Body: {"action":"NEAR|FAR|STOP|AUTO"}"""
    cam = PTZCamera.query.get_or_404(camera_id)
    body = request.get_json(silent=True) or {}
    action = str(body.get("action", "STOP")).upper()
    if action not in {"NEAR", "FAR", "STOP", "AUTO"}:
        return _err("action must be NEAR, FAR, STOP, or AUTO")
    client = ptz_client_from_camera(cam, current_app.config)
    code, data = run_async(client.focus_control(action))
    return jsonify({"status": code, "response": data})


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------

@cameras_api_bp.route("/cameras/<int:camera_id>/presets", methods=["GET"])
def list_presets(camera_id: int):
    cam = PTZCamera.query.get_or_404(camera_id)
    return jsonify([p.to_dict() for p in cam.presets])


@cameras_api_bp.route("/cameras/<int:camera_id>/presets", methods=["POST"])
def upsert_preset(camera_id: int):
    """Add or rename a preset label. Body: {"preset_number": 0-99, "name": "..."}"""
    cam = PTZCamera.query.get_or_404(camera_id)
    body = request.get_json(silent=True) or {}
    num = body.get("preset_number")
    name = str(body.get("name", "")).strip()

    if num is None or not isinstance(num, int) or not (0 <= num <= 99):
        return _err("preset_number must be an integer 0–99")
    if not name:
        return _err("name is required")
    if len(name) > 100:
        return _err("name too long (max 100 chars)")

    preset = CameraPreset.query.filter_by(camera_id=camera_id, preset_number=num).first()
    if preset:
        preset.name = name
    else:
        preset = CameraPreset(camera_id=camera_id, preset_number=num, name=name)
        db.session.add(preset)
    db.session.commit()
    return jsonify(preset.to_dict()), 201


@cameras_api_bp.route("/cameras/<int:camera_id>/presets/<int:preset_num>", methods=["DELETE"])
def delete_preset(camera_id: int, preset_num: int):
    PTZCamera.query.get_or_404(camera_id)   # 404 if camera gone
    preset = CameraPreset.query.filter_by(
        camera_id=camera_id, preset_number=preset_num
    ).first_or_404()
    db.session.delete(preset)
    db.session.commit()
    return jsonify({"deleted": preset_num})


@cameras_api_bp.route("/cameras/<int:camera_id>/presets/<int:preset_num>/recall", methods=["POST"])
def recall_preset(camera_id: int, preset_num: int):
    if not (0 <= preset_num <= 99):
        return _err("preset_number must be 0–99")
    cam = PTZCamera.query.get_or_404(camera_id)
    client = ptz_client_from_camera(cam, current_app.config)
    code, data = run_async(client.recall_preset(preset_num))
    label = next((p.name for p in cam.presets if p.preset_number == preset_num), None)
    logger.info("Camera %s: recalled preset %d (%s) → HTTP %d",
                cam.display_name, preset_num, label or "unlabeled", code)
    return jsonify({"status": code, "response": data, "preset_number": preset_num, "label": label})


@cameras_api_bp.route("/cameras/<int:camera_id>/presets/<int:preset_num>/save", methods=["POST"])
def save_preset(camera_id: int, preset_num: int):
    """Save current camera position as a preset. Optionally set/update the label."""
    if not (0 <= preset_num <= 99):
        return _err("preset_number must be 0–99")
    cam = PTZCamera.query.get_or_404(camera_id)
    body = request.get_json(silent=True) or {}

    client = ptz_client_from_camera(cam, current_app.config)
    code, data = run_async(client.save_preset(preset_num))

    if code == 200 and body.get("name"):
        name = str(body["name"]).strip()[:100]
        if name:
            preset = CameraPreset.query.filter_by(
                camera_id=camera_id, preset_number=preset_num
            ).first()
            if preset:
                preset.name = name
            else:
                db.session.add(CameraPreset(
                    camera_id=camera_id, preset_number=preset_num, name=name
                ))
            db.session.commit()

    return jsonify({"status": code, "response": data, "preset_number": preset_num})
