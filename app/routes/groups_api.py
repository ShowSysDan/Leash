"""
Groups / Tags API

GET    /api/groups                          list all groups
POST   /api/groups                          create group
GET    /api/groups/<id>                     get group (includes receivers)
PUT    /api/groups/<id>                     update name / color / description
DELETE /api/groups/<id>                     delete group
POST   /api/groups/<id>/receivers           add receivers  {"receiver_ids": [1,2,3]}
DELETE /api/groups/<id>/receivers           remove receivers {"receiver_ids": [1,2]}
POST   /api/groups/<id>/source              send source to all receivers in group
"""
import asyncio
from datetime import datetime

from flask import Blueprint, current_app, jsonify, request

from app import db
from app.models import NDIReceiver, ReceiverGroup
from app.routes._helpers import err as _err
from app.services.audit_log import device_error, group_source_sent, source_changed, source_change_failed
from app.services.birddog_client import client_from_receiver, run_async

groups_api_bp = Blueprint("groups_api", __name__)


@groups_api_bp.route("/groups", methods=["GET"])
def list_groups():
    groups = ReceiverGroup.query.order_by(ReceiverGroup.name).all()
    return jsonify([g.to_dict() for g in groups])


@groups_api_bp.route("/groups", methods=["POST"])
def create_group():
    body = request.get_json(silent=True) or {}
    if not body.get("name"):
        return _err("name is required")
    if ReceiverGroup.query.filter_by(name=body["name"]).first():
        return _err(f"Group '{body['name']}' already exists", 409)

    group = ReceiverGroup(
        name=body["name"],
        color=body.get("color", "#0d6efd"),
        description=body.get("description", ""),
    )
    db.session.add(group)
    db.session.commit()
    return jsonify(group.to_dict()), 201


@groups_api_bp.route("/groups/<int:group_id>", methods=["GET"])
def get_group(group_id: int):
    group = ReceiverGroup.query.get_or_404(group_id)
    return jsonify(group.to_dict(include_receivers=True))


@groups_api_bp.route("/groups/<int:group_id>", methods=["PUT"])
def update_group(group_id: int):
    group = ReceiverGroup.query.get_or_404(group_id)
    body = request.get_json(silent=True) or {}
    if "name" in body:
        group.name = body["name"]
    if "color" in body:
        group.color = body["color"]
    if "description" in body:
        group.description = body["description"]
    db.session.commit()
    return jsonify(group.to_dict())


@groups_api_bp.route("/groups/<int:group_id>", methods=["DELETE"])
def delete_group(group_id: int):
    group = ReceiverGroup.query.get_or_404(group_id)
    db.session.delete(group)
    db.session.commit()
    return jsonify({"deleted": group_id})


@groups_api_bp.route("/groups/<int:group_id>/receivers", methods=["POST"])
def add_to_group(group_id: int):
    group = ReceiverGroup.query.get_or_404(group_id)
    body = request.get_json(silent=True) or {}
    ids = body.get("receiver_ids", [])
    if not ids:
        return _err("receiver_ids list is required")

    receivers = NDIReceiver.query.filter(NDIReceiver.id.in_(ids)).all()
    added = []
    for r in receivers:
        if r not in group.receivers:
            group.receivers.append(r)
            added.append(r.id)

    db.session.commit()
    return jsonify({"added": added, "group": group.to_dict(include_receivers=True)})


@groups_api_bp.route("/groups/<int:group_id>/receivers", methods=["DELETE"])
def remove_from_group(group_id: int):
    group = ReceiverGroup.query.get_or_404(group_id)
    body = request.get_json(silent=True) or {}
    ids = body.get("receiver_ids", [])

    removed = []
    for r in list(group.receivers):
        if r.id in ids:
            group.receivers.remove(r)
            removed.append(r.id)

    db.session.commit()
    return jsonify({"removed": removed, "group": group.to_dict(include_receivers=True)})


@groups_api_bp.route("/groups/<int:group_id>/source", methods=["POST"])
def set_group_source(group_id: int):
    """Send the same NDI source to every online receiver in the group concurrently."""
    group = ReceiverGroup.query.get_or_404(group_id)
    body = request.get_json(silent=True) or {}
    source_name = (body.get("source_name") or "").strip()
    if not source_name:
        return _err("source_name is required")

    cfg = current_app.config
    targets = [r for r in group.receivers if r.status != "offline"]

    async def _apply_all():
        async def _one(recv):
            client = client_from_receiver(recv, cfg)
            code, data = await client.set_connect_to(source_name)
            return {"receiver_id": recv.id, "status": code, "ok": code == 200}

        tasks = [asyncio.create_task(_one(r)) for r in targets]
        return await asyncio.gather(*tasks, return_exceptions=False)

    results = run_async(_apply_all())

    # Persist successful updates
    ok_ids = {r["receiver_id"] for r in results if r.get("ok")}
    now = datetime.utcnow()
    recv_map = {recv.id: recv for recv in targets}
    for recv in targets:
        old_source = recv.current_source
        if recv.id in ok_ids:
            recv.current_source = source_name
            recv.updated_at = now
            source_changed(
                recv.display_name,
                recv.ip_address, old_source, source_name, via=f"group:{group.name}",
            )
        else:
            http_status = next((r["status"] for r in results if r["receiver_id"] == recv.id), 0)
            device_error(recv.ip_address, "group_set_source", http_status)
            source_change_failed(
                recv.display_name,
                recv.ip_address, source_name, http_status, via=f"group:{group.name}",
            )
    db.session.commit()

    group_source_sent(group.name, source_name, len(targets), len(ok_ids))

    return jsonify({
        "source_name": source_name,
        "attempted": len(targets),
        "succeeded": len(ok_ids),
        "results": results,
    })
