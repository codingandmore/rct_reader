import argparse
import socket
from datetime import datetime


def main():
    # host = 'HF-A21.fritz.box'
    buffer = bytearray(2048)

    parser = argparse.ArgumentParser(
        prog='rct-dump',
        description='Listen on the RCT socket and dump received frames to a file',
    )
    parser.add_argument('--host', help='host-name or IP of device', required=True)
    parser.add_argument('--port', default=8899, help='Port to connect to, default 8899')
    parser.add_argument('-f', '--outfile', help='file name for output', required=True)
    parsed = parser.parse_args()

    print(f'Capturing packets now to file {parsed.outfile}.')
    print('Press Ctrl-C/Cmd-C to stop.')
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        with open(parsed.outfile, 'wb') as f:
            sock.connect((parsed.host, parsed.port))
            while True:
                bytes_read = sock.recv_into(buffer, len(buffer))
                now = datetime.now()
                print(f'{now.strftime("%H:%M:%S")}: read bytes from socket: {bytes_read}')
                mv = memoryview(buffer)[0:bytes_read]
                print(f'Buffer length: {bytes_read}: {mv.hex(" ")}')
                f.write(mv)


if __name__ == '__main__':
    main()
