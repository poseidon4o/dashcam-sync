#!/bin/bash

set -euox pipefail

# ---------- CONFIGURATION ----------
srv_name="rpi_lifepo4"
url_base="http://192.168.1.128:8123/api/states"
token="REDACTED"

DEVICE_IDENTIFIERS="[\"${srv_name}\"]"
DEVICE_NAME="LiFePO4wered Pi UPS"
DEVICE_MANUFACTURER="xOrbit"
DEVICE_MODEL="LiFePO4wered/Pi+"

# ---------- FUNCTIONS ----------

send_to_ha() {
  local sensor_name=$1
  local value=$2
  local friendly_name=$3
  local icon=$4
  local device_class=$5
  local unit=$6
  local state_class=$7

  local url="${url_base}/sensor.${srv_name}_${sensor_name}"
  local device_info="{\"identifiers\":${DEVICE_IDENTIFIERS},\"name\":\"${DEVICE_NAME}\",\"manufacturer\":\"${DEVICE_MANUFACTURER}\",\"model\":\"${DEVICE_MODEL}\"}"

  local payload="{
    \"state\": \"${value}\",
    \"attributes\": {
      \"friendly_name\": \"${friendly_name}\",
      \"icon\": \"${icon}\",
      \"device_class\": \"${device_class}\",
      \"unit_of_measurement\": \"${unit}\",
      \"state_class\": \"${state_class}\"
    },
    \"device\": ${device_info}
  }"

  curl -s -X POST \
    -H "Authorization: Bearer ${token}" \
    -H "Content-type: application/json" \
    --data "${payload}" \
    "${url}" >/dev/null
}

get_val() {
  echo "$DATA" | awk -F' = ' -v key="$1" '$1 == key {print $2}'
}

# ---------- DATA COLLECTION ----------

DATA=$(lifepo4wered-cli get)

VIN=$(get_val VIN)
VBAT=$(get_val VBAT)
VOUT=$(get_val VOUT)
IOUT=$(get_val IOUT)
PI_RUNNING=$(get_val PI_RUNNING)

VIN_V=$(printf "%.2f" "$(echo "$VIN / 1000" | bc -l)")
VBAT_V=$(printf "%.2f" "$(echo "$VBAT / 1000" | bc -l)")
VOUT_V=$(printf "%.2f" "$(echo "$VOUT / 1000" | bc -l)")
IOUT_A=$(printf "%.2f" "$(echo "$IOUT / 1000" | bc -l)")
POWER_W=$(printf "%.2f" "$(echo "$VOUT_V * $IOUT_A" | bc -l)")

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

send_to_ha "battery_voltage" "$VBAT_V" \
  "Battery Voltage" "mdi:battery" "voltage" "V" "measurement"

send_to_ha "battery_percent" "$BATTERY_PERCENT" \
  "Battery Level" "mdi:battery-high" "battery" "%" "measurement"

send_to_ha "input_voltage" "$VIN_V" \
  "Input Voltage" "mdi:power-plug" "voltage" "V" "measurement"

send_to_ha "output_voltage" "$VOUT_V" \
  "Output Voltage" "mdi:chip" "voltage" "V" "measurement"

send_to_ha "output_current" "$IOUT_A" \
  "Output Current" "mdi:current-dc" "current" "A" "measurement"

send_to_ha "output_power" "$POWER_W" \
  "Output Power" "mdi:flash" "power" "W" "measurement"

send_to_ha "pi_running" "$PI_RUNNING" \
  "Pi Running" "mdi:raspberry-pi" "" "" ""
