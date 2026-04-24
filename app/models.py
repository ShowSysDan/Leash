from datetime import datetime
from flask import current_app
from app import db


class NDIReceiver(db.Model):
    __tablename__ = "ndi_receivers"

    id = db.Column(db.Integer, primary_key=True)
    # Ordering / display index — auto-assigned as int(ip_last_octet) on scan
    index = db.Column(db.Integer, unique=True, nullable=False)
    label = db.Column(db.String(100))
    # Last octet is the stable lookup key; enforce uniqueness
    ip_last_octet = db.Column(db.String(3), unique=True, nullable=False)

    # ── Live values from /about (refreshed on scan/poll) ──────────────────
    hostname = db.Column(db.String(255))
    current_source = db.Column(db.String(255))
    # online / offline / unknown
    status = db.Column(db.String(20), default="unknown")

    # Device identity
    hardware_version = db.Column(db.String(100))
    firmware_version = db.Column(db.String(100))
    serial_number = db.Column(db.String(50))
    mcu_version = db.Column(db.String(50))
    video_format = db.Column(db.String(50))

    # Network info
    network_config_method = db.Column(db.String(20))  # static / dhcp
    gateway = db.Column(db.String(20))
    network_mask = db.Column(db.String(20))
    fallback_ip = db.Column(db.String(20))

    # Discovery bookkeeping
    first_seen = db.Column(db.DateTime, default=datetime.utcnow)
    last_seen = db.Column(db.DateTime)
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
