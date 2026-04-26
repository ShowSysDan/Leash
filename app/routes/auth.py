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

# Endpoints that are always public (no login required)
_PUBLIC_ENDPOINTS = frozenset({"auth.login", "auth.logout", "static"})

_ALLOWED_ROLES = frozenset({"admin", "staff"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _auth_enabled() -> bool:
    return bool(current_app.config.get("AUTH_DB_SCHEMA", "").strip())


def _is_public_endpoint() -> bool:
    ep = request.endpoint or ""
    if ep in _PUBLIC_ENDPOINTS:
        return True
    # External API has its own key-based auth — skip session check
    if ep.startswith("v1."):
        return True
    return False


def _ua_hash() -> str:
    ua = request.headers.get("User-Agent", "")
    return hashlib.sha256(ua.encode()).hexdigest()[:16]


def _populate_session(user: dict) -> None:
    """Write all session keys after a successful login."""
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
    """Re-query the user's role from the DB; force logout if gone or demoted."""
    user_id = session.get("user_id")
    if not user_id:
        session.clear()
        return

    info = refresh_user_role(int(user_id))
    if info is None or info.get("role") not in _ALLOWED_ROLES:
        # User deleted or no longer authorised — kill the session
        session.clear()
        return

    session["role"] = info["role"]
    session["must_change_password"] = bool(info.get("must_change_password"))
    session["last_role_refresh"] = datetime.utcnow().isoformat()


# ---------------------------------------------------------------------------
# Decorators (also used by other blueprints)
# ---------------------------------------------------------------------------

def login_required(f):
    """Redirect to /login if the user is not authenticated."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if _auth_enabled() and not session.get("logged_in"):
            if request.is_json:
                return jsonify({"error": "Authentication required"}), 401
            return redirect(url_for("auth.login", next=request.path))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    """Return 403 unless the authenticated user has the 'admin' role."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if _auth_enabled():
            if not session.get("logged_in"):
                if request.is_json:
                    return jsonify({"error": "Authentication required"}), 401
                return redirect(url_for("auth.login", next=request.path))
            if session.get("role") != "admin":
                if request.is_json:
                    return jsonify({"error": "Admin access required"}), 403
                flash("You need admin access to do that.", "danger")
                return redirect(url_for("main.index"))
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# App-wide before_request hooks (registered by init_auth)
# ---------------------------------------------------------------------------

def _register_before_request(app) -> None:

    @app.before_request
    def _check_login():
        """Enforce authentication on every non-public request."""
        if not _auth_enabled() or _is_public_endpoint():
            return None

        if not session.get("logged_in"):
            if request.is_json:
                return jsonify({"error": "Authentication required"}), 401
            return redirect(url_for("auth.login", next=request.path))

        # Role refresh every 5 minutes
        last_str = session.get("last_role_refresh")
        if last_str:
            try:
                last_dt = datetime.fromisoformat(last_str)
                if datetime.utcnow() - last_dt > timedelta(minutes=5):
                    _do_role_refresh()
                    if not session.get("logged_in"):
                        if request.is_json:
                            return jsonify({"error": "Session expired"}), 401
                        return redirect(url_for("auth.login"))
            except (ValueError, TypeError):
                pass

        # must_change_password → nudge toward external auth app
        if session.get("must_change_password"):
            forgot_url = current_app.config.get("AUTH_FORGOT_PASSWORD_URL", "")
            if forgot_url:
                flash(
                    f'Your password must be changed. '
                    f'<a href="{forgot_url}" target="_blank" class="alert-link">'
                    f'Change it here</a>.',
                    "warning",
                )

        # Expose auth info to templates via g
        g.current_user = {
            "id": session.get("user_id"),
            "username": session.get("username"),
            "display_name": session.get("display_name"),
            "role": session.get("role"),
        }

    @app.before_request
    def _csrf_check():
        """CSRF protection for HTML form POSTs (login form only)."""
        if request.method != "POST":
            return None
        ep = request.endpoint or ""
        # Only validate the CSRF token on the login form (all API endpoints
        # use Content-Type: application/json which browsers can't forge
        # cross-origin without a CORS preflight — so same-origin is guaranteed).
        if ep != "auth.login":
            return None
        token = request.form.get("csrf_token", "")
        expected = session.get("_csrf_token", "")
        if not expected or not secrets.compare_digest(token, expected):
            flash("Invalid form token — please try again.", "danger")
            return redirect(url_for("auth.login"))
        return None

    @app.context_processor
    def _inject_auth():
        """Make auth info available in every template."""
        return {
            "current_user": g.get("current_user") or {},
            "auth_enabled": _auth_enabled(),
            "auth_forgot_url": current_app.config.get("AUTH_FORGOT_PASSWORD_URL", ""),
        }


def init_auth(app) -> None:
    """Call from create_app() after all blueprints are registered."""
    _register_before_request(app)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@auth_bp.route("/login", methods=["GET"])
def login():
    if session.get("logged_in"):
        return redirect(url_for("main.index"))
    # Ensure a CSRF token exists for the form
    if "_csrf_token" not in session:
        session["_csrf_token"] = secrets.token_hex(32)
    return render_template("login.html", next=request.args.get("next", ""))


@auth_bp.route("/login", methods=["POST"])
@limiter.limit("15 per minute")
def login_post():
    # Rate limiting is applied via the limiter in __init__.py using a
    # decorator on this view (registered after the limiter is initialised).
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    next_url = request.form.get("next", "").strip()

    if not username or not password:
        flash("Username and password are required.", "danger")
        return render_template("login.html", next=next_url), 400

    user = get_user_by_username(username)

    if user is None:
        # Run dummy check to equalise timing
        dummy_password_check()
        logger.warning("auth: login attempt for unknown user %r from %s", username, request.remote_addr)
        flash("Invalid username or password.", "danger")
        return render_template("login.html", next=next_url), 401

    if not check_password_hash(user["password_hash"], password):
        logger.warning("auth: bad password for user %r from %s", username, request.remote_addr)
        flash("Invalid username or password.", "danger")
        return render_template("login.html", next=next_url), 401

    role = user.get("role", "")
    if role not in _ALLOWED_ROLES:
        logger.warning(
            "auth: login denied for user %r (role=%r) — not staff or admin", username, role
        )
        flash("Your account does not have access to Leash.", "danger")
        return render_template("login.html", next=next_url), 403

    _populate_session(user)
    logger.info("auth: user %r (role=%r) logged in from %s", username, role, request.remote_addr)

    # Safe redirect: only allow relative paths
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
