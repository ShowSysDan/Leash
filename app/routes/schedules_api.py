"""
Schedules API

GET    /api/schedules              list all
POST   /api/schedules              create
GET    /api/schedules/<id>         get one
PUT    /api/schedules/<id>         update
DELETE /api/schedules/<id>         delete
PATCH  /api/schedules/<id>/toggle  flip enabled flag
"""
import re
from datetime import datetime

from flask import Blueprint, current_app, jsonify, request

from app import db
from app.models import ScheduledRecall, Snapshot

schedules_api_bp = Blueprint("schedules_api", __name__)

_TIME_RE = re.compile(r"^\d{2}:\d{2}$")
_VALID_DAYS = {"0", "1", "2", "3", "4", "5", "6"}


def _err(msg, code=400):
    return jsonify({"error": msg}), code


def _reload():
    """Re-register scheduler jobs after any change."""
    from app.services.scheduler import get_scheduler
    scheduler = get_scheduler()
    if scheduler:
        from app.services.scheduler import _check_schedules  # noqa: F401
        # Jobs are re-evaluated every minute from DB — no dynamic job reload needed.
        # This is a no-op hook kept for future use if we switch to per-job cron triggers.
        pass


def _validate_body(body: dict):
    """Return (cleaned_data, error_string). error_string is None on success."""
    name = (body.get("name") or "").strip()
    if not name:
        return None, "name is required"

    snap_id = body.get("snapshot_id")
    if snap_id is None:
        return None, "snapshot_id is required"
    if not Snapshot.query.get(int(snap_id)):
        return None, f"Snapshot {snap_id} not found"

    days_raw = body.get("days_of_week", "")
    if isinstance(days_raw, list):
        days_list = [str(d) for d in days_raw]
    else:
        days_list = [d.strip() for d in str(days_raw).split(",") if d.strip()]
    if not days_list or not all(d in _VALID_DAYS for d in days_list):
        return None, "days_of_week must be a non-empty list/string of day numbers 0 (Mon) – 6 (Sun)"
    days_str = ",".join(sorted(set(days_list), key=int))

    time_str = (body.get("time_of_day") or "").strip()
    if not _TIME_RE.match(time_str):
        return None, "time_of_day must be HH:MM (24-hour, local server time)"
    h, m = int(time_str[:2]), int(time_str[3:])
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None, "time_of_day out of range"

    return {
        "name": name,
        "snapshot_id": int(snap_id),
        "days_of_week": days_str,
        "time_of_day": time_str,
        "enabled": bool(body.get("enabled", True)),
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
    _reload()
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
    _reload()
    return jsonify(sched.to_dict())


@schedules_api_bp.route("/schedules/<int:sched_id>", methods=["DELETE"])
def delete_schedule(sched_id: int):
    sched = ScheduledRecall.query.get_or_404(sched_id)
    db.session.delete(sched)
    db.session.commit()
    _reload()
    return jsonify({"deleted": sched_id})


@schedules_api_bp.route("/schedules/<int:sched_id>/toggle", methods=["PATCH"])
def toggle_schedule(sched_id: int):
    sched = ScheduledRecall.query.get_or_404(sched_id)
    sched.enabled = not sched.enabled
    sched.updated_at = datetime.utcnow()
    db.session.commit()
    _reload()
    return jsonify(sched.to_dict())
