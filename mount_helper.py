"""mount_helper.py

Helpers to mount block devices and copy files to staging.

Provided functions/classes:
- mount_block_device(device, mount_base, readonly=True) -> mount_path
- unmount(mount_path)
- copy_to_staging(mount_path, staging_dir, use_rsync=True)
- MountedDevice context manager to auto-unmount
"""
from __future__ import annotations

import logging
import os
import subprocess
import shutil
from contextlib import contextmanager
from typing import Optional

logger = logging.getLogger(__name__)


def ensure_dir(path: str, mode: int = 0o755) -> None:
    os.makedirs(path, exist_ok=True)
    try:
        os.chmod(path, mode)
    except Exception:
        pass


def mount_block_device(device: str, mount_base: str, readonly: bool = True, label: Optional[str] = None) -> str:
    """Mount a block device under mount_base and return the mount path.

    device: /dev/sda1
    mount_base: directory where mounts will be created (e.g. /mnt/cam)
    readonly: mount read-only if True
    label: optional suffix for the mount directory; defaults to device name
    """
    if not os.path.exists(device):
        raise FileNotFoundError(f"Device {device} does not exist")
    ensure_dir(mount_base)
    name = label or os.path.basename(device)
    mount_path = os.path.join(mount_base, name)
    ensure_dir(mount_path)

    opts = 'ro' if readonly else 'rw'
    cmd = ['mount', '-o', opts, device, mount_path]
    logger.info('Mounting %s -> %s (opts=%s)', device, mount_path, opts)
    subprocess.run(cmd, check=True)
    return mount_path


def unmount(mount_path: str) -> None:
    if os.path.ismount(mount_path):
        logger.info('Unmounting %s', mount_path)
        subprocess.run(['umount', mount_path], check=True)
    else:
        logger.debug('%s is not mounted', mount_path)


def copy_to_staging(mount_path: str, staging_dir: str, use_rsync: bool = True) -> None:
    """Copy files from mounted camera filesystem to staging_dir.

    If `use_rsync` and rsync exists, prefer rsync for speed and resilience.
    Does not delete source files by default.
    """
    ensure_dir(staging_dir)

    # safe trailing slash semantics
    src = os.path.join(mount_path, '')
    dst = os.path.join(staging_dir, '')

    rsync_path = shutil.which('rsync')
    if use_rsync and rsync_path:
        cmd = [rsync_path, '-a', '--checksum', '--partial', src, dst]
        logger.info('Running rsync: %s', ' '.join(cmd))
        subprocess.run(cmd, check=True)
    else:
        # fallback to shutil copy
        logger.info('Rsync not available, using shutil.copytree for %s -> %s', mount_path, staging_dir)
        # copy tree contents into staging_dir
        for entry in os.listdir(mount_path):
            s = os.path.join(mount_path, entry)
            d = os.path.join(staging_dir, entry)
            if os.path.isdir(s):
                shutil.copytree(s, d, dirs_exist_ok=True)
            else:
                shutil.copy2(s, d)


@contextmanager
def MountedDevice(device: str, mount_base: str, readonly: bool = True, label: Optional[str] = None):
    mount_path = None
    try:
        mount_path = mount_block_device(device, mount_base, readonly=readonly, label=label)
        yield mount_path
    finally:
        if mount_path:
            try:
                unmount(mount_path)
            except Exception:
                logger.exception('Failed to unmount %s', mount_path)


if __name__ == '__main__':
    import argparse
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser()
    ap.add_argument('device')
    ap.add_argument('--mount-base', default='/mnt/cam')
    ap.add_argument('--staging', default='/tmp/cam-staging')
    args = ap.parse_args()
    with MountedDevice(args.device, args.mount_base) as mnt:
        print('Mounted at', mnt)
        copy_to_staging(mnt, args.staging)
        print('Copied to', args.staging)
