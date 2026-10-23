"""Standalone Raspberry Pi 3 hub: Modbus telemetry, web UI, GPIO relays, RFID reset."""
from __future__ import annotations

import hmac
import math
import os
import secrets
import struct
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")


def int_list(value: str, default: tuple[int, ...]) -> list[int]:
    try:
        result = [int(part.strip()) for part in value.split(",") if part.strip()]
        return result or list(default)
    except ValueError:
        return list(default)


def float_setting(name: str, default: float, minimum: float = 0.05) -> float:
    try:
        value = float(os.getenv(name, str(default)))
        return value if math.isfinite(value) and value >= minimum else default
    except ValueError:
        return default


KEYS = ("acu", "outlet", "lighting")
NAMES = {"acu": "ACU", "outlet": "Outlet", "lighting": "Lighting"}
GPIO_PINS = int_list(os.getenv("RELAY_GPIO_PINS", "17,27,22"), (17, 27, 22))
SLAVE_IDS = int_list(os.getenv("PZEM_SLAVE_IDS", "1,2,3"), (1, 2, 3))[:3]
if len(GPIO_PINS) != 3 or len(set(GPIO_PINS)) != 3:
    GPIO_PINS = [17, 27, 22]
if len(SLAVE_IDS) != 3 or len(set(SLAVE_IDS)) != 3 or any(not 1 <= value <= 247 for value in SLAVE_IDS):
    SLAVE_IDS = [1, 2, 3]

PORT = os.getenv("PZEM_PORT", "/dev/ttyUSB0")
BAUDRATE = int_list(os.getenv("PZEM_BAUDRATE", "9600"), (9600,))[0]
SERIAL_TIMEOUT = float_setting("PZEM_TIMEOUT_SECONDS", 0.5)
POLL_INTERVAL = float_setting("POLL_INTERVAL_SECONDS", 2.0)
POWER_SCALE = float_setting("PZEM_POWER_SCALE", 10.0)
VOLTAGE_SCALE = float_setting("PZEM_VOLTAGE_SCALE", 10.0)
CURRENT_SCALE = float_setting("PZEM_CURRENT_SCALE", 1000.0)
ENERGY_SCALE = float_setting("PZEM_ENERGY_SCALE", 1.0)
RFID_IDS = set(int_list(os.getenv("RFID_RESET_IDS", ""), ()))
STALE_AFTER = max(5.0, POLL_INTERVAL * 3.0)

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE") == "1",
    MAX_CONTENT_LENGTH=16 * 1024,
)

try:
    import serial
except ImportError:
    serial = None

try:
    import RPi.GPIO as GPIO
except ImportError:
    GPIO = None

try:
    from mfrc522 import SimpleMFRC522
except ImportError:
    SimpleMFRC522 = None

lock = threading.RLock()
state = {
    "readings": {key: {"slave_id": SLAVE_IDS[i], "voltage": None, "current": None,
                       "power": None, "energy": None, "error": "Waiting for first poll"}
                 for i, key in enumerate(KEYS)},
    "relays": {key: False for key in KEYS},
    "tripped": {key: False for key in KEYS},
    "updated_at": None,
    "rfid_status": "RFID reset disabled until RFID_RESET_IDS is configured",
}
stop_event = threading.Event()


def modbus_crc(data: bytes) -> bytes:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0xA001 if crc & 1 else 0)
    return struct.pack("<H", crc)


def read_registers(port, slave_id: int, address: int, quantity: int) -> bytes:
    if serial is None:
        raise RuntimeError("pyserial is not installed")
    body = bytes((slave_id, 0x04, address >> 8, address & 0xFF,
                  quantity >> 8, quantity & 0xFF))
    frame = body + modbus_crc(body)
    port.reset_input_buffer()
    written = port.write(frame)
    if written != len(frame):
        raise IOError(f"Short Modbus write: {written}/{len(frame)} bytes")
    port.flush()
    head = port.read(3)
    if len(head) != 3:
        raise TimeoutError(f"Slave {slave_id}: no Modbus response")
    if head[1] & 0x80:
        tail = port.read(2)
        raise IOError(f"Slave {slave_id}: Modbus exception {tail[0] if tail else 'unknown'}")
    if head[0] != slave_id or head[1] != 0x04:
        raise IOError(f"Slave {slave_id}: unexpected Modbus response header")
    rest = port.read(head[2] + 2)
    response = head + rest
    if len(rest) != head[2] + 2 or response[-2:] != modbus_crc(response[:-2]):
        raise IOError(f"Slave {slave_id}: incomplete response or CRC error")
    if head[2] != quantity * 2:
        raise IOError(f"Slave {slave_id}: unexpected register byte count")
    return response[3:-2]


def read_pzem_bus() -> list[dict]:
    """Poll all configured slaves sequentially on the single daisy-chain bus."""
    if serial is None:
        raise RuntimeError("Install pyserial to enable PZEM readings")
    results = []
    with serial.Serial(PORT, baudrate=BAUDRATE, bytesize=8, parity="N", stopbits=1,
                       timeout=SERIAL_TIMEOUT, write_timeout=SERIAL_TIMEOUT) as bus:
        for slave_id in SLAVE_IDS:
            row = {"slave_id": slave_id, "voltage": None, "current": None,
                   "power": None, "energy": None, "error": None}
            errors = []
            registers = (("voltage", 0x0000, 1, VOLTAGE_SCALE),
                         ("current", 0x0001, 2, CURRENT_SCALE),
                         ("power", 0x0003, 2, POWER_SCALE),
                         ("energy", 0x0005, 2, ENERGY_SCALE))
            for name, address, quantity, scale in registers:
                try:
                    raw = read_registers(bus, slave_id, address, quantity)
                    value = int.from_bytes(raw, "big") / scale
                    if not math.isfinite(value) or value < 0:
                        raise ValueError("invalid numeric measurement")
                    row[name] = value
                except Exception as exc:
                    errors.append(f"{name}: {exc}")
            row["error"] = "; ".join(errors) or None
            results.append(row)
    return results


def _gpio_output(key: str, is_on: bool) -> None:
    if GPIO is None:
        raise RuntimeError("RPi.GPIO unavailable; relay control is disabled on this computer")
    GPIO.output(GPIO_PINS[KEYS.index(key)], GPIO.HIGH if is_on else GPIO.LOW)


def all_relays_off() -> None:
    for key in KEYS:
        try:
            _gpio_output(key, False)
        except Exception as exc:
            print(f"Failed to command {key} OFF: {exc}")
        state["relays"][key] = False


def trip_channel(key: str, reason: str) -> None:
    try:
        _gpio_output(key, False)
    except Exception as exc:
        print(f"Failed to command {key} OFF after trip: {exc}")
    state["relays"][key] = False
    state["tripped"][key] = True
    state["readings"][key]["error"] = reason


def telemetry_loop() -> None:
    while not stop_event.is_set():
        try:
            fresh_rows = read_pzem_bus()
            now = time.time()
            with lock:
                for key, row in zip(KEYS, fresh_rows):
                    state["readings"][key] = row
                    if row["error"] or row["power"] is None:
                        trip_channel(key, row["error"] or "No valid power reading")
                    elif state["tripped"][key]:
                        state["relays"][key] = False
                state["updated_at"] = datetime.now(timezone.utc).isoformat()
                state["last_update_epoch"] = now
        except Exception as exc:
            with lock:
                for key in KEYS:
                    trip_channel(key, f"PZEM bus error: {exc}")
                state["updated_at"] = datetime.now(timezone.utc).isoformat()
                state["last_update_epoch"] = time.time()
        stop_event.wait(POLL_INTERVAL)


def rfid_loop() -> None:
    if not RFID_IDS:
        return
    if SimpleMFRC522 is None:
        with lock:
            state["rfid_status"] = "RFID library unavailable; reset disabled"
        return
    try:
        reader = SimpleMFRC522()
    except Exception as exc:
        with lock:
            state["rfid_status"] = f"RFID initialization failed: {exc}"
        return

    last_card = None
    clear_since = time.monotonic()
    while not stop_event.is_set():
        try:
            scan = getattr(reader, "read_id_no_block", None)
            card_id = scan() if callable(scan) else None
            if card_id is None:
                last_card = None
                clear_since = time.monotonic()
            elif card_id != last_card and time.monotonic() - clear_since >= 0.5:
                last_card = card_id
                with lock:
                    if card_id in RFID_IDS:
                        all_relays_off()
                        state["tripped"] = {key: False for key in KEYS}
                        state["rfid_status"] = "Authorized scan: trips cleared; all relays remain OFF"
                    else:
                        state["rfid_status"] = "Unrecognized RFID card"
            time.sleep(0.15)
        except Exception as exc:
            with lock:
                state["rfid_status"] = f"RFID read error: {exc}"
            time.sleep(1)


def authenticated() -> bool:
    return bool(session.get("authenticated"))


def csrf_token() -> str:
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


@app.before_request
def require_login_and_csrf():
    if request.endpoint in {"login", "static"}:
        return None
    if not authenticated():
        if request.path.startswith("/api/"):
            return jsonify({"error": "login required"}), 401
        return redirect(url_for("login"))
    if request.method == "POST":
        supplied = request.headers.get("X-CSRFToken", "") or request.form.get("csrf_token", "")
        expected = session.get("csrf_token", "")
        if not expected or not hmac.compare_digest(expected, supplied):
            return jsonify({"error": "invalid CSRF token"}), 403
    return None


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        expected_user = os.getenv("WEB_USERNAME", "")
        expected_password = os.getenv("WEB_PASSWORD", "")
        if (expected_user and expected_password
                and hmac.compare_digest(username, expected_user)
                and hmac.compare_digest(password, expected_password)):
            session.clear()
            session["authenticated"] = True
            session["csrf_token"] = secrets.token_urlsafe(32)
            return redirect(url_for("dashboard"))
        error = "Login failed. Check WEB_USERNAME and WEB_PASSWORD in .env."
    return render_template("login.html", error=error)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
def dashboard():
    return render_template("index.html", csrf=csrf_token(), keys=KEYS, names=NAMES)


@app.route("/api/status")
def api_status():
    with lock:
        snapshot = {key: value for key, value in state.items()}
        snapshot["readings"] = {key: dict(value) for key, value in state["readings"].items()}
        snapshot["relays"] = dict(state["relays"])
        snapshot["tripped"] = dict(state["tripped"])
        age = time.time() - state.get("last_update_epoch", 0)
        snapshot["stale"] = age > STALE_AFTER
    return jsonify(snapshot)


@app.route("/api/relay", methods=["POST"])
def api_relay():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "JSON object required"}), 400
    key = payload.get("relay")
    requested = payload.get("on")
    if key not in KEYS or not isinstance(requested, bool):
        return jsonify({"error": "relay must be a known channel and on must be true/false"}), 400
    with lock:
        if requested:
            reading = state["readings"][key]
            age = time.time() - state.get("last_update_epoch", 0)
            if state["tripped"][key]:
                return jsonify({"error": "Trip is latched; scan an authorized RFID card first"}), 409
            if age > STALE_AFTER or reading.get("error") or reading.get("power") is None:
                return jsonify({"error": "Fresh valid PZEM data is required before turning this relay ON"}), 409
        try:
            _gpio_output(key, requested)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 503
        state["relays"][key] = requested
    return jsonify({"status": "updated", "relay": key, "on": requested})


def initialize_hardware() -> None:
    if GPIO is None:
        print("RPi.GPIO unavailable. The web app can show an error, but relay controls will not work.")
        return
    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)
    for pin in GPIO_PINS:
        GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)
    all_relays_off()


def shutdown_hardware() -> None:
    stop_event.set()
    with lock:
        all_relays_off()
    if GPIO is not None:
        GPIO.cleanup(GPIO_PINS)


if __name__ == "__main__":
    if not os.getenv("WEB_USERNAME") or not os.getenv("WEB_PASSWORD"):
        raise SystemExit("Set WEB_USERNAME and WEB_PASSWORD in simple_rpi3_hub/.env before starting.")
    initialize_hardware()
    threading.Thread(target=telemetry_loop, name="pzem-poll", daemon=True).start()
    threading.Thread(target=rfid_loop, name="rfid-reset", daemon=True).start()
    try:
        app.run(host=os.getenv("WEB_HOST", "0.0.0.0"),
                port=int(os.getenv("WEB_PORT", "5000")), debug=False, threaded=True,
                use_reloader=False)
    finally:
        shutdown_hardware()
