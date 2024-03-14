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
from rct_parser import ResponseFrame
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
    def __init__(self, host: str, port: str, timeout: float = 3.0, buffer_size: int = 2048):
        self.buffer = bytearray(buffer_size)
        self.start = 0
        self.host = host
        self.port = port
        self.timeout = timeout
        self.parser = rct_parser.FrameParser()
        self.sock = None

    def __enter__(self):
        # open the socket and connect to the remote device:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect((self.host, self.port))
        return self

    def __exit__(self, exc_type, exc_value, exc_traceback):
        self.sock.close()
        return False

    def read_frames_send_all_before_receive(self, oids: list[str]):
        result = []
        for oid in oids:
            oid = R.get_by_name(oid)
            send_frame = make_frame(command=Command.READ, id=oid.object_id)
            print(f'Sending command {oid}')
            self.sock.sendall(send_frame)

        self.read_frame(len(oids))
        return result

    def read_frames(self, oids: list[str]):
        result = []
        for oid in oids:
            oid = R.get_by_name(oid)
            send_frame = make_frame(command=Command.READ, id=oid.object_id)
            print(f'Sending command {oid}')
            self.sock.sendall(send_frame)
            self.read_frame(1)
        return result

    def read_frame(self, no_frames: int = 0) -> list[ResponseFrame]:
        # query information about an object ID (here: battery.soc):
        bytes_read = self.parser.current_pos
        buffer_pos = 0
        frames_received = 0
        continue_parsing = True
        mv: memoryview = None
        responses: list[ResponseFrame] = []

        # continue parsing until either all expected frames are received or
        # a timeout occurs and no more data are available:
        while continue_parsing:
            pos = self.parser.current_pos
            # read next chunk if remaining bytes in buffer are an incomplete
            # frame or buffer is empty:
            if (not self.parser.complete_frame) or pos == bytes_read:
                buffer_pos += bytes_read
                socket_buffer_view = memoryview(self.buffer)[buffer_pos:len(self.buffer)]
                try:
                    bytes_read = self.sock.recv_into(socket_buffer_view, len(socket_buffer_view))
                    print(f'read bytes from socket: {bytes_read} to {buffer_pos}')
                except TimeoutError:
                    print('Timeout, exiting recv')
                    break
                if bytes_read == 0:
                    print(f'Disconnect with {len(responses)} response frames')
                    return responses  # no more data available, connection closed

                # create a new memory view for parser and reset pos:
                print(f'creating memory view from {buffer_pos} to {buffer_pos + bytes_read}')
                mv = memoryview(self.buffer)[0:buffer_pos + bytes_read]

            # try to parse next frame
            frame = self.parser.parse(mv)
            print(f'Parser complete: {self.parser.complete_frame}')
            if self.parser.complete_frame:
                frames_received += 1
                if no_frames > 0:
                    print(f'Received {frames_received}, expected: {no_frames}')
                    continue_parsing = frames_received < no_frames

                oid = R.get_by_id(frame.oid)
                print(f'Response frame received: {oid}, crc ok: {frame.crc_ok}')
                value = decode_value(oid.response_data_type, frame.payload)
                print(f'Value: {value}, type: {oid.response_data_type}')
                responses.append(frame)

                # if all bytes are consumed we can rewind buffer to read next chunk at buffer start:
                if self.parser.current_pos == bytes_read:
                    print('Rewinding buffer')
                    buffer_pos = 0
                    bytes_read = 0
                    self.parser.rewinded()

            # rewind buffer if it fills up and copy remaining data then
            if buffer_pos + bytes_read > len(self.buffer) / 2:
                print("Enforce rewind, potential overflow")
                pos = self.parser.current_pos
                self.buffer[0:bytes_read - pos] = mv[pos:bytes_read]
                self.parser.rewinded()
                mv = memoryview(self.buffer)[0:bytes_read - pos]
                buffer_pos = 0
                bytes_read = bytes_read - pos

        print("Finished parsing")
        return responses


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
        oids = ['g_sync.p_acc_lp', 'g_sync.p_ac_load_sum_lp', 'g_sync.p_ac_grid_sum_lp', 'battery.soc',
                'inv_struct.cosinus_phi']
        # reader.read_frames(oids)
        reader.read_frames_send_all_before_receive(oids)


if __name__ == '__main__':
    main()
