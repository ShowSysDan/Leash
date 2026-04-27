"""
Layouts API

GET    /api/layouts                         list all layouts
POST   /api/layouts                         create layout
GET    /api/layouts/<id>                    get layout with positions
PUT    /api/layouts/<id>                    update name / description / bg_color
DELETE /api/layouts/<id>                    delete layout
PUT    /api/layouts/<id>/positions          save all positions at once (full replace)
POST   /api/layouts/<id>/receivers          add a receiver to layout (default pos 10,10)
DELETE /api/layouts/<id>/receivers/<rid>    remove receiver from layout
"""
from datetime import datetime

from flask import Blueprint, jsonify, request

from app import db
from app.models import Layout, LayoutLabel, LayoutPosition
from app.routes._helpers import (
    err as _err,
    valid_hex_color,
    valid_name,
    MAX_DESCRIPTION,
)

layouts_api_bp = Blueprint("layouts_api", __name__)

_DEFAULT_BG = "#0a1628"


@layouts_api_bp.route("/layouts", methods=["GET"])
def list_layouts():
    layouts = Layout.query.order_by(Layout.name).all()
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

    layout = Layout(name=name, description=desc, bg_color=bg)
    db.session.add(layout)
    db.session.commit()
    return jsonify(layout.to_dict(include_positions=True)), 201


@layouts_api_bp.route("/layouts/<int:layout_id>", methods=["GET"])
def get_layout(layout_id: int):
    layout = Layout.query.get_or_404(layout_id)
    return jsonify(layout.to_dict(include_positions=True))


@layouts_api_bp.route("/layouts/<int:layout_id>", methods=["PUT"])
def update_layout(layout_id: int):
    layout = Layout.query.get_or_404(layout_id)
    body = request.get_json(silent=True) or {}

    if "name" in body:
        ok, name = valid_name(body["name"])
        if not ok:
            return _err("name is required (max 100 characters)")
        layout.name = name
    if "description" in body:
        layout.description = (body["description"] or "").strip()[:MAX_DESCRIPTION]
    if "bg_color" in body:
        if not valid_hex_color(body["bg_color"]):
            return _err("bg_color must be a hex colour (e.g. #0a1628 or #abc)")
        layout.bg_color = body["bg_color"]

    layout.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify(layout.to_dict())


@layouts_api_bp.route("/layouts/<int:layout_id>", methods=["DELETE"])
def delete_layout(layout_id: int):
    layout = Layout.query.get_or_404(layout_id)
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
    db.session.commit()
    return jsonify(pos.to_dict()), 201


@layouts_api_bp.route("/layouts/<int:layout_id>/receivers/<int:receiver_id>", methods=["DELETE"])
def remove_receiver_from_layout(layout_id: int, receiver_id: int):
    pos = LayoutPosition.query.filter_by(
        layout_id=layout_id, receiver_id=receiver_id
    ).first_or_404()
    db.session.delete(pos)
    db.session.commit()
    return jsonify({"deleted": receiver_id})
