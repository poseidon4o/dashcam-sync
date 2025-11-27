Plan: Dashcam Manager Service

TL;DR — Build a Python daemon `camera_service.py` run by `systemd` that monitors UPS via `battery-info.sh`, toggles the camera between recording/data modes using the existing USB control scripts (`start-for-recording.sh`, `start-for-data.sh`, `stop-all-ports.sh`), uploads footage to `192.168.1.203` when reachable, and enforces battery-based disconnect/shutdown rules to conserve power.

Goals / Requirements
- Upload recorded footage when the Pi is connected and `192.168.1.203` is reachable.
- Record while Pi is not in network (camera must remain in recording mode).
- When network available and host reachable, switch camera to USB/data, copy footage to Pi storage, then stop camera to conserve battery.
- If UPS battery drops below 50%: disconnect camera (conserve battery).
- If UPS battery drops below 25%: shut down Pi.
- By default on startup pi disables all usb ports to conserve power.

High-level design
1. Long-running Python daemon `camera_service.py`:
   - Poll `battery-info.sh` at a configurable interval, parse JSON to get `battery_percent` and `battery_voltage`.
   - Monitor network reachability to `192.168.1.203` (configurable protocol/port); if reachable and battery >= safe threshold, trigger upload flow.
   - Control camera mode using existing scripts (`start-for-data.sh` to enable data, `start-for-recording.sh` to enable recording-only, `stop-all-ports.sh` to power off if needed).
   - Detect camera device enumeration (block device or MTP); mount and copy footage to local staging area, then upload.
   - Enforce battery thresholds: <50% disconnect camera, <25% shut down Pi.
   - Handle signals (SIGTERM/SIGINT) to unmount and leave camera in safe state.
   - Ignore batttery readings if UPS is on AC power. Fix for that in `battery-info.sh` if needed.

2. Config file `config.yaml` (or JSON) containing:
   - `upload` block: `host` (192.168.1.203), `method` (rsync/scp/smb/http), credentials (key/password), `port`, `dest_path`.
   - `camera` block: `hub_location` (e.g., 1-1), `port_number`, optional vendor/product IDs or sysfs filters.
   - `thresholds` block: `disconnect_percent` (default 50), `shutdown_percent` (default 25).
   - `paths`: `mount_base`, `local_staging`, `log_path`.
   - `poll_interval_seconds`, `upload_retries`, timeouts.

3. Uploader module `uploader.py`:
   - Encapsulates chosen protocol rsync with retries, exponential backoff, checksum validation, and optional dry-run.

4. Script fixes:
   - `battery-info.sh`: add proper shebang `#!/usr/bin/env bash` and ensure executable.
   - `start-for-data.sh` and `start-for-recording.sh`: fix shebang (remove trailing slash), add safety checks (verify `/sys/.../authorized` exists), and wait for device enumeration.

5. systemd unit `systemd/camera_service.service`:
   - Run as `root` (or user with required capabilities), `Restart=on-failure`, appropriate `TimeoutStopSec`, and `ExecStart` pointing to `camera_service.py`.

Detailed flows
A. Normal (no network)
- Ensure camera is in recording mode: run `start-for-recording.sh`.
- Poll battery periodically; if battery < disconnect threshold, disconnect camera; if battery < shutdown threshold, schedule shutdown. If on AC power, ignore battery levels.

B. Network becomes available and `192.168.1.203` reachable
- Confirm battery >= `disconnect_percent` (configurable) to allow data transfer.
- Run `start-for-data.sh` to enable USB data and keep power on.
- Wait for device enumeration (udev); detect mountable block device or MTP device.
- Mount under `/mnt/cam` or use MTP copy tools; copy footage to `local_staging` and verify checksums.
- Disconnect camera data mode: run `stop-all-ports.sh` to conserve battery.
- Upload to remote host using `uploader.py`.
- After successful upload, remove local footage.

C. Emergency battery handling - if not on AC power
- On every poll, if `battery_percent` < `disconnect_percent` (default 50): ensure camera disconnected (call `stop-all-ports.sh` or run `start-for-recording.sh` with data disabled if you want camera recording but not connected). Implementation should be configurable: full power off vs data-disabled.
- If `battery_percent` < `shutdown_percent` (default 25): perform `shutdown -h now` after clean unmounts.

Device detection notes
- After scripts run, the camera has few seconds delay. Handle gracefully.
- Always wait for udev to settle, with timeouts and retries. Do not hardcode `1-1` unless verified stable for your hardware.

Safety and robustness
- Service must run as `root` (or with capabilities) because writing `/sys/.../authorized` and calling `uhubctl` require privileges.
- Catch signals and cleanly unmount before toggling ports or shutting down.
- Check for available disk space before copying; implement rotation/cleanup policy for local storage.
- Preflight checks on startup for `uhubctl`, `lifepo4wered-cli`, and the control scripts being executable.
- Do not run copying of camera files to the local storage and uploading simultaneously; serialize to avoid I/O contention.
- Implement exponential backoff for upload retries and avoid infinite upload loops that drain battery.

Files to add
- `camera_service.py` — main daemon.
- `config.yaml` — configuration.
- `uploader.py` — upload implementation.
- `systemd/camera_service.service` — unit file.
- `README.md` — installation and operation steps.

Missing info required from user to finalize implementation
- Camera identification (vendor/product id or confirmation `1-1` is fixed).
  - Current fstab mount that works: `UUID=67EF-9CED  /mnt/cam  vfat  rw,nofail,x-systemd.automount,x-systemd.device-timeout=5,uid=1000,gid=1000,umask=022  0  0`
- How the camera exposes storage: mass-storage block device or MTP.
  - USB storage that can be mounted by the provided fstab line.
- Upload protocol and credentials for `192.168.1.203` (rsync/SCP/SMB/HTTP and auth details).
  - rsync over SSH, with key-based auth preferred.
- Whether to fully power off camera when battery <50% or only disable data connection.
  - Fully power off camera below 50%.
- Desired local storage and retention policy for footage.
  - Delete as soon as uploaded successfully.

Next steps I can take (pick one):
- Draft `camera_service.py` prototype and `config.yaml` (needs upload details to wire final upload code).
- Create small patches to fix shebangs in the existing scripts.
- Draft the `systemd` unit and `README.md` for install and safety notes.

If you want me to proceed now, tell me which upload method and camera identification approach to use; otherwise I will default to `rsync` over SSH for uploads and vendor/product ID detection via `/sys/bus/usb/devices`.
