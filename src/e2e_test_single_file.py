#!/usr/bin/env python3
"""e2e_test_single_file.py

End-to-end test that exercises the full hardware + staging + upload flow.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime


def load_config(path: str):
    try:
        import yaml
        with open(path, 'r') as f:
            return yaml.safe_load(f) or {}
    except Exception:
        with open(path, 'r') as f:
            try:
                return json.load(f)
            except Exception:
                return {}


def run_cmd(cmd, check=False):
    logging.info('RUN: %s', ' '.join(cmd) if isinstance(cmd, (list, tuple)) else cmd)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, check=check)
        logging.debug('STDOUT: %s', r.stdout)
        logging.debug('STDERR: %s', r.stderr)
        return r.returncode == 0
    except subprocess.CalledProcessError as e:
        logging.error('Command failed: %s', e)
        logging.debug('stdout: %s', e.stdout)
        logging.debug('stderr: %s', e.stderr)
        return False


def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', default='./config.yaml')
    ap.add_argument('--timeout', type=int, default=120)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--staging', default=None)
    ap.add_argument('--max-files', type=int, default=1, help='Maximum number of files to select and transfer')
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    cfg = load_config(args.config) or {}

    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(script_dir)
    bin_dir = os.path.join(repo_root, 'bin')
    start_data = os.path.join(bin_dir, 'logged-start-for-data.sh')
    stop_all = os.path.join(bin_dir, 'logged-stop-all-ports.sh')

    upload_cfg = cfg.get('upload', {})
    camera_cfg = cfg.get('camera', {})
    paths_cfg = cfg.get('paths', {})

    staging_base = args.staging or paths_cfg.get('local_staging', '/var/lib/camera_service/staging')
    ensure_dir(staging_base)

    if args.dry_run:
        logging.info('DRY-RUN: would enable camera data via %s', start_data)
    else:
        ok = run_cmd([start_data])
        if not ok:
            logging.warning('start-for-data script returned non-zero; continuing anyway')

    sys.path.insert(0, script_dir)
    try:
        import device_detector
        import mount_helper
        import uploader
    except Exception as e:
        logging.exception('Failed to import modules: %s', e)
        return 2

    logging.info('Waiting up to %s seconds for camera block device...', args.timeout)
    dev = None
    if not args.dry_run:
        dev = device_detector.wait_for_camera_device(hub=camera_cfg.get('hub_location'), timeout=args.timeout)
    if not dev:
        logging.error('No camera block device detected; attempting to disable data and exit')
        if not args.dry_run:
            run_cmd([stop_all])
        return 3

    logging.info('Detected device: %s', dev)

    mount_path = None
    mounted_via_context = False
    mount_ctx = None
    try:
        try:
            p = subprocess.run(['findmnt', '-n', '-o', 'TARGET', dev], capture_output=True, text=True)
            cand = p.stdout.strip()
            if cand:
                mount_path = cand
                logging.info('Device already mounted at %s', mount_path)
        except Exception:
            pass

        if not mount_path:
            base_name = os.path.basename(dev)
            dev_dir = '/dev'
            possible = []
            for entry in os.listdir(dev_dir):
                if entry.startswith(base_name) and entry != base_name:
                    possible.append(os.path.join(dev_dir, entry))
            for pdev in possible:
                try:
                    p = subprocess.run(['findmnt', '-n', '-o', 'TARGET', pdev], capture_output=True, text=True)
                    cand = p.stdout.strip()
                    if cand:
                        mount_path = cand
                        logging.info('Found partition mounted at %s', mount_path)
                        break
                except Exception:
                    continue

        if not mount_path:
            part = None
            if possible:
                part = possible[0]
            else:
                part = dev
            logging.info('Attempting to mount %s under %s', part, camera_cfg.get('mount_base', '/mnt/cam'))
            if args.dry_run:
                logging.info('DRY-RUN: would mount %s', part)
            else:
                mount_ctx = mount_helper.MountedDevice(part, camera_cfg.get('mount_base', '/mnt/cam'), readonly=True)
                mount_path = mount_ctx.__enter__()
                mounted_via_context = True

        if not mount_path:
            logging.error('Unable to determine a mount path for device %s', dev)
            run_cmd([stop_all])
            return 4

        copy_subdirs = camera_cfg.get('copy_subdirs') or ['.']
        selected = mount_helper.select_files_to_copy(
            mount_path,
            copy_subdirs,
            max_files=args.max_files,
            max_bytes=None,
            strategy=camera_cfg.get('transfer_select_strategy', 'newest'),
        )
        if not selected:
            logging.error('No files selected for transfer; cleaning up and exiting')
            if mounted_via_context and mount_ctx:
                mount_ctx.__exit__(None, None, None)
            run_cmd([stop_all])
            return 5

        ts = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
        staging_dir = os.path.join(staging_base, f'e2e-{ts}')
        ensure_dir(staging_dir)

        logging.info('Selected file: %s', selected[0])
        if args.dry_run:
            logging.info('DRY-RUN: would copy selected file to %s', staging_dir)
        else:
            mount_helper.copy_to_staging(mount_path, staging_dir, use_rsync=True, files_list=selected)

        staged_entries = []
        for root, dirs, files in os.walk(staging_dir):
            for f in files:
                staged_entries.append(os.path.relpath(os.path.join(root, f), staging_dir))
        logging.info('Staged entries: %s', staged_entries)

        if args.dry_run:
            logging.info('DRY-RUN: would disable camera via %s', stop_all)
        else:
            run_cmd([stop_all])

        if not args.dry_run:
            deadline = time.time() + 30
            while time.time() < deadline:
                if not os.path.exists(dev):
                    logging.info('Device %s removed', dev)
                    break
                time.sleep(0.5)
            else:
                logging.warning('Device still present after wait')

        if mounted_via_context and mount_ctx:
            try:
                mount_ctx.__exit__(None, None, None)
            except Exception:
                logging.exception('Error while unmounting')

        if args.dry_run:
            logging.info('DRY-RUN: would upload staging dir %s to %s', staging_dir, upload_cfg.get('dest_path'))
        else:
            ok = uploader.upload_dir(staging_dir, upload_cfg, dry_run=False, retries=cfg.get('poll', {}).get('upload_retries', 3))
            if ok:
                logging.info('Upload succeeded; removing staged dir %s', staging_dir)
                try:
                    for entry in os.listdir(staging_dir):
                        path = os.path.join(staging_dir, entry)
                        if os.path.isdir(path):
                            import shutil
                            shutil.rmtree(path)
                        else:
                            os.remove(path)
                    os.rmdir(staging_dir)
                except Exception:
                    logging.exception('Failed to clean up staging after upload')
            else:
                logging.error('Upload failed; staged files remain at %s', staging_dir)

    finally:
        try:
            if mounted_via_context and mount_ctx:
                mount_ctx.__exit__(None, None, None)
        except Exception:
            pass

    logging.info('E2E test complete')
    return 0


if __name__ == '__main__':
    sys.exit(main())
