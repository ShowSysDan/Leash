from datetime import datetime
from flask import current_app
from app import db


class NDIReceiver(db.Model):
    __tablename__ = "ndi_receivers"

    id = db.Column(db.Integer, primary_key=True)
    index = db.Column(db.Integer, unique=True, nullable=False)  # 1–83
    label = db.Column(db.String(100))
    ip_last_octet = db.Column(db.String(3), nullable=False)

    # Cached live values (refreshed on demand)
    hostname = db.Column(db.String(255))
    current_source = db.Column(db.String(255))
    status = db.Column(db.String(20), default="unknown")  # online / offline / unknown
    firmware_version = db.Column(db.String(50))
    serial_number = db.Column(db.String(50))
    video_format = db.Column(db.String(50))

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @property
    def ip_address(self) -> str:
        prefix = current_app.config.get("NDI_SUBNET_PREFIX", "10.1.248.")
        return f"{prefix}{self.ip_last_octet}"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "index": self.index,
            "label": self.label or f"Receiver {self.index}",
            "ip_last_octet": self.ip_last_octet,
            "ip_address": self.ip_address,
            "hostname": self.hostname,
            "current_source": self.current_source,
            "status": self.status,
            "firmware_version": self.firmware_version,
            "serial_number": self.serial_number,
            "video_format": self.video_format,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


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
