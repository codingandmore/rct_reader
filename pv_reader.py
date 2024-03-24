import argparse
import logging
import time
from datetime import datetime, timedelta

from rctclient.registry import REGISTRY as R
from rctclient.types import DataType
from rctclient.utils import decode_value
from rctclient.exceptions import ReceiveFrameError
from rct_reader import RctReader
from rct_parser import ResponseFrame
from influxdb_client import InfluxDBClient, Point

# https://realpython.com/async-io-python/

log = logging.getLogger(__name__)


def read_oid_set(reader: RctReader, oid_set) -> dict[str, any]:
    readings: dict[str, any] = {}
    frames = reader.read_frames(oid_set)
    for frame in frames:
        if frame is None:
            log.error("Error: no response received")
        elif frame.crc_ok:
            oi = R.get_by_id(frame.oid)
            if oi.response_data_type != DataType.UNKNOWN:
                value = decode_value(oi.response_data_type, frame.payload)
                value = round(value, 1)
            else:
                value = None
        else:
            log.error("Error wrong crc in response!")
            value = None

        readings[oi.name] = value
    log.debug(f'Readings complete len: {len(readings)}.')
    return readings


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
            print(f'{now.strftime("%H:%M:%S")}: Response {oid.name} ({oid.object_id}): '
                  f'value: {value}, type: {ftype}')
        else:
            value = frame.payload.hex(' ')
            print(f'{now.strftime("%H:%M:%S")}: Response with unknown type {oid.name} '
                  f'({oid}): raw value: {value}')
    except KeyError as ex:
        print(f'Error: Cannot decode value: {ex}')


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
        print(f'Sending command {command}')
        frame = reader.read_frame(command)
        if frame is None:
            print("Error no response received")
        else:
            oid = R.get_by_id(frame.oid)
            print(f'Response frame received: {oid}, crc ok: {frame.crc_ok}')
            value = decode_value(oid.response_data_type, frame.payload)
            print(f'Value: {value}, type: {oid.response_data_type}')


def monitor_inverter_influx(
    rct_inverter_host: str,
    rct_inverter_port: str = '8899',
    influx_host: str = None,
    influx_port: str = '8899',
):
    influx_url: str = f'http://{influx_host}{influx_port}',
    username = "admin"
    password = "admin"

    with InfluxDBClient(url=influx_url, token=f'{username}:{password}', org='-') as influx:
        with influx.write_api() as write_api:
            monitor_inverter(rct_inverter_host, rct_inverter_port, write_api)


def monitor_inverter(
    rct_inverter_host: str,
    rct_inverter_port: str = '8899',
    write_api=None,
):
    short_interval_readings = {
        'dc_conv.dc_conv_struct[0].p_dc': 'power_panel_0',
        'dc_conv.dc_conv_struct[1].p_dc': 'power_panel_1',
        'g_sync.p_ac_load_sum_lp': 'power_used',
        'g_sync.p_ac_grid_sum_lp': 'power_grid',
        'g_sync.p_ac_load[0]': 'power_phase_0',
        'g_sync.p_ac_load[1]': 'power_phase_1',
        'g_sync.p_ac_load[2]': 'power_phase_2',
        'g_sync.p_acc_lp': 'power_battery',
        'grid_pll[0].f': 'grid_frequency',
    }

    long_interval_readings = {
        'battery.soc': 'charge_battery',
        'power_mng.amp_hours': 'battery_amp_hours',
        'battery.voltage': 'battery_voltage',
        'prim_sm.island_flag': 'grid_separated',
        'energy.e_ac_day': 'day_energy',
        'energy.e_ac_total': 'total_energy',
        'energy.e_grid_load_day': 'day_energy_grid',
        'energy.e_dc_day[0]': 'day_energy_panel_0',
        'energy.e_dc_day[1]': 'day_energy_panel_1',
    }

    units = get_units(short_interval_readings.keys())
    units |= get_units(long_interval_readings.keys())

    bucket = 'photovoltaic/autogen'

    interval_short = timedelta(seconds=5)
    interval_long = timedelta(minutes=5)
    last_time_long = datetime.now() - interval_long
    readings: dict[str, any] = {}

    with RctReader(rct_inverter_host, rct_inverter_port, buffer_size=512, timeout=3.0,
                   ignore_crc=True) as reader:
        while True:
            print("Reading...")
            start = datetime.now()
            try:
                readings = read_oid_set(reader, short_interval_readings.keys())

                now = datetime.now()
                print(f'{now.strftime("%H:%M:%S")}: Summary Short Readings:')
                for k, v in readings.items():
                    print(f'{k}: {v} {units[k]}')
                print('----')

                if write_api:
                    point = Point("pv").tag("inverter", "RCT")

                    for k, v in short_interval_readings.items():
                        if k in readings:
                            point = point.field(v, readings[k])
                    write_api.write(bucket=bucket, record=point)

                if start - last_time_long >= interval_long:
                    # read long lived values...
                    readings = read_oid_set(reader, long_interval_readings.keys())
                    now = datetime.now()
                    print(f'{now.strftime("%H:%M:%S")}: Summary Long Readings:')
                    for k, v in readings.items():
                        print(f'{k}: {v}{units[k]}')

                    if write_api:
                        for k, v in long_interval_readings.items():
                            if k in readings:
                                point = point.field(v, readings[k])
                        write_api.write(bucket=bucket, record=point)
                    print('----')
                    last_time_long = start
                end = datetime.now()
            except TimeoutError:
                now = datetime.now()
                log.error(f'{now.strftime("%H:%M:%S")}: Timeout when readying, retrying now')
                end = start + interval_short  # immediately retry again
            remaining = (interval_short - (end - start)).seconds
            if remaining > 0:
                time.sleep(remaining)


def main():
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
    parser.add_argument('--command', help='send single command to device')
    parser.add_argument('-v', '--verbose', help='enable debug logging', action='store_true')

    parsed = parser.parse_args()
    if parsed.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.WARNING)

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
        else:
            monitor_inverter(
                rct_inverter_host=parsed.host,
                rct_inverter_port=parsed.port,

            )


if __name__ == '__main__':
    main()
