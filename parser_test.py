import logging
import rct_parser
import rctclient.frame
from rctclient.types import Command, FrameType, DataType
from rctclient.utils import decode_value, encode_value


def check_int_response(frame_id: int, address: int, frame_type: FrameType, intValue: int):
    command = Command.RESPONSE
    dt = DataType.INT32
    payload = encode_value(dt, intValue)
    data = rctclient.frame.make_frame(command, frame_id, payload, address, frame_type)
    parser = rct_parser.FrameParser(data)
    _ = parser.parse()
    assert parser.crc_ok
    assert parser.id == frame_id
    value = decode_value(dt, parser.data)
    assert value == intValue


def test_parser_simple():
    frame_id = 42
    address = 0
    frame_type = FrameType.STANDARD
    intValue = -12345678
    check_int_response(frame_id, address, frame_type, intValue)


def test_parser_escaped_int():
    frame_id = 42
    address = 0
    frame_type = FrameType.STANDARD
    intValue = 0x2B000102
    check_int_response(frame_id, address, frame_type, intValue)
    intValue = 0x2D000102
    check_int_response(frame_id, address, frame_type, intValue)
    intValue = 0x2D00012B
    check_int_response(frame_id, address, frame_type, intValue)


if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    test_parser_escaped_int()
