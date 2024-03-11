import logging
import rct_parser
import rctclient.frame
from rctclient.types import Command, FrameType, DataType
from rctclient.utils import decode_value, encode_value

LOREM = '''Lorem ipsum dolor sit amet, consetetur sadipscing elitr, sed
    diam nonumy eirmod tempor invidunt ut labore et dolore magna aliquyam
    erat, sed diam voluptua. At vero eos et accusam et justo duo dolores et
    ea rebum. Stet clita kasd gubergren, no sea takimata sanctus est Lorem
    ipsum dolor sit amet. Lorem ipsum dolor sit amet, consetetur sadipscing
    elitr, sed diam nonumy eirmod tempor invidunt ut labore et dolore magna
    aliquyam erat, sed diam voluptua. At vero eos et accusam et justo duo
    dolores et ea rebum. Stet clita kasd gubergren, no sea takimata sanctus
    est Lorem ipsum dolor sit amet.
'''


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


def check_response(frame: Frame, frame_bytes: bytes = None) -> rct_parser.FrameParser:
    if frame_bytes:
        frame_buffer = frame_bytes
    else:
        frame_buffer = frame.make_frame()
    frame_buffer = memoryview(frame_buffer)
    parser = rct_parser.FrameParser()
    parser.parse(frame_buffer)
    assert parser.complete
    assert parser.crc_ok
    assert parser.id == frame.frame_id
    value = decode_value(frame.dataType, parser.data)
    assert value == frame.value
    return parser


def test_parser_simple():
    frame = Frame()
    check_response(frame)


def test_parser_escaped_int():
    intValue = 0x2B000102
    frame = Frame(value=intValue)
    check_response(frame)
    intValue = 0x2D000102
    frame = Frame(value=intValue)
    check_response(frame)
    intValue = 0x2D00012B
    frame = Frame(value=intValue)
    check_response(frame)


def test_parser_leading_bytes():
    frame = Frame()
    test_frame = bytes.fromhex('00 00 00 00') + frame.make_frame()
    check_response(frame, test_frame)


def test_parser_leading_bytes_with_escaped_start_token():
    frame = Frame()
    test_frame = bytes.fromhex('00 2D 2B 00') + frame.make_frame()
    check_response(frame, test_frame)


def test_parser_garbage_data():
    test_frame = bytes.fromhex('00 00 FF FF 01')
    parser = rct_parser.FrameParser()
    test_frame = memoryview(test_frame)
    parser.parse(test_frame)
    assert not parser.complete
    assert not parser.data


def test_parser_incomplete_frame():
    frame = Frame()
    buffer = frame.make_frame()
    mid = int(len(buffer) / 2)
    buffer1 = bytearray(buffer[:mid])
    buffer2 = buffer[mid:]
    print(f'using frame: {buffer1} and {buffer2}')
    parser = rct_parser.FrameParser()
    parser.parse(memoryview(buffer1))
    # should succeed but be inclompete now
    assert not parser.complete
    # assume another socket read call receiving the remaining bytes
    buffer1 += buffer2
    # now parse complete frame
    parser.parse(memoryview(buffer1))
    assert parser.complete
    assert parser.crc_ok
    value = decode_value(frame.dataType, parser.data)
    assert value == frame.value


def test_parser_two_frames():
    frame = Frame()
    buffer = frame.make_frame()
    dbl_buffer = buffer + buffer
    parser = rct_parser.FrameParser()
    dbl_buffer = memoryview(dbl_buffer)
    parser.parse(dbl_buffer)
    assert parser.complete
    assert parser.crc_ok
    value = decode_value(frame.dataType, parser.data)
    assert value == frame.value
    # now parse again to get the second frame
    parser.parse(dbl_buffer)
    assert parser.complete
    assert parser.crc_ok
    value = decode_value(frame.dataType, parser.data)
    assert value == frame.value


def test_parser_plant_message():
    frame = Frame(
        command=Command.PLANT_RESPONSE,
        dataType=DataType.INT32,
        value=1234,
        frame_type=FrameType.PLANT,
        address=4711,
    )
    parser = check_response(frame)
    assert parser.address == frame.address


def test_parser_string_message():
    frame = Frame(
        command=Command.RESPONSE,
        dataType=DataType.STRING,
        value="Lorem ipsum dolor sit amet.",
        frame_type=FrameType.STANDARD,
    )
    parser = check_response(frame)
    assert parser.address == frame.address


def test_parser_float_message():
    f_val = 123456E-12
    # avoid rounding errors from double to float precision
    f_bytes = encode_value(DataType.FLOAT, f_val)
    f_val = decode_value(DataType.FLOAT, f_bytes)
    frame = Frame(
        command=Command.RESPONSE,
        dataType=DataType.FLOAT,
        value=f_val,
        frame_type=FrameType.STANDARD,
    )
    parser = check_response(frame)
    assert parser.address == frame.address


def test_long_frame():
    frame = Frame(
        command=Command.LONG_RESPONSE,
        dataType=DataType.STRING,
        value=LOREM,
        frame_type=FrameType.STANDARD,
    )
    parser = check_response(frame)
    assert parser.address == frame.address


def test_long_frame_plant():
    frame = Frame(
        command=Command.PLANT_LONG_RESPONSE,
        dataType=DataType.STRING,
        value=LOREM,
        frame_type=FrameType.PLANT,
        address=4711,
    )
    parser = check_response(frame)
    assert parser.address == frame.address


def test_buffer_rewind():
    frame = Frame()
    buffer = frame.make_frame()
    frame2 = Frame(value=456)
    buffer2 = frame2.make_frame()
    dbl_buffer = buffer + buffer2  # create two messages
    org_len = len(dbl_buffer)
    # parse first message
    parser = rct_parser.FrameParser()
    _, current_pos = parser.parse(dbl_buffer)
    # "rewind buffer by copying remaining bytes to front"
    assert type(buffer) is bytearray
    remaining_len = org_len - current_pos
    dbl_buffer[0:remaining_len] = dbl_buffer[current_pos:org_len]
    assert len(dbl_buffer) == org_len
    parser.rewinded()
    parser.parse(dbl_buffer)
    assert parser.complete
    assert parser.crc_ok
    value = decode_value(frame.dataType, parser.data)
    assert value == frame2.value


def test_parser_incomplete_second_frame():
    intValue = 0x2B000102  # escaped number
    frame1 = Frame(value=intValue)
    buffer1 = frame1.make_frame()
    frame2 = Frame(value=789)
    buffer2 = frame2.make_frame()
    mid = int(len(buffer2) / 2)
    buffer_total = buffer1 + buffer2
    parser = rct_parser.FrameParser()
    parser.parse(memoryview(buffer_total)[0:len(buffer1) + mid])
    assert parser.complete
    assert parser.crc_ok
    value = decode_value(frame1.dataType, parser.data)
    assert value == frame1.value
    assert parser.current_pos < len(buffer1) + mid
    assert buffer_total[parser.current_pos] == ord(b'+')
    assert parser.current_pos == 14

    # assume another socket read call receiving the remaining bytes
    mv = memoryview(buffer_total)[parser.current_pos:]
    # now parse complete frame
    print("parsing second frame")
    parser.parse(mv)
    assert parser.complete
    assert parser.crc_ok
    value = decode_value(frame2.dataType, parser.data)
    assert value == frame2.value


if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    #  test_parser_incomplete_frame()
    test_parser_incomplete_second_frame()
