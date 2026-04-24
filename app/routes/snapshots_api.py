"""
Snapshots API

GET    /api/snapshots                       list snapshots
POST   /api/snapshots                       capture current state
                                            body: {"name":"...", "receiver_ids":[1,2]}
                                            omit receiver_ids to capture all
GET    /api/snapshots/<id>                  get snapshot with entries
POST   /api/snapshots/<id>/recall           apply snapshot to devices concurrently
                                            body: {"receiver_ids":[1,2]}  (optional subset)
DELETE /api/snapshots/<id>                  delete snapshot
"""
import asyncio
from datetime import datetime

from flask import Blueprint, current_app, jsonify, request

from app import db
from app.models import NDIReceiver, Snapshot, SnapshotEntry
from app.services.birddog_client import BirdDogClient, run_async

snapshots_api_bp = Blueprint("snapshots_api", __name__)


def _err(msg, code=400):
    return jsonify({"error": msg}), code


@snapshots_api_bp.route("/snapshots", methods=["GET"])
def list_snapshots():
    snaps = Snapshot.query.order_by(Snapshot.created_at.desc()).all()
    return jsonify([s.to_dict() for s in snaps])


@snapshots_api_bp.route("/snapshots", methods=["POST"])
def create_snapshot():
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return _err("name is required")

    # Determine which receivers to include
    ids = body.get("receiver_ids")
    if ids:
        receivers = NDIReceiver.query.filter(NDIReceiver.id.in_(ids)).all()
    else:
        receivers = NDIReceiver.query.all()

    snap = Snapshot(
        name=name,
        description=body.get("description", ""),
    )
    db.session.add(snap)
    db.session.flush()  # get snap.id

    for recv in receivers:
        entry = SnapshotEntry(
            snapshot_id=snap.id,
            receiver_id=recv.id,
            source_name=recv.current_source or "",
        )
        db.session.add(entry)

    db.session.commit()
    return jsonify(snap.to_dict(include_entries=True)), 201


@snapshots_api_bp.route("/snapshots/<int:snap_id>", methods=["GET"])
def get_snapshot(snap_id: int):
    snap = Snapshot.query.get_or_404(snap_id)
    return jsonify(snap.to_dict(include_entries=True))


@snapshots_api_bp.route("/snapshots/<int:snap_id>/recall", methods=["POST"])
def recall_snapshot(snap_id: int):
    """
    Apply saved sources to devices concurrently.
    Optional body: {"receiver_ids": [1,2]} to restore only a subset.
    Skips offline devices and entries with empty source_name.
    """
    snap = Snapshot.query.get_or_404(snap_id)
    body = request.get_json(silent=True) or {}
    filter_ids = set(body.get("receiver_ids", []))

    entries = snap.entries
    if filter_ids:
        entries = [e for e in entries if e.receiver_id in filter_ids]

    # Only send to online receivers that have a non-empty saved source
    to_apply = [
        e for e in entries
        if e.source_name and e.receiver and e.receiver.status != "offline"
    ]

    cfg = current_app.config

    async def _recall_all():
        async def _one(entry):
            recv = entry.receiver
            client = BirdDogClient(
                ip=recv.ip_address,
                port=cfg["NDI_DEVICE_PORT"],
                password=cfg["NDI_DEVICE_PASSWORD"],
                timeout=cfg["HTTP_TIMEOUT"],
            )
            code, _ = await client.set_connect_to(entry.source_name)
            return {
                "receiver_id": recv.id,
                "source_name": entry.source_name,
                "status": code,
                "ok": code == 200,
            }

        tasks = [asyncio.create_task(_one(e)) for e in to_apply]
        return await asyncio.gather(*tasks, return_exceptions=False)

    results = run_async(_recall_all())

    # Persist successful applies
    ok_map = {r["receiver_id"]: r["source_name"] for r in results if r.get("ok")}
    now = datetime.utcnow()
    for recv in NDIReceiver.query.filter(NDIReceiver.id.in_(ok_map)).all():
        recv.current_source = ok_map[recv.id]
        recv.updated_at = now
    db.session.commit()

    return jsonify({
        "snapshot": snap.name,
        "attempted": len(to_apply),
        "succeeded": len(ok_map),
        "skipped": len(entries) - len(to_apply),
        "results": results,
    })


@snapshots_api_bp.route("/snapshots/<int:snap_id>", methods=["DELETE"])
def delete_snapshot(snap_id: int):
    snap = Snapshot.query.get_or_404(snap_id)
    db.session.delete(snap)
    db.session.commit()
    return jsonify({"deleted": snap_id})
