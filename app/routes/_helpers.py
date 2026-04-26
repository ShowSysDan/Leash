"""
Shared helpers for Leash route blueprints.

Deduplicates _err() (was copied into 5 blueprints) and provides input
validators that are used at API boundaries.

Auth decorators (login_required, admin_required) are defined in
app.routes.auth and re-exported here for convenience.
"""
import re

from flask import jsonify

# ── Response helpers ──────────────────────────────────────────────────────

def err(msg: str, code: int = 400):
    """JSON error response. Single source of truth replacing 5 duplicated _err()s."""
    return jsonify({"error": msg}), code


# ── Validators ────────────────────────────────────────────────────────────

_HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_TIME_RE = re.compile(r"^\d{2}:\d{2}$")

# DB column lengths for common fields (matches app/models.py)
MAX_NAME = 100
MAX_DESCRIPTION = 255
MAX_LABEL = 100


def valid_hex_color(value: str | None) -> bool:
    """Accept only strict #rgb or #rrggbb — prevents CSS injection in style='...' attributes."""
    return bool(value) and bool(_HEX_COLOR_RE.match(value))


def valid_time_of_day(value: str | None) -> bool:
    if not value or not _TIME_RE.match(value):
        return False
    h, m = int(value[:2]), int(value[3:])
    return 0 <= h <= 23 and 0 <= m <= 59


def valid_octet(value) -> tuple[bool, str]:
    """Return (ok, cleaned_str) for an IPv4 last-octet value (1–254)."""
    if value is None:
        return False, ""
    s = str(value).strip()
    if not s.isdigit():
        return False, s
    n = int(s)
    if n < 1 or n > 254:
        return False, s
    return True, s


def valid_name(value, max_len: int = MAX_NAME) -> tuple[bool, str]:
    """Non-empty trimmed string within length limit. Returns (ok, cleaned)."""
    if value is None:
        return False, ""
    s = str(value).strip()
    if not s or len(s) > max_len:
        return False, s
    return True, s


# ── Auth decorators (thin re-exports from app.routes.auth) ────────────────

def login_required(f):
    from app.routes.auth import login_required as _lr
    return _lr(f)


def admin_required(f):
    from app.routes.auth import admin_required as _ar
    return _ar(f)
