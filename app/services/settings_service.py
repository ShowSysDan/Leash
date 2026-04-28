"""
Database-backed settings store.

All application settings except SECRET_KEY and DATABASE_URL live in the
app_settings table.  On every startup the following happens:

  1. seed_defaults() — for each schema entry, if no DB row exists yet,
     write the current app.config value (which already has any env-var
     override applied) as the initial value.  On the very first boot this
     migrates whatever was in .env into the DB automatically.

  2. load_into_app() — read every DB row and overwrite app.config so the
     DB is the single source of truth from that point on.

Live changes via the Settings UI / PUT /api/settings take effect immediately
without a restart: update_setting() writes to DB and updates app.config in
the same request, then reschedules any affected APScheduler jobs.
"""
import logging
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema — canonical list of every DB-managed setting
# ---------------------------------------------------------------------------

SETTINGS_SCHEMA: list[dict] = [
    # BirdDog Network
    dict(key="NDI_SUBNET_PREFIX",      type="string", default="10.1.248.",
         group="BirdDog Network", label="Subnet Prefix",
         description="IP prefix for the BirdDog device subnet (e.g. 10.1.248.)"),
    dict(key="NDI_DEVICE_PORT",        type="int",    default="8080",
         group="BirdDog Network", label="Device Port",
         description="HTTP port on BirdDog PLAY devices"),
    dict(key="NDI_DEVICE_PASSWORD",    type="string", default="birddog",
         group="BirdDog Network", label="Device Password",
         description="Authentication password for BirdDog devices", sensitive=True),
    dict(key="HTTP_TIMEOUT",           type="int",    default="5",
         group="BirdDog Network", label="HTTP Timeout (s)",
         description="Per-device HTTP request timeout in seconds"),
    # Tractus MV
    dict(key="TRACTUS_MV_HOSTS",       type="string", default="10.1.248.191,10.1.248.192",
         group="Tractus MV", label="Tractus MV Hosts",
         description="Comma-separated IPs of Tractus MV servers (primary first)"),
    dict(key="TRACTUS_MV_PORT",        type="int",    default="8901",
         group="Tractus MV", label="Tractus MV Port",
         description="HTTP port for the Tractus MV /sources API"),
    # Polling
    dict(key="SOURCE_POLL_INTERVAL",   type="int",    default="60",
         group="Polling", label="Source Sync Interval (s)",
         description="How often to sync NDI sources from Tractus MV"),
    dict(key="RECEIVER_POLL_INTERVAL", type="int",    default="15",
         group="Polling", label="Receiver Poll Interval (s)",
         description="How often to poll all receivers for their current source"),
    dict(key="ENFORCEMENT_INTERVAL",   type="int",    default="60",
         group="Polling", label="Enforcement Interval (s)",
         description="How often the enforcement poller corrects source drift"),
    # Recalls
    dict(key="RECALL_CONCURRENCY",     type="int",    default="10",
         group="Recalls", label="Recall Concurrency",
         description="Max simultaneous BirdDog API calls during snapshot recalls"),
    # External API
    dict(key="API_KEY",                type="string", default="",
         group="External API", label="API Key",
         description="Secret key for /api/v1/ endpoints. Leave empty for open access.",
         sensitive=True),
    # Authentication
    dict(key="AUTH_FORGOT_PASSWORD_URL", type="string", default="",
         group="Authentication", label="Forgot Password URL",
         description="URL of the external auth app where users can reset their password. "
                     "Shown as a link on the login page and in must-change-password warnings."),
    # Syslog
    dict(key="SYSLOG_ENABLED",      type="bool",   default="true",
         group="Syslog", label="Enable Syslog",
         description="Send audit events to syslog. Disable to silence syslog without changing other settings."),
    dict(key="SYSLOG_HOST",         type="string", default="",
         group="Syslog", label="Remote Syslog Host",
         description="Hostname or IP for a remote syslog server. Leave empty to log to the local Unix socket."),
    dict(key="SYSLOG_PORT",         type="int",    default="514",
         group="Syslog", label="Remote Syslog Port",
         description="UDP port of the remote syslog server (only used when host is set)."),
    dict(key="SYSLOG_FACILITY",     type="string", default="local0",
         group="Syslog", label="Facility",
         description="Syslog facility name (e.g. local0–local7, user, daemon)."),
]

_SCHEMA_MAP: dict[str, dict] = {s["key"]: s for s in SETTINGS_SCHEMA}

# Interval settings → APScheduler job IDs that need rescheduling on change
_INTERVAL_JOBS: dict[str, str] = {
    "ENFORCEMENT_INTERVAL":   "leash_enforcement",
    "SOURCE_POLL_INTERVAL":   "leash_source_sync",
    "RECEIVER_POLL_INTERVAL": "leash_receiver_poll",
}


# ---------------------------------------------------------------------------
# Type coercion
# ---------------------------------------------------------------------------

def _coerce(key: str, raw: str) -> Any:
    """Convert a raw DB string to the appropriate Python type."""
    schema = _SCHEMA_MAP.get(key)
    if not schema:
        return raw
    if schema["type"] == "int":
        try:
            return int(raw)
        except (ValueError, TypeError):
            return int(schema["default"])
    if schema["type"] == "bool":
        return raw.strip().lower() in ("1", "true", "yes")
    # Special case: TRACTUS_MV_HOSTS is a comma-separated string → list
    if key == "TRACTUS_MV_HOSTS":
        return [h.strip() for h in raw.split(",") if h.strip()]
    return raw


def _to_db_string(key: str, value: Any) -> str:
    """Serialize a Python app.config value to the DB text format."""
    if isinstance(value, list):
        return ",".join(str(v) for v in value)
    return str(value) if value is not None else ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def seed_defaults(app) -> None:
    """Insert DB rows for any schema key that doesn't have one yet.

    The initial value is taken from app.config (honours env-var overrides
    set before the DB was ready), falling back to the schema default.
    """
    from app import db
    from app.models import AppSetting

    inserted = 0
    for s in SETTINGS_SCHEMA:
        key = s["key"]
        if AppSetting.query.filter_by(key=key).first():
            continue

        raw = app.config.get(key)
        db_value = _to_db_string(key, raw) if raw is not None else s["default"]

        db.session.add(AppSetting(
            key=key,
            value=db_value,
            value_type=s["type"],
            label=s.get("label", key),
            description=s.get("description", ""),
            group_name=s.get("group", ""),
            sensitive=s.get("sensitive", False),
        ))
        inserted += 1

    if inserted:
        db.session.commit()
        logger.info("Settings: seeded %d new key(s) into DB", inserted)


def load_into_app(app) -> None:
    """Seed defaults then overwrite app.config with every DB-persisted value."""
    try:
        seed_defaults(app)
    except Exception:
        logger.exception("Settings: seed_defaults failed — using config.py values")
        return

    from app.models import AppSetting
    loaded = 0
    for row in AppSetting.query.all():
        try:
            app.config[row.key] = _coerce(row.key, row.value)
            loaded += 1
        except Exception:
            logger.exception("Settings: failed to load key=%s", row.key)

    logger.info("Settings: loaded %d setting(s) from DB", loaded)


def update_setting(app, key: str, raw_value: str) -> None:
    """Write a new value to DB, update app.config, and reschedule any affected jobs."""
    from app import db
    from app.models import AppSetting

    row = AppSetting.query.filter_by(key=key).first()
    if row:
        row.value = raw_value
    else:
        s = _SCHEMA_MAP.get(key, {})
        db.session.add(AppSetting(
            key=key,
            value=raw_value,
            value_type=s.get("type", "string"),
            label=s.get("label", key),
            description=s.get("description", ""),
            group_name=s.get("group", ""),
            sensitive=s.get("sensitive", False),
        ))
    db.session.commit()

    coerced = _coerce(key, raw_value)
    app.config[key] = coerced

    job_id = _INTERVAL_JOBS.get(key)
    if job_id and isinstance(coerced, int) and coerced > 0:
        from app.services.scheduler import get_scheduler
        sched = get_scheduler()
        if sched:
            try:
                sched.reschedule_job(job_id, trigger="interval", seconds=coerced)
                logger.info("Settings: rescheduled job %s → %ds", job_id, coerced)
            except Exception:
                logger.exception("Settings: could not reschedule job %s", job_id)

    # Reapply syslog handler when any SYSLOG_* setting changes
    if key.startswith("SYSLOG_"):
        try:
            from app.services.syslog_service import apply_syslog_config
            apply_syslog_config(app)
        except Exception:
            logger.exception("Settings: failed to reapply syslog config")


def all_settings_dicts(mask_sensitive: bool = True) -> list[dict]:
    """Return all settings grouped in schema order, suitable for the API."""
    from app.models import AppSetting
    db_rows = {r.key: r.value for r in AppSetting.query.all()}

    out = []
    for s in SETTINGS_SCHEMA:
        key = s["key"]
        value = db_rows.get(key, s["default"])
        is_sensitive = s.get("sensitive", False)
        out.append({
            "key":         key,
            "value":       "***" if (mask_sensitive and is_sensitive and value) else value,
            "type":        s["type"],
            "group":       s.get("group", ""),
            "label":       s.get("label", key),
            "description": s.get("description", ""),
            "sensitive":   is_sensitive,
        })
    return out
