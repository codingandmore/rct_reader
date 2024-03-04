
from rctclient.types import Command  # , FrameType
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


class FrameParser:

    def __init__(self, buffer: bytearray, ignore_crc: bool = False):
        self.frame_len: int
        self.complete: bool  # true if complete frame is read
        self.command: Command
        self._frame_length: int  # length of frame
        self.address: int
        self.id: int
        self.data: bytes
        self._crc16: int
        self.crc_ok: bool
        self.ignore_crc_mismatch: bool = ignore_crc
        self.buffer = buffer
        self.start: int = -1        # index of start token
        self.current_pos: int = 0   # index where to start parsing next frame
        # set initially to the minimum length a frame header (i.e. everything before the data) can be.
        # 1 byte start, 1 byte command, 1 byte length, no address, 4 byte ID
        self._frame_header_length: int = 1 + 1 + 1 + 0 + 4

        self._reset()  # init all variables

    def _reset(self):
        self.frame_len = 0
        self.complete = False
        self.command = Command._NONE  # pylint: disable=protected-access
        self.address: int = 0
        self.id: int = 0
        self.data = b''
        self._frame_length = 0
        self._crc16 = 0
        self.crc_ok = False
        self.ignore_crc_mismatch = False

    def _unescape_buffer(self):
        esc_seq = b'-+'
        new_buffer = self.buffer

        pos = self.buffer.find(esc_seq, self.start)
        if pos >= 0:
            log.debug('Found escape sequence 1 at %d,', pos)
            new_buffer = self.buffer.replace(esc_seq, b'+')
        esc_seq = b'--'
        pos = new_buffer.find(esc_seq, self.start)
        if pos >= 0:
            log.debug('Found escape sequence 2 at %d,', pos)
            new_buffer = new_buffer.replace(esc_seq, b'-')
        return new_buffer

    def parse(self):
        log.debug('Buffer length: %d: %s', len(self.buffer), self.buffer.hex(' '))

        # start token not yet found, find it
        i = self.current_pos
        length = len(self.buffer)

        if self.complete and self.current_pos < length:
            log.debug("trying to find next frame")
            self._reset()

        while self.start < 0 and i < length:
            c = self.buffer[i]
            log.debug('read: 0x%x at index %d', c, i)
            # sync to start_token
            if c == START_TOKEN:
                if i > 0 and self.buffer[i - 1] == ESCAPE_TOKEN:
                    log.debug('escaped start token found, ignoring')
                else:
                    log.debug('start token found')
                    self.start = i
            i += 1

        if self.start < 0:  # no start token found, exit
            log.debug('no start token invalid data received len:%d ', length)
            self.current_pos = length  # we do not scan garbage data next time
            return

        unescaped_buffer = self._unescape_buffer()
        unescaped_buffer = memoryview(unescaped_buffer)[self.start:]
        length = len(unescaped_buffer)
        i = 1  # index 0 is now start token
        log.debug('unescaped length: %d', length)

        c = unescaped_buffer[i]
        log.debug('read: 0x%x at index %d', c, i)

        if (length - i >= BUFFER_LEN_COMMAND and
                self.command == Command._NONE):  # pylint: disable=protected-access
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
                log.debug('plant frame, extending header length by 4 to %d',
                    self._frame_header_length)
            if Command.is_long(self.command):
                self._frame_header_length += 1
                log.debug('long cmd, extending header length by 1 to %d',
                                    self._frame_header_length)
            i += 1
        if length >= self._frame_header_length and self._frame_length == 0:
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
            else:
                # length field includes id length == 4 bytes
                self._frame_length = (self._frame_header_length - 4) + data_length + FRAME_LENGTH_CRC16
                oid_idx = address_idx

            log.debug('data_length: %d bytes, frame_length: %d', data_length,
                self._frame_length)
            self.id = struct.unpack('>I', unescaped_buffer[oid_idx:oid_idx + 4])[0]
            log.debug('oid index: %d, OID: 0x%X', oid_idx, self.id)
            i = oid_idx + 4
            log.debug('i is: %d', i)
        if self._frame_length > 0 and length >= self._frame_length:
            data_length -= 4  # includes length of oid
            log.debug('buffer contains full frame, index: %d', i)
            self.data = unescaped_buffer[i : i + data_length]
            self.complete = True
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
            self.current_pos = i + 2
        else:
            log.debug('frame is incomplete, stopping at %d', i)
            self._reset()
