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


# ── NDI Sources ───────────────────────────────────────────────────────────

class NDISource(db.Model):
    __tablename__ = "ndi_sources"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), unique=True, nullable=False)
    discovered = db.Column(db.Boolean, default=False)
    last_seen = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "discovered": self.discovered,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
        }
