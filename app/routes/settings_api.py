"""
Settings API blueprint.

  GET  /api/settings          list all settings (sensitive values masked)
  PUT  /api/settings          bulk-update one or more settings
  GET  /api/settings/schema   schema metadata for the UI (labels, types, groups)
"""
import logging

from flask import Blueprint, current_app, jsonify, request

from app.routes._helpers import admin_required, err as _err
from app.services.settings_service import (
    _SCHEMA_MAP,
    all_settings_dicts,
    update_setting,
)

logger = logging.getLogger(__name__)

settings_api_bp = Blueprint("settings_api", __name__)


@settings_api_bp.route("/settings", methods=["GET"])
@admin_required
def get_settings():
    return jsonify(all_settings_dicts(mask_sensitive=True))


@settings_api_bp.route("/settings/schema", methods=["GET"])
@admin_required
def get_schema():
    """Return schema metadata (no values) for front-end form construction."""
    from app.services.settings_service import SETTINGS_SCHEMA
    return jsonify([
        {k: v for k, v in s.items() if k not in ("default",)}
        for s in SETTINGS_SCHEMA
    ])


@settings_api_bp.route("/settings", methods=["PUT"])
@admin_required
def update_settings():
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return _err("Expected a JSON object of {key: value} pairs")

    app = current_app._get_current_object()
    updated = []
    errors = []

    for key, raw_value in body.items():
        if key not in _SCHEMA_MAP:
            errors.append(f"Unknown setting key: {key!r}")
            continue

        raw_str = str(raw_value).strip()

        # Don't overwrite a sensitive field when the client sends the placeholder
        schema = _SCHEMA_MAP[key]
        if schema.get("sensitive") and raw_str == "***":
            continue

        # Type validation for ints
        if schema["type"] == "int":
            try:
                int(raw_str)
            except (ValueError, TypeError):
                errors.append(f"{key}: must be an integer")
                continue

        try:
            update_setting(app, key, raw_str)
            updated.append(key)
        except Exception as exc:
            logger.exception("Settings update failed for key=%s", key)
            errors.append(f"{key}: {exc}")

    if errors and not updated:
        return _err("; ".join(errors))

    return jsonify({"updated": updated, "errors": errors})
