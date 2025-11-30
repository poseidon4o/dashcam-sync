**Execution Plan**

This document describes a typical single run of the camera service (connect -> copy -> upload -> disconnect) and maps each step to the concrete file and function or script that executes it. Use this as a reference for debugging and log inspection.

1) Service entrypoint
- File: `camera_service.py`
- Function / symbol: `main()`
- Notes: `systemd` (unit `camera_service.service`) or manual invocation runs `camera_service.py --config /home/pi/camera-scripts/config.yaml`. The `main()` loop polls UPS, checks network reachability, and performs the upload flow when appropriate.

2) Battery check (every poll)
- File: `camera_service.py`
- Function: `get_battery_info(script_path)` (calls a control script)
- Script invoked: `logged-battery-info.sh` (wrapper) -> `battery-info.sh` (original)
- Expected logs: `/var/log/camera/logged-battery-info.log` contains a line like `battery-info output battery_percent=95` and `camera_service` logs the parsed percentage.

3) Network reachability check
- File: `camera_service.py`
- Function: `is_host_reachable(host, port)`
- Notes: If reachable and battery above thresholds, proceed to connect/copy/upload flow.

4) Enable camera USB data (connect)
- File (called from): `camera_service.py` (uses `maybe_run()` to call control script)
- Script invoked: `logged-start-for-data.sh` -> `start-for-data.sh` (writes `/sys/bus/usb/devices/.../authorized` and calls `uhubctl`)
- Expected logs: `/var/log/camera/logged-start-for-data.log` contains `Invoking /home/pi/camera-scripts/start-for-data.sh` and `completed successfully`; syslog entries with `camera_service` tag; console/`connect_test.run.log` shows `uhubctl` output.

5) Wait for device enumeration
- File: `device_detector.py`
- Function: `wait_for_camera_device(hub=..., timeout=...)` or `detect_camera_block_device()`
- Notes: This function detects block device (e.g. `/dev/sda`) by scanning `/dev/disk/by-id` and sysfs; it tolerates slow enumeration and returns a block device path or `None` on timeout.
- Expected logs: `camera_service.py` logs `Detected device: /dev/sda` and wrapper logs show udev/automount activity in `/var/log/syslog` or captured in wrapper output files.

6) Prefer existing automount or mount read-only
- Files: `camera_service.py`, `mount_helper.py`
- Function: `mount_helper.MountedDevice(dev, mount_base, readonly=True)` (context manager) and `mount_helper.copy_to_staging(src, dest, use_rsync=True)`
- Notes: If `/media/pi/<LABEL>` already exists the service uses that mountpoint; otherwise the context manager mounts the device and ensures unmount in `finally`.

7) Compute sizes and enforce staging safety
- File: `camera_service.py` (local logic)
- Action: compute `du -sb` on the mount, compute `shutil.disk_usage(staging)` and enforce `projected_used <= 0.9 * total`.
- Expected logs: `camera_service.py` logs the staging totals and either `Enough space available; copying ...` or `Not enough staging space: ... skipping copy`.

8) Copy desired subdirectories
- File: `mount_helper.py` and `camera_service.py`
- Function: `mount_helper.copy_to_staging(src, dest, use_rsync=True)` — wraps `rsync` or `cp` and preserves helpful logging
- Config: `config.yaml` `camera.copy_subdirs` lists subpaths such as `Normal/Front`
- Expected logs: wrapper/rsync output captured in `/var/log/camera` (via wrappers or service stdout) and `camera_service.py` logs per-subdir copy actions.

9) Disable camera USB data/power (disconnect)
- File: `camera_service.py` -> calls `logged-stop-all-ports.sh` -> `stop-all-ports.sh` (calls `uhubctl -l 1-1 -a off`)
- Expected logs: `/var/log/camera/logged-stop-all-ports.log` contains invocation/completion lines; system messages show `Port X: 0000 off` from `uhubctl` output.

10) Wait for device removal
- File: `camera_service.py`
- Action: after disabling data, the service waits a short configurable timeout for `/dev/sdX` to disappear; logs either `Device removed: /dev/sda` or a warning `still present after disconnect attempt`.

11) Upload staged files
- File: `uploader.py`
- Function: `upload_dir(staging, upload_config, dry_run=False, retries=...)`
- Notes: uses `rsync` over SSH; check `config.yaml` for `upload.host`, `upload.user`, and optional `upload.ssh_key`. Upload failures are logged and depending on settings the staged files may be kept for retry.
- Expected logs: uploader logs success/failure and `rsync` output goes to service logs or `/var/log/camera` when invoked via wrapper.

12) Clean up staged files (on successful upload)
- File: `camera_service.py`
- Action: remove directories in staging using `shutil.rmtree()` or `os.remove()` and log cleanup success or errors.

13) Loop / sleep until next poll
- File: `camera_service.py` main loop; sleep controlled by `poll.interval_seconds` in `config.yaml`.

Mapping of important functions and files
- camera_service.py: `main()`, `get_battery_info()`, `is_host_reachable()`, control flow around detection/mount/copy/upload
- device_detector.py: `wait_for_camera_device()`, `detect_camera_block_device()`
- mount_helper.py: `MountedDevice` context manager, `copy_to_staging()`
- uploader.py: `upload_dir()`
- Shell control scripts (wrapped): `logged-start-for-data.sh` -> `start-for-data.sh`, `logged-stop-all-ports.sh` -> `stop-all-ports.sh`, `logged-battery-info.sh` -> `battery-info.sh`

Logging locations
- Per-script wrapper logs: `/var/log/camera/logged-*.log`
- Test-run outputs: `/var/log/camera/connect_test.run.log`
- Service unit journals: `journalctl -u camera_service` (systemd journal)

Typical diagnostic steps
- To reproduce a single run manually:
  - Run `sudo python3 /home/pi/camera-scripts/camera_service.py --config /home/pi/camera-scripts/config.yaml --once`
  - Or run the `connect_test.py` as root to exercise connect/disconnect only.
- To inspect logs: `sudo tail -n 200 /var/log/camera/logged-start-for-data.log` and `sudo journalctl -u camera_service -n 200`.

Notes
- The wrappers keep original scripts intact; if you edit the original scripts in the future, wrappers still log invocations and results.
- The service requires root privileges to write sysfs and to use `uhubctl`; running under `systemd` as `User=root` or starting the service with `sudo` is expected for full automation.
