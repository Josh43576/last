# PZEM-004T V4 setup for the Raspberry Pi reader

## Confirmed project settings

- The reader uses `/dev/ttyUSB0`, 9600 baud, 8 data bits, no parity, and 1 stop bit.
- `sensors/pzem.py` polls Modbus IDs 1, 2, and 3 sequentially.
- The Pi sees the USB serial adapter at `/dev/ttyUSB0`; this confirms Linux detects a serial device, not that the meters answer.
- Peacefair identifies PZEM-004T V4 as a TTL UART model. `TTL485-V2.0` is a TTL-to-RS485 converter, not the meter or its Modbus address.
- A V4-targeted implementation uses function 0x04 and the same measurement register addresses as this reader. Do not change the reader to the conflicting register table from non-vendor pages without confirming against the exact meter documentation.

## Communication topology

Each meter's TTL interface must go to its own TTL485 converter. Join the converters' RS485 A/B sides to the RS485 bus, and connect that bus to a USB-to-RS485 adapter at the Pi. Confirm the terminal labels and orientation from the exact converter documentation. Stop `main.py` before communication wiring checks; have a qualified electrician handle work near the AC side. Never change wiring while energized.

## Assign unique addresses

The V4 default address is 1. Program one meter at a time; all three must not be connected to the communication bus during an address change. The address utility uses Modbus function `0x06` (Write Single Register) to set register `0x0002` to the new ID. The meter manual reserves command `0x41` for calibration; do not use it for address changes.

On the Pi, stop `main.py`, then from `~/last` run one command per meter. Keep only the target meter connected to the RS485 communication bus during each operation:

```bash
python3 configure_pzem_address.py --port /dev/ttyUSB0 --current-id 1 --new-id 2
python3 configure_pzem_address.py --port /dev/ttyUSB0 --current-id 1 --new-id 3
```

The first meter remains at ID 1 and needs no address change. The script requires typing `ONE PZEM ONLY`, sends the V4 address command, then verifies a voltage-register response at the new ID. If verification fails, stop and inspect the adapter, address, and bus; do not keep sending write commands.

After programming, reconnect the three-meter bus and confirm IDs 1, 2, and 3 are unique. Start `main.py` with `BASELINE_MODE=1` and confirm clean readings for every ID before enabling any relay control.

References:

- [Peacefair PZEM-004T V4 product page](https://en.peacefair.cn/product/772.html)
- [PZEM-004T register and address-setting manual](https://eleceasy.com/uploads/short-url/8VLmeLXH3Apgu5QvI9emrjlBmuV.pdf)
