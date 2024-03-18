#!/usr/bin/env python3

import logging
import socket
from rctclient.frame import make_frame  # , ReceiveFrame
from rctclient.registry import REGISTRY as R
from rctclient.types import Command
from rctclient.utils import decode_value
from rct_parser import ResponseFrame
from rct_parser import FrameParser

# https://stackoverflow.com/questions/10742639/faster-sockets-in-python
log = logging.getLogger(__name__)


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
        self.parser = FrameParser()
        self.sock = None
        log.debug(f'Reader initialized with buffer size {buffer_size}')

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
            logging.debug(f'Sending command {oid}')
            self.sock.sendall(send_frame)

        self.read_frame(len(oids))
        return result

    def read_frames(self, oids: list[str]) -> list[ResponseFrame]:
        result = []
        for oid in oids:
            oid = R.get_by_name(oid)
            send_frame = make_frame(command=Command.READ, id=oid.object_id)
            logging.debug(f'Sending command {oid}')
            self.sock.sendall(send_frame)
            result += self.read_frame(1)
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
                    logging.debug(f'read bytes from socket: {bytes_read} to {buffer_pos}')
                except TimeoutError:
                    logging.debug('Timeout, exiting recv')
                    break
                if bytes_read == 0:
                    logging.debug(f'Disconnect with {len(responses)} response frames')
                    return responses  # no more data available, connection closed

                # create a new memory view for parser and reset pos:
                logging.debug(f'creating memory view from {buffer_pos} to {buffer_pos + bytes_read}')
                mv = memoryview(self.buffer)[0:buffer_pos + bytes_read]

            # try to parse next frame
            frame = self.parser.parse(mv)
            logging.debug(f'Parser complete: {self.parser.complete_frame}')
            if self.parser.complete_frame:
                frames_received += 1
                if no_frames > 0:
                    logging.debug(f'Received {frames_received}, expected: {no_frames}')
                    continue_parsing = frames_received < no_frames

                oid = R.get_by_id(frame.oid)
                logging.debug(f'Response frame received: {oid}, crc ok: {frame.crc_ok}')
                value = decode_value(oid.response_data_type, frame.payload)
                logging.debug(f'Value: {value}, type: {oid.response_data_type}')
                responses.append(frame)

                # if all bytes are consumed we can rewind buffer to read next chunk at buffer start:
                if self.parser.current_pos == buffer_pos + bytes_read:
                    logging.debug('Rewinding buffer')
                    buffer_pos = 0
                    bytes_read = 0
                    self.parser.rewinded()

            # rewind buffer if it fills up and copy remaining data then
            if buffer_pos + bytes_read > len(self.buffer) / 2:
                logging.debug("Enforce rewind, potential overflow")
                pos = self.parser.current_pos
                remaining_bytes = len(mv) - pos
                logging.debug(f'rewind buffer: {bytes_read=}, {pos=}, {len(mv)=}, {remaining_bytes=}')
                self.buffer[0:remaining_bytes] = mv[pos:]
                self.parser.rewinded()
                buffer_pos = 0
                bytes_read = len(mv) - pos
                mv = memoryview(self.buffer)[0:remaining_bytes]

        logging.debug("Finished parsing")
        return responses


