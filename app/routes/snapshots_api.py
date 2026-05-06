"""
Snapshots API

GET    /api/snapshots                       list snapshots
POST   /api/snapshots                       capture current state
                                            body: {"name":"...",
                                                   "receiver_ids":[1,2],
                                                   "group_ids":[3,4]}
                                            receiver_ids and group_ids are unioned;
                                            omit both to capture every receiver.
GET    /api/snapshots/<id>                  get snapshot with entries
POST   /api/snapshots/<id>/recall           apply snapshot to devices concurrently
                                            body: {"receiver_ids":[1,2]}  (optional subset)
DELETE /api/snapshots/<id>                  delete snapshot
"""
import asyncio
from datetime import datetime

from flask import Blueprint, current_app, jsonify, request

from app import db
from app.models import NDIReceiver, ReceiverGroup, Snapshot, SnapshotEntry
from app.routes._helpers import err as _err, valid_name, MAX_DESCRIPTION
from app.services.audit_log import device_error, snapshot_recalled, snapshot_source_changed
from app.services.birddog_client import client_from_receiver, run_async

snapshots_api_bp = Blueprint("snapshots_api", __name__)


@snapshots_api_bp.route("/snapshots", methods=["GET"])
def list_snapshots():
    snaps = Snapshot.query.order_by(Snapshot.created_at.desc()).all()
    return jsonify([s.to_dict() for s in snaps])


@snapshots_api_bp.route("/snapshots", methods=["POST"])
def create_snapshot():
    body = request.get_json(silent=True) or {}
    ok, name = valid_name(body.get("name"))
    if not ok:
        return _err("name is required (max 100 characters)")

    # Determine which receivers to include. receiver_ids and group_ids are
    # unioned; omit both to capture every receiver.
    receiver_ids = list(body.get("receiver_ids") or [])
    group_ids = list(body.get("group_ids") or [])

    if group_ids:
        groups = ReceiverGroup.query.filter(ReceiverGroup.id.in_(group_ids)).all()
        missing_groups = set(group_ids) - {g.id for g in groups}
        if missing_groups:
            return _err(f"Unknown group ids: {sorted(missing_groups)}")
        for g in groups:
            receiver_ids.extend(r.id for r in g.receivers)

    if receiver_ids or group_ids:
        unique_ids = list(dict.fromkeys(receiver_ids))  # preserve order, dedupe
        receivers = NDIReceiver.query.filter(NDIReceiver.id.in_(unique_ids)).all()
        if body.get("receiver_ids"):
            missing = set(body["receiver_ids"]) - {r.id for r in receivers}
            if missing:
                return _err(f"Unknown receiver ids: {sorted(missing)}")
    else:
        receivers = NDIReceiver.query.all()

    snap = Snapshot(
        name=name,
        description=(body.get("description") or "").strip()[:MAX_DESCRIPTION],
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
            try:
                client = client_from_receiver(recv, cfg)
                code, _ = await client.set_connect_to(entry.source_name)
            except Exception:
                code = 0
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
    failed_map = {r["receiver_id"]: r for r in results if not r.get("ok")}
    now = datetime.utcnow()

    recv_by_id = {e.receiver_id: e.receiver for e in to_apply}
    for recv in NDIReceiver.query.filter(NDIReceiver.id.in_(ok_map)).all():
        old_source = recv.current_source
        recv.current_source = ok_map[recv.id]
        recv.updated_at = now
        snapshot_source_changed(
            recv.display_name,
            recv.ip_address, old_source, ok_map[recv.id], snap.name,
        )
    for recv_id, result in failed_map.items():
        recv = recv_by_id.get(recv_id)
        if recv:
            device_error(recv.ip_address, "snapshot_recall", result.get("status", 0))
    db.session.commit()

    snapshot_recalled(snap.name, len(to_apply), len(ok_map))

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


@snapshots_api_bp.route("/snapshots/<int:snap_id>/entries/<int:entry_id>", methods=["PATCH"])
def update_entry(snap_id: int, entry_id: int):
    """Update the saved source_name on a single snapshot entry."""
    Snapshot.query.get_or_404(snap_id)
    entry = SnapshotEntry.query.filter_by(id=entry_id, snapshot_id=snap_id).first_or_404()
    body = request.get_json(silent=True) or {}
    entry.source_name = str(body.get("source_name", "")).strip()
    db.session.commit()
    return jsonify(entry.to_dict())
