"""
Minimal startup configuration.

Only DATABASE_URL and SECRET_KEY stay here — everything else is stored in
the app_settings DB table and loaded by settings_service.load_into_app()
after the DB is ready.

The values below serve as fallbacks during the very first boot (before the
DB has been seeded) or in test environments where the DB might not exist.
They are overwritten by the DB-persisted values on every subsequent startup.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "change-me-in-production")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # PostgreSQL schema name to use when the DB is shared with other apps.
    # Ignored when DATABASE_URL points at SQLite (SQLite has no schemas).
    DATABASE_SCHEMA = os.environ.get("DATABASE_SCHEMA", "leash")

    @classmethod
    def init_app(cls, app):
        """Common URL normalization and Postgres schema wiring.

        Subclasses should call super().init_app(app) before adding their own logic.
        """
        uri = app.config.get("SQLALCHEMY_DATABASE_URI") or ""
        # Heroku/Render style postgres:// → SQLAlchemy needs postgresql://
        if uri.startswith("postgres://"):
            uri = uri.replace("postgres://", "postgresql://", 1)
            app.config["SQLALCHEMY_DATABASE_URI"] = uri

        # On Postgres, pin every new connection's search_path to our schema so
        # all unqualified CREATE TABLE statements (including alembic_version)
        # land in DATABASE_SCHEMA.  No effect on SQLite.
        if uri.startswith("postgresql"):
            schema = app.config.get("DATABASE_SCHEMA") or "leash"
            engine_opts = app.config.setdefault("SQLALCHEMY_ENGINE_OPTIONS", {})
            connect_args = engine_opts.setdefault("connect_args", {})
            connect_args["options"] = f"-csearch_path={schema},public"

    # --- Fallback values used before DB settings are loaded ---
    # These are seeded into the DB on first boot.  After that, the DB wins.
    NDI_SUBNET_PREFIX   = os.environ.get("NDI_SUBNET_PREFIX",   "10.1.248.")
    NDI_DEVICE_PORT     = int(os.environ.get("NDI_DEVICE_PORT",     "8080"))
    NDI_DEVICE_PASSWORD = os.environ.get("NDI_DEVICE_PASSWORD",     "birddog")
    HTTP_TIMEOUT        = int(os.environ.get("HTTP_TIMEOUT",         "5"))
    RECALL_CONCURRENCY  = int(os.environ.get("RECALL_CONCURRENCY",   "10"))
    ENFORCEMENT_INTERVAL  = int(os.environ.get("ENFORCEMENT_INTERVAL",   "60"))
    SOURCE_POLL_INTERVAL  = int(os.environ.get("SOURCE_POLL_INTERVAL",   "60"))
    RECEIVER_POLL_INTERVAL = int(os.environ.get("RECEIVER_POLL_INTERVAL", "15"))
    TRACTUS_MV_HOSTS = [
        h.strip() for h in
        os.environ.get("TRACTUS_MV_HOSTS", "10.1.248.191,10.1.248.192").split(",")
        if h.strip()
    ]
    TRACTUS_MV_PORT = int(os.environ.get("TRACTUS_MV_PORT", "8901"))
    API_KEY = os.environ.get("API_KEY") or None


class DevelopmentConfig(Config):
    DEBUG = True
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL",
        f"sqlite:///{BASE_DIR / 'leash_dev.db'}",
    )


class ProductionConfig(Config):
    DEBUG = False
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL")

    @classmethod
    def init_app(cls, app):
        if app.config.get("SECRET_KEY") in (None, "", "change-me-in-production"):
            raise RuntimeError(
                "SECRET_KEY must be set to a secure random value in production. "
                "Set the SECRET_KEY environment variable before starting Leash."
            )
        super().init_app(app)


config = {
    "development": DevelopmentConfig,
    "production":  ProductionConfig,
    "default":     DevelopmentConfig,
}
