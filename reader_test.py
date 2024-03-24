import logging
import socket

import pytest
import rct_reader
from rctclient.utils import decode_value, encode_value
from rctclient.frame import make_frame
from rctclient.registry import REGISTRY as Reg
from rctclient.types import Command, DataType


# https://docs.pytest.org/en/7.1.x/how-to/monkeypatch.html
class SocketMock:
    def __init__(self):
        self.counter = 0
        self.packets = None

    def connect(self, address):
        pass

    def close(self):
        pass

    def settimeout(self, value):
        pass

    def recv_into(self, buffer, nbytes=..., flags=...):  # pylint: disable=W0613
        if self.counter < len(self.packets):
            packet_to_send = self.packets[self.counter]
            buffer[0:len(packet_to_send)] = packet_to_send
            self.counter += 1
            return len(packet_to_send)
        else:
            return 0

    def set_receive_data(self, packets: list[bytes]):
        self.packets = packets
        self.counter = 0


@pytest.fixture
def mock_socket(monkeypatch):
    def mocked_recv_into(af, sock_type):    # pylint: disable=unused-argument
        return new_mock

    new_mock = SocketMock()
    monkeypatch.setattr(socket, "socket", mocked_recv_into)
    return new_mock


def test_reader_simple(mock_socket, caplog):  # pylint: disable=unused-argument, redefined-outer-name
    test_buffer = bytearray([0x2b, 0x05, 0x08, 0x3c, 0x24, 0xf3, 0xe8, 0x00, 0x00, 0x00, 0x00, 0x94, 0x90])
    caplog.set_level(logging.DEBUG)
    mock_socket.set_receive_data([test_buffer])
    with rct_reader.RctReader('localhost', "8899") as reader:
        responses = reader.recv_frame(1)
        resp = responses[0]
        assert len(responses) == 1
        oid = Reg.get_by_id(resp.oid)
        value = decode_value(oid.response_data_type, resp.payload)
        print(f'Value: {value}, type: {oid.response_data_type}')


# pylint: disable=redefined-outer-name
def create_frames_and_check_result(
        mock_socket,
        at_once: bool = False,
        with_garbage: bool = False,
        unknown_size: bool = False,
        cut_packet: bool = False,  # split packets so that recv get half frane
):
    p_acc_lp_objinfo = Reg.get_by_name('g_sync.p_acc_lp')
    p_acc_lp_value = 123.456
    bat_cycles_objinfo = Reg.get_by_name('battery.cycles')
    bat_cycles_value = 42

    test_packets = [
        make_frame(
            command=Command.RESPONSE,
            id=p_acc_lp_objinfo.object_id,
            payload=encode_value(p_acc_lp_objinfo.request_data_type, p_acc_lp_value)
        ),
        make_frame(
            command=Command.RESPONSE,
            id=bat_cycles_objinfo.object_id,
            payload=encode_value(bat_cycles_objinfo.request_data_type, bat_cycles_value)
        ),
    ]
    expected_frames = len(test_packets)

    if at_once:
        join_char = b'' if with_garbage else b'###'
        test_packets = [join_char.join(test_packets)]
    elif with_garbage:
        test_packets += bytearray([0x0, 0x1, 0x0])

    if not at_once and cut_packet:
        cut_point = int(len(test_packets[0]) / 2)
        packet1 = test_packets[0] + test_packets[1][0:cut_point]
        packet2 = test_packets[1][cut_point:]
        test_packets = [packet1, packet2]

    mock_socket.set_receive_data(test_packets)

    with rct_reader.RctReader('localhost', "8899") as reader:
        if unknown_size:
            responses = reader.recv_frame()
        else:
            responses = reader.recv_frame(expected_frames)

        assert len(responses) == expected_frames
        resp = responses[0]
        value = decode_value(p_acc_lp_objinfo.response_data_type, resp.payload)
        org_float_value = decode_value(DataType.FLOAT, encode_value(DataType.FLOAT, p_acc_lp_value))
        assert value == org_float_value
        resp = responses[1]
        value = decode_value(bat_cycles_objinfo.response_data_type, resp.payload)
        assert value == bat_cycles_value


def test_multiple_frames_one_by_one(mock_socket, caplog):
    caplog.set_level(logging.DEBUG)
    create_frames_and_check_result(mock_socket, False, False)


def test_multiple_frames_at_once(mock_socket, caplog):
    caplog.set_level(logging.DEBUG)
    create_frames_and_check_result(mock_socket, True, False)


def test_with_garbage_data(mock_socket, caplog):
    caplog.set_level(logging.DEBUG)
    create_frames_and_check_result(mock_socket, True, True)
    create_frames_and_check_result(mock_socket, False, True)


def test_with_unknown_size(mock_socket, caplog):
    caplog.set_level(logging.DEBUG)
    create_frames_and_check_result(mock_socket, False, False, True)
    create_frames_and_check_result(mock_socket, True, False, True)


def test_cut_frame_(mock_socket, caplog):
    caplog.set_level(logging.DEBUG)
    create_frames_and_check_result(mock_socket, False, False, cut_packet=True)


def test_buffer_rewind(mock_socket, caplog):
    caplog.set_level(logging.DEBUG)
    string_objinfo = Reg.get_by_name('inverter_sn')
    string_value = "1.2.3-0de83a78334c64250b18b5191f6cbd6b97e77f84+0de83a78334c64250b18b5191f6cbd6b97e77f84"
    bat_cycles_objinfo = Reg.get_by_name('battery.cycles')
    bat_cycles_value = 42

    test_packets = [
        make_frame(
            command=Command.RESPONSE,
            id=bat_cycles_objinfo.object_id,
            payload=encode_value(bat_cycles_objinfo.request_data_type, bat_cycles_value)
        ),
        make_frame(
            command=Command.RESPONSE,
            id=string_objinfo.object_id,
            payload=encode_value(string_objinfo.request_data_type, string_value)
        ),
        make_frame(
            command=Command.RESPONSE,
            id=bat_cycles_objinfo.object_id,
            payload=encode_value(bat_cycles_objinfo.request_data_type, bat_cycles_value)
        ),
    ]
    expected_frames = len(test_packets)

    test_packets[1] = test_packets[1] + b'###'  # add some garbage data to enforce copy remaining data
    mock_socket.set_receive_data(test_packets)

    with rct_reader.RctReader('dummy', "dummy", buffer_size=128) as reader:
        responses = reader.recv_frame(expected_frames)

        assert len(responses) == expected_frames
        resp = responses[0]
        value = decode_value(bat_cycles_objinfo.response_data_type, resp.payload)
        assert value == bat_cycles_value
        resp = responses[1]
        value = decode_value(string_objinfo.response_data_type, resp.payload)
        assert value == string_value
        resp = responses[2]
        value = decode_value(bat_cycles_objinfo.response_data_type, resp.payload)
        assert value == bat_cycles_value
