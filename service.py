#!/usr/bin/python3
import subprocess
import logging
import asyncio
import glob
import os
import argparse

logger = logging.getLogger(__name__)

LOCAL_FILES_DIR = '/home/pi/camera-scripts/local_files/'
LOCAL_FILES_LIST = '/home/pi/camera-scripts/local_files.txt'

REMAINING_LOCAL_SPACE = 1024 * 1024 * 1024  # 1GB
SYS_PATH_ENABLE_USB = '/sys/bus/usb/devices/1-1/authorized'

REMOTE_HOST = '192.168.1.203'
REMOTE_FILE_LIST = '/home/pi/uploads.txt'
REMOTE_FILE_DIR = '/home/pi/uploads/'


class GlobalTestableCommands:
    REGISTERED_FUNCTIONS = {}

    @staticmethod
    def testable_function(func):
        GlobalTestableCommands.REGISTERED_FUNCTIONS[func.__name__] = func
        return func

    def __init__(self, parser):
        parser.add_argument('--test', type=str, help='Run in test mode without executing commands')

    def run(self, args):
        if 'test' not in args or len(args.test) == 0:
            logger.warning(f'No test function specified')
            return False
        if args.test in GlobalTestableCommands.REGISTERED_FUNCTIONS:
            func = GlobalTestableCommands.REGISTERED_FUNCTIONS[args.test]
            logger.info(f'Running test function: {args.test}, result:')
            result = asyncio.run(func())
            logger.info(f'{result}')
            return True
        raise ValueError(f'Unknown test function: {args.test}')


async def run_command(command: str) -> str:
    logger.debug(f'Running command: {command}')
    proc = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        logger.error(f'Command "{command}" failed with error: {stderr.decode().strip()}')
        return ''
    return proc.returncode, stdout.decode().strip(), stderr.decode().strip()


@GlobalTestableCommands.testable_function
async def get_local_space_stats():
    logger.debug('Getting root filesystem space stats')
    code, out, err = await run_command(f'df {LOCAL_FILES_DIR}')
    if code != 0:
        logger.error(f'Error getting root space stats: {err}')
        return {}
    lines = out.splitlines()
    if len(lines) < 2:
        logger.error('Unexpected output from df command')
        return {}
    headers = lines[0].split()
    values = lines[1].split()
    stats = dict(zip([h.lower() for h in headers], values))
    logger.info(f'Root filesystem stats: {stats}')
    return stats


@GlobalTestableCommands.testable_function
async def read_stats_map():
    """
    I2C_REG_VER = 7
    I2C_ADDRESS = 67
    LED_STATE = 1
    TOUCH_STATE = 0
    TOUCH_CAP_CYCLES = 0
    TOUCH_THRESHOLD = 12
    TOUCH_HYSTERESIS = 2
    DCO_RSEL = 14
    DCO_DCOMOD = 155
    VIN = 5208
    VBAT = 3611
    VOUT = 4979
    IOUT = 857
    VBAT_MIN = 2850
    VBAT_SHDN = 2950
    VBAT_BOOT = 3150
    VOUT_MAX = 3500
    VIN_THRESHOLD = 4498
    IOUT_SHDN_THRESHOLD = 0
    VBAT_OFFSET = 34
    VOUT_OFFSET = 26
    VIN_OFFSET = 50
    IOUT_OFFSET = 0
    AUTO_BOOT = 3
    WAKE_TIME = 0
    SHDN_DELAY = 64
    AUTO_SHDN_TIME = 65535
    PI_BOOT_TO = 300
    PI_SHDN_TO = 120
    RTC_TIME = 1764534359
    RTC_WAKE_TIME = 0
    WATCHDOG_CFG = 0
    WATCHDOG_GRACE = 20
    WATCHDOG_TIMER = 20
    PI_RUNNING = 1
    CFG_WRITE = 0
    """
    _, out, _ = await run_command('lifepo4wered-cli get')
    stats = {}
    for line in out.splitlines():
        key, value = line.split('=', 1)
        key, value = key.strip(), value.strip()
        stats[key] = value
    logger.info(f'Reading battery stats: {len(stats)} entries')
    return stats


@GlobalTestableCommands.testable_function
async def get_battery_stats():
    logger.debug('Getting battery stats')
    stats_map = await read_stats_map()
    result = {
        'battery_voltage': float(stats_map.get('VBAT', '0')) / 1000.0,
        'minium_boot_voltage': float(stats_map.get('VBAT_BOOT', '0')) / 1000.0,
        'input_voltage': float(stats_map.get('VIN', '0')) / 1000.0,
        'output_voltage': float(stats_map.get('VOUT', '0')) / 1000.0,
        'output_current': float(stats_map.get('IOUT', '0')) / 1000.0,
        'wake_timer_remaining': int(stats_map.get('WAKE_TIME', '0')),
        'ac_power': float(stats_map.get('VIN', '0')) >= float(stats_map.get('VIN_THRESHOLD', '0')),
    }
    logger.info(f'Battery voltage remaining: {result["minium_boot_voltage"] - result["battery_voltage"]}V, AC power: {result["ac_power"]}')
    return result


async def set_camera_usb(enable: bool):
    logger.info(f'Setting camera USB {"enabled" if enable else "disabled"}')
    def write_to_file(value: str):
        logger.debug(f'Writing "{value}" to {SYS_PATH_ENABLE_USB}')
        with open(SYS_PATH_ENABLE_USB, 'w') as f:
            f.write('1' if enable else '0')

    await asyncio.to_thread(write_to_file, '1' if enable else '0')
    return True


async def wait_for_camera_usb_connect():
    device_path = '/dev/disk/by-id/usb-NOVATEKN_vt-DSC_96680-00000-001-0:0'
    retry_interval = 0.5
    logger.info(f'Waiting for camera {device_path} USB device to connect, checking every {retry_interval} seconds')
    while True:
        if os.path.exists(device_path):
            return True
        logger.debug('Waiting for camera device at %s', device_path)
        await asyncio.sleep(retry_interval)


async def set_usb_port_power(enabled: bool):
    logger.info(f'Setting USB port power to {"on" if enabled else "off"}')
    action = 'on' if enabled else 'off'
    code, out, err = await run_command(f'uhubctl -l 1-1 -a {action}')
    if code != 0:
        logger.error(f'Error setting USB port power {action}: {err}')
        return False
    logger.info(f'USB port power set to {action} successfully: {out}')
    return True


@GlobalTestableCommands.testable_function
async def mount_camera():
    logger.info(f'Starting usb for reading and mounting camera')
    await set_camera_usb(True)
    await set_usb_port_power(True)
    await wait_for_camera_usb_connect()
    code, out, err = await run_command('mount -t vfat -o rw,uid=1000,gid=1000,umask=022 UUID=67EF-9CED /mnt/cam')
    if code != 0:
        logger.error(f'Error mounting camera: {err}')
        return False
    logger.info(f'Camera mounted successfully {out}')
    return True


@GlobalTestableCommands.testable_function
async def start_camera_recording():
    logger.info(f'Starting camera recording mode')
    await set_camera_usb(False)
    await set_usb_port_power(True)
    await asyncio.sleep(25)


@GlobalTestableCommands.testable_function
async def disconnect_camera():
    await set_usb_port_power(False)
    await asyncio.sleep(20)


@GlobalTestableCommands.testable_function
async def is_host_reachable():
    logger.debug(f'Pinging host {REMOTE_HOST} to check reachability')
    code, _, _ = await run_command(f'ping -c 1 -W 1 {REMOTE_HOST}')
    is_reachable = code == 0
    logger.info(f'Host {REMOTE_HOST} is {"reachable" if is_reachable else "not reachable"}')
    return is_reachable


async def wait_for_host():
    logger.info(f'Waiting for host {REMOTE_HOST} to become reachable')
    retry_interval = 5.0
    while True:
        if await is_host_reachable():
            logger.info(f'Host {REMOTE_HOST} is reachable')
            return True
        logger.debug(f'Waiting for host {REMOTE_HOST} to become reachable')
        await asyncio.sleep(retry_interval)


async def wait_for_no_host():
    logger.info(f'Waiting for host {REMOTE_HOST} to become unreachable')
    retry_interval = 5.0
    while True:
        if not is_host_reachable():
            logger.info(f'Host {REMOTE_HOST} is not reachable')
            return True
        logger.debug(f'Waiting for host {REMOTE_HOST} to become unreachable')
        await asyncio.sleep(retry_interval)


@GlobalTestableCommands.testable_function
async def get_missing_files():
    logger.info('Getting list of missing files from camera')
    def get_local_files_set():
        files_set = set()
        if not os.path.exists(LOCAL_FILES_LIST):
            return files_set
        with open(LOCAL_FILES_LIST, 'r') as f:
            for line in f:
                files_set.add(line.strip())
        return files_set

    def get_camera_files_set():
        files_set = set()
        camera_files_list = '/mnt/cam/Normal/Front/'
        if not os.path.exists(camera_files_list):
            return files_set
        files_set = glob.glob(os.path.join(camera_files_list, '*.MP4'))
        return set(os.path.basename(f) for f in files_set)

    local_files = await asyncio.to_thread(get_local_files_set)
    camera_files = await asyncio.to_thread(get_camera_files_set)
    missing_files = camera_files - local_files
    logger.info(f'Found {len(camera_files)} out of them missing are: {missing_files}')
    return missing_files


@GlobalTestableCommands.testable_function
async def copy_files_to_staging():
    file_list = await get_missing_files()
    for filename in file_list:
        src_path = os.path.join('/mnt/cam/Normal/Front/', filename)
        space_stats = await get_local_space_stats()
        transfer_file_size = os.path.getsize(src_path)
        reimaining_after_transfer = int(space_stats['available']) * 1024 - transfer_file_size
        if reimaining_after_transfer < REMAINING_LOCAL_SPACE:
            logger.warning(f'Not enough space to copy file {filename}; stopping copy')
            return

        if not os.path.exists(src_path):
            logger.warning(f'Source file {src_path} does not exist; stopping copy')
            return
        dest_path = os.path.join(LOCAL_FILES_DIR, filename)
        code, _, _, = await run_command(f'rsync -a --info=progress2 "{src_path}" "{dest_path}"')
        if code == 0:
            logger.info(f'Copied file {filename} to staging')
            with open('/home/pi/camera-scripts/local_files.txt', 'a') as f:
                f.write(f'{filename}\n')


async def task_disconnect_on_low_battery():
    while True:
        stats = await get_battery_stats()
        if stats['ac_power'] or stats['battery_voltage'] > stats['minium_boot_voltage']:
            await asyncio.sleep(60 if stats['ac_power'] else 20)
        else:
            logger.warning('Battery low and no AC power; disconnecting camera to save power')
            await set_usb_port_power(False)
            break


async def service_main():
    asyncio.create_task(task_disconnect_on_low_battery())
    await disconnect_camera()
    while True:
        await mount_camera()
        await wait_for_camera_usb_connect()
        await copy_files_to_staging() # if in range of host, it will extract
        await set_usb_port_power(False)
        await start_camera_recording()
        await wait_for_no_host()
        await asyncio.sleep(300)  # wait 5 min
        await wait_for_host()
        await set_usb_port_power(False)


def main():
    os.makedirs(LOCAL_FILES_DIR, exist_ok=True)
    if not os.path.exists(LOCAL_FILES_LIST):
        with open(LOCAL_FILES_LIST, 'w'): pass

    parser = argparse.ArgumentParser(description="Dashcam manager")
    tester = GlobalTestableCommands(parser)

    parser.add_argument('--debug', action='store_true', help='Enable debug logging')
    args = parser.parse_args()

    if 'test' in args:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s %(levelname)s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
    else:
        logging.basicConfig(
            filename='/var/log/camera/service.log',
            level=logging.DEBUG,
            format="%(asctime)s %(levelname)s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )

    logger.info(f'Args: {args}')

    if tester.run(args):
        logger.info('Test mode executed; exiting')
        return

    logger.info('Starting dashcam service main loop')
    asyncio.run(service_main())

if __name__ == '__main__':
    main()