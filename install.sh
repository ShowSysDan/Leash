#!/usr/bin/env bash
set -euo pipefail

APP_NAME="leash"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$SCRIPT_DIR"
SERVICE_FILE="/etc/systemd/system/${APP_NAME}.service"

# ── helpers ──────────────────────────────────────────────────────────────────

info()  { echo "[INFO]  $*"; }
error() { echo "[ERROR] $*" >&2; exit 1; }

# ── pre-flight ────────────────────────────────────────────────────────────────

[[ "$EUID" -eq 0 ]] || error "Run with sudo: sudo bash install.sh"

# Determine the user who owns the repo (the one who invoked sudo)
RUN_USER="${SUDO_USER:-}"
[[ -n "$RUN_USER" ]] || error "Could not determine the run user. Run via sudo, not as root directly."

id "$RUN_USER" &>/dev/null || error "User '$RUN_USER' does not exist."

[[ -f "${APP_DIR}/run.py" ]]          || error "run.py not found in ${APP_DIR}. Run install.sh from the Leash directory."
[[ -f "${APP_DIR}/venv/bin/gunicorn" ]] || error "venv not found. Run: python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt"
[[ -f "${APP_DIR}/.env" ]]            || error ".env not found. Copy .env.example to .env and configure it."

# ── write service file ────────────────────────────────────────────────────────

info "Writing ${SERVICE_FILE}"

cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Leash NDI Source Control
After=network.target
StartLimitAction=none

[Service]
Type=simple
User=${RUN_USER}
Group=${RUN_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
ExecStart=${APP_DIR}/venv/bin/gunicorn \\
    --workers 1 \\
    --threads 4 \\
    --bind 0.0.0.0:5000 \\
    --timeout 120 \\
    --access-logfile - \\
    --error-logfile - \\
    run:app
Restart=always
RestartSec=10s
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${APP_NAME}
StartLimitIntervalSec=300
StartLimitBurst=10

[Install]
WantedBy=multi-user.target
EOF

# ── enable and start ──────────────────────────────────────────────────────────

info "Reloading systemd"
systemctl daemon-reload

info "Enabling ${APP_NAME}"
systemctl enable "$APP_NAME" --quiet

info "Starting ${APP_NAME}"
systemctl restart "$APP_NAME"

systemctl status "$APP_NAME" --no-pager

info "Done. Logs: sudo journalctl -u ${APP_NAME} -f"
