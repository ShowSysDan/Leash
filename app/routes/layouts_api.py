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
from app.models import Layout, LayoutPosition
from app.routes._helpers import err as _err

layouts_api_bp = Blueprint("layouts_api", __name__)


@layouts_api_bp.route("/layouts", methods=["GET"])
def list_layouts():
    layouts = Layout.query.order_by(Layout.name).all()
    return jsonify([l.to_dict() for l in layouts])


@layouts_api_bp.route("/layouts", methods=["POST"])
def create_layout():
    body = request.get_json(silent=True) or {}
    if not body.get("name"):
        return _err("name is required")

    layout = Layout(
        name=body["name"],
        description=body.get("description", ""),
        bg_color=body.get("bg_color", "#0a1628"),
    )
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
    for field in ("name", "description", "bg_color"):
        if field in body:
            setattr(layout, field, body[field])
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
    Full replace of all positions.
    Body: {"positions": [{"receiver_id": 1, "x_pct": 12.5, "y_pct": 38.0}, ...]}
    """
    layout = Layout.query.get_or_404(layout_id)
    body = request.get_json(silent=True) or {}
    positions_data = body.get("positions", [])

    # Delete existing, re-create
    LayoutPosition.query.filter_by(layout_id=layout_id).delete()

    created = []
    for p in positions_data:
        rid = p.get("receiver_id")
        if not rid:
            continue
        pos = LayoutPosition(
            layout_id=layout_id,
            receiver_id=int(rid),
            x_pct=float(p.get("x_pct", 0)),
            y_pct=float(p.get("y_pct", 0)),
        )
        db.session.add(pos)
        created.append(pos)

    layout.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify(layout.to_dict(include_positions=True))


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
