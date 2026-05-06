"""
Authentication blueprint — login / logout and app-wide before_request hooks.

Authentication is only enforced when AUTH_DB_SCHEMA is set in config.
Leave it empty (the default) to run without auth (dev / SQLite).

Protected: every route except /login, /logout, static files, and the
external API at /api/v1/* (which has its own API-key auth).

Rate limiting: 15 POST attempts / minute per IP on /login.
"""
import hashlib
import logging
import secrets
from datetime import datetime, timedelta
from functools import wraps

from flask import (
    Blueprint,
    current_app,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash

from app.extensions import limiter
from app.services.auth_service import (
    dummy_password_check,
    get_user_by_username,
    refresh_user_role,
)

auth_bp = Blueprint("auth", __name__)
logger = logging.getLogger(__name__)

_PUBLIC_ENDPOINTS = frozenset({"auth.login", "auth.login_post", "auth.logout", "static"})
_ALLOWED_ROLES = frozenset({"admin", "staff"})

# State-changing methods that must carry a non-simple Content-Type so the
# browser is forced into a CORS preflight (which we don't allow) before the
# request can be sent cross-origin. Blocks form-submission CSRF against the
# session-authenticated /api/* endpoints.
_STATE_CHANGING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_SAFE_API_CONTENT_TYPES = ("application/json",)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _auth_enabled() -> bool:
    return bool(current_app.config.get("AUTH_DB_SCHEMA", "").strip())


def _is_public_endpoint() -> bool:
    ep = request.endpoint or ""
    # External API has its own key-based auth
    return ep in _PUBLIC_ENDPOINTS or ep.startswith("v1.")


def _ua_hash() -> str:
    ua = request.headers.get("User-Agent", "")
    return hashlib.sha256(ua.encode()).hexdigest()[:16]


def _redirect_to_login():
    return redirect(url_for("auth.login", next=request.path))


def _populate_session(user: dict) -> None:
    session.clear()  # session fixation protection
    session.permanent = True
    session["logged_in"] = True
    session["user_id"] = user["id"]
    session["username"] = user["username"]
    session["role"] = user["role"]
    session["display_name"] = user.get("display_name") or user["username"]
    session["must_change_password"] = bool(user.get("must_change_password"))
    session["login_time"] = datetime.utcnow().isoformat()
    session["last_role_refresh"] = datetime.utcnow().isoformat()
    session["ip"] = request.remote_addr
    session["user_agent_hash"] = _ua_hash()
    session["_csrf_token"] = secrets.token_hex(32)


def _do_role_refresh() -> None:
    user_id = session.get("user_id")
    if not user_id:
        session.clear()
        return

    info = refresh_user_role(int(user_id))
    if info is None or info.get("role") not in _ALLOWED_ROLES:
        session.clear()
        return

    session["role"] = info["role"]
    session["must_change_password"] = bool(info.get("must_change_password"))
    session["last_role_refresh"] = datetime.utcnow().isoformat()


# ---------------------------------------------------------------------------
# Decorators (also exported via _helpers.py)
# ---------------------------------------------------------------------------

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if _auth_enabled() and not session.get("logged_in"):
            if request.is_json:
                return jsonify({"error": "Authentication required"}), 401
            return _redirect_to_login()
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    """Require admin role; also enforces login via login_required."""
    @wraps(f)
    def _admin_check(*args, **kwargs):
        if _auth_enabled() and session.get("role") != "admin":
            if request.is_json:
                return jsonify({"error": "Admin access required"}), 403
            flash("You need admin access to do that.", "danger")
            return redirect(url_for("main.index"))
        return f(*args, **kwargs)
    return login_required(_admin_check)


# ---------------------------------------------------------------------------
# App-wide before_request hooks (registered by init_auth)
# ---------------------------------------------------------------------------

def _register_before_request(app) -> None:

    @app.before_request
    def _check_login():
        if not _auth_enabled() or _is_public_endpoint():
            return None

        if not session.get("logged_in"):
            if request.is_json:
                return jsonify({"error": "Authentication required"}), 401
            return _redirect_to_login()

        # Re-query role every 5 minutes; clear session if user gone or demoted
        last_str = session.get("last_role_refresh")
        if last_str:
            try:
                if datetime.utcnow() - datetime.fromisoformat(last_str) > timedelta(minutes=5):
                    _do_role_refresh()
                    if not session.get("logged_in"):
                        if request.is_json:
                            return jsonify({"error": "Session expired"}), 401
                        return redirect(url_for("auth.login"))
            except (ValueError, TypeError):
                pass

        g.current_user = {
            "id": session.get("user_id"),
            "username": session.get("username"),
            "display_name": session.get("display_name"),
            "role": session.get("role"),
            "must_change_password": session.get("must_change_password", False),
        }

    @app.before_request
    def _csrf_check():
        # 1) Login form: explicit double-submit token check.
        if request.method == "POST" and (request.endpoint or "") == "auth.login_post":
            token = request.form.get("csrf_token", "")
            expected = session.get("_csrf_token", "")
            if not expected or not secrets.compare_digest(token, expected):
                flash("Invalid form token — please try again.", "danger")
                return redirect(url_for("auth.login"))
            return None

        # 2) Session-authenticated /api/* endpoints: refuse state-changing
        # requests that don't declare Content-Type: application/json. Simple
        # cross-origin form submissions can only set application/x-www-form-
        # urlencoded, multipart/form-data, or text/plain, so requiring JSON
        # forces a CORS preflight (which we never answer) before the browser
        # will dispatch a hostile request.  /api/v1/* has its own API-key
        # auth and no session cookie, so it is exempt.
        if request.method not in _STATE_CHANGING_METHODS:
            return None
        path = request.path or ""
        if not path.startswith("/api/") or path.startswith("/api/v1/"):
            return None
        # No body at all → safe for CSRF since browsers always send a
        # Content-Type when there is one. Skip the check so endpoints like
        # bodyless DELETE keep working from in-app fetch().
        ctype = (request.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if not ctype and request.content_length in (None, 0):
            return None
        if not any(ctype == ok for ok in _SAFE_API_CONTENT_TYPES):
            return jsonify({
                "error": "Refusing state-changing request: Content-Type must be application/json",
            }), 415
        return None

    @app.context_processor
    def _inject_auth():
        return {
            "current_user": g.get("current_user") or {},
            "auth_enabled": _auth_enabled(),
            "auth_forgot_url": current_app.config.get("AUTH_FORGOT_PASSWORD_URL", ""),
        }


def init_auth(app) -> None:
    _register_before_request(app)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@auth_bp.route("/login", methods=["GET"])
def login():
    if session.get("logged_in"):
        return redirect(url_for("main.index"))
    if "_csrf_token" not in session:
        session["_csrf_token"] = secrets.token_hex(32)
    return render_template("login.html", next=request.args.get("next", ""))


@auth_bp.route("/login", methods=["POST"])
@limiter.limit("15 per minute")
def login_post():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    next_url = request.form.get("next", "").strip()

    def fail(msg: str, code: int = 401):
        flash(msg, "danger")
        return render_template("login.html", next=next_url), code

    if not username or not password:
        return fail("Username and password are required.", 400)

    user = get_user_by_username(username)

    if user is None:
        dummy_password_check()
        logger.warning("auth: login attempt for unknown user %r from %s", username, request.remote_addr)
        return fail("Invalid username or password.")

    if not check_password_hash(user["password_hash"], password):
        logger.warning("auth: bad password for user %r from %s", username, request.remote_addr)
        return fail("Invalid username or password.")

    role = user.get("role", "")
    if role not in _ALLOWED_ROLES:
        logger.warning("auth: login denied for user %r (role=%r) — not staff or admin", username, role)
        return fail("Your account does not have access to Leash.", 403)

    _populate_session(user)
    logger.info("auth: user %r (role=%r) logged in from %s", username, role, request.remote_addr)

    if next_url and next_url.startswith("/") and not next_url.startswith("//"):
        return redirect(next_url)
    return redirect(url_for("main.index"))


@auth_bp.route("/logout")
def logout():
    username = session.get("username", "unknown")
    session.clear()
    logger.info("auth: user %r logged out", username)
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))
