
from datetime import datetime
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
        self.ignore_crc_mismatch: bool = ignore_crc
        self.complete_frame: bool
        self.current_pos: int = 0   # index where to start parsing next frame
        self.escape_indexes = []
        self.reset()  # init all variables

    def reset(self):
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
            log.debug(f'Found escape sequence 1 at {pos}')
            new_buffer = buffer.tobytes().replace(esc_seq, b'+')
            new_buffer = memoryview(new_buffer)
        esc_seq = b'--'
        pos = self._find_byte_tuple(new_buffer, esc_seq)
        if pos >= 0:
            log.debug(f'Found escape sequence 2 at {pos}')
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
            log.debug(f'Found escape sequence at {pos}')
            new_buffer = new_buffer.tobytes()
            new_buffer = new_buffer[0:pos] + new_buffer[pos + 1:]
            new_buffer = memoryview(new_buffer)
            self.escape_indexes.append(pos + self.current_pos)
        return new_buffer

    def log_state_into_file(self, msg: str, buffer: memoryview):
        now = datetime.now()
        fname = f'{now.strftime("%Y:%m:%d-%H:%M:%S")}-parserstate.log'
        with open(fname, 'w', encoding='utf-8') as f:
            f.write(f'Exception: {msg}\n')
            f.write(f'Buffer Length: {buffer}\n')
            f.write(f'Current pos: {self.current_pos}\n')
            f.write('Buffer:\n')
            f.write(f'{buffer.hex(" ")}\n')
            f.write(f'Escape Indexes: {self.escape_indexes}\n')

    def parse(self, buffer: memoryview) -> ResponseFrame:
        frame: ResponseFrame = None
        frame_type: FrameType = None
        data = bytearray()
        address: int = 0
        frame_length: int = 0
        crc_ok: bool = False
        # set initially to the minimum length a frame header (i.e. everything before the data) can be.
        # 1 byte start, 1 byte command, 1 byte length, no address, 4 byte ID
        frame_header_length: int = 1 + 1 + 1 + 0 + 4

        log.debug(f'Buffer length: {len(buffer)}: {buffer.hex(" ")}')
        log.debug(f'current pos: {self.current_pos}')
        # start token not yet found, find it
        i = self.current_pos
        start = -1
        length = len(buffer)
        if self.complete_frame and self.current_pos < length:
            log.debug("trying to find next frame")
            self.reset()

        while start < 0 and i < length:
            c = buffer[i]
            log.debug(f'read: 0x{c:02x} at index {i}')
            # sync to start_token
            if c == START_TOKEN:
                if i > 0 and buffer[i - 1] == ESCAPE_TOKEN:
                    log.debug('escaped start token found, ignoring')
                else:
                    j = i + 1
                    while j < length and buffer[j] == START_TOKEN:
                        j += 1  # there are special "end of block" markers 2B 2B 2B" -> skip
                    if j == i + 1:  # no more following 1Bs -> start token found
                        log.debug('start token found')
                        start = i
                    else:
                        i = j      # skip 1B sequence
            i += 1

        if start < 0:  # no start token found, exit
            log.debug(f'no start token invalid data received len: {length}')
            self.current_pos = length  # we do not scan garbage data next time
            self.complete_frame = False
            return None

        start_token_pos = i - 1
        unescaped_buffer = memoryview(buffer)[start:]
        unescaped_buffer = self._unescape_buffer(unescaped_buffer)
        log.debug(f'Escaped buffer length: {len(unescaped_buffer)}: {unescaped_buffer.hex(" ")}',)

        length = len(unescaped_buffer)
        i = 1  # index 0 is now start token
        log.debug(f'unescaped length: {length}')

        if i < length:
            c = unescaped_buffer[i]
            log.debug(f'read: 0x{c:02x} at index {i}')

        if length - i >= BUFFER_LEN_COMMAND:
            try:
                command = Command(c)
            except ValueError as exc:
                self.log_state_into_file(str(exc), buffer)
                raise InvalidCommand(str(exc), c, i) from exc

            if command == Command.EXTENSION:
                raise InvalidCommand('EXTENSION is not supported', c, i)

            log.debug(f'have command: 0x{command:02x}, is_plant: {Command.is_plant(command)}')
            if Command.is_plant(command):
                frame_header_length += 4
                frame_type = FrameType.PLANT
                log.debug(f'plant frame, extending header length by 4 to {frame_header_length}')
            if Command.is_long(command):
                frame_header_length += 1
                frame_type = FrameType.STANDARD
                log.debug(f'long cmd, extending header length by 1 to {frame_header_length}')
            i += 1
        if length >= frame_header_length:
            log.debug(f'buffer length {i} indicates that it contains entire header')
            if Command.is_long(command):
                data_length = struct.unpack('>H', unescaped_buffer[i:i + 2])[0]
                address_idx = 4
            else:
                data_length = struct.unpack('>B', bytes([unescaped_buffer[i]]))[0]
                address_idx = 3
            log.debug(f'found data_length: {data_length} bytes')
            if Command.is_plant(command):
                # length field includes address and id length == 8 bytes
                frame_length = (frame_header_length - 8) + data_length + FRAME_LENGTH_CRC16
                address = struct.unpack('>I', unescaped_buffer[address_idx:address_idx + 4])[0]
                oid_idx = address_idx + 4
                data_length -= 8  # includes length of oid and plant-id
            else:
                # length field includes id length == 4 bytes
                frame_length = (frame_header_length - 4) + data_length + FRAME_LENGTH_CRC16
                oid_idx = address_idx
                data_length -= 4  # includes length of oid

            log.debug(f'data_length: {data_length} bytes, frame_length: {frame_length}')
            oid = struct.unpack('>I', unescaped_buffer[oid_idx:oid_idx + 4])[0]
            log.debug(f'oid index: {oid_idx}, OID: 0x{oid:02x}')
            i = oid_idx + 4
            log.debug(f'i is: {i}')
        if frame_length > 0 and length >= frame_length:
            log.debug(f'buffer contains full frame, index: {i}')
            data[:] = unescaped_buffer[i:i + data_length]
            log.debug(f'extracted data from: {i} to {i + data_length}: {data.hex(" ")}')
            i += data_length
            log.debug(f'crc i is: {i}')
            crc16 = struct.unpack('>H', unescaped_buffer[i:i + 2])[0]
            calc_crc16 = CRC16(unescaped_buffer[1:i])
            crc_ok = crc16 == calc_crc16
            log.debug(f'crc: {crc16:04x} calculated: {calc_crc16:04x} match: {crc_ok}')

            if not crc_ok and not self.ignore_crc_mismatch:
                raise FrameCRCMismatch('CRC mismatch', crc16, calc_crc16, i)
            self.current_pos = start_token_pos + i + 2
            self.complete_frame = True
            log.debug(f'returning completed frame, len: {frame_length}, start pos: {start_token_pos}, '
                f'next pos: {self.current_pos}')
            frame = ResponseFrame(
                command=command,
                oid=oid,
                crc16=calc_crc16,
                crc_ok=crc_ok,
                address=address,
                frame_length=frame_length,
                frame_type=frame_type,
                payload=data,
            )
        else:
            log.debug(f'frame is incomplete, stopping at {i}')
            self.reset()
            self.complete_frame = False

        for escape_index in self.escape_indexes:
            if self.current_pos >= escape_index:
                self.current_pos += 1
                break
        return frame
