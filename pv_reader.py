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
    oids = list(oid_set)
    frames = reader.read_frames(oids)
    i = 0
    for frame in frames:
        if frame is None:
            print("Error no response received")
        elif frame.crc_ok:
            oid = R.get_by_id(frame.oid)
            req_oid = R.get_by_name(oids[i])
            value = decode_value(oid.response_data_type, frame.payload)
            if req_oid == oid:
                # print(f'{oid.name} ({oid.description}): {value}, type: '
                #         f'{oid.response_data_type}')
                readings[oids[i]] = value
            else:
                print(f'Warning: device returned not requested data: {req_oid.name} '
                        f'({req_oid.description}), got: {oid.name} ({oid.description} value: '
                        f'{value}. Ignoring value.')
                readings[oids[i]] = None
        else:
            print("Error wrong crc!")
            readings[oids[i]] = None
        i += 1
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


def monitor_inverter(
    rct_inverter_host: str,
    rct_inverter_port: str = '8899',
    influx_url: str = 'http://localhost:8086',
    use_db: bool = True,
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

    username = "admin"
    password = "admin"
    interval_short = timedelta(seconds=5)
    interval_long = timedelta(minutes=5)
    last_time_long = datetime.now() - interval_long
    readings: dict[str, any] = {}
    use_db = False

    with InfluxDBClient(url=influx_url, token=f'{username}:{password}', org='-') as influx:
        with influx.write_api() as write_api:
            with RctReader(rct_inverter_host, rct_inverter_port, buffer_size=512) as reader:
                while True:
                    print("Reading...")
                    start = datetime.now()
                    readings = read_oid_set(reader, short_interval_readings.keys())

                    if use_db:
                        point = Point("pv")
                        #     .tag("inverter", "RCT") \
                        #     .field("power_panels", values[0]) \
                        #     .field("charge_battery", values[3]) \
                        #     .field("power_grid", values[2]) \
                        #     .field("energy_daily", values[1])
                        write_api.write(bucket=bucket, record=point)

                    print('Summary Short Readings:')
                    for k, v in short_interval_readings.items():
                        print(f'{v}: {readings[k]}{units[k]}')
                    print('----')

                    if start - last_time_long >= interval_long:
                        # read long lived values...
                        readings = read_oid_set(reader, long_interval_readings.keys())
                        print('Summary Long  Readings:')
                        for k, v in long_interval_readings.items():
                            print(f'{v}: {readings[k]}{units[k]}')
                        print('----')
                        last_time_long = start

                    end = datetime.now()
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
    parser.add_argument('--no-db', help='do not write into database', action='store_true')
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
        monitor_inverter(
            rct_inverter_host=parsed.host,
            rct_inverter_port=parsed.port,
            use_db=not parsed.no_db
        )


if __name__ == '__main__':
    main()
