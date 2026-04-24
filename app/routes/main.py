from flask import Blueprint, current_app, render_template

from app.models import NDIReceiver, NDISource, ReceiverGroup, Layout, Snapshot, ScheduledRecall

main_bp = Blueprint("main", __name__)


def _online_sources():
    """Sources visible on the network right now, ordered by stable index."""
    return NDISource.query.filter_by(discovered=True).order_by(NDISource.source_index).all()


@main_bp.route("/")
def index():
    receivers = NDIReceiver.query.order_by(NDIReceiver.index).all()
    sources = _online_sources()
    groups = ReceiverGroup.query.order_by(ReceiverGroup.name).all()
    return render_template("index.html", receivers=receivers, sources=sources, groups=groups)


@main_bp.route("/receivers/<int:receiver_id>")
def receiver_detail(receiver_id: int):
    receiver = NDIReceiver.query.get_or_404(receiver_id)
    sources = _online_sources()
    groups = ReceiverGroup.query.order_by(ReceiverGroup.name).all()
    return render_template("receiver_detail.html", receiver=receiver, sources=sources, groups=groups)


@main_bp.route("/sources")
def sources():
    # Show all sources with full status so operators can see what's offline
    all_sources = NDISource.query.order_by(NDISource.source_index).all()
    return render_template("sources.html", sources=all_sources)


@main_bp.route("/groups")
def groups():
    all_groups = ReceiverGroup.query.order_by(ReceiverGroup.name).all()
    all_receivers = NDIReceiver.query.order_by(NDIReceiver.index).all()
    sources = _online_sources()
    return render_template("groups.html", groups=all_groups, receivers=all_receivers, sources=sources)


@main_bp.route("/layouts")
def layouts():
    all_layouts = Layout.query.order_by(Layout.name).all()
    return render_template("layouts.html", layouts=all_layouts)


@main_bp.route("/layouts/<int:layout_id>")
def layout_view(layout_id: int):
    layout = Layout.query.get_or_404(layout_id)
    all_receivers = NDIReceiver.query.order_by(NDIReceiver.index).all()
    sources = _online_sources()
    return render_template("layout_view.html", layout=layout, receivers=all_receivers, sources=sources)


@main_bp.route("/snapshots")
def snapshots():
    all_snaps = Snapshot.query.order_by(Snapshot.created_at.desc()).all()
    all_receivers = NDIReceiver.query.order_by(NDIReceiver.index).all()
    return render_template("snapshots.html", snapshots=all_snaps, receivers=all_receivers)


@main_bp.route("/schedules")
def schedules():
    all_schedules = ScheduledRecall.query.order_by(ScheduledRecall.time_of_day).all()
    all_snapshots = Snapshot.query.order_by(Snapshot.name).all()
    return render_template(
        "schedules.html",
        schedules=all_schedules,
        snapshots=all_snapshots,
        concurrency=current_app.config.get("RECALL_CONCURRENCY", 10),
        enforcement_interval=current_app.config.get("ENFORCEMENT_INTERVAL", 60),
    )
