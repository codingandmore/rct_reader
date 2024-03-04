import logging
import rct_parser
import rctclient.frame
from rctclient.types import Command, FrameType, DataType
from rctclient.utils import decode_value, encode_value


class Frame:
    def __init__(
        self,
        command: Command = Command.RESPONSE,
        dataType: DataType = DataType.INT32,
        value: any = -12345678,
        frame_type: FrameType = FrameType.STANDARD,
        frame_id: int = 42,
        address: int = 0,
    ):
        self.frame_id = frame_id
        self.address = address
        self.frame_type = frame_type
        self.value = value
        self.command = command
        self.dataType = dataType
        self.payload = encode_value(dataType, value)

    def make_frame(self) -> bytes:
        return rctclient.frame.make_frame(self.command, self.frame_id, self.payload, self.address,
            self.frame_type)


def check_int_response(frame: Frame, frame_bytes: bytes = None):
    if frame_bytes:
        frame_buffer = frame_bytes
    else:
        frame_buffer = frame.make_frame()
    parser = rct_parser.FrameParser(frame_buffer)
    parser.parse()
    assert parser.complete
    assert parser.crc_ok
    assert parser.id == frame.frame_id
    value = decode_value(frame.dataType, parser.data)
    assert value == frame.value


def test_parser_simple():
    frame = Frame()
    check_int_response(frame)


def test_parser_escaped_int():
    intValue = 0x2B000102
    frame = Frame(value=intValue)
    check_int_response(frame)
    intValue = 0x2D000102
    frame = Frame(value=intValue)
    check_int_response(frame)
    intValue = 0x2D00012B
    frame = Frame(value=intValue)
    check_int_response(frame)


def test_parser_leading_bytes():
    frame = Frame()
    test_frame = bytes.fromhex('00 00 00 00') + frame.make_frame()
    check_int_response(frame, test_frame)


def test_parser_leading_bytes_with_escaped_start_token():
    frame = Frame()
    test_frame = bytes.fromhex('00 2D 2B 00') + frame.make_frame()
    check_int_response(frame, test_frame)


def test_parser_incomplete_frame():
    frame = Frame()
    buffer = frame.make_frame()
    mid = int(len(buffer) / 2)
    buffer1 = bytearray(buffer[:mid])
    buffer2 = buffer[mid:]
    print(f'using frame: {buffer1} and {buffer2}')
    parser = rct_parser.FrameParser(buffer1)
    parser.parse()
    # should succeed but be inclompete now
    assert not parser.complete
    # now parse complete frame
    buffer1 += buffer2
    parser.parse()
    assert parser.complete
    assert parser.crc_ok
    value = decode_value(frame.dataType, parser.data)
    assert value == frame.value




if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    test_parser_two_frames()
