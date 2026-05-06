"""
External Integration API — v1
─────────────────────────────
All responses are JSON.  Designed for QSYS, Crestron, AMX, or any HTTP client.

Authentication (optional)
  Set API_KEY in .env.  If set, callers must include:
    Header:  X-API-Key: <key>
    OR query string: ?api_key=<key>
  If API_KEY is not set, all endpoints are open.

Source index guarantee
  Every NDI source is assigned a stable 1-based integer (source_index) when
  first discovered.  This index NEVER changes and is NEVER reassigned, even
  if the source goes offline.  External systems can use the index as a
  permanent address.

Endpoints
  GET  /api/v1/sources              All known sources (online + offline)
  GET  /api/v1/sources/online       Currently-visible sources only
  GET  /api/v1/sources/<index>      Get one source by stable index

  GET  /api/v1/receivers            All known receivers
  GET  /api/v1/receivers/<octet>    Get receiver by IP last octet

  POST /api/v1/route                Route one receiver to a source
  POST /api/v1/route/bulk           Route multiple receivers at once
"""
import asyncio
import hmac
import logging
from datetime import datetime
from functools import wraps

from flask import Blueprint, current_app, jsonify, request

from app import db
from app.models import NDIReceiver, NDISource
from app.services.audit_log import device_error, source_change_failed, source_changed
from app.services.birddog_client import client_from_ip, run_async

v1_bp = Blueprint("v1", __name__)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Optional API key auth
# ---------------------------------------------------------------------------

def _check_api_key():
    key = current_app.config.get("API_KEY")
    if not key:
        return None  # auth disabled
    provided = request.headers.get("X-API-Key") or request.args.get("api_key") or ""
    # Constant-time compare to prevent timing attacks on the key.
    if not hmac.compare_digest(provided, key):
        return jsonify({"error": "Unauthorized"}), 401
    return None


def require_api_key(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        err = _check_api_key()
        if err:
            return err
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Source registry
# ---------------------------------------------------------------------------

@v1_bp.route("/sources", methods=["GET"])
@require_api_key
def list_all_sources():
    """
    All known sources ordered by stable index.
    Includes offline sources so the index list is always complete.
    """
    sources = NDISource.query.order_by(NDISource.source_index).all()
    return jsonify({
        "count": len(sources),
        "online_count": sum(1 for s in sources if s.discovered),
        "sources": [s.to_dict() for s in sources],
    })


@v1_bp.route("/sources/online", methods=["GET"])
@require_api_key
def list_online_sources():
    """Currently-visible sources only."""
    sources = NDISource.query.filter_by(discovered=True).order_by(NDISource.source_index).all()
    return jsonify({
        "count": len(sources),
        "sources": [s.to_dict() for s in sources],
    })


@v1_bp.route("/sources/<int:index>", methods=["GET"])
@require_api_key
def get_source_by_index(index: int):
    """Look up a source by its stable index."""
    source = NDISource.query.filter_by(source_index=index).first()
    if not source:
        return jsonify({"error": f"No source with index {index}"}), 404
    return jsonify(source.to_dict())


# ---------------------------------------------------------------------------
# Receiver info
# ---------------------------------------------------------------------------

@v1_bp.route("/receivers", methods=["GET"])
@require_api_key
def list_receivers():
    receivers = NDIReceiver.query.order_by(NDIReceiver.index).all()
    return jsonify({
        "count": len(receivers),
        "online_count": sum(1 for r in receivers if r.status == "online"),
        "receivers": [_recv_summary(r) for r in receivers],
    })


@v1_bp.route("/receivers/<octet>", methods=["GET"])
@require_api_key
def get_receiver(octet: str):
    receiver = NDIReceiver.query.filter_by(ip_last_octet=str(octet)).first()
    if not receiver:
        return jsonify({"error": f"No receiver with IP octet {octet}"}), 404
    return jsonify(_recv_summary(receiver))


def _recv_summary(r: NDIReceiver) -> dict:
    return {
        "ip_last_octet": r.ip_last_octet,
        "ip_address": r.ip_address,
        "hostname": r.hostname,
        "label": r.label or r.hostname or f"Player {r.ip_last_octet}",
        "status": r.status,
        "current_source": r.current_source,
        "current_source_index": _source_index_for(r.current_source),
    }


def _source_index_for(name: str | None) -> int | None:
    if not name:
        return None
    src = NDISource.query.filter_by(name=name).first()
    return src.source_index if src else None


# ---------------------------------------------------------------------------
# Routing — single and bulk
# ---------------------------------------------------------------------------

def _resolve_source(value) -> tuple[str | None, str | None]:
    """
    Accept either a source_index (int) or a source name (str).
    Returns (source_name, error_message).
    """
    if value is None:
        return None, "source is required (index integer or name string)"

    if isinstance(value, int) or (isinstance(value, str) and value.lstrip("-").isdigit()):
        idx = int(value)
        src = NDISource.query.filter_by(source_index=idx).first()
        if not src:
            return None, f"No source with index {idx}"
        return src.name, None

    # String name — look up to confirm it exists (but allow unknown names through
    # so operators can set sources that haven't been discovered yet)
    return str(value).strip(), None


def _do_route(ip_octet: str, source_name: str, cfg: dict) -> dict:
    """Synchronously route one receiver.  Returns result dict."""
    ip = f"{cfg['NDI_SUBNET_PREFIX']}{ip_octet}"
    code, data = run_async(client_from_ip(ip, cfg).set_connect_to(source_name))
    ok = code == 200
    recv = NDIReceiver.query.filter_by(ip_last_octet=str(ip_octet)).first()
    label = recv.display_name if recv else ip_octet
    if ok:
        if recv:
            old_source = recv.current_source
            recv.current_source = source_name
            recv.updated_at = datetime.utcnow()
            db.session.commit()
        else:
            old_source = None
        source_changed(label, ip, old_source, source_name, via="v1")
    else:
        device_error(ip, "v1_route", code)
        source_change_failed(label, ip, source_name, code, via="v1")
    return {
        "ip_octet": ip_octet,
        "ip_address": ip,
        "source_name": source_name,
        "http_status": code,
        "ok": ok,
    }


@v1_bp.route("/route", methods=["POST"])
@require_api_key
def route_one():
    """
    Route a single receiver to a source.

    Body (JSON):
      {
        "ip_octet":  "83",          // last octet of receiver IP
        "source":    4              // source_index (int) OR source name (str)
      }
    """
    body = request.get_json(silent=True) or {}
    octet = str(body.get("ip_octet", "")).strip()
    if not octet:
        return jsonify({"error": "ip_octet is required"}), 400

    source_name, err = _resolve_source(body.get("source"))
    if err:
        return jsonify({"error": err}), 400

    cfg = current_app.config
    result = _do_route(octet, source_name, cfg)
    status_code = 200 if result["ok"] else 502
    return jsonify(result), status_code


@v1_bp.route("/route/bulk", methods=["POST"])
@require_api_key
def route_bulk():
    """
    Route multiple receivers concurrently.

    Body (JSON array):
      [
        {"ip_octet": "83", "source": 4},
        {"ip_octet": "84", "source": "Camera 1 (NDI)"},
        ...
      ]

    All routes are dispatched concurrently; each result shows ok/fail.
    """
    body = request.get_json(silent=True)
    if not isinstance(body, list):
        return jsonify({"error": "Body must be a JSON array of route objects"}), 400

    # Cap batch size so a single call can't tie up the worker for minutes.
    MAX_BATCH = 500
    if len(body) > MAX_BATCH:
        return jsonify({
            "error": f"Batch too large — max {MAX_BATCH} entries per call (got {len(body)})"
        }), 413

    cfg = current_app.config

    # Resolve names first (sync, cheap)
    tasks = []
    errors = []
    for item in body:
        if not isinstance(item, dict):
            errors.append({"error": "entries must be objects", "item": item})
            continue
        octet = str(item.get("ip_octet", "")).strip()
        if not octet.isdigit() or not (1 <= int(octet) <= 254):
            errors.append({"error": "ip_octet must be an integer 1–254", "item": item})
            continue
        source_name, err = _resolve_source(item.get("source"))
        if err:
            errors.append({"error": err, "ip_octet": octet})
            continue
        tasks.append((octet, source_name))

    concurrency = max(1, int(cfg.get("RECALL_CONCURRENCY", 10) or 10))

    async def _apply_all():
        prefix = cfg["NDI_SUBNET_PREFIX"]
        sem = asyncio.Semaphore(concurrency)

        async def _one(octet, source_name):
            ip = f"{prefix}{octet}"
            async with sem:
                try:
                    code, _ = await client_from_ip(ip, cfg).set_connect_to(source_name)
                except Exception as exc:
                    logger.warning("v1_route_bulk %s raised: %s", ip, exc)
                    code = 0
            return {
                "ip_octet": octet,
                "ip_address": ip,
                "source_name": source_name,
                "http_status": code,
                "ok": code == 200,
            }

        coros = [asyncio.create_task(_one(o, s)) for o, s in tasks]
        return await asyncio.gather(*coros, return_exceptions=False)

    results = run_async(_apply_all()) if tasks else []

    # Persist successful routes and emit audit events
    now = datetime.utcnow()
    for r in results:
        recv = NDIReceiver.query.filter_by(ip_last_octet=r["ip_octet"]).first()
        label = recv.display_name if recv else r["ip_octet"]
        if r["ok"]:
            if recv:
                old_source = recv.current_source
                recv.current_source = r["source_name"]
                recv.updated_at = now
            else:
                old_source = None
            source_changed(label, r["ip_address"], old_source, r["source_name"], via="v1_bulk")
        else:
            device_error(r["ip_address"], "v1_route_bulk", r.get("http_status", 0))
            source_change_failed(label, r["ip_address"], r["source_name"],
                                 r.get("http_status", 0), via="v1_bulk")
    if results:
        db.session.commit()

    succeeded = sum(1 for r in results if r.get("ok"))
    return jsonify({
        "attempted": len(tasks),
        "succeeded": succeeded,
        "failed": len(tasks) - succeeded,
        "errors": errors,
        "results": results,
    })
