import argparse
import logging
import rct_parser
from rctclient.types import DataType
from rctclient.utils import decode_value  # , encode_value
from rctclient.registry import REGISTRY as Registry
from rctclient.exceptions import InvalidCommand


def main():
    parser = argparse.ArgumentParser(
        prog='parsedump',
        description='Read a file with captured frames and display parsed data',
    )
    parser.add_argument('-f', '--infile', help='file name of file with captured packages', required=True)
    parser.add_argument('-v', '--verbose', help='enable debug logging', action='store_true')

    parsed = parser.parse_args()

    if parsed.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.WARN)

    with open(parsed.infile, 'rb') as f:
        frame_buffer = f.read()
    print(f'read {len(frame_buffer)} bytes')
    finished = False
    mv = memoryview(frame_buffer)
    parser = rct_parser.FrameParser()
    counter = 0

    while not finished:
        pos = parser.current_pos
        try:
            frame = parser.parse(mv)
            finished = not parser.complete_frame
            if frame:
                if not frame.crc_ok:
                    print('Error: wrong CRC sum')
                oid = Registry.get_by_id(frame.oid)
                ftype = oid.response_data_type
                if ftype != DataType.UNKNOWN:
                    value = decode_value(ftype, frame.payload)
                else:
                    value = frame.payload.hex(' ')
                print(f'{counter:04}:: OID: {oid.name}, Value: {value}, type: {ftype} found at: {pos}')
                counter += 1

        except InvalidCommand as ex:
            print(f'Exception at {parser.current_pos}: {ex}')
            parser.current_pos += 1
            counter = 0

    print(f'Parsing finished at pos: {parser.current_pos} from {len(mv)}')


if __name__ == '__main__':
    main()
