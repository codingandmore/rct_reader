# RCT Tools to read data from RCT inverters

This repository is based on [python-rctclient](https://github.com/svalouch/python-rctclient). It enhances it by:

* providing an improved packet parser,
* a tool to send commands and see the response
* a tool to listen what data the inverter sends periodically
* a tool to store data from inverter to InfluxDB
* a script to capture data sent from inverter to a file
* a script to parse captured files and see the data

## Improvements to python-rctclient

* The parser script interfers with data sent from the inverter by default. This potentially occurs only on newer RCT firmware versions.
* The original parser does not work reliably in all situations.
* The original parser does copy operations with received data not being necessary.
* Unit tests for parsing and reading data

## Notes

This project is work-in-progress. The manufactures does not support any API level access and does not document
the protocol. It might be possible to damage the device by sending certain commands. Use at your own risk!

It is intended to submit the enhancements and fixes to the original project.

This project uses the licenses of the original project which is [GPL Version 3](LICENSE.txt)

## Tools

### pv_reader.py

`pv_reader.py` is tool for sending commands and displayig values. Optionally data can be sent to an InfluxDB.

```
usage: rct-reader [-h] --host HOST [--port <port>] [--influx-host INFLUX_HOST] [--influx-port INFLUX_PORT] [--listen-only] [--command COMMAND] [-v]

Read data from RCT inverter

options:
  -h, --help            show this help message and exit
  --host HOST           host-name or IP of device
  --port <port>         Port to connect to, default 8899
  --influx-host INFLUX_HOST
                        host of influxdb database
  --influx-port INFLUX_PORT
                        port of influxdb database
  --listen-only         debug do not send commands
  --command COMMAND     send single command to device
  -v, --verbose         enable debug logging

```

Examples:

`python pv_reader.py --host=HF-A21.fritz.box --influx-host=localhost`

Send periodically commands to the inverter and store the received values into the Influx database. The set of parameters to read and the field names in the InfluxDB can be changed in the file `pv_reader.py`

`python pv_reader.py --host=HF-A21.fritz.box`
Send periodically commands to the inverter and display the received values. The set of parameters to read can be changed in the file `pv_reader.py`

`python pv_reader.py --host=HF-A21.fritz.box --listen-only`
Listen to the inverter for periodically sent packets and display the received values.

### dump.py

dump.py is a script to capture data from the inverter sent periodically. Newer firmware version send data by default periodically.

```
usage: rct-dump [-h] --host HOST [--port PORT] -f OUTFILE

Listen on the RCT socket and dump received frames to a file

options:
  -h, --help            show this help message and exit
  --host HOST           host-name or IP of device
  --port PORT           Port to connect to, default 8899
  -f OUTFILE, --outfile OUTFILE
                        file name for output
```

Example:

`python dump.py --host HF-A21.fritz.box --outfile dump.bin`

or

`python dump.py --host 192.168.178.56 --outfile dump.bin`

### parsedump.py

`parsedump.py` reads a file with captured data and displays the received
data.

```
usage: parsedump [-h] -f INFILE [-v]

Read a file with captured frames and display parsed data

options:
  -h, --help            show this help message and exit
  -f INFILE, --infile INFILE
                        file name of file with captured packages
  -v, --verbose         enable debug logging
```
