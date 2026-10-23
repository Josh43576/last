#!/usr/bin/env python3
"""
Read-only manual monitor for the Raspberry Pi power system.

Workflow:
1. Scan RFID card to start
2. Read electrical parameters from PZEM sensors via RS485
3. Print readings to the terminal (or view them in the web dashboard)
4. Check if power consumption exceeds preset limit
5. If limit exceeded, issue an alert and prompt for an admin acknowledgement
6. Return to step 1

This script is read-only: it does not operate relays or enforce a cutoff. Do not
run it alongside main.py because both would access the same serial bus.
"""

import time
import math
from sensors import read_all_pzem
from auth.rfid import read_rfid_id, is_authorized, AUTHORIZED_IDS

# Configuration
DEFAULT_PRESET_LIMIT = 500.0  # watts
APPLIANCE_NAMES = ["Appliance 1", "Appliance 2", "Appliance 3"]
PRESET_LIMIT_WATTS = DEFAULT_PRESET_LIMIT

# State tracking
power_limit_exceeded = False
last_total_power = 0.0


def get_total_power(readings):
    """Calculate total power consumption from all sensors."""
    total = 0.0
    for reading in readings:
        try:
            power = float(reading["power"])
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(power) and power >= 0:
            total += power
    return total


def check_power_limit(total_power):
    """Check if total power exceeds preset limit."""
    return total_power > PRESET_LIMIT_WATTS


def valid_power(reading):
    try:
        value = float(reading["power"])
    except (KeyError, TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def prompt_admin_reset():
    """
    Prompt admin to scan their RFID card to confirm reset.
    Returns True if authorized admin scans, False otherwise.
    """
    print("\n" + "="*60)
    print("POWER LIMIT ALERT (monitor only; no relay cutoff is performed)")
    print(f"Total Power: {last_total_power:.1f}W > Limit: {PRESET_LIMIT_WATTS}W")
    print("="*60)
    print("\nAdmin: Scan your RFID card to confirm reset and continue.")
    print("(Must be an authorized admin)")
    print()

    admin_id = read_rfid_id()
    if admin_id is None:
        print("No RFID card detected. Returning to scan mode.")
        return False

    if is_authorized(admin_id):
        print("✓ Admin authorized. Resetting limit check. Returning to scan mode.")
        return True
    else:
        print("✗ Admin card not authorized. Please use an authorized admin card.")
        print("Returning to scan mode.")
        return False


def wait_for_user_card():
    """Wait for a user RFID card to start the session."""
    print("\n" + "="*60)
    print("READY FOR USER")
    print("="*60)
    print("Waiting for user RFID card...")
    print()

    while True:
        card_id = read_rfid_id()
        if card_id is not None:
            return card_id
        time.sleep(1)


def read_and_report_sensors():
    """Read all PZEM sensors and report them in the terminal."""
    readings = read_all_pzem()
    print("\nReading sensors...")
    for index, data in enumerate(readings):
        name = APPLIANCE_NAMES[index] if index < len(APPLIANCE_NAMES) else f"Appliance {index + 1}"
        if data.get("error"):
            print(f"  {name} (ID {data.get('slave_id')}): ERROR - {data['error']}")
        else:
            print(f"  {name}: V={data['voltage']}V, I={data['current']}A, P={data['power']}W, E={data['energy']}Wh")

    return readings


def main():
    """Main loop for the manual interface."""
    global power_limit_exceeded, last_total_power

    print("\n" + "="*60)
    print("POWER MONITOR - MANUAL INTERFACE (RFID + LCD)")
    print("="*60)
    print(f"Preset Power Limit: {PRESET_LIMIT_WATTS}W")
    print(f"Authorized Admin IDs: {AUTHORIZED_IDS}")
    print()

    try:
        while True:
            # Step 1: Wait for user card
            user_card = wait_for_user_card()
            print(f"User card scanned: {user_card}")

            # Step 2: Read sensors
            readings = read_and_report_sensors()
            total_power = get_total_power(readings)
            last_total_power = total_power

            print(f"\n>>> Total Power Consumption: {total_power:.1f}W")

            # Step 3: Check power limit
            sensor_fault = any(valid_power(reading) is None for reading in readings)
            if sensor_fault:
                print("Sensor fault: incomplete data; this monitor cannot confirm a safe load.")
            if check_power_limit(total_power):
                print("⚠ ALERT: Power limit exceeded!")
                power_limit_exceeded = True

                # Step 4: Prompt admin reset
                while power_limit_exceeded:
                    if prompt_admin_reset():
                        power_limit_exceeded = False
                        break
                    else:
                        # Keep prompting until authorized
                        time.sleep(2)

            # Step 5: Return to step 1
            print("\nReady for next user.")
            time.sleep(2)

    except KeyboardInterrupt:
        print("\n\nShutdown requested.")
    except Exception as e:
        print(f"\nUnexpected error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("Manual interface stopped.")


if __name__ == "__main__":
    main()
