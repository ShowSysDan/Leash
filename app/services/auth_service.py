"""
Authentication service — cross-schema Postgres user lookup.

Reads from the shared database's AUTH_DB_SCHEMA users table.
Never writes to that table; all access is read-only.

If AUTH_DB_SCHEMA is empty (dev / SQLite), all functions return None
and the before_request hook skips authentication entirely.
"""
import logging
from typing import Optional

from werkzeug.security import generate_password_hash

logger = logging.getLogger(__name__)

# Pre-computed dummy hash used to normalize response time when a username
# is not found — prevents user enumeration via timing.
_DUMMY_HASH: str = generate_password_hash("__leash_dummy_not_a_real_password__")


def _schema(app) -> str:
    return (app.config.get("AUTH_DB_SCHEMA") or "").strip()


def get_user_by_username(username: str) -> Optional[dict]:
    """
    Return a user dict or None.

    Dict keys: id, username, password_hash, role, display_name,
               must_change_password
    """
    from flask import current_app
    from app import db
    from sqlalchemy import text

    schema = _schema(current_app)
    if not schema:
        return None

    safe = schema.replace('"', '""')
    try:
        sql = text(f"""
            SELECT id,
                   username,
                   password_hash,
                   role,
                   must_change_password
            FROM "{safe}".users
            WHERE username = :username
            LIMIT 1
        """)
        with db.engine.connect() as conn:
            row = conn.execute(sql, {"username": username}).mappings().first()
            if row is None:
                return None
            d = dict(row)
            # display_name: use username as the best-effort fallback
            d.setdefault("display_name", d["username"])
            d["must_change_password"] = bool(d.get("must_change_password"))
            return d
    except Exception:
        logger.exception("auth_service: failed to query user %r", username)
        return None


def refresh_user_role(user_id: int) -> Optional[dict]:
    """
    Re-query role + must_change_password for an already-authenticated user.

    Returns dict with keys: id, role, must_change_password — or None if the
    user no longer exists or the DB is unreachable.
    """
    from flask import current_app
    from app import db
    from sqlalchemy import text

    schema = _schema(current_app)
    if not schema:
        return None

    safe = schema.replace('"', '""')
    try:
        sql = text(f"""
            SELECT id, role, must_change_password
            FROM "{safe}".users
            WHERE id = :user_id
            LIMIT 1
        """)
        with db.engine.connect() as conn:
            row = conn.execute(sql, {"user_id": user_id}).mappings().first()
            if row is None:
                return None
            d = dict(row)
            d["must_change_password"] = bool(d.get("must_change_password"))
            return d
    except Exception:
        logger.exception("auth_service: failed to refresh role for user_id=%d", user_id)
        return None


def dummy_password_check() -> None:
    """
    Run a bcrypt/scrypt check against a dummy hash.

    Call this when a username is not found so the response time matches a
    failed password check and attackers cannot enumerate valid usernames.
    """
    from werkzeug.security import check_password_hash
    check_password_hash(_DUMMY_HASH, "not_a_real_password_probe")
