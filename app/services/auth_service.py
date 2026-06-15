"""
Authentication service — cross-schema Postgres user lookup.

Reads from the shared database's AUTH_DB_SCHEMA users table.
Never writes to that table; all access is read-only.

Access is gated by two per-user flags on the shared users table:
  * is_app_user  — 1 if this account may use the shared app (login gate)
  * is_app_admin — 1 if this account is an administrator of the shared app
Both are INTEGER 0/1 (default 0) and fully independent — an account can
have either, both, or neither. Leash reads them; it never sets them
(the sibling 321Theater app owns writing them).

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
    from flask import current_app
    from app import db
    from sqlalchemy import text

    schema = _schema(current_app)
    if not schema:
        return None

    safe = schema.replace('"', '""')
    try:
        sql = text(f"""
            SELECT id, username, password_hash, is_app_user, is_app_admin, must_change_password
            FROM "{safe}".users
            WHERE username = :username
            LIMIT 1
        """)
        row = db.session.execute(sql, {"username": username}).mappings().first()
        if row is None:
            return None
        d = dict(row)
        d.setdefault("display_name", d["username"])
        d["is_app_user"] = bool(d.get("is_app_user"))
        d["is_app_admin"] = bool(d.get("is_app_admin"))
        d["must_change_password"] = bool(d.get("must_change_password"))
        return d
    except Exception:
        logger.exception("auth_service: failed to query user %r", username)
        return None


def refresh_user_flags(user_id: int) -> Optional[dict]:
    """Re-read a user's access flags.

    Returns {id, is_app_user, is_app_admin, must_change_password} or None if
    the user is gone / DB unreachable. Used by the periodic session re-check so
    a revoked is_app_user / is_app_admin flag takes effect without re-login.
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
            SELECT id, is_app_user, is_app_admin, must_change_password
            FROM "{safe}".users
            WHERE id = :user_id
            LIMIT 1
        """)
        row = db.session.execute(sql, {"user_id": user_id}).mappings().first()
        if row is None:
            return None
        d = dict(row)
        d["is_app_user"] = bool(d.get("is_app_user"))
        d["is_app_admin"] = bool(d.get("is_app_admin"))
        d["must_change_password"] = bool(d.get("must_change_password"))
        return d
    except Exception:
        logger.exception("auth_service: failed to refresh flags for user_id=%d", user_id)
        return None


def dummy_password_check() -> None:
    """
    Run a bcrypt/scrypt check against a dummy hash.

    Call this when a username is not found so the response time matches a
    failed password check and attackers cannot enumerate valid usernames.
    """
    from werkzeug.security import check_password_hash
    check_password_hash(_DUMMY_HASH, "not_a_real_password_probe")
