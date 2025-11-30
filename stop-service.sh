#!/usr/bin/env bash
set -euo pipefail
# stop-service.sh
# Stops and disables the `camera_service.service` via systemd. Will use sudo if not run as root.

SERVICE=camera_service.service

if [ "$(id -u)" -ne 0 ]; then
  echo "Stopping $SERVICE (requires sudo)..."
  exec sudo systemctl stop "$SERVICE"
else
  systemctl stop "$SERVICE"
fi
