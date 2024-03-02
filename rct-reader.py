#!/usr/bin/env python3

import argparse
import socket
import select
# import sys
# from rctclient.exceptions import FrameCRCMismatch
from rctclient.frame import ReceiveFrame, make_frame
from rctclient.registry import REGISTRY as R
from rctclient.types import Command
# from rctclient.types import DataType
from rctclient.utils import decode_value
# from rctclient.utils import  encode_value
# https://stackoverflow.com/questions/10742639/faster-sockets-in-python
import logging

import rct_parser

# def read_frame2(hostname: str, oid_name: str):
#     BUFSIZE = 512
#     recv_buf: bytearray = bytearray(BUFSIZE)
#     recv_len: int = 0
#     done = False

#     # open the socket and connect to the remote device:
#     sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
#     sock.connect((hostname, 8899))

#     # query information about an object ID (here: battery.soc):
#     oid = R.get_by_name(oid_name)

#     # construct a byte stream that will send a read command for the object ID we want, and send it
#     send_frame = make_frame(command=Command.READ, id=oid.object_id)
#     sock.sendall(send_frame)

#     frame = ReceiveFrame()
#     while not done:
#         len_chunk = sock.recv(BUFSIZE - recv_len)
#         if len_chunk == 0:
#             done = True
#         else:
#             recv_len += len_chunk
#             frame.consume(recv_buf)
#             if frame.complete():
#                 done = True

#     if frame.id != oid.object_id:
#         raise ValueError(f'Got unexpected frame oid 0x{frame.id:08X}')


def read_frame(hostname: str, oid_name: str):
    # open the socket and connect to the remote device:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((hostname, 8899))

    # query information about an object ID (here: battery.soc):
    oid = R.get_by_name(oid_name)

    # construct a byte stream that will send a read command for the object ID we want, and send it
    send_frame = make_frame(command=Command.READ, id=oid.object_id)
    sock.sendall(send_frame)

    # loop until we got the entire response frame
    frame = ReceiveFrame()
    while True:
        ready_read, _, _ = select.select([sock], [], [], 2.0)
        if sock in ready_read:
            # receive content of the input buffer
            buf = sock.recv(256)
            # if there is content, let the frame consume it
            if len(buf) > 0:
                frame.consume(buf)
                # if the frame is complete, we're done
                if frame.complete():
                    break
            else:
                # the socket was closed by the device, exit
                raise ValueError("Could not receive data from socket.")

            # in case something (such as a "net.package") slips in, make sure to ignore
            #  all irelevant responses
            if frame.id != oid.object_id:
                raise ValueError(f'Got unexpected frame oid 0x{frame.id:08X}')

    # decode the frames payload
    value = decode_value(oid.response_data_type, frame.data)

    # and print the result:
    print(f'Response value: {value}')


def read_frame2(sock: socket.socket, oid_name):

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
    consumed = parser.parse(bytes_read)
    print(f'Consumed bytes: {consumed}, complete: {parser.complete}')
    if parser.complete:
        print(f'Command received: {parser._command}, crc ok: {parser._crc_ok}')
        value = decode_value(oid.response_data_type, parser._data)
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
        read_frame2(sock, oid)


if __name__ == '__main__':
    main()
