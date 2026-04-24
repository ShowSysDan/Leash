import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "change-me-in-production")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # BirdDog network
    NDI_SUBNET_PREFIX = os.environ.get("NDI_SUBNET_PREFIX", "10.1.248.")
    NDI_DEVICE_PORT = int(os.environ.get("NDI_DEVICE_PORT", 8080))
    NDI_DEVICE_PASSWORD = os.environ.get("NDI_DEVICE_PASSWORD", "birddog")

    # Async request timeout (seconds)
    HTTP_TIMEOUT = int(os.environ.get("HTTP_TIMEOUT", 5))

    # Max concurrent BirdDog HTTP calls during scheduled recalls / bulk ops
    RECALL_CONCURRENCY = int(os.environ.get("RECALL_CONCURRENCY", 10))

    # How often (seconds) the enforcement poller checks active persistence windows
    ENFORCEMENT_INTERVAL = int(os.environ.get("ENFORCEMENT_INTERVAL", 60))

    # Tractus MV API — source of truth for available NDI sources
    # Comma-separated list of hosts; tried in order, first success wins.
    TRACTUS_MV_HOSTS = [
        h.strip() for h in
        os.environ.get("TRACTUS_MV_HOSTS", "10.1.248.191,10.1.248.192").split(",")
        if h.strip()
    ]
    TRACTUS_MV_PORT = int(os.environ.get("TRACTUS_MV_PORT", 8901))

    # How often (seconds) the background job syncs NDISource table from Tractus MV
    SOURCE_POLL_INTERVAL = int(os.environ.get("SOURCE_POLL_INTERVAL", 60))

    # How often (seconds) the background job polls receiver current_source
    RECEIVER_POLL_INTERVAL = int(os.environ.get("RECEIVER_POLL_INTERVAL", 15))

    # External API key — leave unset to disable auth (open network only)
    API_KEY = os.environ.get("API_KEY") or None


class DevelopmentConfig(Config):
    DEBUG = True
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL",
        f"sqlite:///{BASE_DIR / 'leash_dev.db'}",
    )


class ProductionConfig(Config):
    DEBUG = False
    # Expects DATABASE_URL=postgresql://user:pass@host/dbname
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL")

    @classmethod
    def init_app(cls, app):
        # Fail closed if SECRET_KEY wasn't overridden for production.
        if app.config.get("SECRET_KEY") in (None, "", "change-me-in-production"):
            raise RuntimeError(
                "SECRET_KEY must be set to a secure random value in production. "
                "Set the SECRET_KEY environment variable before starting Leash."
            )
        # Render/Heroku supply postgres:// — SQLAlchemy requires postgresql://
        uri = cls.SQLALCHEMY_DATABASE_URI or ""
        if uri.startswith("postgres://"):
            cls.SQLALCHEMY_DATABASE_URI = uri.replace("postgres://", "postgresql://", 1)


config = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "default": DevelopmentConfig,
}
