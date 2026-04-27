from flask import Blueprint, current_app, render_template

from app.models import NDIReceiver, NDISource, PTZCamera, ReceiverGroup, Layout, Snapshot, ScheduledRecall
from app.services.settings_service import SETTINGS_SCHEMA, all_settings_dicts

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
    sources = _online_sources()
    return render_template("snapshots.html", snapshots=all_snaps, receivers=all_receivers, sources=sources)


@main_bp.route("/cameras")
def cameras():
    all_cameras = PTZCamera.query.order_by(PTZCamera.index).all()
    return render_template("cameras.html", cameras=all_cameras)


@main_bp.route("/cameras/<int:camera_id>")
def camera_detail(camera_id: int):
    cam = PTZCamera.query.get_or_404(camera_id)
    preset_map = {p.preset_number: p.name for p in cam.presets}
    return render_template("camera_detail.html", camera=cam, preset_map=preset_map)


@main_bp.route("/schedules")
def schedules():
    all_schedules = ScheduledRecall.query.order_by(ScheduledRecall.time_of_day).all()
    all_snapshots = Snapshot.query.order_by(Snapshot.name).all()
    all_cameras = PTZCamera.query.order_by(PTZCamera.index).all()
    return render_template(
        "schedules.html",
        schedules=all_schedules,
        snapshots=all_snapshots,
        cameras=all_cameras,
        concurrency=current_app.config.get("RECALL_CONCURRENCY", 10),
        enforcement_interval=current_app.config.get("ENFORCEMENT_INTERVAL", 60),
    )


@main_bp.route("/settings")
def settings():
    settings_list = all_settings_dicts(mask_sensitive=True)
    # Group by group_name for the template
    groups: dict[str, list] = {}
    for s in settings_list:
        groups.setdefault(s["group"], []).append(s)
    return render_template("settings.html", setting_groups=groups)
