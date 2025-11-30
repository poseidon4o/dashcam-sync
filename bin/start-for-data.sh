#!/usr/bin/env bash
set -x
SYS_PATH="/sys/bus/usb/devices/1-1/authorized"
command -v uhubctl >/dev/null 2>&1 || { echo "uhubctl not found in PATH" >&2; exit 2; }
if [ ! -e "$SYS_PATH" ]; then
	echo "sysfs path $SYS_PATH does not exist" >&2
	exit 3
fi
if [ ! -w "$SYS_PATH" ]; then
	echo "sysfs path $SYS_PATH not writable (need root)" >&2
	exit 4
fi
echo 1 > "$SYS_PATH"
uhubctl -l 1-1 -p 1 -a on
