#!/usr/bin/env python3
"""uploader.py

Simple uploader using rsync (over SSH) with retries and exponential backoff.

This file was moved into `src/` as part of repo reorganization.
"""

from __future__ import annotations

import argparse
import logging
import os
import shlex
import shutil
import subprocess
import sys
import time
from typing import Dict, Optional

logger = logging.getLogger(__name__)


def build_rsync_command(source: str, dest: str, *, ssh_key: Optional[str] = None,
                        host: Optional[str] = None, user: Optional[str] = None,
                        port: Optional[int] = None, compress: bool = True) -> list:
    rsync = shutil.which('rsync') or 'rsync'
    cmd = [rsync, '-a', '--checksum', '--partial', '--inplace']
    if compress:
        cmd.append('-z')

    if host and host not in ('localhost', '127.0.0.1'):
        ssh_parts = ['ssh', '-o', 'StrictHostKeyChecking=no', '-o', 'UserKnownHostsFile=/dev/null']
        if ssh_key:
            try:
                if os.path.exists(ssh_key) and os.path.isfile(ssh_key):
                    ssh_parts.extend(['-i', ssh_key])
                else:
                    logger.warning('Configured SSH key %s not found; not passing -i', ssh_key)
            except Exception:
                logger.exception('Error checking SSH key %s; skipping -i', ssh_key)
        if port:
            ssh_parts.extend(['-p', str(port)])
        cmd.extend(['-e', ' '.join(ssh_parts)])
        if user:
            target = f"{user}@{host}:{dest}"
        else:
            target = f"{host}:{dest}"
    else:
        target = dest
    src = os.path.join(source, '')
    return cmd + [src, target]


def upload_dir(staging_dir: str, upload_cfg: Dict, dry_run: bool = True, retries: int = 3,
               backoff_factor: float = 2.0, initial_delay: float = 2.0,
               run_as_user: Optional[str] = None) -> bool:
    if not os.path.isdir(staging_dir):
        logger.error('Staging dir %s does not exist', staging_dir)
        return False

    method = upload_cfg.get('method', 'rsync')
    host = upload_cfg.get('host')
    user = upload_cfg.get('user')
    port = upload_cfg.get('port')
    dest = upload_cfg.get('dest_path')
    ssh_key = upload_cfg.get('ssh_key')

    if method != 'rsync':
        logger.error('Unsupported upload method: %s', method)
        return False
    if not dest:
        logger.error('No destination path configured')
        return False

    cmd = build_rsync_command(staging_dir, dest, ssh_key=ssh_key, host=host, user=user, port=port)
    logger.info('Rsync command: %s', ' '.join(shlex.quote(c) for c in cmd))

    if run_as_user is None and os.geteuid() == 0:
        run_as_user = os.environ.get('SUDO_USER')

    final_cmd = cmd
    if run_as_user:
        final_cmd = ['sudo', '-u', run_as_user, '--'] + cmd
        logger.info('Will run rsync as user: %s', run_as_user)

    attempt = 0
    delay = initial_delay
    while attempt <= retries:
        attempt += 1
        if dry_run:
            logger.info('Dry-run enabled: would run rsync (attempt %d)', attempt)
            return True
        try:
            logger.info('Running rsync (attempt %d)', attempt)
            subprocess.run(final_cmd, check=True)
            logger.info('Rsync completed successfully')
            return True
        except subprocess.CalledProcessError as e:
            logger.warning('Rsync failed (attempt %d/%d): %s', attempt, retries, e)
            if attempt > retries:
                logger.error('Exceeded retry limit')
                return False
            logger.info('Backing off for %.1f seconds before retry', delay)
            time.sleep(delay)
            delay *= backoff_factor
        except Exception as e:
            logger.exception('Unexpected error during rsync: %s', e)
            return False
    return False


def main(argv=None):
    parser = argparse.ArgumentParser(description='Uploader using rsync (with retries)')
    parser.add_argument('--staging', required=True, help='Local staging directory to upload')
    parser.add_argument('--host', help='Remote host (omit or localhost for local copy)')
    parser.add_argument('--user', help='Remote user for SSH')
    parser.add_argument('--dest', required=True, help='Destination path on remote or local system')
    parser.add_argument('--port', type=int, help='SSH port (optional)')
    parser.add_argument('--ssh-key', help='SSH private key path (optional)')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--retries', type=int, default=3)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    cfg = {
        'method': 'rsync',
        'host': args.host,
        'user': args.user,
        'port': args.port,
        'dest_path': args.dest,
        'ssh_key': args.ssh_key,
    }
    ok = upload_dir(args.staging, cfg, dry_run=args.dry_run, retries=args.retries)
    if ok:
        logger.info('Upload finished successfully')
        return 0
    else:
        logger.error('Upload failed')
        return 2


if __name__ == '__main__':
    sys.exit(main())
