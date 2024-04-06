#!/usr/bin/env python3

import logging
import socket
from typing import Callable

from rctclient.frame import make_frame  # , ReceiveFrame
from rctclient.registry import REGISTRY as R, ObjectInfo
from rctclient.types import Command, DataType
from rctclient.utils import decode_value
from rctclient.exceptions import FrameError
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

# The following ids are received by default every 30s without sending commands
# received in multiple chunks spread across the 30s interval
# predefined_readings = {
#     'battery.stored_energy',
#     'battery.used_energy',
#     'battery.voltage',
#     'battery.bms_software_version',
#     'battery.bms_sn',
#     'g_sync.p_acc_lp',
#     'battery.soc',
#     'battery.temperature',
#     'dc_conv.dc_conv_struct[0].u_sg_lp',
#     'dc_conv.dc_conv_struct[1].u_sg_lp',
#     'dc_conv.dc_conv_struct[0].p_dc_lp',
#     'dc_conv.dc_conv_struct[1].p_dc_lp',
#     'energy.e_ext_total',
#     'fault[0].flt',
#     'fault[1].flt',
#     'fault[2].flt',
#     'fault[3].flt',
#     'buf_v_control.power_reduction_max_solar_grid',
#     'energy.e_grid_feed_total',
#     'energy.e_grid_load_total',
#     'g_sync.p_ac_load_sum_lp',
#     'energy.e_load_total',
#     'g_sync.i_dr_eff[0]',
#     'g_sync.i_dr_eff[1]',
#     'g_sync.i_dr_eff[2]',
#     'g_sync.u_l_rms[0]',
#     'g_sync.u_l_rms[1]',
#     'g_sync.u_l_rms[2]',
#     'g_sync.p_ac_grid_sum_lp',
#     'grid_pll[0].f',
#     'energy.e_dc_total[0]',
#     'energy.e_dc_total[1]',
#     'energy.e_ac_total',
#     'io_board.s0_external_power',
#     'energy.e_ac_day',
#     'battery.stack_software_version[0]',
#     'battery.stack_software_version[1]',
#     'battery.stack_software_version[2]',
#     'battery.stack_software_version[3]',
#     'battery.stack_software_version[4]',
#     'battery.stack_software_version[5]',
#     'battery.stack_software_version[6]',
#     'energy.e_dc_day[0]',
#     'energy.e_dc_day[1]',
#     'energy.e_ext_day',
#     'energy.e_grid_feed_day',
#     'energy.e_grid_load_day',
#     'energy.e_load_day',
#     'prim_sm.island_flag',
#     'parameter_file',
#     'net.slave_data'
# }

MAX_FRAME_SIZE = 1024


class InvalidOidError(FrameError):
    '''
    Unknown OID Received in frame.

    :param message: A message describing the error.
    :param consumed_bytes: How many bytes were consumed.
    '''
    def __init__(self, message: str, consumed_bytes: int = 0) -> None:
        super().__init__(message)
        self.consumed_bytes = consumed_bytes


class RctReader:
    def __init__(
            self,
            host: str,
            port: str,
            timeout: float = 3.0,
            buffer_size: int = 2048,
            ignore_crc: bool = False
    ):
        self.buffer = bytearray(buffer_size)
        self.start = 0
        self.host = host
        self.port = port
        self.timeout = timeout
        self.parser = FrameParser(ignore_crc)
        self.sock = None
        self.on_frame_received = None
        self.rewind_threshold = min(MAX_FRAME_SIZE, buffer_size / 2)
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

    def register_callback(self, fn: Callable[[ResponseFrame], None]):
        self.on_frame_received = fn

    def read_frames(self, oid_names: set[str]) -> list[ResponseFrame]:
        result = []
        for oid_name in oid_names:  # frames_to_request:
            oid = R.get_by_name(oid_name)
            log.debug(f'Sending command {oid_name}')
            result.append(self._read_frame(oid))

        return result

    def read_frame(self, oid_name: str) -> ResponseFrame:
        oid = R.get_by_name(oid_name)
        return self._read_frame(oid)

    def _read_frame(self, oid: ObjectInfo, wanted_ids: set[int] = None) -> ResponseFrame:
        def on_received(frame: ResponseFrame):
            nonlocal response_frame
            if frame.oid in wanted_ids:
                log.debug('received wanted frame')
                response_frame = frame
            else:
                log.debug("discarding unwanted frame")

        if wanted_ids is None:
            wanted_ids = {oid.object_id}
        response_frame = None
        self.register_callback(on_received)
        if oid:
            send_frame = make_frame(command=Command.READ, id=oid.object_id)
            log.debug(f'Sending command {oid.name}')
            self.sock.sendall(send_frame)
        while not response_frame:
            self.recv_frame(1)
        return response_frame

    def recv_frame(self, no_frames: int = 0) -> list[ResponseFrame]:
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
                    log.debug(f'read bytes from socket: {bytes_read} to {buffer_pos}')
                except TimeoutError:
                    log.warning('Timeout, exiting recv')
                    raise
                if bytes_read == 0:
                    log.debug(f'Disconnect with {len(responses)} response frames')
                    return responses  # no more data available, connection closed

                # create a new memory view for parser and reset pos:
                log.debug(f'creating memory view from {buffer_pos} to {buffer_pos + bytes_read}')
                mv = memoryview(self.buffer)[0:buffer_pos + bytes_read]

            # try to parse next frame
            frame = self.parser.parse(mv)
            log.debug(f'Parser complete: {self.parser.complete_frame}')
            if self.parser.complete_frame:
                frames_received += 1
                if no_frames > 0:
                    log.debug(f'Received {frames_received}, expected: {no_frames}')
                    continue_parsing = frames_received < no_frames

                try:
                    oid = R.get_by_id(frame.oid)
                    log.debug(f'Response frame received: {oid}, crc ok: {frame.crc_ok}')
                    if log.getEffectiveLevel() == logging.DEBUG:
                        if oid.response_data_type != DataType.UNKNOWN:
                            try:
                                value = decode_value(oid.response_data_type, frame.payload)
                            except KeyError as ex:
                                log.debug(f'Error when decoding frame: {ex}')
                                value = frame.payload.hex(' ')
                        else:
                            value = frame.payload.hex(' ')
                        log.debug(f'Value: {value}, type: {oid.response_data_type}')
                except KeyError as ex:
                    msg = f'Unknown OID received: {frame.oid:04x}'
                    self.parser.log_state_into_file(msg, mv)
                    raise InvalidOidError(msg) from ex
                if self.on_frame_received:
                    self.on_frame_received(frame)
                else:
                    responses.append(frame)

                # if all bytes are consumed we can rewind buffer to read next chunk at buffer start:
                if self.parser.current_pos == buffer_pos + bytes_read:
                    log.debug('Rewinding buffer')
                    buffer_pos = 0
                    bytes_read = 0
                    self.parser.rewinded()

            # rewind buffer if it fills up and copy remaining data then
            if buffer_pos + bytes_read > len(self.buffer) - self.rewind_threshold:
                log.debug("Enforce rewind, potential overflow")
                pos = self.parser.current_pos
                remaining_bytes = len(mv) - pos
                log.debug(f'rewind buffer: {bytes_read=}, {pos=}, {len(mv)=}, {remaining_bytes=}')
                self.buffer[0:remaining_bytes] = mv[pos:]
                self.parser.rewinded()
                buffer_pos = 0
                bytes_read = len(mv) - pos
                mv = memoryview(self.buffer)[0:remaining_bytes]

        log.debug("Finished parsing")
        return responses
