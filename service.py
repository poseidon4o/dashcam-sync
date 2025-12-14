#!/usr/bin/python3
import subprocess
import logging
import asyncio
import glob
import os
import argparse

logger = logging.getLogger(__name__)

LOCAL_FILES_DIR = '/opt/dashcam/'
LOCAL_FILES_LIST = '/home/pi/camera-scripts/local_files.txt'
CAMERA_MOUNT_POINT = '/mnt/cam/'
CAMERA_DEVICE_PATH = '/dev/disk/by-id/usb-NOVATEKN_vt-DSC_96680-00000-001-0:0'

REMAINING_LOCAL_SPACE = 1024 * 1024 * 1024  # 1GB
SYS_PATH_ENABLE_USB = '/sys/bus/usb/devices/1-1/1-1.1/authorized'

CAMERA_PORT = '1'
USB_WIFI_PORT = '4'

class GlobalTestableCommands:
    REGISTERED_FUNCTIONS = {}

    @staticmethod
    def testable_function(func):
        GlobalTestableCommands.REGISTERED_FUNCTIONS[func.__name__] = func
        return func

    def __init__(self, parser):
        parser.add_argument('--test', type=str, help='Run in test mode without executing commands')

    def run(self, args):
        if args.test is None or len(args.test) == 0:
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
        return -1, '', stderr.decode().strip()
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
    logger.info(f'Battery voltage remaining: {result["battery_voltage"] - result["minium_boot_voltage"]}V, AC power: {result["ac_power"]}')
    return result


async def set_camera_usb(enable: bool):
    logger.info(f'Setting camera USB {"enabled" if enable else "disabled"}')
    def write_to_file(value: str):
        logger.debug(f'Writing "{value}" to {SYS_PATH_ENABLE_USB}')
        with open(SYS_PATH_ENABLE_USB, 'w') as f:
            f.write('1' if enable else '0')

    await asyncio.to_thread(write_to_file, '1' if enable else '0')
    return True


def is_camera_connected():
    return os.path.exists(CAMERA_DEVICE_PATH)


async def wait_for_camera_usb_connect():
    retry_interval = 0.5
    logger.info(f'Waiting for camera {CAMERA_DEVICE_PATH} USB device to connect, checking every {retry_interval} seconds')
    while True:
        if is_camera_connected():
            return True
        logger.debug('Waiting for camera device at %s', CAMERA_DEVICE_PATH)
        await asyncio.sleep(retry_interval)


async def set_usb_port_power(enabled: bool, port: str = CAMERA_PORT):
    logger.info(f'Setting USB port power to {"on" if enabled else "off"}')
    action = 'on' if enabled else 'off'
    code, out, err = await run_command(f'uhubctl -l 1-1 -p {port} -a {action}')
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
    code, out, err = await run_command(f'mount -t vfat -o rw,uid=1000,gid=1000,umask=022 UUID=67EF-9CED {CAMERA_MOUNT_POINT}')
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
    logger.info('Camera recording started; waiting for 25 seconds to stabilize')
    await asyncio.sleep(25)


@GlobalTestableCommands.testable_function
async def disconnect_camera():
    await set_usb_port_power(False)
    logger.info('Camera disconnected; waiting for 20 seconds to ensure power down')
    await asyncio.sleep(20)


@GlobalTestableCommands.testable_function
async def ensure_permissions():
    logger.info('Ensuring permissions for downloaded files')
    code, out, err = await run_command(f'chown -R cam-downloader:cam-downloader {LOCAL_FILES_DIR}')
    if code != 0:
        logger.error(f'Error setting permissions: {err}')
        return False
    logger.info(f'Permissions set successfully: {out}')
    return True


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
        camera_files_list = f'{CAMERA_MOUNT_POINT}/Normal/Front/'
        if not os.path.exists(camera_files_list):
            return files_set
        files_set = glob.glob(os.path.join(camera_files_list, '*.MP4'))
        return set(os.path.basename(f) for f in files_set)

    await ensure_permissions()
    local_files = await asyncio.to_thread(get_local_files_set)
    camera_files = await asyncio.to_thread(get_camera_files_set)
    missing_files = camera_files - local_files
    logger.info(f'Found {len(camera_files)} out of them missing are: {missing_files}')
    return missing_files


@GlobalTestableCommands.testable_function
async def copy_files_to_staging():
    file_list = await get_missing_files()
    remaining_files = len(file_list)
    for filename in file_list:
        if not is_camera_connected():
            logger.warning('Camera disconnected during file copy; stopping copy')
            return
        src_path = os.path.join(f'{CAMERA_MOUNT_POINT}/Normal/Front/', filename)
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
            await ensure_permissions()
            remaining_files -= 1
            logger.info(f'Copied file {filename} to staging, remaining files: {remaining_files}')
            with open(LOCAL_FILES_LIST, 'a') as f:
                f.write(f'{filename}\n')


@GlobalTestableCommands.testable_function
async def task_disconnect_on_low_battery():
    # TODO: interrupt all other tasks and go to sleep mode
    while True:
        stats = await get_battery_stats()
        if stats['ac_power'] or stats['battery_voltage'] > stats['minium_boot_voltage']:
            await asyncio.sleep(60 if stats['ac_power'] else 20)
        else:
            logger.warning('Battery low and no AC power; disconnecting camera to save power')
            await set_usb_port_power(False)
            break


async def task_disconnect_camera_on_ac():
    logger.info('Starting task to monitor AC power and disconnect camera when plugged in')
    while True:
        stats = await get_battery_stats()
        if stats['ac_power']:
            logger.info('No AC power detected; disconnecting camera')
            await disconnect_camera()
            return
        await asyncio.sleep(1)


@GlobalTestableCommands.testable_function
async def copy_files_while_no_ac():
    await mount_camera()
    await asyncio.gather(
        copy_files_to_staging(),
        task_disconnect_camera_on_ac()
    )


@GlobalTestableCommands.testable_function
async def wait_for_no_ac_power():
    stats = await get_battery_stats()
    logger.info(f'Waiting for AC power to be disconnected, current state: {stats["ac_power"]}')
    while True:
        stats = await get_battery_stats()
        if not stats['ac_power']:
            logger.info('AC power disconnected')
            return
        await asyncio.sleep(1)


@GlobalTestableCommands.testable_function
async def get_wifi_interface_info():
    logger.info('Getting wifi interface UUIDs')
    code, out, err = await run_command('nmcli -t -f NAME,DEVICE,UUID,STATE connection show')
    if code != 0:
        logger.error(f'Error checking network status: {err}')
        return dict()
    interface_info = {}
    for line in out.splitlines():
        name, interface, uuid, state = line.split(':')
        if 'localonlywifi' in name:
            interface_info[interface] = {
                'uuid': uuid,
                'state': state,
                'name': name
            }
    logger.info(f'WiFi interfaces: {interface_info}')
    return interface_info


@GlobalTestableCommands.testable_function
async def ensure_wifi():
    while True:
        interfaces = await get_wifi_interface_info()
        for interface, info in interfaces.items():
            if info['state'] == 'activated':
                logger.info(f'WiFi interface {interface} is active')
                continue
            else:
                logger.info(f'WiFi interface {interface} is not active; attempting to connect')
                code, out, err = await run_command(f'nmcli connection up uuid {info["uuid"]}')
                if code == 0:
                    logger.info(f'WiFi interface {interface} connected successfully')
                    return True
                else:
                    logger.error(f'Error connecting WiFi interface {interface}: {err}, will restart port {USB_WIFI_PORT}')
                    await set_usb_port_power(False, port=USB_WIFI_PORT)
                    await asyncio.sleep(2)
                    await set_usb_port_power(True, port=USB_WIFI_PORT)
        logger.info('Will check WiFi status again in 10 seconds')
        await asyncio.sleep(10)

    return True


async def service_main():
    # asyncio.create_task(task_disconnect_on_low_battery())
    asyncio.create_task(ensure_wifi())

    await disconnect_camera()

    while True:
        is_on_ac = (await get_battery_stats())['ac_power']
        logger.info(f'service_main: Starting new cycle; on AC power: {is_on_ac}')
        if is_on_ac:
            await start_camera_recording()
            logger.info('service_main: On AC power, recording started; waiting for AC power loss')
            await wait_for_no_ac_power() # block until AC power is lost
            logger.info('service_main: AC power lost, disconnecing camera')
            await disconnect_camera()
        else: # on battery power
            logger.info('service_main: On battery power; starting file copy from camera')
            await copy_files_while_no_ac()
            logger.info('service_main: File copy complete or interrupted; disconnecting camera')


def main():
    os.makedirs(CAMERA_MOUNT_POINT, exist_ok=True)
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

    logger.info('=' * 20 + ' Dashcam Service Starting ' + '=' * 20)
    logger.info(f'Args: {args}')

    if tester.run(args):
        logger.info('Test mode executed; exiting')
        return

    logger.info('Starting dashcam service main loop')
    asyncio.run(service_main())

if __name__ == '__main__':
    main()
