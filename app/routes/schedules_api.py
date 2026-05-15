"""
Schedules API

GET    /api/schedules                       list all
POST   /api/schedules                       create
GET    /api/schedules/<id>                  get one
PUT    /api/schedules/<id>                  update
DELETE /api/schedules/<id>                  delete
PATCH  /api/schedules/<id>/toggle           flip enabled flag
DELETE /api/schedules/<id>/enforcement      end an active enforcement window early
POST   /api/schedules/<id>/duplicate        clone + (optionally) re-time
PATCH  /api/schedules/<id>/reschedule       drag-to-move handler (date and/or time)
GET    /api/schedules/occurrences           expand schedules into per-date events
"""
from datetime import datetime, date as date_type, timedelta

from flask import Blueprint, current_app, jsonify, request

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
    if stype == "camera" and not current_app.config.get("CAMERAS_ENABLED", False):
        return None, "Camera support is disabled — only 'ndi' schedules can be created"

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
        cam_id_raw = body.get("camera_id")
        preset_raw = body.get("preset_number")
        if cam_id_raw is None:
            return None, "camera_id is required for camera schedules"
        try:
            cam_id = int(cam_id_raw)
        except (TypeError, ValueError):
            return None, "camera_id must be an integer"
        if not PTZCamera.query.get(cam_id):
            return None, f"Camera {cam_id} not found"
        try:
            preset_num = int(preset_raw) if preset_raw is not None else None
        except (TypeError, ValueError):
            return None, "preset_number must be an integer 0–99"
        if preset_num is None or not (0 <= preset_num <= 99):
            return None, "preset_number must be 0–99"
        return {**base, "camera_id": cam_id, "preset_number": preset_num}, None
    else:
        snap_id_raw = body.get("snapshot_id")
        if snap_id_raw is None:
            return None, "snapshot_id is required for ndi schedules"
        try:
            snap_id = int(snap_id_raw)
        except (TypeError, ValueError):
            return None, "snapshot_id must be an integer"
        if not Snapshot.query.get(snap_id):
            return None, f"Snapshot {snap_id} not found"
        try:
            persist_minutes = int(body.get("persist_minutes", 60))
        except (TypeError, ValueError):
            return None, "persist_minutes must be an integer"
        if persist_minutes < 1 or persist_minutes > 1440:
            return None, "persist_minutes must be between 1 and 1440"
        return {
            **base,
            "snapshot_id": snap_id,
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


# ── Calendar helpers ──────────────────────────────────────────────────────


def _expand_schedule(sched: ScheduledRecall, start: date_type, end: date_type):
    """Yield occurrence dates for a schedule between start and end (inclusive)."""
    mode = sched.schedule_mode or "weekly"

    if mode == "once":
        if sched.run_date and start <= sched.run_date <= end:
            yield sched.run_date
        return

    days = {int(d) for d in (sched.days_of_week or "").split(",") if d.strip().isdigit()}
    if not days:
        return

    cutoff = sched.end_date if mode == "weekly_until" else None
    cur = start
    one_day = timedelta(days=1)
    while cur <= end:
        if cutoff and cur > cutoff:
            break
        if cur.weekday() in days:
            yield cur
        cur += one_day


@schedules_api_bp.route("/schedules/occurrences", methods=["GET"])
def list_occurrences():
    """Expand all schedules into individual date occurrences in a range.

    Used by the calendar views. Each occurrence carries enough metadata for
    the client to render a pill and decide whether drag is allowed.
    """
    start_str = request.args.get("start", "")
    end_str = request.args.get("end", "")
    start, err = _parse_date(start_str)
    if err:
        return _err(f"start: {err}")
    end, err = _parse_date(end_str)
    if err:
        return _err(f"end: {err}")
    if end < start:
        return _err("end must be on or after start")
    if (end - start).days > 366:
        return _err("range cannot exceed 366 days")

    today = datetime.now().date()
    cameras_enabled = bool(current_app.config.get("CAMERAS_ENABLED", False))

    occurrences = []
    for sched in ScheduledRecall.query.all():
        if sched.schedule_type == "camera" and not cameras_enabled:
            continue
        for occ_date in _expand_schedule(sched, start, end):
            occurrences.append({
                "schedule_id": sched.id,
                "name": sched.name,
                "date": occ_date.isoformat(),
                "time_of_day": sched.time_of_day,
                "schedule_mode": sched.schedule_mode or "weekly",
                "schedule_type": sched.schedule_type,
                "enabled": sched.enabled,
                "is_past": occ_date < today,
                "snapshot_name": sched.snapshot.name if sched.schedule_type == "ndi" and sched.snapshot else None,
                "camera_name": sched.camera.display_name if sched.schedule_type == "camera" and sched.camera else None,
                "preset_number": sched.preset_number,
                "persistent": bool(sched.persistent) if sched.schedule_type == "ndi" else False,
            })
    return jsonify(occurrences)


@schedules_api_bp.route("/schedules/<int:sched_id>/reschedule", methods=["PATCH"])
def reschedule(sched_id: int):
    """Drag-to-move handler for the calendar views.

    For "once" schedules, accepts a new run_date and/or time_of_day.
    For "weekly" / "weekly_until" schedules, accepts time_of_day only —
    changing days requires the full Edit modal (clearer UX).
    """
    sched = ScheduledRecall.query.get_or_404(sched_id)
    body = request.get_json(silent=True) or {}

    new_date_raw = body.get("run_date")
    new_time_raw = body.get("time_of_day")

    if not new_date_raw and not new_time_raw:
        return _err("run_date or time_of_day required")

    mode = sched.schedule_mode or "weekly"
    if new_date_raw and mode != "once":
        return _err("Only one-time schedules can be moved to a different date — use Edit for recurring schedules")

    if new_date_raw:
        new_date, derr = _parse_date(new_date_raw)
        if derr:
            return _err(f"run_date: {derr}")
        sched.run_date = new_date

    if new_time_raw:
        time_str = str(new_time_raw).strip()
        if not valid_time_of_day(time_str):
            return _err("time_of_day must be HH:MM (24-hour)")
        sched.time_of_day = time_str

    sched.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify(sched.to_dict())
