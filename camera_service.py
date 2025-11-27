#!/usr/bin/env python3
"""
camera_service.py (dry-run prototype)

- Reads config YAML (if PyYAML available) or falls back to defaults
- Polls `battery-info.sh` for JSON output
- Checks reachability of configured host
- Logs actions; in `--dry-run` mode it will not call control scripts
- Supports `--once` for a single iteration (useful for testing)

Run example (dry-run, single loop):

  python3 camera_service.py --config config.example.yaml --dry-run --once

"""
import argparse
import json
import logging
import os
import signal
import socket
import subprocess
import sys
import time

STOP = False


def handle_sigterm(signum, frame):
    global STOP
    logging.info("Received signal %s, stopping", signum)
    STOP = True


signal.signal(signal.SIGINT, handle_sigterm)
signal.signal(signal.SIGTERM, handle_sigterm)


DEFAULT_CONFIG = {
    "upload": {"method": "rsync", "host": "192.168.1.203", "port": 22},
    "camera": {"hub_location": "1-1", "port_number": 1, "mount_base": "/mnt/cam"},
    "paths": {"local_staging": "/var/lib/camera_service/staging", "log_path": "/var/log/camera_service.log"},
    "thresholds": {"disconnect_percent": 50, "shutdown_percent": 25},
    "poll": {"interval_seconds": 30, "mount_timeout_seconds": 20, "upload_retries": 3},
}


def load_config(path):
    if not path:
        return DEFAULT_CONFIG
    try:
        import yaml
        with open(path, "r") as f:
            cfg = yaml.safe_load(f)
            if not cfg:
                return DEFAULT_CONFIG
            return {**DEFAULT_CONFIG, **cfg}
    except Exception as e:
        logging.warning("Could not load YAML (PyYAML missing or parse error): %s", e)
        # try JSON as fallback
        try:
            with open(path, "r") as f:
                cfg = json.load(f)
                return {**DEFAULT_CONFIG, **cfg}
        except Exception:
            logging.warning("Falling back to defaults")
            return DEFAULT_CONFIG


def get_battery_info(script_path="./battery-info.sh"):
    try:
        proc = subprocess.run([script_path], capture_output=True, text=True, check=True)
        out = proc.stdout.strip()
        # battery-info.sh emits JSON
        data = json.loads(out)
        return data
    except subprocess.CalledProcessError as e:
        logging.error("battery-info.sh failed: %s", e)
    except json.JSONDecodeError as e:
        logging.error("battery-info.sh output not JSON: %s", e)
    except FileNotFoundError:
        logging.error("battery-info.sh not found at %s", script_path)
    return None


def is_host_reachable(host, port=22, timeout=3):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def maybe_run(cmd, dry_run=False):
    logging.info("Would run: %s", " ".join(cmd) if isinstance(cmd, (list, tuple)) else cmd)
    if dry_run:
        return 0
    return subprocess.call(cmd)


def main():
    parser = argparse.ArgumentParser(description="Dashcam manager (prototype)")
    parser.add_argument("--config", default="/home/pi/camera-scripts/config.example.yaml")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--once", action="store_true", help="Run one iteration and exit")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.info("Starting camera_service (dry_run=%s)", args.dry_run)

    cfg = load_config(args.config)
    host = cfg.get("upload", {}).get("host", DEFAULT_CONFIG["upload"]["host"])
    port = cfg.get("upload", {}).get("port", DEFAULT_CONFIG["upload"]["port"])
    interval = cfg.get("poll", {}).get("interval_seconds", 30)
    disconnect_pct = cfg.get("thresholds", {}).get("disconnect_percent", 50)
    shutdown_pct = cfg.get("thresholds", {}).get("shutdown_percent", 25)

    # Ensure control scripts exist
    base_dir = os.path.dirname(os.path.abspath(__file__))
    start_data = os.path.join(base_dir, "start-for-data.sh")
    start_rec = os.path.join(base_dir, "start-for-recording.sh")
    stop_all = os.path.join(base_dir, "stop-all-ports.sh")

    logging.info("Control scripts: data=%s rec=%s stop=%s", start_data, start_rec, stop_all)

    while not STOP:
        logging.info("Beginning poll iteration")
        info = get_battery_info(os.path.join(base_dir, "battery-info.sh"))
        if info is None:
            logging.warning("No battery info available; skipping iteration")
        else:
            pct = info.get("battery_percent")
            logging.info("Battery percent: %s", pct)
            if isinstance(pct, (int, float)):
                if pct < shutdown_pct:
                    logging.warning("Battery %.1f%% below shutdown threshold %.1f%% — shutting down (dry_run=%s)", pct, shutdown_pct, args.dry_run)
                    if not args.dry_run:
                        # safe unmounts would go here
                        maybe_run(["/sbin/shutdown", "-h", "now"], dry_run=False)
                    else:
                        logging.info("DRY-RUN: skipping shutdown")
                    break
                if pct < disconnect_pct:
                    logging.info("Battery %.1f%% below disconnect threshold %.1f%% — ensuring camera disconnected", pct, disconnect_pct)
                    maybe_run([stop_all], dry_run=args.dry_run)
                else:
                    # check network
                    reachable = is_host_reachable(host, port)
                    logging.info("Host %s reachable=%s", host, reachable)
                    if reachable:
                        logging.info("Host reachable and battery sufficient — performing upload flow (dry_run=%s)", args.dry_run)
                        # Enable data
                        maybe_run([start_data], dry_run=args.dry_run)
                        # Wait a short time for device to enumerate
                        logging.info("Waiting for device enumeration (sleep 3s)")
                        time.sleep(3)
                        # Device detection, mount and copy would go here
                        logging.info("(Prototype) Would detect device, mount it, copy to staging, then disable data")
                        maybe_run([stop_all], dry_run=args.dry_run)
                        # Call uploader here
                        logging.info("(Prototype) Would run uploader to %s", host)
                    else:
                        logging.info("Network not available — ensure camera remains in recording mode")
                        maybe_run([start_rec], dry_run=args.dry_run)

        if args.once:
            logging.info("--once specified, exiting after single iteration")
            break
        # sleep until next poll or until STOP
        slept = 0
        while slept < interval and not STOP:
            time.sleep(1)
            slept += 1

    logging.info("camera_service exiting")


if __name__ == "__main__":
    main()
