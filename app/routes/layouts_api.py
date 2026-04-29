"""
Layouts API

GET    /api/layouts                         list all layouts
POST   /api/layouts                         create layout
PUT    /api/layouts/reorder                 set manual sort order from a list of ids
GET    /api/layouts/<id>                    get layout with positions
PUT    /api/layouts/<id>                    update name / description / bg_color
DELETE /api/layouts/<id>                    delete layout
PUT    /api/layouts/<id>/positions          save all positions at once (full replace)
POST   /api/layouts/<id>/receivers          add a receiver to layout (default pos 10,10)
DELETE /api/layouts/<id>/receivers/<rid>    remove receiver from layout

Each layout has a ReceiverGroup auto-managed in lockstep — created on layout
creation, renamed when the layout is renamed, membership synced on every
receiver add/remove/save_positions, and deleted when the layout is deleted.
"""
from datetime import datetime

from flask import Blueprint, jsonify, request

from app import db
from app.models import Layout, LayoutLabel, LayoutPosition, NDIReceiver, ReceiverGroup
from app.routes._helpers import (
    err as _err,
    valid_hex_color,
    valid_name,
    MAX_DESCRIPTION,
)

layouts_api_bp = Blueprint("layouts_api", __name__)

_DEFAULT_BG = "#0a1628"


def _sync_layout_group(layout: Layout, *, previous_name: str | None = None) -> None:
    """Keep a ReceiverGroup in lockstep with this layout.

    Membership = the layout's positioned receivers. Name follows the layout.
    Called from every endpoint that mutates a layout's identity or membership.
    Caller is responsible for db.session.commit().
    """
    target_name = layout.name
    receiver_ids = [p.receiver_id for p in layout.positions]
    receivers = (
        NDIReceiver.query.filter(NDIReceiver.id.in_(receiver_ids)).all()
        if receiver_ids else []
    )

    # Find the existing auto-group: prefer its previous name (handles renames),
    # falling back to current name.
    group = None
    if previous_name and previous_name != target_name:
        group = ReceiverGroup.query.filter_by(name=previous_name).first()
    if group is None:
        group = ReceiverGroup.query.filter_by(name=target_name).first()

    if group is None:
        group = ReceiverGroup(
            name=target_name,
            description=f"Auto-generated from layout: {target_name}",
        )
        db.session.add(group)
    else:
        group.name = target_name
        if not group.description or group.description.startswith("Auto-generated from layout"):
            group.description = f"Auto-generated from layout: {target_name}"

    group.receivers = receivers


def _delete_layout_group(layout_name: str) -> None:
    """Remove the auto-generated group when a layout is deleted."""
    group = ReceiverGroup.query.filter_by(name=layout_name).first()
    if group:
        db.session.delete(group)


@layouts_api_bp.route("/layouts", methods=["GET"])
def list_layouts():
    layouts = Layout.query.order_by(Layout.sort_order, Layout.name).all()
    return jsonify([l.to_dict() for l in layouts])


@layouts_api_bp.route("/layouts", methods=["POST"])
def create_layout():
    body = request.get_json(silent=True) or {}
    ok, name = valid_name(body.get("name"))
    if not ok:
        return _err("name is required (max 100 characters)")
    bg = body.get("bg_color", _DEFAULT_BG)
    if not valid_hex_color(bg):
        return _err("bg_color must be a hex colour (e.g. #0a1628 or #abc)")
    desc = (body.get("description") or "").strip()[:MAX_DESCRIPTION]

    # Append new layouts to the end of the manual order.
    max_order = db.session.query(db.func.max(Layout.sort_order)).scalar()
    next_order = (max_order or 0) + 1

    layout = Layout(name=name, description=desc, bg_color=bg, sort_order=next_order)
    db.session.add(layout)
    db.session.flush()
    _sync_layout_group(layout)
    db.session.commit()
    return jsonify(layout.to_dict(include_positions=True)), 201


@layouts_api_bp.route("/layouts/reorder", methods=["PUT"])
def reorder_layouts():
    """Apply a new manual sort order from a list of layout ids.

    Body: {"order": [id1, id2, ...]}  — leftmost id is first.
    Ids not present keep their existing sort_order but get pushed past the
    explicitly ordered ones so the supplied list always wins.
    """
    body = request.get_json(silent=True) or {}
    order = body.get("order") or []
    if not isinstance(order, list):
        return _err("order must be a list of layout ids")
    try:
        ids = [int(x) for x in order]
    except (TypeError, ValueError):
        return _err("order entries must be integer layout ids")

    layouts_by_id = {l.id: l for l in Layout.query.all()}
    for position, lid in enumerate(ids, start=1):
        layout = layouts_by_id.get(lid)
        if layout is not None:
            layout.sort_order = position

    # Push any unspecified layouts to the end, preserving their relative order.
    tail_start = len(ids) + 1
    leftovers = [l for lid, l in layouts_by_id.items() if lid not in set(ids)]
    leftovers.sort(key=lambda l: (l.sort_order, l.name))
    for offset, layout in enumerate(leftovers):
        layout.sort_order = tail_start + offset

    db.session.commit()
    layouts = Layout.query.order_by(Layout.sort_order, Layout.name).all()
    return jsonify([l.to_dict() for l in layouts])


@layouts_api_bp.route("/layouts/<int:layout_id>", methods=["GET"])
def get_layout(layout_id: int):
    layout = Layout.query.get_or_404(layout_id)
    return jsonify(layout.to_dict(include_positions=True))


@layouts_api_bp.route("/layouts/<int:layout_id>", methods=["PUT"])
def update_layout(layout_id: int):
    layout = Layout.query.get_or_404(layout_id)
    body = request.get_json(silent=True) or {}

    previous_name = layout.name
    name_changed = False

    if "name" in body:
        ok, name = valid_name(body["name"])
        if not ok:
            return _err("name is required (max 100 characters)")
        if name != layout.name:
            name_changed = True
        layout.name = name
    if "description" in body:
        layout.description = (body["description"] or "").strip()[:MAX_DESCRIPTION]
    if "bg_color" in body:
        if not valid_hex_color(body["bg_color"]):
            return _err("bg_color must be a hex colour (e.g. #0a1628 or #abc)")
        layout.bg_color = body["bg_color"]

    layout.updated_at = datetime.utcnow()
    if name_changed:
        _sync_layout_group(layout, previous_name=previous_name)
    db.session.commit()
    return jsonify(layout.to_dict())


@layouts_api_bp.route("/layouts/<int:layout_id>", methods=["DELETE"])
def delete_layout(layout_id: int):
    layout = Layout.query.get_or_404(layout_id)
    _delete_layout_group(layout.name)
    db.session.delete(layout)
    db.session.commit()
    return jsonify({"deleted": layout_id})


@layouts_api_bp.route("/layouts/<int:layout_id>/positions", methods=["PUT"])
def save_positions(layout_id: int):
    """
    Full replace of receiver positions; in-place update of label positions.
    Body: {"positions": [...], "labels": [{"id": N, "x_pct": X, "y_pct": Y}, ...]}
    """
    layout = Layout.query.get_or_404(layout_id)
    body = request.get_json(silent=True) or {}
    positions_data = body.get("positions", [])
    labels_data = body.get("labels", [])

    # Full replace of receiver positions
    LayoutPosition.query.filter_by(layout_id=layout_id).delete()
    for p in positions_data:
        rid = p.get("receiver_id")
        if not rid:
            continue
        db.session.add(LayoutPosition(
            layout_id=layout_id,
            receiver_id=int(rid),
            x_pct=float(p.get("x_pct", 0)),
            y_pct=float(p.get("y_pct", 0)),
        ))

    # Update label positions in-place
    for ld in labels_data:
        lid = ld.get("id")
        if not lid:
            continue
        label = LayoutLabel.query.filter_by(id=int(lid), layout_id=layout_id).first()
        if label:
            label.x_pct = float(ld.get("x_pct", label.x_pct))
            label.y_pct = float(ld.get("y_pct", label.y_pct))

    layout.updated_at = datetime.utcnow()
    db.session.flush()
    _sync_layout_group(layout)
    db.session.commit()
    return jsonify(layout.to_dict(include_positions=True))


@layouts_api_bp.route("/layouts/<int:layout_id>/labels", methods=["POST"])
def add_label(layout_id: int):
    Layout.query.get_or_404(layout_id)
    body = request.get_json(silent=True) or {}
    text = str(body.get("text", "")).strip()[:200]
    if not text:
        return _err("text is required")
    label = LayoutLabel(
        layout_id=layout_id,
        text=text,
        x_pct=float(body.get("x_pct", 5.0)),
        y_pct=float(body.get("y_pct", 5.0)),
    )
    db.session.add(label)
    db.session.commit()
    return jsonify(label.to_dict()), 201


@layouts_api_bp.route("/layouts/<int:layout_id>/labels/<int:label_id>", methods=["DELETE"])
def delete_label(layout_id: int, label_id: int):
    label = LayoutLabel.query.filter_by(id=label_id, layout_id=layout_id).first_or_404()
    db.session.delete(label)
    db.session.commit()
    return jsonify({"deleted": label_id})


@layouts_api_bp.route("/layouts/<int:layout_id>/receivers", methods=["POST"])
def add_receiver_to_layout(layout_id: int):
    layout = Layout.query.get_or_404(layout_id)
    body = request.get_json(silent=True) or {}
    rid = body.get("receiver_id")
    if not rid:
        return _err("receiver_id required")

    existing = LayoutPosition.query.filter_by(layout_id=layout_id, receiver_id=rid).first()
    if existing:
        return _err("Receiver already on this layout", 409)

    # Place at next available slot (ripple down from 0,0 in steps of 15%)
    count = LayoutPosition.query.filter_by(layout_id=layout_id).count()
    cols = 5
    x = (count % cols) * 20.0
    y = (count // cols) * 20.0

    pos = LayoutPosition(
        layout_id=layout_id,
        receiver_id=int(rid),
        x_pct=float(body.get("x_pct", x)),
        y_pct=float(body.get("y_pct", y)),
    )
    db.session.add(pos)
    db.session.flush()
    _sync_layout_group(layout)
    db.session.commit()
    return jsonify(pos.to_dict()), 201


@layouts_api_bp.route("/layouts/<int:layout_id>/receivers/<int:receiver_id>", methods=["DELETE"])
def remove_receiver_from_layout(layout_id: int, receiver_id: int):
    pos = LayoutPosition.query.filter_by(
        layout_id=layout_id, receiver_id=receiver_id
    ).first_or_404()
    layout = Layout.query.get_or_404(layout_id)
    db.session.delete(pos)
    db.session.flush()
    _sync_layout_group(layout)
    db.session.commit()
    return jsonify({"deleted": receiver_id})
