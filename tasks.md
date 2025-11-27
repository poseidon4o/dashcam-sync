Dashcam Manager — Detailed Execution Tasks

Overview
This document lists concrete, ordered tasks to implement the Dashcam Manager service described in `PLAN.md`. Each task includes sub-steps, commands to run, and verification checks.

Prerequisites
- A Raspberry Pi with root access (sudo), connected to the camera and the UPS HAT.
- Installed tools: `lifepo4wered-cli`, `uhubctl`, `rsync` (or chosen uploader), `python3` (3.8+), and `pip` for dependencies.
- The existing control scripts are in the repo: `battery-info.sh`, `start-for-data.sh`, `start-for-recording.sh`, `stop-all-ports.sh`.

How to use this file
- Follow tasks in numeric order. Many tasks are implementation + test steps. Mark progress in your task tracker or the repository TODOs.

Tasks

1) Preflight checks
- Objective: Confirm environment and that the camera can mount normally.
- Steps:
  - On Pi, check tool availability:

```bash
which lifepo4wered-cli || echo "install lifepo4wered-cli"
which uhubctl || echo "install uhubctl"
which rsync || echo "install rsync"
python3 -V
```

  - Confirm `battery-info.sh` prints valid JSON:

```bash
cd /home/pi/camera-scripts
chmod +x battery-info.sh || true
./battery-info.sh
# Expect a single JSON object with keys `battery_percent`, `battery_voltage`, etc.
```

  - Test fstab mount (if you have `fstab` entry). Mount camera manually and verify files:

```bash
sudo mount /mnt/cam || journalctl -u systemd --since "1 minute ago"  # or check /var/log/messages
ls -la /mnt/cam
sudo umount /mnt/cam
```

- Verification: `battery-info.sh` outputs JSON and camera storage mounts fine manually.

2) Fix control scripts (safe changes)
- Objective: Make scripts robust and executable.
- Steps:
  - Edit `battery-info.sh` to add shebang and set `set -euo pipefail` (already present) and ensure `#!/usr/bin/env bash` at top.
  - Replace bad shebangs in `start-for-data.sh` and `start-for-recording.sh` with `#!/usr/bin/env sh` or `#!/usr/bin/env bash`. Add tests to ensure `/sys/bus/usb/devices/1-1/authorized` exists before writing.
  - Make scripts executable:

```bash
chmod +x start-for-data.sh start-for-recording.sh stop-all-ports.sh battery-info.sh
```

  - Add minimal safety wrapper (example inside `start-for-data.sh`): check `id` and `sudo` rights or exit with error code.

- Verification: Running `start-for-data.sh` and `start-for-recording.sh` should not error; check `dmesg` for device enumeration events.

3) Create `config.yaml`
- Objective: A single config file to drive the daemon.
- Suggested fields with example values:

```yaml
upload:
  method: rsync
  host: 192.168.1.203
  user: camera_uploader
  port: 22
  dest_path: /data/dashcam
  ssh_key: /etc/camera_service/id_rsa
camera:
  hub_location: 1-1
  port_number: 1
  mount_base: /mnt/cam
paths:
  local_staging: /var/lib/camera_service/staging
  log_path: /var/log/camera_service.log
thresholds:
  disconnect_percent: 50
  shutdown_percent: 25
poll:
  interval_seconds: 30
  mount_timeout_seconds: 20
  upload_retries: 3
```

- Place `config.yaml` in `/etc/camera_service/config.yaml` (recommended) and make it readable by root only.

- Verification: A simple Python snippet correctly reads and validates the config.

4) Implement device detection module (`device_detector.py`)
- Objective: Reliably map the camera to a block device or MTP resource after enabling USB data.
- Key responsibilities:
  - Given optional vendor/product id or hub path, find matching `/sys/bus/usb/devices/*` with `idVendor`/`idProduct` or matching parent path.
  - Wait for udev / kernel block device creation with a configurable timeout.
  - Return the device path (e.g., `/dev/sda1`) and a unique id for mount naming.
- Implementation hints:
  - Use `glob.glob('/sys/bus/usb/devices/*')` and read `idVendor`/`idProduct`.
  - Use `udevadm settle --timeout=10` or poll `/dev` for block devices.

- Verification: After running `start-for-data.sh`, the module returns a block device path within the mount timeout.

5) Implement mount and copy helpers (`mount_helper.py`)
- Objective: Mount the camera storage and copy files safely to a local staging area.
- Responsibilities:
  - Given a block device, create a mount path under `mount_base` (e.g., `/mnt/cam-67EF9CED`), mount read-only or read-write as appropriate, and ensure `sync` and `umount` are called.
  - Copy files with checksum verification (`rsync --checksum` or `sha256sum` followed by compare).
  - Enforce disk-space checks: verify `df --output=avail` against required space before copying.
  - Ensure cleanup on exceptions (try/finally) and use `subprocess.run([...], check=True)`.

- Verification: Copy small test files from camera to `local_staging` and verify checksums match.

6) Implement uploader module (`uploader.py`)
- Objective: Upload staged footage to `192.168.1.203` using `rsync` over SSH.
- Responsibilities:
  - Invoke `rsync -avz --remove-source-files --partial --inplace -e "ssh -i /path/to/key -p PORT" source/ user@host:/dest` with configurable flags.
  - Validate successful exit codes, and optionally verify remote checksum (via `ssh user@host 'sha256sum /dest/file'`).
  - Implement retry with exponential backoff and a max retry count.
  - Provide `dry_run` option.

- Verification: From Pi, run the rsync command manually to the destination host and verify files appear and are correct.

7) Implement `camera_service.py` (main daemon)
- Objective: Orchestrate polling, battery checks, mode switching, copy, upload, and shutdown.
- Responsibilities / Flow:
  - Start: run preflight checks and load `config.yaml`.
  - On startup, disable all USB ports by default (policy: start disconnected). Use `stop-all-ports.sh`.
  - Main loop (every `interval_seconds`):
    - Call `battery-info.sh` and parse JSON result.
    - If UPS reports AC power (if `PI_RUNNING` or another key indicates AC), ignore battery thresholds and prefer uploads.
    - If `battery_percent` < `shutdown_percent`: perform safe unmounts and `shutdown -h now`.
    - If `battery_percent` < `disconnect_percent`: ensure camera is powered off and data disabled.
    - Else if remote host reachable and `battery_percent >= disconnect_percent`: run upload flow:
      - Call `start-for-data.sh` to enable data.
      - Wait for device detection via `device_detector.py`.
      - Mount, copy to local staging via `mount_helper.py`.
      - Call `stop-all-ports.sh` or `start-for-recording.sh` (configurable) to conserve battery.
      - Run `uploader.py` to upload staged files.
  - Handle signals (SIGINT/SIGTERM): unmount and leave camera in a safe state (recording-only or powered-off per config).
  - Log actions (info/warn/error) to `log_path` and to stdout for systemd.

- Implementation notes:
  - Use `asyncio` or threading to keep code responsive, but avoid concurrent uploads/copies.
  - Use file-based locking (e.g., `fcntl.flock`) on staging dir to prevent overlapping runs.

- Verification steps:
  - Run `camera_service.py --dry-run` and confirm it logs the expected sequence without toggling USB.
  - Run live with a test camera and simulate network reachability and battery levels.

8) Create `systemd` unit
- Objective: Run the daemon on boot and keep it running.
- Example unit (place as `/etc/systemd/system/camera_service.service`):

```ini
[Unit]
Description=Dashcam Manager Service
After=network.target

[Service]
Type=simple
User=root
ExecStart=/usr/bin/python3 /home/pi/camera-scripts/camera_service.py --config /etc/camera_service/config.yaml
Restart=on-failure
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
```

- Steps to install & enable:

```bash
sudo cp systemd/camera_service.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now camera_service.service
sudo journalctl -u camera_service -f
```

- Verification: Service starts, logs to `journalctl`, and restarts on failure.

9) Logging, rotation and monitoring
- Objective: Keep logs manageable and discoverable.
- Steps:
  - Configure logging to `/var/log/camera_service.log` and to systemd.
  - Add `/etc/logrotate.d/camera_service` with weekly rotation and compression.

10) Tests and dry-run mode
- Objective: Ensure code is safe to run on the Pi without toggling hardware.
- Steps:
  - Implement `--dry-run` for `camera_service.py` where calls to control scripts and rsync are logged but not executed.
  - Create unit tests for JSON parsing of `battery-info.sh` and for `device_detector.py` returning fake devices.

11) Documentation and README
- Objective: Provide clear install and troubleshooting guidance.
- Content to include:
  - Prereqs & installs: `sudo apt install uhubctl rsync python3-venv` and where to get `lifepo4wered-cli`.
  - Configuration example and location of `config.yaml`.
  - How to enable & inspect the service (`systemctl status`, `journalctl`).
  - How to run in `--dry-run` and revert tests.

12) Field validation and deployment
- Objective: Validate behavior on the real device.
- Steps:
  - Test scenario A: No network — camera remains in recording mode.
  - Test scenario B: Network available and reachable host — camera switches to data, files copied, camera stopped and upload completes.
  - Test scenario C: Battery thresholds
    - Bring battery below `disconnect_percent`: camera disconnected/powered-off.
    - Bring battery below `shutdown_percent`: Pi shuts down (after clean unmount).
  - For each scenario, inspect logs for timestamps and expected actions.

Cleanup policy (recommended)
- Delete staged files after successful upload. Keep a configurable number of days of local backups (default: delete immediately).

Safety notes
- Always test with a spare camera or simulated environment first.
- Ensure `ssh` keys used for `rsync` are restricted and stored under `/etc/camera_service` with `chmod 600`.
- Avoid disabling `lifepo4wered` auto-shutdown settings unless you know the implications.

If you want, I can now implement the first code changes: fix the three script shebangs and create a `config.yaml` example and `camera_service.py` prototype (dry-run). Which should I do next?