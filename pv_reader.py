import argparse
import logging
import time
import traceback
import signal
import sys
from datetime import datetime, timedelta

from rctclient.registry import REGISTRY as R
from rctclient.types import DataType
from rctclient.utils import decode_value
from rctclient.exceptions import ReceiveFrameError
from rct_reader import RctReader
from rct_parser import ResponseFrame
from influxdb_client import InfluxDBClient, Point, WriteApi
from influxdb_client.client.write_api import SYNCHRONOUS
import urllib3

# https://realpython.com/async-io-python/

log = logging.getLogger(__name__)


def read_oid_set(reader: RctReader, oid_set) -> dict[str, any]:
    readings: dict[str, any] = {}
    frames = reader.read_frames(oid_set)
    for frame in frames:
        value = None
        if frame is None:
            log.error("Error: no response received")
        elif frame.crc_ok:
            oi = R.get_by_id(frame.oid)
            if oi.response_data_type != DataType.UNKNOWN:
                try:
                    value = decode_value(oi.response_data_type, frame.payload)
                except ValueError:
                    value = frame.payload.hex(' ')
                if isinstance(value, (int, float)):
                    value = round(value, 1)
            else:
                value = frame.payload.hex(' ')
            readings[oi.name] = value
        else:
            log.error("Error wrong crc in response!")

    log.debug(f'Readings complete len: {len(readings)}.')
    return readings


def read_oid(reader: RctReader, oid: str) -> tuple[str, any]:
    frame = reader.read_frame(oid)
    if frame is None:
        log.error("Error: no response received")
    elif frame.crc_ok:
        oi = R.get_by_id(frame.oid)
        if oi.response_data_type != DataType.UNKNOWN:
            try:
                value = decode_value(oi.response_data_type, frame.payload)
            except ValueError:
                value = frame.payload.hex(' ')

            if isinstance(value, (int, float)):
                value = round(value, 1)
            return (oi.name, value)
        else:
            return oi.name, frame.payload.hex(' ')
    else:
        log.error("Error wrong crc in response!")
        return oid, None


def get_units(oid_names: set[str]) -> dict[str, str]:
    result = {}
    for oid_name in oid_names:
        oid = R.get_by_name(oid_name)
        result[oid_name] = oid.unit if oid.unit else ''
    return result


def report_frame_callback(frame: ResponseFrame):
    now = datetime.now()
    oid = R.get_by_id(frame.oid)
    ftype = oid.response_data_type
    try:
        if ftype != DataType.UNKNOWN:
            value = decode_value(ftype, frame.payload)
            log.info(f'{now.strftime("%H:%M:%S")}: Response {oid.name} ({oid.object_id}): '
                  f'value: {value}, type: {ftype}')
        else:
            value = frame.payload.hex(' ')
            log.info(f'{now.strftime("%H:%M:%S")}: Response with unknown type {oid.name} '
                  f'({oid}): raw value: {value}')
    except KeyError as ex:
        log.error(f'Error: Cannot decode value: {ex}')


def listen_only(rct_inverter_host: str, rct_inverter_port: str):
    with RctReader(rct_inverter_host, rct_inverter_port, timeout=30) as reader:
        reader.register_callback(report_frame_callback)
        while True:
            try:
                reader.recv_frame()
            except TimeoutError:
                print('Timeout occured')
            except ReceiveFrameError as ex:
                print(f'Error: Frame read error received: {ex}')


def send_command(command: str, rct_inverter_host: str, rct_inverter_port: str):
    with RctReader(rct_inverter_host, rct_inverter_port) as reader:
        log.info(f'Sending command {command}')
        frame = reader.read_frame(command)
        if frame is None:
            log.error("Error no response received")
        else:
            oid = R.get_by_id(frame.oid)
            log.info(f'Response frame received: {oid}, crc ok: {frame.crc_ok}')
            value = decode_value(oid.response_data_type, frame.payload)
            log.info(f'Value: {value}, type: {oid.response_data_type}')


def monitor_inverter_influx(
    rct_inverter_host: str,
    rct_inverter_port: str = '8899',
    influx_host: str = None,
    influx_port: str = '8899',
):
    influx_url: str = f'http://{influx_host}:{influx_port}'
    username = "admin"
    password = "admin"

    while True:
        try:
            with InfluxDBClient(url=influx_url, token=f'{username}:{password}', org='-') as influx:
                with influx.write_api(write_options=SYNCHRONOUS) as write_api:
                    monitor_inverter(rct_inverter_host, rct_inverter_port, write_api)
        except urllib3.exceptions.HTTPError as ex:
            log.error(f'HTTP error when writing to database: {ex}')
            time.sleep(15.0)
            log.error('Reconnecting to InfluxDB')


def monitor_inverter(
    rct_inverter_host: str,
    rct_inverter_port: str = '8899',
    write_api: WriteApi = None,
):
    short_interval_readings = {
        'dc_conv.dc_conv_struct[0].p_dc': 'power_panel_0',  # Power panel 0 (W)
        'dc_conv.dc_conv_struct[1].p_dc': 'power_panel_1',  # Power panel 1 (W)
        'g_sync.p_ac_load_sum_lp': 'power_used',            # Power household (W)
        'g_sync.p_ac_grid_sum_lp': 'power_grid',            # Power grid (W)
        'g_sync.p_ac_load[0]': 'power_phase_0',             # Power household phase 0 (W)
        'g_sync.p_ac_load[1]': 'power_phase_1',             # Power household phase 1 (W)
        'g_sync.p_ac_load[2]': 'power_phase_2',             # Power household phase 2 (W)
        'g_sync.p_acc_lp': 'power_battery',                 # Power battery (W)
        'grid_pll[0].f': 'grid_frequency',                  # Grid frequency (Hz)
    }

    long_interval_readings = {
        'battery.soc': 'charge_battery',
        'battery.soc_target': 'charge_battery_target',
        'power_mng.amp_hours': 'battery_amp_hours',
        'battery.voltage': 'battery_voltage',
        'battery.used_energy': 'battery_used_energy',
        'battery.stored_energy': 'battery_stored_energy',
        'prim_sm.island_flag': 'grid_separated',
        'energy.e_ac_day': 'day_energy',                       # Day energy produced (Wh)
        'energy.e_load_day': 'day_energy_used',                # Household day energy (Wh)
        'energy.e_ac_total': 'total_energy',
        'energy.e_grid_feed_day_sum': 'day_energy_grid_feed',  # Day energy fed into grid (Wh)
        'energy.e_grid_load_day': 'day_energy_grid_load',      # Day energy consumed from grid (Wh)
        'energy.e_dc_day[0]': 'day_energy_panel_0',            # Day energy produced string 0 (Wh)
        'energy.e_dc_day[1]': 'day_energy_panel_1',            # Day energy produced string 1 (Wh)
    }

    units = get_units(short_interval_readings.keys())
    units |= get_units(long_interval_readings.keys())

    bucket = 'photovoltaic/autogen'

    interval_short = timedelta(seconds=5)
    interval_long = timedelta(minutes=1)
    last_time_long = datetime.now() - interval_long
    readings: dict[str, any] = {}
    read_retries: int = 0
    connect_retries: int = 0
    max_retries: int = 5

    while connect_retries < max_retries:
        try:
            with RctReader(rct_inverter_host, rct_inverter_port, buffer_size=512, timeout=3.0,
                        ignore_crc=True) as reader:
                read_retries = 0
                while read_retries < max_retries and not reader.server_closed_conn:
                    log.info("Reading...")
                    start = datetime.now()
                    try:
                        readings = read_oid_set(reader, short_interval_readings.keys())

                        now = datetime.now()
                        log.info(f'{now.strftime("%H:%M:%S")}: Summary Short Readings:')
                        for k, v in readings.items():
                            log.info(f'{k}: {v} {units[k]}')
                        log.info('----')

                        if readings and write_api:
                            point = Point("pv").tag("inverter", "RCT")

                            for k, v in short_interval_readings.items():
                                if k in readings:
                                    point = point.field(v, readings[k])
                            point = point.field('power_panel', readings['dc_conv.dc_conv_struct[0].p_dc'] +
                                                    readings['dc_conv.dc_conv_struct[1].p_dc'])
                            log.info('writing to InfluxDB')
                            write_api.write(bucket=bucket, record=point)
                            read_retries = 0

                        if start - last_time_long >= interval_long:
                            # read long lived values...
                            readings = read_oid_set(reader, long_interval_readings.keys())
                            now = datetime.now()
                            log.info(f'{now.strftime("%H:%M:%S")}: Summary Long Readings:')
                            for k, v in readings.items():
                                log.info(f'{k}: {v}{units[k]}')

                            if readings and write_api:
                                for k, v in long_interval_readings.items():
                                    if k in readings:
                                        point = point.field(v, readings[k])
                                write_api.write(bucket=bucket, record=point)
                            log.info('----')
                            last_time_long = start
                            read_retries = 0
                        end = datetime.now()
                    except TimeoutError:
                        now = datetime.now()
                        log.error(f'{now.strftime("%H:%M:%S")}: Timeout when reading, retrying now')
                        time.sleep(1.0)
                        end = start + interval_short  # immediately retry again
                        read_retries += 1
                    except BaseException as ex:  # pylint: disable=broad-exception-caught
                        now = datetime.now()
                        end = now
                        read_retries += 1
                        log.error(f'{now.strftime("%H:%M:%S")}: General exception {ex}', exc_info=True)

                    if end - start < interval_short:
                        remaining = (interval_short - (end - start)).total_seconds()
                        time.sleep(remaining)
                retries = 0
                if reader.server_closed_conn:
                    log.error("Server closed connection, reconnecting in 5s")
                else:
                    log.error(f'max retries {retries} exceeded, reconnecting in {5.0 * retries}s')
        except Exception as ex:   # pylint: disable=broad-exception-caught
            log.error(f'Error when connecting to inverter: {ex}')
        time.sleep(5.0 * retries)
        log.error('reconnecting')
    raise RuntimeError('Aborting program, too many attempts to connect to connect to inverter.')


def read_all_values(rct_inverter_host: str, rct_inverter_port: str = '8899'):
    all_params = [x.name for x in R.all()]
    units = get_units(all_params)

    with RctReader(rct_inverter_host, rct_inverter_port, buffer_size=512, timeout=3.0,
                   ignore_crc=True) as reader:
        log.info(f'Reading {len(all_params)} values...')
        for oid_name in all_params:
            now = datetime.now()
            retries = 3
            retry = 0
            success = False
            while not success and retry < retries:
                try:
                    log.error(f'{now.strftime("%H:%M:%S")}: Timeout, retrying {retry}/{retries}')
                    name, value = read_oid(reader, oid_name)
                    log.info(f'{name}: {value} {units[name]}')
                    success = True
                except TimeoutError:
                    time.sleep(1.0 * (retry + 1))
                    retry += 1
                    log.error('Timeout.')


def print_stacktrace(_sig, _frame):
    traceback.print_stack()
    sys.exit(1)


def listen():
    signal.signal(signal.SIGTERM, print_stacktrace)  # Register handler


def main():
    listen()
    # HF-A21.fritz.box
    parser = argparse.ArgumentParser(
        prog='rct-reader',
        description='Read data from RCT inverter',
    )
    parser.add_argument('--host', help='host-name or IP of device', required=True)
    parser.add_argument('--port', default=8899, help='Port to connect to, default 8899',
              metavar='<port>')
    parser.add_argument('--influx-host', help='host of influxdb database')
    parser.add_argument('--influx-port', help='port of influxdb database')
    parser.add_argument('--listen-only', help='debug do not send commands', action='store_true')
    parser.add_argument('--read-all', help='read all values from inverter', action='store_true')
    parser.add_argument('--command', help='send single command to device')
    parser.add_argument('-v', '--verbose', help='enable debug logging', action='store_true')

    parsed = parser.parse_args()

    if parsed.verbose:
        logLevel = logging.DEBUG
    else:
        logLevel = logging.WARNING

    logging.basicConfig(
        level=logLevel,
        format='%(asctime)s %(levelname)-8s %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    if parsed.command:
        send_command(parsed.command, parsed.host, parsed.port)
    elif parsed.listen_only:
        print('Listening to inverter port')
        try:
            listen_only(parsed.host, parsed.port)
        except TimeoutError:
            print('Stop reading: timeout occured')
        except ReceiveFrameError as ex:
            print(f'Stop reading: Frame read error received: {ex}')

    else:
        if parsed.influx_host:
            monitor_inverter_influx(
                rct_inverter_host=parsed.host,
                rct_inverter_port=parsed.port,
                influx_host=parsed.influx_host,
                influx_port=parsed.influx_port,
            )
        elif parsed.read_all:
            read_all_values(parsed.host, parsed.port)
        else:
            monitor_inverter(
                rct_inverter_host=parsed.host,
                rct_inverter_port=parsed.port,
            )


if __name__ == '__main__':
    main()
