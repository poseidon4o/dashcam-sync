#!/usr/bin/env bash
set -euo pipefail

command -v lifepo4wered-cli >/dev/null 2>&1 || { echo "lifepo4wered-cli not found in PATH" >&2; exit 2; }

DATA=$(lifepo4wered-cli get)

get_val() {
  echo "$DATA" | awk -F' = ' -v key="$1" '$1 == key {print $2}'
}

VIN=$(get_val VIN)
VBAT=$(get_val VBAT)
VOUT=$(get_val VOUT)
IOUT=$(get_val IOUT)
PI_RUNNING=$(get_val PI_RUNNING)
WAKE_TIME=$(get_val WAKE_TIME)
AUTO_SHDN_TIME=$(get_val AUTO_SHDN_TIME)

VIN_V=$(printf "%.2f" "$(echo "$VIN / 1000" | bc -l)")
VBAT_V=$(printf "%.2f" "$(echo "$VBAT / 1000" | bc -l)")
VOUT_V=$(printf "%.2f" "$(echo "$VOUT / 1000" | bc -l)")
IOUT_A=$(printf "%.2f" "$(echo "$IOUT / 1000" | bc -l)")
POWER_W=$(printf "%.2f" "$(echo "$VOUT_V * $IOUT_A" | bc -l)")

# Refined LiFePO4 voltage → percentage curve
battery_pct() {
  local v=$1

  if (( $(echo "$v >= 3.65" | bc -l) )); then echo 100
  elif (( $(echo "$v >= 3.60" | bc -l) )); then echo 95
  elif (( $(echo "$v >= 3.55" | bc -l) )); then echo 90
  elif (( $(echo "$v >= 3.50" | bc -l) )); then echo 80
  elif (( $(echo "$v >= 3.45" | bc -l) )); then echo 70
  elif (( $(echo "$v >= 3.40" | bc -l) )); then echo 60
  elif (( $(echo "$v >= 3.35" | bc -l) )); then echo 50
  elif (( $(echo "$v >= 3.30" | bc -l) )); then echo 40
  elif (( $(echo "$v >= 3.25" | bc -l) )); then echo 30
  elif (( $(echo "$v >= 3.20" | bc -l) )); then echo 20
  elif (( $(echo "$v >= 3.10" | bc -l) )); then echo 10
  else echo 5
  fi
}

BATTERY_PERCENT=$(battery_pct "$VBAT_V")

cat <<EOF
{
  "battery_voltage": $VBAT_V,
  "battery_percent": $BATTERY_PERCENT,
  "input_voltage": $VIN_V,
  "output_voltage": $VOUT_V,
  "output_current": $IOUT_A,
  "output_power": $POWER_W,
  "pi_running": $PI_RUNNING,
  "wake_timer_remaining": $WAKE_TIME,
  "auto_shutdown_time": $AUTO_SHDN_TIME
}
EOF
