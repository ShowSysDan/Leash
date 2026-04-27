import logging
import logging.handlers
import os
from pathlib import Path

from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate

from app.extensions import limiter
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


def _ensure_pg_schema(app: Flask) -> None:
    """If running on Postgres, CREATE SCHEMA IF NOT EXISTS for DATABASE_SCHEMA.

    Must run before any migrations / table creation so that the search_path
    on the connection has somewhere to point to.  No-op on SQLite.
    """
    uri = app.config.get("SQLALCHEMY_DATABASE_URI") or ""
    if not uri.startswith("postgresql"):
        return

    schema = app.config.get("DATABASE_SCHEMA") or "leash"
    # Quote the identifier defensively even though we control the value.
    safe_schema = schema.replace('"', '""')
    from sqlalchemy import text
    with db.engine.connect() as conn:
        conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{safe_schema}"'))
        conn.commit()
    logger.info("Leash: Postgres schema '%s' ready", schema)


def _auto_migrate(app: Flask) -> None:
    """Apply any pending Alembic migrations on startup.

    Handles three cases:
    1. No migrations/ directory yet  → init + generate initial migration + upgrade
    2. migrations/ exists but no version files → generate initial migration + upgrade
    3. Normal case → detect schema drift, generate migration if needed, upgrade
    """
    from flask_migrate import upgrade
    from flask_migrate import init as flask_db_init
    from flask_migrate import migrate as flask_db_migrate

    migrations_dir = Path(app.root_path).parent / "migrations"
    versions_dir = migrations_dir / "versions"

    try:
        _ensure_pg_schema(app)

        if not migrations_dir.exists():
            logger.info("Leash: initialising migrations directory")
            flask_db_init()
            flask_db_migrate(message="initial schema")
        elif not versions_dir.exists() or not list(versions_dir.glob("*.py")):
            logger.info("Leash: generating initial migration")
            flask_db_migrate(message="initial schema")
        else:
            # Check if the live models differ from what the DB has.
            # If so, auto-generate a migration so new tables/columns appear.
            if _schema_has_changes(app):
                logger.info("Leash: schema drift detected — generating migration")
                flask_db_migrate(message="auto schema update")

        logger.info("Leash: applying pending migrations")
        upgrade()
    except Exception:
        logger.exception("Leash: auto-migration failed — check the database connection")
        raise


def _schema_has_changes(app: Flask) -> bool:
    """Return True if SQLAlchemy models differ from the current DB schema."""
    try:
        from alembic.runtime.migration import MigrationContext
        from alembic.autogenerate import compare_metadata
        with db.engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            diffs = compare_metadata(ctx, db.metadata)
            return bool(diffs)
    except Exception:
        return False


def create_app(config_name: str = "default") -> Flask:
    app = Flask(__name__)
    app.config.from_object(config[config_name])

    cfg = config[config_name]
    if hasattr(cfg, "init_app"):
        cfg.init_app(app)

    db.init_app(app)
    migrate.init_app(app, db)
    limiter.init_app(app)

    # Belt-and-suspenders: if a request raises, roll back any pending SA state
    # so the next request starts on a clean session. Flask-SQLAlchemy already
    # calls session.remove() on teardown, but an explicit rollback on error
    # makes the intent clear and guards against any future custom commits.
    @app.teardown_request
    def _rollback_on_error(exc):
        if exc is not None:
            db.session.rollback()

    from app.routes.auth import auth_bp, init_auth
    from app.routes.main import main_bp
    from app.routes.api import api_bp
    from app.routes.cameras_api import cameras_api_bp
    from app.routes.groups_api import groups_api_bp
    from app.routes.layouts_api import layouts_api_bp
    from app.routes.snapshots_api import snapshots_api_bp
    from app.routes.external_api import v1_bp
    from app.routes.schedules_api import schedules_api_bp
    from app.routes.settings_api import settings_api_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp, url_prefix="/api")
    app.register_blueprint(cameras_api_bp, url_prefix="/api")
    app.register_blueprint(groups_api_bp, url_prefix="/api")
    app.register_blueprint(layouts_api_bp, url_prefix="/api")
    app.register_blueprint(snapshots_api_bp, url_prefix="/api")
    app.register_blueprint(v1_bp, url_prefix="/api/v1")
    app.register_blueprint(schedules_api_bp, url_prefix="/api")
    app.register_blueprint(settings_api_bp, url_prefix="/api")

    from app.__version__ import __version__
    app.config["LEASH_VERSION"] = __version__

    # Register auth before_request hooks and context processors
    init_auth(app)

    _configure_syslog(app)

    with app.app_context():
        _auto_migrate(app)
        # Load DB-persisted settings into app.config (seeds defaults on first boot).
        from app.services.settings_service import load_into_app as _load_settings
        _load_settings(app)

    # Start the background scheduler — only in the real worker process, not
    # Flask's reloader monitor parent (which never serves requests).
    if not app.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        from app.services.scheduler import init_scheduler
        init_scheduler(app)

    return app
