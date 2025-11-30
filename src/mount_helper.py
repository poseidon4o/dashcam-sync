"""mount_helper.py

Helpers to mount block devices and copy files to staging.

This file was moved into `src/` as part of repo reorganization.
"""
from __future__ import annotations

import logging
import os
import subprocess
import shutil
from contextlib import contextmanager
from typing import Optional, List, Tuple
import tempfile

logger = logging.getLogger(__name__)


def ensure_dir(path: str, mode: int = 0o755) -> None:
    os.makedirs(path, exist_ok=True)
    try:
        os.chmod(path, mode)
    except Exception:
        pass


def mount_block_device(device: str, mount_base: str, readonly: bool = True, label: Optional[str] = None) -> str:
    if not os.path.exists(device):
        raise FileNotFoundError(f"Device {device} does not exist")
    ensure_dir(mount_base)
    name = label or os.path.basename(device)
    mount_path = os.path.join(mount_base, name)
    ensure_dir(mount_path)

    opts = 'ro' if readonly else 'rw'
    cmd = ['mount', '-o', opts, device, mount_path]
    logger.info('Mounting %s -> %s (opts=%s)', device, mount_path, opts)
    try:
        subprocess.run(cmd, check=True)
        return mount_path
    except subprocess.CalledProcessError:
        try:
            p = subprocess.run(['findmnt', '-n', '-o', 'TARGET', device], capture_output=True, text=True)
            cand = p.stdout.strip()
            if cand:
                logger.info('Device %s appears already mounted at %s; using existing mount', device, cand)
                return cand
        except Exception:
            logger.exception('Error while checking for existing mount of %s', device)
        raise


def unmount(mount_path: str) -> None:
    if os.path.ismount(mount_path):
        logger.info('Unmounting %s', mount_path)
        subprocess.run(['umount', mount_path], check=True)
    else:
        logger.debug('%s is not mounted', mount_path)


def copy_to_staging(mount_path: str, staging_dir: str, use_rsync: bool = True, files_list: Optional[List[str]] = None) -> None:
    ensure_dir(staging_dir)
    src = os.path.join(mount_path, '')
    dst = os.path.join(staging_dir, '')
    rsync_path = shutil.which('rsync')
    if files_list:
        logger.info('Copying explicit file list (%d entries) to staging', len(files_list))
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as tf:
            for p in files_list:
                tf.write(p + '\n')
            tmpname = tf.name
        try:
            if use_rsync and rsync_path:
                cmd = [rsync_path, '-a', '--partial', '--files-from', tmpname, src, dst]
                logger.info('Running rsync --files-from: %s', ' '.join(cmd))
                subprocess.run(cmd, check=True)
            else:
                logger.info('Rsync unavailable; copying %d files individually', len(files_list))
                for rel in files_list:
                    s = os.path.join(mount_path, rel)
                    d = os.path.join(staging_dir, rel)
                    ensure_dir(os.path.dirname(d))
                    shutil.copy2(s, d)
        finally:
            try:
                os.remove(tmpname)
            except Exception:
                pass
    else:
        if use_rsync and rsync_path:
            cmd = [rsync_path, '-a', '--checksum', '--partial', src, dst]
            logger.info('Running rsync: %s', ' '.join(cmd))
            subprocess.run(cmd, check=True)
        else:
            logger.info('Rsync not available, using shutil.copytree for %s -> %s', mount_path, staging_dir)
            for entry in os.listdir(mount_path):
                s = os.path.join(mount_path, entry)
                d = os.path.join(staging_dir, entry)
                if os.path.isdir(s):
                    shutil.copytree(s, d, dirs_exist_ok=True)
                else:
                    shutil.copy2(s, d)


def select_files_to_copy(mount_path: str, subdirs: List[str], max_files: Optional[int] = None, max_bytes: Optional[int] = None, strategy: str = 'newest', extensions: Optional[List[str]] = None) -> List[str]:
    candidates: List[Tuple[str, int, float]] = []
    for sd in subdirs:
        base = os.path.join(mount_path, sd)
        if not os.path.exists(base):
            logger.debug('Subdir %s does not exist on mount %s', sd, mount_path)
            continue
        for root, _, files in os.walk(base):
            for fn in files:
                if extensions:
                    if not any(fn.lower().endswith(ext) for ext in extensions):
                        continue
                full = os.path.join(root, fn)
                try:
                    st = os.stat(full)
                except FileNotFoundError:
                    continue
                rel = os.path.relpath(full, mount_path)
                candidates.append((rel, st.st_size, st.st_mtime))

    if not candidates:
        return []

    if strategy == 'newest':
        candidates.sort(key=lambda x: x[2], reverse=True)
    elif strategy == 'oldest':
        candidates.sort(key=lambda x: x[2])
    elif strategy == 'largest':
        candidates.sort(key=lambda x: x[1], reverse=True)
    else:
        logger.warning('Unknown strategy %s, defaulting to newest', strategy)
        candidates.sort(key=lambda x: x[2], reverse=True)

    selected: List[str] = []
    total_bytes = 0
    for rel, size, _ in candidates:
        if max_files is not None and len(selected) >= max_files:
            break
        if max_bytes is not None and (total_bytes + size) > max_bytes:
            continue
        selected.append(rel)
        total_bytes += size

    logger.info('Selected %d files totaling %d bytes (strategy=%s)', len(selected), total_bytes, strategy)
    return selected


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
