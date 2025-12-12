#!/usr/bin/env bash
set -euo pipefail

# Ensure running as root (re-exec via sudo if needed)
if [ "${EUID:-$(id -u)}" -ne 0 ]; then
  exec sudo bash "$0" "$@"
fi

# Copy the service file from the current working directory to systemd
SERVICE_SRC="$PWD/dashcam-sync.service"
SERVICE_DST="/etc/systemd/system/dashcam-sync.service"

if [ ! -f "$SERVICE_SRC" ]; then
  echo "Service file not found in current working directory: $SERVICE_SRC" >&2
  exit 1
fi

cp -f "$SERVICE_SRC" "$SERVICE_DST"
chmod 644 "$SERVICE_DST"

mkdir -p /var/log/camera
touch /var/log/camera/service.log
chmod 644 /var/log/camera/service.log

systemctl daemon-reload
systemctl enable --now dashcam-sync.service

echo "dashcam-sync.service installed and started"
