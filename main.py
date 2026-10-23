"""Raspberry Pi device loop: read sensors, upload telemetry, enforce limits, drive relays."""
import json
import math
import os
import signal
import tempfile
import time
from datetime import date, datetime, timezone

from sensors import read_all_pzem
from controls.relay import control_appliance, cleanup as cleanup_relay, init as init_relays
from utils.server import (
    get_appliance_configs, get_control_commands, report_trip,
    send_to_server, set_appliance_names,
)

shutdown_requested = False
BASELINE_MODE = os.environ.get("BASELINE_MODE", "0").strip().lower() in {"1", "true", "yes"}
DEVICE_KEYS = ("acu", "outlet", "lighting")
DEFAULT_APPLIANCE_NAMES = ["Appliance 1", "Appliance 2", "Appliance 3"]
appliance_names = DEFAULT_APPLIANCE_NAMES.copy()
root_dir = os.path.dirname(os.path.abspath(__file__))
state_file = os.path.join(root_dir, "daily_usage_state.json")
history_file = os.path.join(root_dir, "daily_usage_history.json")
reported_cutoffs = set()


def utc_now():
    return datetime.now(timezone.utc)


def _atomic_json_write(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".state-", dir=os.path.dirname(path), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(value, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_appliance_names():
    try:
        with open(os.path.join(root_dir, "appliance_config.json"), encoding="utf-8") as source:
            names = json.load(source).get("appliance_names")
        if isinstance(names, list) and len(names) == 3:
            return [str(name).strip() or f"Appliance {i + 1}" for i, name in enumerate(names)]
    except (OSError, ValueError, TypeError):
        pass
    return None


def configure_appliance_names():
    global appliance_names
    existing = load_appliance_names()
    if existing:
        appliance_names = existing
    else:
        print("Configure appliance names before starting the monitor.")
        appliance_names = [input(f"Appliance {i + 1} name: ").strip() or f"Appliance {i + 1}" for i in range(3)]
        _atomic_json_write(os.path.join(root_dir, "appliance_config.json"), {"appliance_names": appliance_names})
    # Always synchronize persisted local names with Flask after startup.
    set_appliance_names(appliance_names)


def load_daily_state():
    try:
        with open(state_file, encoding="utf-8") as source:
            state = json.load(source)
        if not isinstance(state, dict):
            raise ValueError("daily state is not an object")
    except (OSError, ValueError):
        state = {}
    try:
        daily_total = float(state.get("daily_energy_kwh", 0.0) or 0.0)
    except (TypeError, ValueError):
        daily_total = 0.0
    return {
        "date": state.get("date"),
        "daily_energy_kwh": daily_total if math.isfinite(daily_total) and daily_total >= 0 else 0.0,
        "last_energy_wh": state.get("last_energy_wh", {}) if isinstance(state.get("last_energy_wh", {}), dict) else {},
    }


def update_daily_energy(readings, state, today=None):
    """Accumulate differences between each PZEM's cumulative Wh counter."""
    today = today or date.today().isoformat()
    if state.get("date") != today:
        state = {"date": today, "daily_energy_kwh": 0.0, "last_energy_wh": {}}
    previous_values = state.setdefault("last_energy_wh", {})
    if not isinstance(previous_values, dict):
        previous_values = {}
        state["last_energy_wh"] = previous_values
    try:
        total_today = float(state.get("daily_energy_kwh", 0.0))
    except (TypeError, ValueError):
        total_today = 0.0
    state["daily_energy_kwh"] = total_today if math.isfinite(total_today) and total_today >= 0 else 0.0
    for reading in readings:
        try:
            slave_id = str(int(reading["slave_id"]))
            energy_wh = float(reading["energy"])
        except (KeyError, TypeError, ValueError):
            continue
        if not math.isfinite(energy_wh) or energy_wh < 0:
            continue
        previous = previous_values.get(slave_id)
        if previous is not None:
            try:
                previous = float(previous)
            except (TypeError, ValueError):
                previous = None
        if previous is not None and (not math.isfinite(previous) or previous < 0):
            previous = None
        if previous is not None:
            delta = energy_wh - previous if energy_wh >= previous else energy_wh
            state["daily_energy_kwh"] += delta / 1000.0
        previous_values[slave_id] = energy_wh
    state["updated_at"] = utc_now().isoformat()
    _atomic_json_write(state_file, state)
    return state


def append_history(event_type, message, value_watts=0.0, preset_limit=0.0):
    try:
        with open(history_file, encoding="utf-8") as source:
            events = json.load(source)
        if not isinstance(events, list):
            events = []
    except (OSError, ValueError):
        events = []
    events.append({"timestamp": utc_now().isoformat(), "event_type": event_type,
                   "message": message, "value_watts": value_watts,
                   "preset_limit": preset_limit})
    _atomic_json_write(history_file, events[-10000:])


def signal_handler(_sig, _frame):
    global shutdown_requested
    shutdown_requested = True


def valid_power(reading):
    try:
        value = float(reading["power"])
    except (KeyError, TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def configured_limits(configs):
    limits = {}
    for key in DEVICE_KEYS:
        try:
            value = float(configs[key]["kw_limit"])
        except (KeyError, TypeError, ValueError):
            return None
        if not math.isfinite(value) or value <= 0:
            return None
        limits[key] = value
    return limits


def apply_commands(readings, commands, limits):
    """Fail closed on missing commands, sensor data, or limit configuration."""
    if not isinstance(commands, dict):
        commands = {}
    requested_states = commands.get("appliances")
    if (
        not commands.get("connected")
        or limits is None
        or not isinstance(readings, list)
        or not isinstance(requested_states, list)
        or len(requested_states) != len(DEVICE_KEYS)
        or any(state not in ("ON", "OFF") for state in requested_states)
    ):
        for index in range(3):
            control_appliance(index, False)
        return
    by_slave = {}
    for reading in readings:
        if isinstance(reading, dict):
            try:
                slave = int(reading.get('slave_id'))
            except (TypeError, ValueError):
                continue
            if 1 <= slave <= 3:
                by_slave[slave] = reading
    channel_readings = [by_slave.get(index + 1) for index in range(3)]
    measured = [valid_power(reading) if isinstance(reading, dict) else None for reading in channel_readings]
    try:
        total_limit = float(commands.get("preset_limit"))
    except (TypeError, ValueError):
        total_limit = None
    if total_limit is not None and math.isfinite(total_limit) and total_limit > 0 and all(value is not None for value in measured):
        total_power = sum(measured)
        if total_power >= total_limit:
            for index in range(3):
                control_appliance(index, False)
                if requested_states[index] == "ON" and index not in reported_cutoffs:
                    report_trip(index, total_power, total_limit, "system_power_limit")
                    append_history("system_power_limit", "System power preset exceeded; all channels forced OFF.", total_power, total_limit)
                    reported_cutoffs.add(index)
            return
    for index, key in enumerate(DEVICE_KEYS):
        power = measured[index]
        requested = requested_states[index] == "ON"
        if not requested:
            reported_cutoffs.discard(index)
            control_appliance(index, False)
            continue
        if power is None:
            control_appliance(index, False)
            if index not in reported_cutoffs:
                report_trip(index, 0.0, limits[key] * 1000.0 if limits else 0.0, "sensor_fault")
                append_history("sensor_fault", f"{key} has no valid power reading; relay forced OFF.")
                reported_cutoffs.add(index)
            continue
        limit_watts = limits[key] * 1000.0
        allowed = power < limit_watts
        if not allowed:
            control_appliance(index, False)
            if index not in reported_cutoffs:
                report_trip(index, power, limit_watts)
                append_history("power_cutoff", f"{key} exceeded its {limits[key]:.2f} kW limit.", power, limit_watts)
                reported_cutoffs.add(index)
        else:
            reported_cutoffs.discard(index)
            control_appliance(index, requested and allowed)



def main():
    global appliance_names
    print("Starting Raspberry Pi power monitor.")
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    global shutdown_requested
    shutdown_requested = False
    appliance_names = load_appliance_names() or DEFAULT_APPLIANCE_NAMES.copy()
    configure_appliance_names()
    if not init_relays():
        cleanup_relay()
        raise RuntimeError("Relay initialization failed; refusing to run control loop")

    state = load_daily_state()
    try:
        while not shutdown_requested:
            readings = read_all_pzem()
            print("Sensor readings:", readings)
            state = update_daily_energy(readings, state)
            print(f"Today's energy: {state['daily_energy_kwh']:.3f} kWh")
            try:
                send_to_server(readings)
            except Exception as exc:
                print(f"Telemetry upload failed: {exc}")

            if BASELINE_MODE:
                for index in range(3):
                    control_appliance(index, False)
                print("Baseline mode: monitoring only; relay outputs held OFF.")
            else:
                commands = get_control_commands()
                configs = get_appliance_configs()
                limits = configured_limits(configs) if configs is not None else None
                apply_commands(readings, commands, limits)
            time.sleep(5)
    except Exception as exc:
        print(f"Device loop stopped after an unexpected error: {exc}")
        raise
    finally:
        for index in range(3):
            control_appliance(index, False)
        cleanup_relay()
        print("Device loop stopped; relay outputs commanded OFF.")


if __name__ == "__main__":
    main()
