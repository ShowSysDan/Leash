"""
Schedules API

GET    /api/schedules                  list all
POST   /api/schedules                  create
GET    /api/schedules/<id>             get one
PUT    /api/schedules/<id>             update
DELETE /api/schedules/<id>             delete
PATCH  /api/schedules/<id>/toggle      flip enabled flag
DELETE /api/schedules/<id>/enforcement end an active enforcement window early
"""
from datetime import datetime, date as date_type

from flask import Blueprint, jsonify, request

from app import db
from app.models import PTZCamera, ScheduledRecall, Snapshot
from app.routes._helpers import err as _err, valid_time_of_day, valid_name

schedules_api_bp = Blueprint("schedules_api", __name__)

_VALID_DAYS = {"0", "1", "2", "3", "4", "5", "6"}
_VALID_MODES = {"weekly", "once", "weekly_until"}


def _parse_date(val) -> tuple[date_type | None, str | None]:
    """Parse an ISO date string (YYYY-MM-DD). Returns (date, None) or (None, error)."""
    if not val:
        return None, "date is required"
    try:
        return datetime.strptime(str(val).strip(), "%Y-%m-%d").date(), None
    except ValueError:
        return None, f"invalid date '{val}' — expected YYYY-MM-DD"


def _validate_days(body: dict) -> tuple[str | None, str | None]:
    days_raw = body.get("days_of_week", "")
    days_list = [str(d) for d in days_raw] if isinstance(days_raw, list) else \
                [d.strip() for d in str(days_raw).split(",") if d.strip()]
    if not days_list or not all(d in _VALID_DAYS for d in days_list):
        return None, "days_of_week must be day numbers 0 (Mon) – 6 (Sun)"
    return ",".join(sorted(set(days_list), key=int)), None


def _validate_body(body: dict):
    """Return (cleaned_data, error_string). error_string is None on success."""
    ok, name = valid_name(body.get("name"))
    if not ok:
        return None, "name is required (max 100 characters)"

    time_str = (body.get("time_of_day") or "").strip()
    if not valid_time_of_day(time_str):
        return None, "time_of_day must be HH:MM (24-hour, local server time)"

    mode = body.get("schedule_mode", "weekly")
    if mode not in _VALID_MODES:
        return None, f"schedule_mode must be one of: {', '.join(_VALID_MODES)}"

    days_str = ""
    run_date = end_date = None

    if mode == "once":
        run_date, err = _parse_date(body.get("run_date"))
        if err:
            return None, f"run_date: {err}"
    else:
        days_str, err = _validate_days(body)
        if err:
            return None, err
        if mode == "weekly_until":
            end_date, err = _parse_date(body.get("end_date"))
            if err:
                return None, f"end_date: {err}"

    stype = body.get("schedule_type", "ndi")
    if stype not in ("ndi", "camera"):
        return None, "schedule_type must be 'ndi' or 'camera'"

    base = {
        "name": name,
        "schedule_type": stype,
        "schedule_mode": mode,
        "days_of_week": days_str,
        "time_of_day": time_str,
        "run_date": run_date,
        "end_date": end_date,
        "enabled": bool(body.get("enabled", True)),
    }

    if stype == "camera":
        cam_id = body.get("camera_id")
        preset_num = body.get("preset_number")
        if cam_id is None:
            return None, "camera_id is required for camera schedules"
        if not PTZCamera.query.get(int(cam_id)):
            return None, f"Camera {cam_id} not found"
        if preset_num is None or not (0 <= int(preset_num) <= 99):
            return None, "preset_number must be 0–99"
        return {**base, "camera_id": int(cam_id), "preset_number": int(preset_num)}, None
    else:
        snap_id = body.get("snapshot_id")
        if snap_id is None:
            return None, "snapshot_id is required for ndi schedules"
        if not Snapshot.query.get(int(snap_id)):
            return None, f"Snapshot {snap_id} not found"
        persist_minutes = int(body.get("persist_minutes", 60))
        if persist_minutes < 1 or persist_minutes > 1440:
            return None, "persist_minutes must be between 1 and 1440"
        return {
            **base,
            "snapshot_id": int(snap_id),
            "persistent": bool(body.get("persistent", False)),
            "persist_minutes": persist_minutes,
        }, None


@schedules_api_bp.route("/schedules", methods=["GET"])
def list_schedules():
    schedules = ScheduledRecall.query.order_by(ScheduledRecall.time_of_day).all()
    return jsonify([s.to_dict() for s in schedules])


@schedules_api_bp.route("/schedules", methods=["POST"])
def create_schedule():
    body = request.get_json(silent=True) or {}
    data, err = _validate_body(body)
    if err:
        return _err(err)

    sched = ScheduledRecall(**data)
    db.session.add(sched)
    db.session.commit()
    return jsonify(sched.to_dict()), 201


@schedules_api_bp.route("/schedules/<int:sched_id>", methods=["GET"])
def get_schedule(sched_id: int):
    sched = ScheduledRecall.query.get_or_404(sched_id)
    return jsonify(sched.to_dict())


@schedules_api_bp.route("/schedules/<int:sched_id>", methods=["PUT"])
def update_schedule(sched_id: int):
    sched = ScheduledRecall.query.get_or_404(sched_id)
    body = request.get_json(silent=True) or {}
    data, err = _validate_body(body)
    if err:
        return _err(err)

    for k, v in data.items():
        setattr(sched, k, v)
    sched.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify(sched.to_dict())


@schedules_api_bp.route("/schedules/<int:sched_id>", methods=["DELETE"])
def delete_schedule(sched_id: int):
    sched = ScheduledRecall.query.get_or_404(sched_id)
    db.session.delete(sched)
    db.session.commit()
    return jsonify({"deleted": sched_id})


@schedules_api_bp.route("/schedules/<int:sched_id>/toggle", methods=["PATCH"])
def toggle_schedule(sched_id: int):
    sched = ScheduledRecall.query.get_or_404(sched_id)
    sched.enabled = not sched.enabled
    sched.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify(sched.to_dict())


@schedules_api_bp.route("/schedules/<int:sched_id>/enforcement", methods=["DELETE"])
def stop_enforcement(sched_id: int):
    """Manually end an active enforcement window early."""
    sched = ScheduledRecall.query.get_or_404(sched_id)
    sched.enforcing_until = None
    sched.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify(sched.to_dict())


@schedules_api_bp.route("/schedules/<int:sched_id>/duplicate", methods=["POST"])
def duplicate_schedule(sched_id: int):
    """Clone a schedule. Useful for re-running a one-time event on a new date.

    Optional body fields: name, run_date, end_date, time_of_day, days_of_week, schedule_mode.
    Anything not provided is copied from the source. The clone starts disabled
    and never inherits run history or enforcement state.
    """
    src = ScheduledRecall.query.get_or_404(sched_id)
    body = request.get_json(silent=True) or {}

    new_name = (body.get("name") or "").strip() or f"{src.name} (copy)"
    new_mode = body.get("schedule_mode") or src.schedule_mode or "weekly"
    new_time = body.get("time_of_day") or src.time_of_day
    new_days = body.get("days_of_week", src.days_of_week or "")
    new_run_date = body.get("run_date") or (src.run_date.isoformat() if src.run_date else None)
    new_end_date = body.get("end_date") or (src.end_date.isoformat() if src.end_date else None)

    proxy = {
        "name": new_name,
        "schedule_type": src.schedule_type,
        "schedule_mode": new_mode,
        "time_of_day": new_time,
        "days_of_week": new_days,
        "run_date": new_run_date,
        "end_date": new_end_date,
        "snapshot_id": src.snapshot_id,
        "camera_id": src.camera_id,
        "preset_number": src.preset_number,
        "persistent": src.persistent,
        "persist_minutes": src.persist_minutes,
        "enabled": bool(body.get("enabled", False)),
    }

    data, err = _validate_body(proxy)
    if err:
        return _err(err)

    clone = ScheduledRecall(**data)
    db.session.add(clone)
    db.session.commit()
    return jsonify(clone.to_dict()), 201
