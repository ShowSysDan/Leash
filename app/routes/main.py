from flask import Blueprint, render_template
from app.models import NDIReceiver, NDISource
from app import db

main_bp = Blueprint("main", __name__)


@main_bp.route("/")
def index():
    receivers = NDIReceiver.query.order_by(NDIReceiver.index).all()
    sources = NDISource.query.order_by(NDISource.name).all()
    return render_template("index.html", receivers=receivers, sources=sources)


@main_bp.route("/receivers/<int:receiver_id>")
def receiver_detail(receiver_id: int):
    receiver = NDIReceiver.query.get_or_404(receiver_id)
    sources = NDISource.query.order_by(NDISource.name).all()
    return render_template("receiver_detail.html", receiver=receiver, sources=sources)


@main_bp.route("/sources")
def sources():
    all_sources = NDISource.query.order_by(NDISource.name).all()
    return render_template("sources.html", sources=all_sources)
