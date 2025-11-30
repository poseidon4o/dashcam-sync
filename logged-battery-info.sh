#!/usr/bin/env bash
set -euo pipefail
LOG_DIR=/var/log/camera
LOG_FILE="$LOG_DIR/logged-battery-info.log"
mkdir -p "$LOG_DIR" 2>/dev/null || true
log() { local lvl="$1"; shift; local msg="$*"; echo "$(date -Is) [$lvl] $msg" >> "$LOG_FILE" 2>/dev/null || true; logger -t camera_service "[$lvl] $msg"; }

ORIG="$(dirname "$0")/battery-info.sh"
if [ ! -x "$ORIG" ]; then
  log ERROR "Original battery-info script $ORIG not executable"
  exec "$ORIG" "$@" || exit $?
fi

# Capture the JSON output from original and print it unchanged, but log summary info
OUT=$("$ORIG" "$@" )
rc=$?
if [ $rc -ne 0 ]; then
  log ERROR "$ORIG failed with exit code $rc"
  exit $rc
fi

# extract battery_percent if present
BPCT=$(echo "$OUT" | awk -F: '/"battery_percent"/ {gsub(/[^0-9\.]/,"",$2); print $2; exit}' | tr -d ' ,"')
log INFO "battery-info output battery_percent=${BPCT:-unknown}"

# print original output to stdout
printf "%s\n" "$OUT"
exit 0
