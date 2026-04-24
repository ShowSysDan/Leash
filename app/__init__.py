import logging
import logging.handlers
from pathlib import Path

from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate

from config import config

db = SQLAlchemy()
migrate = Migrate()

logger = logging.getLogger(__name__)


def _configure_syslog(app: Flask) -> None:
    """Attach a SysLogHandler to the 'leash' logger hierarchy.

    Tries the Unix domain socket (/dev/log on Linux, /var/run/syslog on macOS).
    Falls back silently if syslog is not available so the app still starts.
    """
    level = logging.DEBUG if app.debug else logging.INFO
    fmt = logging.Formatter("leash %(name)s %(levelname)s: %(message)s")

    for addr in ("/dev/log", "/var/run/syslog"):
        try:
            handler = logging.handlers.SysLogHandler(
                address=addr,
                facility=logging.handlers.SysLogHandler.LOG_LOCAL0,
            )
            handler.setFormatter(fmt)
            handler.setLevel(level)

            leash_log = logging.getLogger("leash")
            leash_log.setLevel(level)
            # Avoid double-adding on reloads (e.g. Flask debug reloader)
            if not any(isinstance(h, logging.handlers.SysLogHandler) for h in leash_log.handlers):
                leash_log.addHandler(handler)

            app.logger.info("Leash: syslog handler attached (%s, LOCAL0)", addr)
            return
        except OSError:
            continue

    app.logger.warning("Leash: syslog socket not found — audit events will not go to syslog")


def _auto_migrate(app: Flask) -> None:
    """Apply any pending Alembic migrations on startup.

    Handles three cases:
    1. No migrations/ directory yet  → init + generate initial migration + upgrade
    2. migrations/ exists but no version files → generate initial migration + upgrade
    3. Normal case → upgrade only (no-op if already current)
    """
    from flask_migrate import upgrade
    from flask_migrate import init as flask_db_init
    from flask_migrate import migrate as flask_db_migrate

    migrations_dir = Path(app.root_path).parent / "migrations"
    versions_dir = migrations_dir / "versions"

    try:
        if not migrations_dir.exists():
            logger.info("Leash: initialising migrations directory")
            flask_db_init()
            flask_db_migrate(message="initial schema")
        elif not versions_dir.exists() or not list(versions_dir.glob("*.py")):
            logger.info("Leash: generating initial migration")
            flask_db_migrate(message="initial schema")

        logger.info("Leash: applying pending migrations")
        upgrade()
    except Exception:
        logger.exception("Leash: auto-migration failed — check the database connection")
        raise


def create_app(config_name: str = "default") -> Flask:
    app = Flask(__name__)
    app.config.from_object(config[config_name])

    cfg = config[config_name]
    if hasattr(cfg, "init_app"):
        cfg.init_app(app)

    db.init_app(app)
    migrate.init_app(app, db)

    from app.routes.main import main_bp
    from app.routes.api import api_bp
    from app.routes.groups_api import groups_api_bp
    from app.routes.layouts_api import layouts_api_bp
    from app.routes.snapshots_api import snapshots_api_bp
    from app.routes.external_api import v1_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp, url_prefix="/api")
    app.register_blueprint(groups_api_bp, url_prefix="/api")
    app.register_blueprint(layouts_api_bp, url_prefix="/api")
    app.register_blueprint(snapshots_api_bp, url_prefix="/api")
    app.register_blueprint(v1_bp, url_prefix="/api/v1")

    _configure_syslog(app)

    with app.app_context():
        _auto_migrate(app)

    return app
