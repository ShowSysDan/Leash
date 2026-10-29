"""
Tractus Project API (session-authenticated, used by the /project page)

GET    /api/project/viewers                 live viewer list from every Tractus host
POST   /api/project/manual                  one-shot project/stop on viewers (all hosts)
GET    /api/project/status                  projector engine state
GET    /api/project/cues                    list cues
POST   /api/project/cues                    create
PUT    /api/project/cues/<id>               update
DELETE /api/project/cues/<id>               delete (stops it first if active)
POST   /api/project/cues/<id>/activate      body: {"duration": s?, "now": bool?}
POST   /api/project/cues/<id>/deactivate
POST   /api/project/clear                   deactivate every cue

Show control should use the API-key protected equivalents under /api/v1/project.
"""
import re

from flask import Blueprint, current_app, jsonify, request

from app import db
from app.models import ProjectCue
from app.routes._helpers import err as _err, valid_name
from app.services import projector, tractus_client
from app.services.audit_log import project_manual
from app.services.birddog_client import run_async

project_api_bp = Blueprint("project_api", __name__)

_CODE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
MAX_SECONDS = 86400


# ---------------------------------------------------------------------------
# Shared helpers (also used by external_api)
# ---------------------------------------------------------------------------

def parse_viewers(raw) -> tuple[list[int] | None, str | None]:
    """Accept [1,2], "1,2" or ""/None (= all).  Returns (list|None, error)."""
    if raw in (None, "", []):
        return None, None
    items = raw if isinstance(raw, list) else str(raw).split(",")
    out = []
    for item in items:
        s = str(item).strip()
        if not s:
            continue
        if not s.isdigit():
            return None, "viewers must be viewer index integers"
        out.append(int(s))
    return (sorted(set(out)) or None), None


def parse_duration(raw) -> tuple[int | None, str | None]:
    if raw in (None, "", 0, "0"):
        return None, None
    s = str(raw).strip()
    if not s.isdigit() or not (1 <= int(s) <= MAX_SECONDS):
        return None, f"duration must be 1–{MAX_SECONDS} seconds"
    return int(s), None


def find_cue(ref) -> ProjectCue | None:
    """Look up a cue by numeric id or by its trigger code."""
    s = str(ref).strip().lower()
    cue = ProjectCue.query.filter_by(code=s).first()
    if cue is None and s.isdigit():
        cue = db.session.get(ProjectCue, int(s))
    return cue


def _int_field(body, key, default, lo, hi) -> tuple[int | None, str | None]:
    raw = body.get(key, default)
    try:
        val = int(raw)
    except (TypeError, ValueError):
        return None, f"{key} must be an integer"
    if not (lo <= val <= hi):
        return None, f"{key} must be between {lo} and {hi}"
    return val, None


def _validate(body: dict, cue_id: int | None = None) -> tuple[dict | None, str | None]:
    ok, name = valid_name(body.get("name"))
    if not ok:
        return None, "name is required (max 100 characters)"

    code = str(body.get("code") or "").strip().lower()
    if not _CODE_RE.match(code):
        return None, "code is required: lowercase letters, digits, - or _ (max 64)"
    clash = ProjectCue.query.filter_by(code=code).first()
    if clash and clash.id != cue_id:
        return None, f"code '{code}' is already used by cue '{clash.name}'"

    ok, source = valid_name(body.get("source_name"), max_len=255)
    if not ok:
        return None, "source_name is required"

    viewers, verr = parse_viewers(body.get("viewers"))
    if verr:
        return None, verr

    mode = body.get("mode", "cycle")
    if mode not in ProjectCue.MODES:
        return None, "mode must be 'cycle' or 'hold'"

    data = {"name": name, "code": code, "source_name": source, "mode": mode,
            "viewers": ",".join(str(v) for v in viewers or [])}
    for key, default, lo, hi in (
        ("dwell_seconds", 10, 1, MAX_SECONDS),
        ("gap_seconds", 30, 0, MAX_SECONDS),
        ("priority", 0, -1000, 1000),
        ("sort_order", 0, -100000, 100000),
    ):
        val, ierr = _int_field(body, key, default, lo, hi)
        if ierr:
            return None, ierr
        data[key] = val
    return data, None


def engine_status() -> dict:
    eng = projector.get_engine()
    if eng is None:
        return {"running": False, "phase": "idle", "current_cue_id": None,
                "phase_seconds_remaining": None, "hosts": [], "viewers": []}
    return eng.status()


def status_payload() -> dict:
    cues = ProjectCue.query.order_by(ProjectCue.sort_order, ProjectCue.name).all()
    st = engine_status()
    cur = next((c for c in cues if c.id == st["current_cue_id"]), None)
    st["current_cue"] = cur.to_dict() if cur else None
    st["active_cues"] = [c.to_dict() for c in cues if c.active]
    return st


def activate_from_request(cue: ProjectCue, body: dict, via: str):
    duration, derr = parse_duration(body.get("duration"))
    if derr:
        return _err(derr)
    jump = str(body.get("now", "")).lower() in ("1", "true", "yes")
    projector.activate(cue, via=via, duration=duration, jump=jump)
    db.session.commit()
    return jsonify(cue.to_dict())


def clear_all(via: str) -> int:
    active = ProjectCue.query.filter_by(active=True).all()
    for cue in active:
        projector.deactivate(cue, via=via)
    db.session.commit()
    return len(active)


# ---------------------------------------------------------------------------
# Viewers + manual control
# ---------------------------------------------------------------------------

def _tractus_cfg():
    cfg = current_app.config
    return (list(cfg.get("TRACTUS_MV_HOSTS") or []),
            int(cfg.get("TRACTUS_MV_PORT", 8901) or 8901),
            min(int(cfg.get("HTTP_TIMEOUT", 5) or 5), projector.MAX_COMMAND_TIMEOUT))


@project_api_bp.route("/project/viewers", methods=["GET"])
def list_viewers():
    """Viewers merged across hosts: one row per viewer index, per-host state."""
    hosts, port, timeout = _tractus_cfg()
    results = run_async(tractus_client.fetch_viewers_all(hosts, port, timeout)) if hosts else {}

    merged: dict[int, dict] = {}
    for host in hosts:
        for v in results[host]["viewers"]:
            row = merged.setdefault(v["index"], {"index": v["index"], "name": v["name"], "hosts": {}})
            row["hosts"][host] = {"projected_source": v["projected_source"],
                                  "ndi_enabled": v["ndi_enabled"]}

    managed = {}
    for item in engine_status()["viewers"]:
        managed.setdefault(item["viewer"], item["desired"])

    viewers = sorted(merged.values(), key=lambda r: r["index"])
    for row in viewers:
        row["managed"] = row["index"] in managed
        row["engine_source"] = managed.get(row["index"])
    return jsonify({
        "hosts": [{"host": h, "ok": results[h]["ok"], "error": results[h]["error"]} for h in hosts],
        "viewers": viewers,
    })


@project_api_bp.route("/project/manual", methods=["POST"])
def manual_project():
    """Body: {"viewers": [0,1], "source": "NAME"}  — omit/null source to stop."""
    body = request.get_json(silent=True) or {}
    viewers, verr = parse_viewers(body.get("viewers"))
    if verr or not viewers:
        return _err(verr or "viewers is required")
    source = (body.get("source") or "").strip() or None

    hosts, port, timeout = _tractus_cfg()
    if not hosts:
        return _err("No Tractus MV hosts configured", 503)
    paths = [tractus_client.project_source_path(v, source) if source
             else tractus_client.project_stop_path(v) for v in viewers]
    results = run_async(tractus_client.broadcast(hosts, port, paths, timeout))
    ok = sum(1 for r in results if r["ok"])
    project_manual(viewers, source, ok, len(results))
    return jsonify({"attempted": len(results), "succeeded": ok, "results": results}), \
        (200 if ok == len(results) else 502)


# ---------------------------------------------------------------------------
# Cues
# ---------------------------------------------------------------------------

@project_api_bp.route("/project/status", methods=["GET"])
def get_status():
    return jsonify(status_payload())


@project_api_bp.route("/project/cues", methods=["GET"])
def list_cues():
    cues = ProjectCue.query.order_by(ProjectCue.sort_order, ProjectCue.name).all()
    return jsonify([c.to_dict() for c in cues])


@project_api_bp.route("/project/cues", methods=["POST"])
def create_cue():
    data, verr = _validate(request.get_json(silent=True) or {})
    if verr:
        return _err(verr)
    cue = ProjectCue(**data)
    db.session.add(cue)
    db.session.commit()
    return jsonify(cue.to_dict()), 201


@project_api_bp.route("/project/cues/<int:cue_id>", methods=["PUT"])
def update_cue(cue_id: int):
    cue = db.get_or_404(ProjectCue, cue_id)
    data, verr = _validate(request.get_json(silent=True) or {}, cue_id=cue.id)
    if verr:
        return _err(verr)
    for k, v in data.items():
        setattr(cue, k, v)
    projector._after_commit_kick(None)
    db.session.commit()
    return jsonify(cue.to_dict())


@project_api_bp.route("/project/cues/<int:cue_id>", methods=["DELETE"])
def delete_cue(cue_id: int):
    cue = db.get_or_404(ProjectCue, cue_id)
    projector.deactivate(cue, via="ui-delete")
    db.session.delete(cue)
    db.session.commit()
    return jsonify({"deleted": cue_id})


@project_api_bp.route("/project/cues/<int:cue_id>/activate", methods=["POST"])
def activate_cue(cue_id: int):
    cue = db.get_or_404(ProjectCue, cue_id)
    return activate_from_request(cue, request.get_json(silent=True) or {}, via="ui")


@project_api_bp.route("/project/cues/<int:cue_id>/deactivate", methods=["POST"])
def deactivate_cue(cue_id: int):
    cue = db.get_or_404(ProjectCue, cue_id)
    projector.deactivate(cue, via="ui")
    db.session.commit()
    return jsonify(cue.to_dict())


@project_api_bp.route("/project/clear", methods=["POST"])
def clear_cues():
    return jsonify({"deactivated": clear_all(via="ui")})
