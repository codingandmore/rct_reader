
from typing import Tuple
from rctclient.types import Command, FrameType
from rctclient.utils import CRC16
from rctclient.exceptions import FrameCRCMismatch, InvalidCommand  # , FrameLengthExceeded

import logging
import struct

#: Token that starts a frame
START_TOKEN = ord('+')      # 0x2B
#: Token that escapes the next value
ESCAPE_TOKEN = ord('-')     # 0x2D
#: Length of the header
FRAME_LENGTH_HEADER = 1
#: Length of a command
FRAME_LENGTH_COMMAND = 1
#: Length of the length information
FRAME_LENGTH_LENGTH = 2
#: Length of a frame, contains 1 byte header, 1 byte command and 2 bytes length
FRAME_HEADER_WITH_LENGTH = FRAME_LENGTH_HEADER + FRAME_LENGTH_COMMAND + FRAME_LENGTH_LENGTH
#: Length of the CRC16 checkum
FRAME_LENGTH_CRC16 = 2

#: Amount of bytes we need to have a command
BUFFER_LEN_COMMAND = 2
log = logging.getLogger(__name__)


class ResponseFrame:
    def __init__(self,
            command: Command,
            oid: int,
            crc16: bool,
            crc_ok: bool,
            address: int,
            frame_length: int,
            frame_type: FrameType,
            payload: bytes,
    ):
        self.command = command
        self.oid = oid
        self.crc16 = crc16
        self.crc_ok = crc_ok
        self.address = address
        self.frame_length = frame_length
        self.frame_type = frame_type
        self.payload = payload


class FrameParser:
    def __init__(self, ignore_crc: bool = False):
        self.frame_len: int
        self.command: Command
        self._frame_length: int  # length of frame
        self.address: int
        self.id: int
        self.data: bytearray
        self._crc16: int
        self.crc_ok: bool
        self.complete_frame: bool
        self.start: int       # index of start token

        self.ignore_crc_mismatch: bool = ignore_crc
        self.current_pos: int = 0   # index where to start parsing next frame
        # set initially to the minimum length a frame header (i.e. everything before the data) can be.
        # 1 byte start, 1 byte command, 1 byte length, no address, 4 byte ID
        self._frame_header_length: int = 1 + 1 + 1 + 0 + 4
        self.escape_indexes = []
        self.reset()  # init all variables

    def reset(self):
        self.frame_len = 0
        self.command = Command._NONE  # pylint: disable=protected-access
        self.address: int = 0
        self.id: int = 0
        self.data = bytearray()
        self.start = -1
        self._frame_length = 0
        self._crc16 = 0
        self.crc_ok = False
        self.complete_frame = True
        self.escape_indexes = []

    def rewinded(self):
        self.current_pos = 0

    def _find_byte_tuple(self, data: bytes, byte_pair: bytes):
        pos = 0
        for pos in range(len(data) - 1):
            if data[pos] == byte_pair[0] and data[pos + 1] == byte_pair[1]:
                return pos
        return -1

    def _unescape_buffer_old(self, buffer: memoryview) -> memoryview:
        esc_seq = b'-+'
        new_buffer = buffer

        pos = self._find_byte_tuple(buffer, esc_seq)
        if pos >= 0:
            log.debug('Found escape sequence 1 at %d,', pos)
            new_buffer = buffer.tobytes().replace(esc_seq, b'+')
            new_buffer = memoryview(new_buffer)
        esc_seq = b'--'
        pos = self._find_byte_tuple(new_buffer, esc_seq)
        if pos >= 0:
            log.debug('Found escape sequence 2 at %d,', pos)
            new_buffer = new_buffer.tobytes().replace(esc_seq, b'-')
            new_buffer = memoryview(new_buffer)
        return new_buffer

    def _find_escaped_byte(self, data: bytes):
        pos = 0
        for pos in range(len(data) - 1):
            if data[pos] == ord(b'-') and (data[pos + 1] == ord(b'+') or data[pos + 1] == ord(b'-')):
                return pos
        return -1

    def _unescape_buffer(self, buffer: memoryview) -> memoryview:
        new_buffer = buffer

        while (pos := self._find_escaped_byte(new_buffer)) >= 0:
            log.debug('Found escape sequence at %d,', pos)
            new_buffer = new_buffer.tobytes()
            new_buffer = new_buffer[0:pos] + new_buffer[pos + 1:]
            new_buffer = memoryview(new_buffer)
            self.escape_indexes.append(pos)
        return new_buffer

    def parse(self, buffer: memoryview) -> Tuple[ResponseFrame, int]:
        frame: ResponseFrame = None
        frame_type: FrameType = None

        log.debug('Buffer length: %d: %s', len(buffer), buffer.hex(' '))
        log.debug('current pos: %d', self.current_pos)
        # start token not yet found, find it
        i = self.current_pos
        length = len(buffer)

        if self.complete_frame and self.current_pos < length:
            log.debug("trying to find next frame")
            self.reset()

        while self.start < 0 and i < length:
            c = buffer[i]
            log.debug('read: 0x%x at index %d', c, i)
            # sync to start_token
            if c == START_TOKEN:
                if i > 0 and buffer[i - 1] == ESCAPE_TOKEN:
                    log.debug('escaped start token found, ignoring')
                else:
                    log.debug('start token found')
                    self.start = i
            i += 1

        if self.start < 0:  # no start token found, exit
            log.debug('no start token invalid data received len:%d ', length)
            self.current_pos = length  # we do not scan garbage data next time
            self.complete_frame = False
            return None, length

        unescaped_buffer = memoryview(buffer)[self.start:]
        unescaped_buffer = self._unescape_buffer(unescaped_buffer)
        log.debug('Escaped buffer length: %d: %s', len(unescaped_buffer), unescaped_buffer.hex(' '))

        length = len(unescaped_buffer)
        i = 1  # index 0 is now start token
        log.debug('unescaped length: %d', length)

        c = unescaped_buffer[i]
        log.debug('read: 0x%x at index %d', c, i)

        if length - i >= BUFFER_LEN_COMMAND:
            try:
                self.command = Command(c)
            except ValueError as exc:
                raise InvalidCommand(str(exc), c, i) from exc

            if self.command == Command.EXTENSION:
                raise InvalidCommand('EXTENSION is not supported', c, i)

            log.debug('have command: 0x%x, is_plant: %s', self.command,
                            Command.is_plant(self.command))
            if Command.is_plant(self.command):
                self._frame_header_length += 4
                frame_type = FrameType.PLANT
                log.debug('plant frame, extending header length by 4 to %d',
                    self._frame_header_length)
            if Command.is_long(self.command):
                self._frame_header_length += 1
                frame_type = FrameType.STANDARD
                log.debug('long cmd, extending header length by 1 to %d',
                                    self._frame_header_length)
            i += 1
        if length >= self._frame_header_length:
            log.debug('buffer length %d indicates that it contains entire header',
                i)
            if Command.is_long(self.command):
                data_length = struct.unpack('>H', unescaped_buffer[i:i + 2])[0]
                address_idx = 4
            else:
                data_length = struct.unpack('>B', bytes([unescaped_buffer[i]]))[0]
                address_idx = 3
            log.debug('found data_length: %d bytes', data_length)
            if Command.is_plant(self.command):
                # length field includes address and id length == 8 bytes
                self._frame_length = (self._frame_header_length - 8) + data_length + FRAME_LENGTH_CRC16
                self.address = struct.unpack('>I', unescaped_buffer[address_idx:address_idx + 4])[0]
                oid_idx = address_idx + 4
                data_length -= 8  # includes length of oid and plant-id
            else:
                # length field includes id length == 4 bytes
                self._frame_length = (self._frame_header_length - 4) + data_length + FRAME_LENGTH_CRC16
                oid_idx = address_idx
                data_length -= 4  # includes length of oid

            log.debug('data_length: %d bytes, frame_length: %d', data_length,
                self._frame_length)
            self.id = struct.unpack('>I', unescaped_buffer[oid_idx:oid_idx + 4])[0]
            log.debug('oid index: %d, OID: 0x%X', oid_idx, self.id)
            i = oid_idx + 4
            log.debug('i is: %d', i)
        if self._frame_length > 0 and length >= self._frame_length:
            log.debug('buffer contains full frame, index: %d', i)
            self.data[:] = unescaped_buffer[i:i + data_length]
            log.debug('extracted data from: %d to %d: %s', i, i + data_length, self.data.hex(' '))
            i += data_length
            log.debug('crc i is: %d', i)
            self._crc16 = struct.unpack('>H', unescaped_buffer[i:i + 2])[0]
            calc_crc16 = CRC16(unescaped_buffer[1:i])
            self.crc_ok = self._crc16 == calc_crc16
            log.debug('crc: %04x calculated: %04x match: %s',
                self._crc16, calc_crc16, self.crc_ok)

            if not self.crc_ok and not self.ignore_crc_mismatch:
                raise FrameCRCMismatch('CRC mismatch', self._crc16, calc_crc16, i)
            log.debug('returning completed frame at %d', self._frame_length)
            self.current_pos += i + 2
            self.complete_frame = True
            frame = ResponseFrame(
                command=self.command,
                oid=self.id,
                crc16=calc_crc16,
                crc_ok=self.crc_ok,
                address=self.address,
                frame_length=self._frame_length,
                frame_type=frame_type,
                payload=self.data,
            )
        else:
            log.debug('frame is incomplete, stopping at %d', i)
            self.reset()
            self.complete_frame = False

        for escape_index in self.escape_indexes:
            if self.current_pos >= escape_index:
                self.current_pos += 1
        return frame, self.current_pos
