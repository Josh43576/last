#!/usr/bin/env python3
"""Compatibility wrapper for the project's shared PZEM Modbus reader."""
import time

from sensors.pzem import PZEM_PORTS, PZEM_SLAVE_IDS, prompt_for_slave_id, read_pzem

SERIAL_PORT = PZEM_PORTS[0]
PZEM_IDS = list(PZEM_SLAVE_IDS)


def read_pzem_module(slave_id, port=SERIAL_PORT):
    return read_pzem(port, slave_id=slave_id)


def print_module_data(data):
    print(f"\n--- PZEM Slave ID {data.get('slave_id', '?')} ---")
    if data.get("error"):
        print(f"ERROR: {data['error']}")
    for key, unit in (("voltage", "V"), ("current", "A"), ("power", "W"), ("energy", "Wh")):
        value = data.get(key)
        print(f"{key.title()}: {value if value is not None else 'unavailable'} {unit}")


def main():
    print(f"Reading shared Modbus bus on {SERIAL_PORT}; available slave IDs: {PZEM_IDS}")
    while True:
        selected_slave = prompt_for_slave_id(PZEM_IDS)
        print(f"\nTesting branch/slave ID {selected_slave} on {SERIAL_PORT}...")
        print_module_data(read_pzem_module(selected_slave))
        again = input("Test another branch? [y/N]: ").strip().lower()
        if again not in {"y", "yes"}:
            print("Stopping PZEM branch test.")
            break
        print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopping PZEM reader.")
