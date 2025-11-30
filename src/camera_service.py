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
import shutil

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
    parser.add_argument("--limit-files", type=int, default=None, help="Override config: max number of files to transfer")
    parser.add_argument("--limit-bytes", type=int, default=None, help="Override config: max total bytes to transfer")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.info("Starting camera_service (dry_run=%s)", args.dry_run)

    cfg = load_config(args.config)
    host = cfg.get("upload", {}).get("host", DEFAULT_CONFIG["upload"]["host"])
    port = cfg.get("upload", {}).get("port", DEFAULT_CONFIG["upload"]["port"])
    interval = cfg.get("poll", {}).get("interval_seconds", 30)
    disconnect_pct = cfg.get("thresholds", {}).get("disconnect_percent", 50)
    shutdown_pct = cfg.get("thresholds", {}).get("shutdown_percent", 25)

    # Ensure control scripts exist in the repo sibling `bin` directory
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(script_dir)
    bin_dir = os.path.join(repo_root, 'bin')
    start_data = os.path.join(bin_dir, "logged-start-for-data.sh")
    start_rec = os.path.join(bin_dir, "logged-start-for-recording.sh")
    stop_all = os.path.join(bin_dir, "logged-stop-all-ports.sh")

    logging.info("Control scripts: data=%s rec=%s stop=%s", start_data, start_rec, stop_all)

    while not STOP:
        logging.info("Beginning poll iteration")
        info = get_battery_info(os.path.join(bin_dir, "logged-battery-info.sh"))
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
                        # Enable data (allows camera to enumerate) and wait until device appears
                        maybe_run([start_data], dry_run=args.dry_run)

                        # Attempt to detect camera block device
                        try:
                            import device_detector
                            import mount_helper
                            import uploader
                        except Exception as e:
                            logging.exception('Required modules missing: %s', e)
                            maybe_run([stop_all], dry_run=args.dry_run)
                            continue

                        # allow a longer, more robust detection timeout (camera enumeration may be slow)
                        cfg_timeout = cfg.get('poll', {}).get('mount_timeout_seconds', 20)
                        detect_timeout = max(cfg_timeout, 60)
                        if args.dry_run:
                            dev = None
                        else:
                            dev = device_detector.wait_for_camera_device(hub=cfg.get('camera', {}).get('hub_location'), timeout=detect_timeout)
                        if not dev:
                            logging.warning('No camera block device detected; will disable data and continue')
                            maybe_run([stop_all], dry_run=args.dry_run)
                            continue

                        staging = cfg.get('paths', {}).get('local_staging', '/var/lib/camera_service/staging')
                        ensure_dir_cmd = ['mkdir', '-p', staging]
                        maybe_run(ensure_dir_cmd, dry_run=args.dry_run)

                        # Mount, measure size, enforce 90% storage constraint, copy, then unmount
                        if args.dry_run:
                            logging.info('DRY-RUN: would mount %s, compute sizes, copy to %s, then upload', dev, staging)
                            maybe_run([stop_all], dry_run=True)
                            logging.info('DRY-RUN: would run uploader to %s', host)
                        else:
                            try:
                                # If the device is already automounted, prefer that mountpoint
                                mount_path = None
                                mounted_via_context = False
                                if dev:
                                    try:
                                        existing = subprocess.run(['findmnt', '-n', '-o', 'TARGET', dev], capture_output=True, text=True, check=True)
                                        candidate = existing.stdout.strip()
                                        if candidate:
                                            mount_path = candidate
                                            logging.info('Device %s already mounted at %s; using existing mount', dev, mount_path)
                                    except subprocess.CalledProcessError:
                                        mount_path = None

                                if not mount_path and dev:
                                    # perform our own mount (will be auto-unmounted via context manager)
                                    cm = mount_helper.MountedDevice(dev, cfg.get('camera', {}).get('mount_base', '/mnt/cam'), readonly=True)
                                    mount_ctx = cm
                                    mount_path = mount_ctx.__enter__()
                                    logging.info('Mounted camera at %s', mount_path)
                                    mounted_via_context = True

                                if not mount_path:
                                    logging.warning('No mount path available for device; skipping copy')
                                else:
                                    # compute size of camera contents (bytes)
                                    try:
                                        du = subprocess.run(['du', '-sb', mount_path], capture_output=True, text=True, check=True)
                                        size_bytes = int(du.stdout.split()[0])
                                    except Exception:
                                        logging.exception('Failed to compute size of camera contents; aborting copy')
                                        size_bytes = None

                                    # check available space on staging filesystem
                                    staging_path = staging
                                    os.makedirs(staging_path, exist_ok=True)
                                    import shutil
                                    total, used, free = shutil.disk_usage(staging_path)
                                    max_allowed = int(total * 0.9)
                                    logging.info('Staging fs total=%d used=%d free=%d max_allowed=%d', total, used, free, max_allowed)

                                    if size_bytes is None:
                                        logging.warning('Unknown camera size; skipping copy')
                                    else:
                                        projected_used = used + size_bytes
                                        if projected_used > max_allowed:
                                            logging.error('Not enough staging space: required=%d would_be_used=%d limit=%d; skipping copy', size_bytes, projected_used, max_allowed)
                                        else:
                                            logging.info('Enough space available; copying camera contents (%d bytes) to %s', size_bytes, staging_path)
                                            # Select files to copy according to config limits and strategy
                                            copy_subdirs = cfg.get('camera', {}).get('copy_subdirs') or []
                                            max_files = cfg.get('camera', {}).get('transfer_max_files')
                                            max_bytes = cfg.get('camera', {}).get('transfer_max_bytes')
                                            # CLI overrides
                                            if args.limit_files is not None:
                                                max_files = args.limit_files
                                            if args.limit_bytes is not None:
                                                max_bytes = args.limit_bytes
                                            strategy = cfg.get('camera', {}).get('transfer_select_strategy', 'newest')

                                            # If no explicit subdirs specified, search the whole mount
                                            search_subdirs = copy_subdirs if copy_subdirs else ['.']

                                            try:
                                                selected = mount_helper.select_files_to_copy(mount_path, search_subdirs, max_files=max_files, max_bytes=max_bytes, strategy=strategy)
                                            except Exception:
                                                logging.exception('File selection failed; falling back to full copy')
                                                selected = []

                                            if not selected:
                                                logging.info('No files selected for transfer (limits or missing files); skipping copy')
                                            else:
                                                logging.info('Selected %d files for transfer; copying to %s', len(selected), staging_path)
                                                # Use relative paths from mount root; copy_to_staging will accept files_list
                                                mount_helper.copy_to_staging(mount_path, staging_path, use_rsync=True, files_list=selected)
                                            logging.info('Copy complete; disabling camera data/power to conserve battery')
                            except Exception:
                                logging.exception('Error during mount/copy flow')
                            finally:
                                # if we mounted via context, ensure we unmount
                                try:
                                    if 'mounted_via_context' in locals() and mounted_via_context:
                                        mount_ctx.__exit__(None, None, None)
                                except Exception:
                                    logging.exception('Error unmounting device')

                            # disable data/power to conserve battery (or switch to recording mode as desired)
                            maybe_run([stop_all], dry_run=False)

                            # Wait briefly for the device to disappear from /dev (ensure it's disconnected)
                            try:
                                if dev:
                                    wait_deadline = time.time() + cfg.get('poll', {}).get('disconnect_wait_seconds', 10)
                                    while time.time() < wait_deadline:
                                        if not os.path.exists(dev):
                                            logging.info('Device %s no longer present', dev)
                                            break
                                        time.sleep(0.5)
                                    else:
                                        logging.warning('Device %s still present after disconnect attempt', dev)
                            except Exception:
                                logging.exception('Error while waiting for device to disconnect')

                            # upload staged files
                            ok = uploader.upload_dir(staging, cfg.get('upload', {}), dry_run=False, retries=cfg.get('poll', {}).get('upload_retries', 3))
                            if ok:
                                logging.info('Upload succeeded; removing staged files')
                                try:
                                    # remove staged contents
                                    for entry in os.listdir(staging):
                                        path = os.path.join(staging, entry)
                                        if os.path.isdir(path):
                                            shutil.rmtree(path)
                                        else:
                                            os.remove(path)
                                except Exception:
                                    logging.exception('Failed to clean up staging after upload')
                            else:
                                logging.error('Upload failed; leaving staged files for retry')
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
