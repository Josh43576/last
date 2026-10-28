import os
import struct
import time
from contextlib import suppress

try:
    import serial
except ModuleNotFoundError:  # pragma: no cover - exercised via import-time dependency guard
    serial = None

__all__ = [
    "PZEM_PORTS",
    "PZEM_SLAVE_IDS",
    "BAUDRATE",
    "get_available_ports",
    "prompt_for_slave_id",
    "build_modbus_request",
    "calculate_crc",
    "validate_modbus_response",
    "read_pzem_register",
    "read_pzem",
    "read_all_pzem",
]

# One RS485 bus with 3 PZEM modules on distinct Modbus slave IDs.
# Keep a single serial port and poll the devices sequentially.
PZEM_PORTS = [
    "/dev/ttyUSB0",
]
UART_PORTS = ["/dev/serial0", "/dev/ttyAMA0", "/dev/ttyS0"]
PZEM_SLAVE_IDS = [1, 2, 3]
BAUDRATE = 9600
SERIAL_TIMEOUT = 1.0

# Optional RS485 transceiver GPIO pin. Configure by exporting RS485_DE_RE_PIN.
# Example: export RS485_DE_RE_PIN=17 before running the service.
RS485_DE_RE_PIN = None
pin_value = os.environ.get("RS485_DE_RE_PIN")
if pin_value:
    try:
        RS485_DE_RE_PIN = int(pin_value)
    except ValueError:
        RS485_DE_RE_PIN = None


def set_rs485_direction(transmit: bool):
    """Drive the RS485 DE/RE control line if a GPIO pin has been configured."""
    if RS485_DE_RE_PIN is None:
        return

    with suppress(Exception):
        import RPi.GPIO as GPIO  # type: ignore

        GPIO.setmode(GPIO.BCM)
        GPIO.setup(RS485_DE_RE_PIN, GPIO.OUT)
        GPIO.output(RS485_DE_RE_PIN, GPIO.HIGH if transmit else GPIO.LOW)


def get_available_ports():
    """Return the serial ports available for the 3 PZEM sensors."""
    available = [port for port in PZEM_PORTS + UART_PORTS if os.path.exists(port)]

    if not available:
        return list(PZEM_PORTS)

    ports = []
    for port in available:
        if port not in ports:
            ports.append(port)
        if len(ports) >= 3:
            break
    return ports


def prompt_for_slave_id(slave_ids=None, input_func=input):
    """Prompt the user to choose a specific PZEM branch/slave ID and validate the answer."""
    available_ids = list(slave_ids if slave_ids is not None else PZEM_SLAVE_IDS)
    if not available_ids:
        raise ValueError("No PZEM slave IDs configured for selection.")

    while True:
        choices = ", ".join(str(item) for item in available_ids)
        raw_value = input_func(f"Select branch/slave ID to test ({choices}): ").strip()
        try:
            selected = int(raw_value)
        except ValueError:
            print("Invalid selection. Please enter a number from the listed slave IDs.")
            continue

        if selected not in available_ids:
            print(f"Invalid selection: {selected}. Choose one of: {choices}")
            continue

        return selected


def calculate_crc(data):
    """Calculate the CRC16-Modbus value for a Modbus RTU frame."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return struct.pack("<H", crc)


def build_modbus_request(slave_id: int, function_code: int, start_register: int, quantity: int) -> bytes:
    """Build a Modbus RTU request for a given slave and register range."""
    request = bytes([
        slave_id,
        function_code,
        (start_register >> 8) & 0xFF,
        start_register & 0xFF,
        (quantity >> 8) & 0xFF,
        quantity & 0xFF,
    ])
    return request + calculate_crc(request)


def validate_modbus_response(response: bytes, expected_function_code: int) -> bytes:
    """Validate Modbus RTU response size, CRC, and exception codes."""
    if not isinstance(response, (bytes, bytearray)):
        raise ValueError("Modbus response must be bytes or bytearray.")

    frame = bytes(response)
    if len(frame) < 5:
        raise ValueError(f"Modbus RTU frame too short: {len(frame)} bytes")

    if frame[1] & 0x80:
        exception_code = frame[2]
        raise ValueError(f"Modbus exception frame from slave {frame[0]}: code 0x{exception_code:02X}")

    if frame[1] != expected_function_code:
        raise ValueError(
            f"Unexpected Modbus function code: expected 0x{expected_function_code:02X}, got 0x{frame[1]:02X}"
        )

    if len(frame) < 5:
        raise ValueError("Modbus RTU response is incomplete")

    actual_crc = frame[-2:]
    expected_crc = calculate_crc(frame[:-2])
    if actual_crc != expected_crc:
        raise ValueError(
            f"CRC mismatch in Modbus RTU response from slave {frame[0]}: "
            f"expected {expected_crc.hex()} got {actual_crc.hex()}"
        )

    byte_count = frame[2]
    expected_length = 5 + byte_count
    if len(frame) != expected_length:
        raise ValueError(
            f"Unexpected Modbus RTU frame length: expected {expected_length}, got {len(frame)}"
        )

    return frame[3:-2]


def _read_modbus_frame(ser, expected_function_code: int, timeout: float = None) -> bytes:
    """Read until a complete RTU frame arrives or the serial timeout expires."""
    deadline = time.monotonic() + (timeout if timeout is not None else max(float(ser.timeout), SERIAL_TIMEOUT))
    frame = bytearray()

    while time.monotonic() < deadline:
        chunk = ser.read(1)
        if not chunk:
            if not frame:
                continue
            break
        frame.extend(chunk)

        if len(frame) >= 5:
            function_code = frame[1]
            if function_code & 0x80:
                break
            if function_code == expected_function_code:
                byte_count = frame[2]
                if len(frame) >= 5 + byte_count:
                    break

    if not frame:
        raise TimeoutError("Timed out waiting for Modbus RTU response")

    return bytes(frame)


def read_pzem_register(ser, slave_id: int, register_address: int, quantity: int = 1, retries: int = 3):
    """Send a Modbus RTU request and return the raw register payload."""
    if not 1 <= slave_id <= 247:
        raise ValueError(f"Slave ID must be between 1 and 247, got {slave_id}")

    function_code = 0x04
    for attempt in range(1, retries + 1):
        try:
            ser.flushInput()
            ser.flushOutput()
            request = build_modbus_request(slave_id, function_code, register_address, quantity)
            set_rs485_direction(True)
            try:
                written = ser.write(request)
                if written != len(request):
                    raise IOError(f"short Modbus write: {written}/{len(request)} bytes")
                ser.flush()
            finally:
                set_rs485_direction(False)

            response = _read_modbus_frame(ser, function_code, timeout=float(ser.timeout) if ser.timeout else SERIAL_TIMEOUT)
            if len(response) < 5:
                raise ValueError("Too short Modbus RTU response")
            return validate_modbus_response(response, function_code)
        except Exception as exc:
            if attempt >= retries:
                raise RuntimeError(f"Modbus read failed for slave {slave_id} after {retries} attempts: {exc}") from exc
            time.sleep(0.2 * attempt)

    raise RuntimeError(f"Modbus read failed for slave {slave_id} without a valid response")


def _decode_register_value(raw: bytes) -> int:
    """Decode PZEM values, whose 32-bit values place the low register first."""
    if len(raw) == 4:
        low_word = int.from_bytes(raw[:2], byteorder="big", signed=False)
        high_word = int.from_bytes(raw[2:], byteorder="big", signed=False)
        return (high_word << 16) | low_word
    return int.from_bytes(raw, byteorder="big", signed=False)


def read_pzem(port, slave_id: int = 1):
    """Read a single PZEM sensor using its configured Modbus slave ID."""
    if serial is None:
        return {
            "slave_id": slave_id,
            "voltage": None,
            "current": None,
            "power": None,
            "energy": None,
            "error": "pyserial is not installed; install the project dependencies to read PZEM sensors.",
        }

    if RS485_DE_RE_PIN in {17, 27, 22}:
        return {"slave_id": slave_id, "voltage": None, "current": None,
                "power": None, "energy": None,
                "error": "RS485 direction pin conflicts with a relay GPIO pin."}

    result = {"slave_id": slave_id, "voltage": None, "current": None,
              "power": None, "energy": None, "error": None}
    try:
        with serial.Serial(
            port,
            baudrate=BAUDRATE,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=SERIAL_TIMEOUT,
        ) as ser:
            reads = (
                ("voltage", 0x0000, 1, 10.0),
                ("current", 0x0001, 2, 1000.0),
                ("power", 0x0003, 2, 10.0),
                ("energy", 0x0005, 2, 1.0),
            )
            errors = []
            for name, address, quantity, scale in reads:
                try:
                    raw = read_pzem_register(ser, slave_id, address, quantity=quantity)
                    result[name] = _decode_register_value(raw) / scale
                except Exception as exc:
                    errors.append(f"{name} read failed: {exc}")
            result["error"] = " | ".join(errors) or None
        return result
    except Exception as exc:
        result["error"] = str(exc)
        return result


def read_all_pzem():
    """Read all PZEM sensors on one Modbus-RTU bus by polling slave IDs 1..3 sequentially."""
    if os.environ.get("SIMULATE_HARDWARE") == "1":
        return [
            {"slave_id": slave_id, "voltage": 230.0, "current": 0.5 * slave_id,
             "power": 115.0 * slave_id, "energy": 0.0, "error": None}
            for slave_id in PZEM_SLAVE_IDS
        ]
    readings = []
    available_ports = get_available_ports()
    port = available_ports[0] if available_ports else PZEM_PORTS[0]

    for slave_id in PZEM_SLAVE_IDS:
        readings.append(read_pzem(port, slave_id=slave_id))

    while len(readings) < 3:
        readings.append({
            "slave_id": len(readings) + 1,
            "voltage": None,
            "current": None,
            "power": None,
            "energy": None,
            "error": "No PZEM sensor detected on this channel",
        })

    return readings[:3]
