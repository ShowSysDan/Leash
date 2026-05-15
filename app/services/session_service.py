"""
DB-backed Flask sessions, shared across apps that point at the same
Postgres `users` table.

The cookie carries only a 256-bit random sid; the actual session payload
lives in `<AUTH_DB_SCHEMA>.app_sessions`. Two apps pointed at the same
database + auth schema share login state automatically.

Mirrors the SHARED_SESSIONS.md guide from the sibling app:
  * sid rotation on login (session fixation defense) — see auth.py
  * server never adopts client-supplied sids it doesn't already know
  * 12-hour expiry, refreshed on every request
  * DISABLE_DB_SESSIONS=1 reverts to Flask's signed-cookie sessions

If AUTH_DB_SCHEMA is empty (dev / SQLite) or DISABLE_DB_SESSIONS is set,
init_db_sessions is a no-op and Flask's default cookie sessions stay
in effect.
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
from datetime import datetime, timedelta

from flask.sessions import SessionInterface, SessionMixin
from sqlalchemy import text
from werkzeug.datastructures import CallbackDict

logger = logging.getLogger(__name__)

_SID_RE = re.compile(r"^[A-Za-z0-9_-]{20,128}$")


class DBSession(CallbackDict, SessionMixin):
    """A dict-like session whose state lives in Postgres."""

    def __init__(self, initial=None, sid: str | None = None, new: bool = False):
        def _on_update(_self):
            _self.modified = True
        CallbackDict.__init__(self, initial, _on_update)
        self.sid = sid
        self.new = new
        self.modified = False


class DBSessionInterface(SessionInterface):
    """Stores session payload in <AUTH_DB_SCHEMA>.app_sessions.

    Queries are explicitly schema-qualified so they don't depend on the
    connection's search_path (which Leash pins to its own schema).
    """

    def __init__(self, db, auth_schema: str):
        self._db = db
        self._auth_schema = auth_schema
        # Defensive quote — schema name comes from config, but never trust.
        self._qschema = '"' + auth_schema.replace('"', '""') + '"'

    @staticmethod
    def _new_sid() -> str:
        # 32 bytes = 256 bits of entropy, base64url-encoded.
        return secrets.token_urlsafe(32)

    def _load(self, sid: str) -> tuple[dict, bool]:
        # Use a dedicated connection so we don't entangle with the
        # request-scoped db.session transaction.
        try:
            with self._db.engine.begin() as conn:
                row = conn.execute(
                    text(
                        f"SELECT data, expires_at FROM {self._qschema}.app_sessions "
                        f"WHERE sid = :sid"
                    ),
                    {"sid": sid},
                ).mappings().first()

                if not row:
                    return {}, False

                expires = row["expires_at"]
                if isinstance(expires, str):
                    try:
                        expires = datetime.fromisoformat(expires.split(".")[0].replace("Z", ""))
                    except ValueError:
                        expires = datetime.utcnow() - timedelta(seconds=1)

                if expires < datetime.utcnow():
                    conn.execute(
                        text(f"DELETE FROM {self._qschema}.app_sessions WHERE sid = :sid"),
                        {"sid": sid},
                    )
                    return {}, False

                raw = row["data"] or "{}"
        except Exception:
            logger.exception("db-sessions: load query failed")
            return {}, False

        try:
            data = json.loads(raw) if isinstance(raw, str) else dict(raw)
            if not isinstance(data, dict):
                data = {}
        except (json.JSONDecodeError, TypeError, ValueError):
            data = {}
        return data, True

    def open_session(self, app, request):
        cookie_name = app.config.get("SESSION_COOKIE_NAME", "session")
        sid = request.cookies.get(cookie_name)
        if sid and _SID_RE.match(sid):
            data, ok = self._load(sid)
            if ok:
                return DBSession(data, sid=sid, new=False)
        # Unknown / malformed / expired — mint a fresh sid. We never adopt
        # a client-supplied sid we don't already know (session-fixation defense).
        return DBSession(sid=self._new_sid(), new=True)

    def save_session(self, app, session, response):
        domain = self.get_cookie_domain(app)
        path = self.get_cookie_path(app)
        cookie_name = app.config.get("SESSION_COOKIE_NAME", "session")

        # Emptied (logout) → delete the row and the cookie.
        if not session:
            if session.modified:
                try:
                    with self._db.engine.begin() as conn:
                        conn.execute(
                            text(f"DELETE FROM {self._qschema}.app_sessions WHERE sid = :sid"),
                            {"sid": session.sid},
                        )
                except Exception:
                    logger.exception("db-sessions: failed to delete session on logout")
                response.delete_cookie(cookie_name, domain=domain, path=path)
            return

        if not session.modified and not session.new:
            return

        lifetime = app.permanent_session_lifetime
        expires = datetime.utcnow() + lifetime

        try:
            payload = json.dumps(dict(session), default=str)
        except (TypeError, ValueError):
            logger.warning("db-sessions: session payload not JSON-serialisable — saving empty")
            payload = "{}"

        user_id = session.get("user_id")
        sql = text(
            f"INSERT INTO {self._qschema}.app_sessions "
            f"  (sid, user_id, data, last_seen, expires_at) "
            f"VALUES (:sid, :uid, :data, CURRENT_TIMESTAMP, :expires) "
            f"ON CONFLICT (sid) DO UPDATE SET "
            f"  user_id    = EXCLUDED.user_id, "
            f"  data       = EXCLUDED.data, "
            f"  last_seen  = CURRENT_TIMESTAMP, "
            f"  expires_at = EXCLUDED.expires_at"
        )
        try:
            with self._db.engine.begin() as conn:
                conn.execute(sql, {
                    "sid": session.sid,
                    "uid": user_id,
                    "data": payload,
                    "expires": expires,
                })
        except Exception:
            logger.exception("db-sessions: save failed — session not persisted")
            return

        response.set_cookie(
            cookie_name,
            session.sid,
            expires=expires,
            httponly=self.get_cookie_httponly(app),
            domain=domain,
            path=path,
            secure=self.get_cookie_secure(app),
            samesite=self.get_cookie_samesite(app),
        )


def rotate_sid(session) -> None:
    """Mint a new sid for an already-open session.

    Called from the login path so the post-auth cookie value differs from
    whatever the client (possibly attacker-influenced) was carrying before.
    No-op when DB sessions aren't active (the session won't have a `sid`
    attribute under Flask's default cookie backend).
    """
    if not hasattr(session, "sid"):
        return
    session.sid = secrets.token_urlsafe(32)
    session.new = True
    session.modified = True


def _ensure_app_sessions_table(db, auth_schema: str) -> None:
    """Create app_sessions in the shared schema if it isn't there yet.

    The sibling app (321Theater) also runs this migration; whichever boots
    first wins. The CREATE is idempotent so a concurrent attempt is safe.
    """
    qschema = '"' + auth_schema.replace('"', '""') + '"'
    ddl = text(f"""
        CREATE TABLE IF NOT EXISTS {qschema}.app_sessions (
            sid         TEXT PRIMARY KEY,
            user_id     INTEGER REFERENCES {qschema}.users(id) ON DELETE CASCADE,
            data        TEXT NOT NULL DEFAULT '{{}}',
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_seen   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at  TIMESTAMP NOT NULL
        )
    """)
    idx_expires = text(
        f"CREATE INDEX IF NOT EXISTS idx_app_sessions_expires "
        f"ON {qschema}.app_sessions(expires_at)"
    )
    idx_user = text(
        f"CREATE INDEX IF NOT EXISTS idx_app_sessions_user "
        f"ON {qschema}.app_sessions(user_id)"
    )
    with db.engine.connect() as conn:
        conn.execute(ddl)
        conn.execute(idx_expires)
        conn.execute(idx_user)
        conn.commit()


def init_db_sessions(app, db) -> bool:
    """Wire DB-backed sessions onto the Flask app.

    Returns True if installed, False if skipped (so callers can log it).
    Skipped when:
      * DISABLE_DB_SESSIONS=1 in the env (escape hatch);
      * AUTH_DB_SCHEMA is empty (dev / SQLite — no shared users table);
      * the database isn't Postgres (this design is Postgres-specific).
    """
    if os.environ.get("DISABLE_DB_SESSIONS", "0") in ("1", "true", "True", "yes"):
        logger.info("db-sessions: disabled via DISABLE_DB_SESSIONS — using cookie sessions")
        return False

    auth_schema = (app.config.get("AUTH_DB_SCHEMA") or "").strip()
    if not auth_schema:
        logger.info("db-sessions: AUTH_DB_SCHEMA not set — using cookie sessions")
        return False

    uri = app.config.get("SQLALCHEMY_DATABASE_URI") or ""
    if not uri.startswith("postgresql"):
        logger.info("db-sessions: not a Postgres deployment — using cookie sessions")
        return False

    try:
        _ensure_app_sessions_table(db, auth_schema)
    except Exception:
        logger.exception(
            "db-sessions: failed to ensure %s.app_sessions exists — falling back to cookie sessions",
            auth_schema,
        )
        return False

    app.session_interface = DBSessionInterface(db, auth_schema)
    logger.info("db-sessions: enabled (schema=%s)", auth_schema)
    return True
