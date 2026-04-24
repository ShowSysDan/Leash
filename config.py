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
    NDI_MAX_RECEIVERS = int(os.environ.get("NDI_MAX_RECEIVERS", 83))

    # Async request timeout (seconds)
    HTTP_TIMEOUT = int(os.environ.get("HTTP_TIMEOUT", 5))


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
        # Render/Heroku supply postgres:// — SQLAlchemy requires postgresql://
        uri = cls.SQLALCHEMY_DATABASE_URI or ""
        if uri.startswith("postgres://"):
            cls.SQLALCHEMY_DATABASE_URI = uri.replace("postgres://", "postgresql://", 1)


config = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "default": DevelopmentConfig,
}
