import socket
from datetime import datetime


def main():
    host = 'HF-A21.fritz.box'
    port = 8899
    buffer = bytearray(2048)

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        with open('dump.bin', 'wb') as f:
            sock.connect((host, port))
            while True:
                bytes_read = sock.recv_into(buffer, len(buffer))
                now = datetime.now()
                print(f'{now.strftime("%H:%M:%S")}: read bytes from socket: {bytes_read}')
                mv = memoryview(buffer)[0:bytes_read]
                print(f'Buffer length: {bytes_read}: {mv.hex(" ")}')
                f.write(mv)


if __name__ == '__main__':
    main()
