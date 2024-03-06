#!/usr/bin/env python3

import argparse
import socket
# import sys
# from rctclient.exceptions import FrameCRCMismatch
from rctclient.frame import make_frame  # , ReceiveFrame
from rctclient.registry import REGISTRY as R
from rctclient.types import Command
# from rctclient.types import DataType
from rctclient.utils import decode_value
# from rctclient.utils import  encode_value
# https://stackoverflow.com/questions/10742639/faster-sockets-in-python
import logging

import rct_parser


# Links and hints:
# battery.soc
# g_sync.p_acc_lp           Battery Power (neg for discharge)  Watts
# g_sync.p_ac_load_sum_lp   Load Household (external power)    Watts
#   -> auch noch für L1,L2,L3          g_sync.p_ac_load[0] [1], [2]
# g_sync.p_ac_grid_sum_lp    Total grid power (see Power grid)
# inv_struct.cosinus_phi cos φ
#
# https://stackoverflow.com/questions/22827794/reusing-python-bytearray-memoryview
# ctypes.memmove(ctypes.addressof(self), bytes, fit)
# copy bytearray: buffer1[:] = buffer2
# Documentation: https://rctclient.readthedocs.io/

class RctReader:
    def __init__(self, host: str, port: str):
        self.buffer = bytearray(2048)
        self.start = 0
        self.host = host
        self.port = port
        self.parser = rct_parser.FrameParser()
        self.sock = None

    def __enter__(self):
        # open the socket and connect to the remote device:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.connect((self.host, self.port))
        return self

    def __exit__(self, exc_type, exc_value, exc_traceback):
        self.sock.close()
        return False

    def read_frames(self, oids: list[str]):
        result = []
        for oid in oids:
            result.append(self.read_frame(oid))
        return result

    def read_frame(self, oid_name) -> any:
        # query information about an object ID (here: battery.soc):
        oid = R.get_by_name(oid_name)

        # construct a byte stream that will send a read command for the object ID we want, and send it
        send_frame = make_frame(command=Command.READ, id=oid.object_id)
        self.sock.sendall(send_frame)

        self.parser.reset()
        while not self.parser.complete:
            pos = self.parser.current_pos
            socket_buffer_view = memoryview(self.buffer)[pos:len(self.buffer) - pos]
            bytes_read = self.sock.recv_into(socket_buffer_view, len(socket_buffer_view))
            print(f'read bytes from socket: {bytes_read}')
            mv = memoryview(self.buffer)[self.start:bytes_read]
            self.parser.parse(mv)
            print(f'Parser complete: {self.parser.complete}')

        print(f'Command received: {self.parser.command}, crc ok: {self.parser.crc_ok}')
        value = decode_value(oid.response_data_type, self.parser.data)
        print(f'Value: {value}, type: {oid.response_data_type}')

        # rewind buffer
        remaining_len = pos + bytes_read - self.parser.current_pos
        if remaining_len > 0:
            self.buffer[0:remaining_len] = self.buffer[self.parser.current_pos:self.parser.current_pos + remaining_len]

        pos = 0
        self.parser.rewinded()

        return value


def main():
    logging.basicConfig(level=logging.DEBUG)
    parser = argparse.ArgumentParser(
        prog='rct-reader',
        description='Read data from RCT inverter',
    )
    parser.add_argument('--host', help='host-name or IP of device', required=True)
    parser.add_argument('--port', default=8899, help='Port to connect to, default 8899',
              metavar='<port>')
    # parser.add_argument('--name', help='OID name from registry', required=False)

    parsed = parser.parse_args()

    with RctReader('localhost', parsed.port) as reader:
        # read_frame(parsed.host, parsed.name)
        oids = {'g_sync.p_acc_lp', 'g_sync.p_ac_load_sum_lp', 'g_sync.p_ac_grid_sum_lp', 'battery.soc',
                'inv_struct.cosinus_phi'}
        values = reader.read_frames(oids)

    for val in values:
        print(f' Received value {val} of type {type(val)}')


if __name__ == '__main__':
    main()
