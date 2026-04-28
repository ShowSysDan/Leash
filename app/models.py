from datetime import datetime
from flask import current_app
from app import db


# ── Many-to-many: NDIReceiver ↔ ReceiverGroup ─────────────────────────────
receiver_group_membership = db.Table(
    "receiver_group_membership",
    db.Column("receiver_id", db.Integer, db.ForeignKey("ndi_receivers.id", ondelete="CASCADE"), primary_key=True),
    db.Column("group_id",    db.Integer, db.ForeignKey("receiver_groups.id", ondelete="CASCADE"), primary_key=True),
)


class NDIReceiver(db.Model):
    __tablename__ = "ndi_receivers"

    id = db.Column(db.Integer, primary_key=True)
    index = db.Column(db.Integer, unique=True, nullable=False)
    label = db.Column(db.String(100))
    ip_last_octet = db.Column(db.String(3), unique=True, nullable=False)

    # Live values from /about
    hostname = db.Column(db.String(255))
    current_source = db.Column(db.String(255))
    status = db.Column(db.String(20), default="unknown")  # online / offline / unknown

    # Device identity
    hardware_version = db.Column(db.String(100))
    firmware_version = db.Column(db.String(100))
    serial_number = db.Column(db.String(50))
    mcu_version = db.Column(db.String(50))
    video_format = db.Column(db.String(50))

    # Network info
    network_config_method = db.Column(db.String(20))
    gateway = db.Column(db.String(20))
    network_mask = db.Column(db.String(20))
    fallback_ip = db.Column(db.String(20))

    first_seen = db.Column(db.DateTime, default=datetime.utcnow)
    last_seen = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    groups = db.relationship("ReceiverGroup", secondary=receiver_group_membership, back_populates="receivers")

    @property
    def ip_address(self) -> str:
        prefix = current_app.config.get("NDI_SUBNET_PREFIX", "10.1.248.")
        return f"{prefix}{self.ip_last_octet}"

    @property
    def display_name(self) -> str:
        """Best human-friendly identifier: label > hostname > IP octet."""
        return self.label or self.hostname or self.ip_last_octet

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "index": self.index,
            "label": self.label or self.hostname or f"Player {self.ip_last_octet}",
            "ip_last_octet": self.ip_last_octet,
            "ip_address": self.ip_address,
            "hostname": self.hostname,
            "current_source": self.current_source,
            "status": self.status,
            "hardware_version": self.hardware_version,
            "firmware_version": self.firmware_version,
            "serial_number": self.serial_number,
            "mcu_version": self.mcu_version,
            "video_format": self.video_format,
            "network_config_method": self.network_config_method,
            "gateway": self.gateway,
            "network_mask": self.network_mask,
            "fallback_ip": self.fallback_ip,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "groups": [{"id": g.id, "name": g.name, "color": g.color} for g in self.groups],
        }


# ── Groups / Tags ─────────────────────────────────────────────────────────

class ReceiverGroup(db.Model):
    __tablename__ = "receiver_groups"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    color = db.Column(db.String(20), default="#0d6efd")   # any CSS colour
    description = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    receivers = db.relationship("NDIReceiver", secondary=receiver_group_membership, back_populates="groups")

    def to_dict(self, include_receivers: bool = False) -> dict:
        d = {
            "id": self.id,
            "name": self.name,
            "color": self.color,
            "description": self.description,
            "receiver_count": len(self.receivers),
        }
        if include_receivers:
            d["receivers"] = [r.to_dict() for r in self.receivers]
        return d


# ── Layouts ───────────────────────────────────────────────────────────────

class Layout(db.Model):
    __tablename__ = "layouts"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(255))
    # CSS colour for the canvas background
    bg_color = db.Column(db.String(20), default="#0a1628")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    positions = db.relationship(
        "LayoutPosition", backref="layout",
        cascade="all, delete-orphan", lazy="joined"
    )
    labels = db.relationship(
        "LayoutLabel", backref="layout",
        cascade="all, delete-orphan", lazy="joined"
    )

    def to_dict(self, include_positions: bool = False) -> dict:
        d = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "bg_color": self.bg_color,
            "receiver_count": len(self.positions),
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        if include_positions:
            d["positions"] = [p.to_dict() for p in self.positions]
            d["labels"] = [l.to_dict() for l in self.labels]
        return d


class LayoutPosition(db.Model):
    __tablename__ = "layout_positions"

    id = db.Column(db.Integer, primary_key=True)
    layout_id = db.Column(db.Integer, db.ForeignKey("layouts.id", ondelete="CASCADE"), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey("ndi_receivers.id", ondelete="CASCADE"), nullable=False)
    # Percentage 0–100 of canvas width / height
    x_pct = db.Column(db.Float, default=0.0)
    y_pct = db.Column(db.Float, default=0.0)

    __table_args__ = (db.UniqueConstraint("layout_id", "receiver_id"),)

    receiver = db.relationship("NDIReceiver", lazy="joined")

    def to_dict(self) -> dict:
        r = self.receiver
        return {
            "id": self.id,
            "receiver_id": self.receiver_id,
            "x_pct": self.x_pct,
            "y_pct": self.y_pct,
            "receiver": r.to_dict() if r else None,
        }


class LayoutLabel(db.Model):
    __tablename__ = "layout_labels"

    id = db.Column(db.Integer, primary_key=True)
    layout_id = db.Column(db.Integer, db.ForeignKey("layouts.id", ondelete="CASCADE"), nullable=False)
    text = db.Column(db.String(200), nullable=False)
    x_pct = db.Column(db.Float, default=5.0)
    y_pct = db.Column(db.Float, default=5.0)

    def to_dict(self) -> dict:
        return {"id": self.id, "text": self.text, "x_pct": self.x_pct, "y_pct": self.y_pct}


# ── Snapshots ─────────────────────────────────────────────────────────────

class Snapshot(db.Model):
    __tablename__ = "snapshots"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    entries = db.relationship(
        "SnapshotEntry", backref="snapshot",
        cascade="all, delete-orphan", lazy="joined"
    )

    def to_dict(self, include_entries: bool = False) -> dict:
        d = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "entry_count": len(self.entries),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if include_entries:
            d["entries"] = [e.to_dict() for e in self.entries]
        return d


class SnapshotEntry(db.Model):
    __tablename__ = "snapshot_entries"

    id = db.Column(db.Integer, primary_key=True)
    snapshot_id = db.Column(db.Integer, db.ForeignKey("snapshots.id", ondelete="CASCADE"), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey("ndi_receivers.id", ondelete="CASCADE"), nullable=False)
    source_name = db.Column(db.String(255))

    __table_args__ = (db.UniqueConstraint("snapshot_id", "receiver_id"),)

    receiver = db.relationship("NDIReceiver", lazy="joined")

    def to_dict(self) -> dict:
        r = self.receiver
        return {
            "id": self.id,
            "receiver_id": self.receiver_id,
            "source_name": self.source_name,
            "receiver_hostname": r.hostname if r else None,
            "receiver_ip": r.ip_address if r else None,
            "receiver_label": (r.label or r.hostname or f"Player {r.ip_last_octet}") if r else None,
            "receiver_status": r.status if r else None,
        }


# ── PTZ Cameras ───────────────────────────────────────────────────────────

class PTZCamera(db.Model):
    __tablename__ = "ptz_cameras"

    id = db.Column(db.Integer, primary_key=True)
    index = db.Column(db.Integer, unique=True, nullable=False)
    label = db.Column(db.String(100))
    ip_last_octet = db.Column(db.String(3), unique=True, nullable=False)
    model = db.Column(db.String(50))   # "P120", "A200GEN2", "P100", …

    hostname = db.Column(db.String(255))
    status = db.Column(db.String(20), default="unknown")  # online / offline / unknown

    hardware_version = db.Column(db.String(100))
    firmware_version = db.Column(db.String(100))
    serial_number = db.Column(db.String(50))
    mcu_version = db.Column(db.String(50))
    network_config_method = db.Column(db.String(20))
    gateway = db.Column(db.String(20))
    network_mask = db.Column(db.String(20))

    first_seen = db.Column(db.DateTime, default=datetime.utcnow)
    last_seen = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    presets = db.relationship(
        "CameraPreset", backref="camera",
        cascade="all, delete-orphan", order_by="CameraPreset.preset_number",
    )

    @property
    def ip_address(self) -> str:
        prefix = current_app.config.get("NDI_SUBNET_PREFIX", "10.1.248.")
        return f"{prefix}{self.ip_last_octet}"

    @property
    def display_name(self) -> str:
        return self.label or self.hostname or self.ip_last_octet

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "index": self.index,
            "label": self.label or self.hostname or f"Camera {self.ip_last_octet}",
            "ip_last_octet": self.ip_last_octet,
            "ip_address": self.ip_address,
            "hostname": self.hostname,
            "model": self.model,
            "status": self.status,
            "hardware_version": self.hardware_version,
            "firmware_version": self.firmware_version,
            "serial_number": self.serial_number,
            "mcu_version": self.mcu_version,
            "network_config_method": self.network_config_method,
            "gateway": self.gateway,
            "network_mask": self.network_mask,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "presets": [p.to_dict() for p in self.presets],
        }


class CameraPreset(db.Model):
    __tablename__ = "camera_presets"

    id = db.Column(db.Integer, primary_key=True)
    camera_id = db.Column(
        db.Integer, db.ForeignKey("ptz_cameras.id", ondelete="CASCADE"), nullable=False
    )
    preset_number = db.Column(db.Integer, nullable=False)   # 0–99
    name = db.Column(db.String(100), nullable=False)

    __table_args__ = (db.UniqueConstraint("camera_id", "preset_number"),)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "camera_id": self.camera_id,
            "preset_number": self.preset_number,
            "name": self.name,
        }




class ScheduledRecall(db.Model):
    __tablename__ = "scheduled_recalls"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)

    # "ndi" = recall an NDI snapshot; "camera" = recall a camera preset
    schedule_type = db.Column(db.String(20), nullable=False, default="ndi")

    # NDI type: null if the snapshot was deleted — schedule stays but skipped until reassigned
    snapshot_id = db.Column(db.Integer, db.ForeignKey("snapshots.id", ondelete="SET NULL"), nullable=True)

    # Camera type: which camera and which preset number (0–99)
    camera_id = db.Column(db.Integer, db.ForeignKey("ptz_cameras.id", ondelete="SET NULL"), nullable=True)
    preset_number = db.Column(db.Integer, nullable=True)

    # "weekly" = existing; "once" = single date; "weekly_until" = weekly with end date
    schedule_mode = db.Column(db.String(20), nullable=False, default="weekly")

    # Comma-separated weekday numbers: 0=Mon … 6=Sun  (e.g. "0,1,2,3,4" = Mon–Fri)
    # Empty string for "once" mode.
    days_of_week = db.Column(db.String(20), nullable=False, default="")
    # "HH:MM" in local server time (24-hour)
    time_of_day = db.Column(db.String(5), nullable=False)

    # One-time date (schedule_mode="once")
    run_date = db.Column(db.Date, nullable=True)
    # Recurring end date (schedule_mode="weekly_until")
    end_date = db.Column(db.Date, nullable=True)

    enabled = db.Column(db.Boolean, default=True, nullable=False)
    last_run = db.Column(db.DateTime)
    last_result = db.Column(db.String(255))

    # Persistence / enforcement — only meaningful for schedule_type="ndi"
    persistent = db.Column(db.Boolean, default=False, nullable=False)
    persist_minutes = db.Column(db.Integer, default=60)
    enforcing_until = db.Column(db.DateTime, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    snapshot = db.relationship("Snapshot", lazy="joined")
    camera = db.relationship("PTZCamera", lazy="joined")

    DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    def day_labels(self) -> list[str]:
        mode = self.schedule_mode or "weekly"
        if mode == "once":
            return [f"Once: {self.run_date.strftime('%b %d %Y')}"] if self.run_date else ["(no date)"]
        labels = [self.DAY_NAMES[int(d)] for d in (self.days_of_week or "").split(",") if d.strip().isdigit()]
        if mode == "weekly_until" and self.end_date:
            labels.append(f"until {self.end_date.strftime('%b %d %Y')}")
        return labels

    def is_enforcing(self) -> bool:
        return bool(self.enforcing_until and self.enforcing_until > datetime.utcnow())

    def enforcement_minutes_remaining(self) -> int | None:
        if not self.is_enforcing():
            return None
        delta = self.enforcing_until - datetime.utcnow()
        return max(0, int(delta.total_seconds() // 60))

    def to_dict(self) -> dict:
        d: dict = {
            "id": self.id,
            "name": self.name,
            "schedule_type": self.schedule_type,
            "schedule_mode": self.schedule_mode or "weekly",
            "days_of_week": self.days_of_week or "",
            "day_labels": self.day_labels(),
            "time_of_day": self.time_of_day,
            "run_date": self.run_date.isoformat() if self.run_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "enabled": self.enabled,
            "last_run": self.last_run.isoformat() if self.last_run else None,
            "last_result": self.last_result,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if self.schedule_type == "camera":
            d["camera_id"] = self.camera_id
            d["camera_name"] = self.camera.display_name if self.camera else None
            d["preset_number"] = self.preset_number
            # Look up preset label from the camera's preset list
            preset_label = None
            if self.camera and self.preset_number is not None:
                for p in self.camera.presets:
                    if p.preset_number == self.preset_number:
                        preset_label = p.name
                        break
            d["preset_label"] = preset_label
        else:
            d["snapshot_id"] = self.snapshot_id
            d["snapshot_name"] = self.snapshot.name if self.snapshot else None
            d["persistent"] = self.persistent
            d["persist_minutes"] = self.persist_minutes
            d["enforcing_until"] = self.enforcing_until.isoformat() if self.enforcing_until else None
            d["is_enforcing"] = self.is_enforcing()
            d["enforcement_minutes_remaining"] = self.enforcement_minutes_remaining()
        return d


# ── Device Events (action history) ────────────────────────────────────────

class DeviceEvent(db.Model):
    """Persisted record of notable receiver activity.

    Mirrors the events that go to syslog via app.services.audit_log so the
    history survives log rotation and can be displayed in-app per receiver.
    """
    __tablename__ = "device_events"

    id = db.Column(db.Integer, primary_key=True)
    receiver_id = db.Column(
        db.Integer,
        db.ForeignKey("ndi_receivers.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    # IP captured at event time so deleted-receiver history still makes sense
    ip_address = db.Column(db.String(40))
    event_type = db.Column(db.String(40), nullable=False, index=True)
    detail = db.Column(db.String(500))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "receiver_id": self.receiver_id,
            "ip_address": self.ip_address,
            "event_type": self.event_type,
            "detail": self.detail,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }


# ── App Settings ─────────────────────────────────────────────────────────

class AppSetting(db.Model):
    """Key-value store for all runtime-configurable settings.

    Populated on first startup from config.py defaults / env-var overrides.
    After that the DB is the source of truth; use the Settings UI or API.
    """
    __tablename__ = "app_settings"

    key = db.Column(db.String(64), primary_key=True)
    value = db.Column(db.Text, nullable=False, default="")
    value_type = db.Column(db.String(16), default="string")  # string | int | bool
    label = db.Column(db.String(100))
    description = db.Column(db.String(256))
    group_name = db.Column(db.String(64))
    sensitive = db.Column(db.Boolean, default=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self, mask_sensitive: bool = True) -> dict:
        display = "***" if (mask_sensitive and self.sensitive and self.value) else self.value
        return {
            "key": self.key,
            "value": display,
            "type": self.value_type,
            "label": self.label,
            "description": self.description,
            "group": self.group_name,
            "sensitive": self.sensitive,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# ── NDI Sources ───────────────────────────────────────────────────────────

class NDISource(db.Model):
    __tablename__ = "ndi_sources"

    id = db.Column(db.Integer, primary_key=True)
    # Stable 1-based index — assigned once, never reused or reassigned.
    # Survives the source going offline. External systems can address by this number.
    source_index = db.Column(db.Integer, unique=True)
    name = db.Column(db.String(255), unique=True, nullable=False)
    # True = seen on the network during last discovery run
    discovered = db.Column(db.Boolean, default=False)
    last_seen = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @classmethod
    def next_index(cls) -> int:
        """Return the next available stable source index."""
        max_idx = db.session.query(db.func.max(cls.source_index)).scalar() or 0
        return max_idx + 1

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source_index": self.source_index,
            "name": self.name,
            "online": self.discovered,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
        }
