#!/usr/bin/env python3
"""
connect_test.py

Performs a live connect/disconnect test without copying files:
 - Enables camera data via `start-for-data.sh`
 - Waits for block device via `device_detector.wait_for_camera_device`
 - Prefers automount and lists a few files under configured subdir(s)
 - Disables camera via `stop-all-ports.sh`
 - Waits for device removal

Usage: python3 connect_test.py --config config.yaml
"""
import argparse
import json
import logging
import os
import subprocess
import sys
import time


def load_config(path):
    try:
        import yaml
        with open(path, 'r') as f:
            return yaml.safe_load(f)
    except Exception:
        try:
            with open(path, 'r') as f:
                return json.load(f)
        except Exception:
            return {}


def run(cmd):
    print(f"RUN: {' '.join(cmd)}")
    r = subprocess.run(cmd, capture_output=True, text=True)
    print(r.stdout, r.stderr)
    return r.returncode == 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default='/home/pi/camera-scripts/config.yaml')
    parser.add_argument('--timeout', type=int, default=120, help='Seconds to wait for device enumeration')
    parser.add_argument('--limit-files', type=int, default=None, help='Max number of files to select (overrides config)')
    parser.add_argument('--limit-bytes', type=int, default=None, help='Max bytes to select (overrides config)')
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    base = os.path.dirname(os.path.abspath(__file__))
    start_data = os.path.join(base, 'logged-start-for-data.sh')
    stop_all = os.path.join(base, 'logged-stop-all-ports.sh')

    cfg = load_config(args.config) or {}
    copy_subdirs = cfg.get('camera', {}).get('copy_subdirs') or ['Normal/Front']

    # ensure workspace on PYTHONPATH for local imports
    sys.path.insert(0, base)

    print('Enabling camera data...')
    if not run([start_data]):
        print('Warning: start-for-data.sh returned non-zero')

    # import device detector
    try:
        import device_detector
    except Exception as e:
        print('Failed to import device_detector:', e)
        return 1

    print('Waiting for camera block device...')
    dev = device_detector.wait_for_camera_device(hub=cfg.get('camera', {}).get('hub_location'), timeout=args.timeout)
    if not dev:
        print('No device detected within timeout')
        # disable data to restore state
        run([stop_all])
        return 2

    print('Detected device:', dev)

    # prefer existing automount
    mount_point = None
    try:
        p = subprocess.run(['findmnt', '-n', '-o', 'TARGET', dev], capture_output=True, text=True)
        candidate = p.stdout.strip()
        if candidate:
            mount_point = candidate
            print('Existing mount point:', mount_point)
    except Exception:
        pass

    if not mount_point:
        print('No automount detected; attempting to locate partition under /dev/disk/by-id')
        # try to find any partition device for the block (e.g., /dev/sda1)
        base_name = os.path.basename(dev)
        dev_dir = '/dev'
        possible = []
        for entry in os.listdir(dev_dir):
            if entry.startswith(base_name) and entry != base_name:
                possible.append(os.path.join(dev_dir, entry))
        if possible:
            print('Found partition candidates:', possible)
            # choose first and see if mounted
            for pdev in possible:
                try:
                    p = subprocess.run(['findmnt', '-n', '-o', 'TARGET', pdev], capture_output=True, text=True)
                    cand = p.stdout.strip()
                    if cand:
                        mount_point = cand
                        print('Found mount at', mount_point)
                        break
                except Exception:
                    continue

    if not mount_point:
        print('No mount point detected; the system may not automount. Files may still be on the device partition.')
    else:
        # list some files from configured subdirs or run selection if limits provided
        try:
            import mount_helper
        except Exception as e:
            print('Failed to import mount_helper:', e)
            mount_helper = None

        # If limits provided, run selection and print chosen files
        if args.limit_files is not None or args.limit_bytes is not None:
            max_files = args.limit_files
            max_bytes = args.limit_bytes
            strategy = cfg.get('camera', {}).get('transfer_select_strategy', 'newest')
            search_subdirs = copy_subdirs if copy_subdirs else ['.']
            if mount_helper:
                sel = mount_helper.select_files_to_copy(mount_point, search_subdirs, max_files=max_files, max_bytes=max_bytes, strategy=strategy)
                print(f'Files selected ({len(sel)}):')
                for s in sel[:200]:
                    print(' -', s)
            else:
                print('Cannot perform selection; mount_helper missing')
        else:
            for sub in copy_subdirs:
                path = os.path.join(mount_point, sub)
                print('\nChecking', path)
                if os.path.exists(path):
                    try:
                        entries = sorted(os.listdir(path))[:10]
                        print('Sample entries:')
                        for e in entries:
                            print(' -', e)
                    except Exception as e:
                        print('Failed to list', path, e)
                else:
                    print('Path does not exist on device:', path)

    print('\nDisabling camera data/power...')
    run([stop_all])

    # wait for device removal
    print('Waiting for device to disappear...')
    deadline = time.time() + 30
    while time.time() < deadline:
        if not os.path.exists(dev):
            print('Device removed:', dev)
            break
        time.sleep(0.5)
    else:
        print('Device still present after wait period')

    print('Connect/disconnect test complete')
    return 0


if __name__ == '__main__':
    sys.exit(main())
