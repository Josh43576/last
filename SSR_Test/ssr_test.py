"""Safely pulse the Raspberry Pi GPIO outputs connected to SSR control inputs.

This checks the Pi's GPIO signal only. It cannot prove that an SSR switches its
load terminals; follow the SSR manufacturer's procedure for that check.
"""
import argparse
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from controls.relay import (  # noqa: E402
    GPIO,
    GPIO_RELAY_PINS,
    HARDWARE_AVAILABLE,
    PHYSICAL_RELAY_PINS,
)


def run_ssr_test(pin=None, pulse_seconds=2):
    if not HARDWARE_AVAILABLE:
        raise RuntimeError(
            "RPi.GPIO hardware support is unavailable. Run this on the Raspberry Pi "
            "with SIMULATE_HARDWARE disabled."
        )

    if pin is not None and pin not in GPIO_RELAY_PINS:
        raise ValueError(f"BCM pin must be one of {GPIO_RELAY_PINS}; got {pin}.")

    pins_to_test = [pin] if pin is not None else GPIO_RELAY_PINS
    physical_by_bcm = dict(zip(GPIO_RELAY_PINS, PHYSICAL_RELAY_PINS))

    print("SSR GPIO control-input test")
    print("Disconnect appliance loads before continuing.")
    print("This test pulses only the low-voltage GPIO input signal.")
    print("It does not verify that the SSR switches its load terminals.")
    print("Do not touch SSR load terminals or exposed mains wiring.")
    confirmation = input('Type "NO LOADS CONNECTED" to continue: ').strip()
    if confirmation != "NO LOADS CONNECTED":
        print("Cancelled; no GPIO test was run.")
        return False

    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)
    try:
        # Begin with every relay output commanded OFF.
        for relay_pin in GPIO_RELAY_PINS:
            GPIO.setup(relay_pin, GPIO.OUT)
            GPIO.output(relay_pin, GPIO.LOW)

        for relay_pin in pins_to_test:
            physical_pin = physical_by_bcm[relay_pin]
            print(
                f"BCM GPIO {relay_pin} (physical pin {physical_pin}): "
                f"HIGH for {pulse_seconds:g} seconds, then LOW."
            )
            print("Observe the SSR input indicator, if present; the Pi cannot read it back.")
            GPIO.output(relay_pin, GPIO.HIGH)
            time.sleep(pulse_seconds)
            GPIO.output(relay_pin, GPIO.LOW)
            time.sleep(1)

        print("GPIO pulse test complete; all relay outputs are LOW.")
        return True
    finally:
        for relay_pin in GPIO_RELAY_PINS:
            try:
                GPIO.output(relay_pin, GPIO.LOW)
            except Exception:
                pass
        GPIO.cleanup(GPIO_RELAY_PINS)
        print("GPIO cleanup complete; relay outputs commanded LOW.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pin", type=int, choices=GPIO_RELAY_PINS,
        help="test only this BCM GPIO pin (default: test all three, one at a time)",
    )
    parser.add_argument(
        "--pulse-seconds", type=float, default=2,
        help="HIGH pulse duration from 0.5 to 5 seconds (default: 2)",
    )
    args = parser.parse_args()
    if not 0.5 <= args.pulse_seconds <= 5:
        parser.error("--pulse-seconds must be between 0.5 and 5")
    try:
        run_ssr_test(pin=args.pin, pulse_seconds=args.pulse_seconds)
    except (RuntimeError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
