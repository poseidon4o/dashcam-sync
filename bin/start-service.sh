#!/usr/bin/env bash
set -euo pipefail
# start-service.sh
# Starts and enables the `camera_service.service` via systemd. Will use sudo if not run as root.

SERVICE=camera_service.service
LOG_DIR=/var/log/camera
LOG_FILE="$LOG_DIR/start-service.log"
mkdir -p "$LOG_DIR" 2>/dev/null || true
log() { local lvl="$1"; shift; local msg="$*"; echo "$(date -Is) [$lvl] $msg" >> "$LOG_FILE" 2>/dev/null || true; logger -t camera_service "[$lvl] $msg"; }

log INFO "Starting $SERVICE (enable and start)"
if [ "$(id -u)" -ne 0 ]; then
  log INFO "Using sudo to reload daemon and enable/start service"
  sudo systemctl daemon-reload
  sudo systemctl enable --now "$SERVICE"
  rc=$?
else
  systemctl daemon-reload
  systemctl enable --now "$SERVICE"
  rc=$?
fi
if [ $rc -eq 0 ]; then
  log INFO "$SERVICE enabled and started"
else
  log ERROR "Failed to enable/start $SERVICE (rc=$rc)"
fi
