import logging
import socket
import rct_reader
from rctclient.utils import decode_value
from rctclient.registry import REGISTRY as Reg

test_buffer = bytearray([0x2b, 0x05, 0x08, 0x3c, 0x24, 0xf3, 0xe8, 0x00, 0x00, 0x00, 0x00, 0x94, 0x90])


# https://docs.pytest.org/en/7.1.x/how-to/monkeypatch.html
class SocketMock:
    def __init__(self, af, sock_type):
        pass

    def connect(self, address):
        pass

    def close(self):
        pass

    def settimeout(self, value):
        pass

    def recv_into(self, buffer, nbytes=..., flags=...):  # pylint: disable=W0613
        buffer[0:len(test_buffer)] = test_buffer
        return len(test_buffer)


def test_reader_simple(monkeypatch, caplog):
    def mocked_recv_into(af, sock_type):
        return SocketMock(af, sock_type)

    monkeypatch.setattr(socket, "socket", mocked_recv_into)

    caplog.set_level(logging.DEBUG)
    with rct_reader.RctReader('localhost', "8899") as reader:
        responses = reader.read_frame(1)
        resp = responses[0]
        assert len(responses) == 1
        oid = Reg.get_by_id(resp.oid)
        value = decode_value(resp.oid.response_data_type, resp.payload)
        print(f'Value: {value}, type: {oid.response_data_type}')
