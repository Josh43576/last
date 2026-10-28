#!/usr/bin/env python3
"""Set the Modbus address of one isolated PZEM-004T V4 meter."""
import argparse

from sensors.pzem import PZEM_PORTS, set_pzem_address


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default=PZEM_PORTS[0], help="USB-RS485 serial port (default: /dev/ttyUSB0)")
    parser.add_argument("--current-id", type=int, required=True, help="meter's current Modbus ID")
    parser.add_argument("--new-id", type=int, required=True, help="new Modbus ID (1..247)")
    args = parser.parse_args()

    print("PZEM-004T V4 address configuration")
    print(f"Port: {args.port}; requested address change: {args.current_id} -> {args.new_id}")
    print("Stop main.py and disconnect every other meter from the RS485 communication bus.")
    print("Only the target meter may be connected when changing its address.")
    confirmation = input('Type "ONE PZEM ONLY" to continue: ').strip()
    if confirmation != "ONE PZEM ONLY":
        print("Cancelled; no address command was sent.")
        return 1

    try:
        set_pzem_address(args.port, args.current_id, args.new_id)
    except Exception as exc:
        print(f"Address change could not be verified: {exc}")
        return 2

    print(f"Verified: PZEM responded at Modbus ID {args.new_id}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
