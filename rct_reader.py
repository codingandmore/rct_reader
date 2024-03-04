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


def read_frame(sock: socket.socket, oid_name):

    # query information about an object ID (here: battery.soc):
    oid = R.get_by_name(oid_name)

    # construct a byte stream that will send a read command for the object ID we want, and send it
    send_frame = make_frame(command=Command.READ, id=oid.object_id)
    sock.sendall(send_frame)

    buffer = bytearray(2048)

    start = 0
    bytes_read = sock.recv_into(buffer, len(buffer))
    print(f'read bytes from socket: {bytes_read}')
    mv = memoryview(buffer)[start:bytes_read]
    parser = rct_parser.FrameParser(mv)
    parser.parse()
    print(f'Parser complete: {parser.complete}')
    if parser.complete:
        print(f'Command received: {parser.command}, crc ok: {parser.crc_ok}')
        value = decode_value(oid.response_data_type, parser.data)
        print(f'Value: {value}, type: {oid.response_data_type}')


def main():
    logging.basicConfig(level=logging.DEBUG)
    parser = argparse.ArgumentParser(
        prog='rct-reader',
        description='Read data from RCT inverter',
    )
    parser.add_argument('--host', help='host-name or IP of device', required=True)
    parser.add_argument('--name', help='OID name from registry', required=False)

    parsed = parser.parse_args()

    # open the socket and connect to the remote device:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((parsed.host, 8899))

    # read_frame(parsed.host, parsed.name)
    oids = {'g_sync.p_acc_lp', 'g_sync.p_ac_load_sum_lp', 'g_sync.p_ac_grid_sum_lp', 'battery.soc',
            'inv_struct.cosinus_phi'}
    for oid in oids:
        read_frame(sock, oid)


if __name__ == '__main__':
    main()
