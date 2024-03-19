import argparse
import logging
import time
from datetime import datetime, timedelta

from rctclient.registry import REGISTRY as R
from rctclient.utils import decode_value
from rct_reader import RctReader
from influxdb_client import InfluxDBClient, Point


log = logging.getLogger(__name__)


def read_oid_set(reader: RctReader, oid_set) -> dict[str, any]:
    readings: dict[str, any] = {}
    oids = list(oid_set)
    frames = reader.read_frames(oids)
    i = 0
    for frame in frames:
        if frame.crc_ok:
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


def main():
    # HF-A21.fritz.box
    logging.basicConfig(level=logging.WARN)
    parser = argparse.ArgumentParser(
        prog='rct-reader',
        description='Read data from RCT inverter',
    )
    parser.add_argument('--host', help='host-name or IP of device', required=True)
    parser.add_argument('--port', default=8899, help='Port to connect to, default 8899',
              metavar='<port>')

    parsed = parser.parse_args()
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

    # oids = ['g_sync.p_acc_lp', 'g_sync.p_ac_load_sum_lp', 'g_sync.p_ac_grid_sum_lp', 'battery.soc',
    #         'inv_struct.cosinus_phi']

# g_sync.p_acc_lp
# Battery Power (negative for discharge) W

# g_sync.p_ac_load_sum_lp
# Load household - external Power momentaner Verbrauch W

# g_sync.p_ac_grid_sum_lp
# Total grid power (see Power grid) Bezug aus dem Netz in W

# battery.soc
# Battery State of Charge   0..1 Ladezustand

# grid_pll[0].f
# Grid frequency [Hz]       Detektierung Stromausfall

# g_sync.p_ac_load[0] (left)
# g_sync.p_ac_load[1] (middle)
# g_sync.p_ac_load[2] (right)
# Load household phase [W]      Leistung pro Phase

# energy.e_ac_day
# Day energy [kWh]

# energy.e_ac_total
# Total energy [MWh]


# energy.e_grid_load_day'
# description='Day energy grid load

# energy.e_ext_day',
# description='External day energy'

# energy.e_dc_day_sum[0]

# 'energy.e_dc_day[0]'
# description='Solar generator A day energy'),
# 'energy.e_dc_day[1]'
# description='Solar generator B day energy'),

# g_sync.s_ac_lp[0]
# Apparent power phase 1

# name='power_mng.amp_hours_measured',
# description='Measured battery capacity'),

# power_mng.amp_hours',
# description='Battery energy'

# prim_sm.island_flag
# description='Grid-separated'

    bucket = 'photovoltaic/autogen'

    username = "admin"
    password = "admin"
    interval_short = timedelta(seconds=5)
    interval_long = timedelta(minutes=5)
    last_time_long = datetime.now() - interval_long
    readings: dict[str, any] = {}
    use_db = False

    with InfluxDBClient(url='http://localhost:8086', token=f'{username}:{password}', org='-') as influx:
        with influx.write_api() as write_api:
            with RctReader(parsed.host, parsed.port, buffer_size=512) as reader:
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


if __name__ == '__main__':
    main()
