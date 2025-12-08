#!/usr/bin/env bash
set -euo pipefail
LOG_DIR=/var/log/camera
LOG_FILE="$LOG_DIR/logged-start-for-data.log"
mkdir -p "$LOG_DIR" 2>/dev/null || true
log() { local lvl="$1"; shift; local msg="$*"; echo "$(date -Is) [$lvl] $msg" >> "$LOG_FILE" 2>/dev/null || true; logger -t camera_service "[$lvl] $msg"; }

ORIG="$(dirname "$0")/start-for-data.sh"
log INFO "Invoking $ORIG"
if [ ! -x "$ORIG" ]; then
  log ERROR "Original script $ORIG not executable"
  exec "$ORIG" || exit $?
fi
if "$ORIG" "$@"; then
  log INFO "$ORIG completed successfully"
  exit 0
else
  rc=$?
  log ERROR "$ORIG failed with exit code $rc"
  exit $rc
fi
