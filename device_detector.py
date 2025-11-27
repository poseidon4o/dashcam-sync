"""device_detector.py

Helpers to detect a camera USB device and map it to a block device path.

Functions provided:
- find_usb_device_by_vid_pid(vid, pid) -> sysfs path or None
- find_block_device_for_usb_device(sysfs_path) -> /dev/sdXN or None
- wait_for_block_device(vid=None, pid=None, hub=None, timeout=20, poll_interval=0.5) -> device_path or None
- detect_camera_block_device(config, timeout) -> device path or None

This module prefers block-device (mass storage) detection. MTP support is left to future work.
"""

from __future__ import annotations

import glob
import os
import time
from typing import Optional
import logging

logger = logging.getLogger(__name__)


def read_sysfs_file(path: str) -> Optional[str]:
    try:
        with open(path, "r") as f:
            return f.read().strip()
    except Exception:
        return None


def find_usb_device_by_vid_pid(vid: str, pid: str) -> Optional[str]:
    """Return sysfs device path for the first USB device matching given vendor/product ids.

    vid/pid should be lower-case hex strings, e.g. '1d6b'
    """
    for dev in glob.glob('/sys/bus/usb/devices/*'):
        idv = read_sysfs_file(os.path.join(dev, 'idVendor'))
        idp = read_sysfs_file(os.path.join(dev, 'idProduct'))
        if not idv or not idp:
            continue
        if idv.lower() == vid.lower() and idp.lower() == pid.lower():
            logger.debug("Matched usb device %s for %s:%s", dev, vid, pid)
            return dev
    return None


def find_usb_device_by_hub(hub_location: str) -> Optional[str]:
    """Return sysfs path for device matching hub_location like '1-1' or '1-1.2'."""
    candidate = os.path.join('/sys/bus/usb/devices', hub_location)
    if os.path.exists(candidate):
        return candidate
    # sometimes the device node may include interface suffixes; try glob
    for dev in glob.glob(f'/sys/bus/usb/devices/{hub_location}*'):
        if os.path.isdir(dev):
            return dev
    return None


def find_block_device_for_usb_device(sysfs_device_path: str) -> Optional[str]:
    """Search under the USB device sysfs directory for block devices (e.g. sda1) and return /dev/<name>.

    Returns first match found.
    """
    # Walk the device tree to find 'block' directories
    for root, dirs, files in os.walk(sysfs_device_path):
        if 'block' in dirs:
            block_dir = os.path.join(root, 'block')
            for entry in os.listdir(block_dir):
                devpath = os.path.join('/dev', entry)
                if os.path.exists(devpath):
                    logger.debug("Found block device %s for usb device %s", devpath, sysfs_device_path)
                    return devpath
    # Another approach: check children like */host*/target*/*/block/*
    # If none found, return None
    return None


def wait_for_block_device(vid: Optional[str] = None,
                          pid: Optional[str] = None,
                          hub: Optional[str] = None,
                          timeout: float = 20.0,
                          poll_interval: float = 0.5) -> Optional[str]:
    """Wait for a block device tied to a USB device to appear.

    Either provide (vid and pid) or hub. Returns device path like /dev/sda1 or None on timeout.
    """
    start = time.time()
    while True:
        if vid and pid:
            sysfs = find_usb_device_by_vid_pid(vid, pid)
        elif hub:
            sysfs = find_usb_device_by_hub(hub)
        else:
            raise ValueError('Either vid/pid or hub must be provided')

        if sysfs:
            dev = find_block_device_for_usb_device(sysfs)
            if dev:
                return dev

        if time.time() - start > timeout:
            logger.debug("Timeout waiting for device (vid=%s pid=%s hub=%s)", vid, pid, hub)
            return None
        time.sleep(poll_interval)


def detect_camera_block_device(cfg: dict, timeout: float = 20.0) -> Optional[str]:
    """High-level detection using config keys.

    cfg may contain:
      - camera.vendor (hex string)
      - camera.product (hex string)
      - camera.hub_location (string '1-1')

    Returns /dev/sdX1 or None.
    """
    camera_cfg = cfg.get('camera', {})
    vid = camera_cfg.get('vendor') or camera_cfg.get('idVendor')
    pid = camera_cfg.get('product') or camera_cfg.get('idProduct')
    hub = camera_cfg.get('hub_location')

    if vid and pid:
        return wait_for_block_device(vid=vid, pid=pid, timeout=timeout)
    if hub:
        return wait_for_block_device(hub=hub, timeout=timeout)
    # If no identification provided, attempt to find ANY removable block device that appeared recently
    # Heuristic: look under /dev/disk/by-id for usb- prefixes
    for root, dirs, files in os.walk('/dev/disk/by-id'):
        for name in dirs + files:
            if name.startswith('usb-'):
                path = os.path.join('/dev/disk/by-id', name)
                try:
                    real = os.path.realpath(path)
                    if os.path.exists(real):
                        logger.debug("Heuristic found device %s -> %s", path, real)
                        return real
                except Exception:
                    continue
    return None


if __name__ == '__main__':
    # quick CLI helper for manual testing
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--vid')
    ap.add_argument('--pid')
    ap.add_argument('--hub')
    ap.add_argument('--timeout', type=float, default=10.0)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    dev = None
    try:
        dev = detect_camera_block_device({'camera': {'vendor': args.vid, 'product': args.pid, 'hub_location': args.hub}}, timeout=args.timeout)
    except Exception as e:
        logger.exception('Error during detection: %s', e)
    print(dev)
