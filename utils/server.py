"""Authenticated HTTP client for the Raspberry Pi device agent."""
import os
from pathlib import Path
import requests
from dotenv import load_dotenv

# Load the same project-level settings when launched directly on the Pi.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

DEFAULT_PRESET_LIMIT = 500.0
DEFAULT_APPLIANCE_NAMES = ["Appliance 1", "Appliance 2", "Appliance 3"]
API_BASE_URL = os.environ.get("API_BASE_URL", "http://127.0.0.1:5000").rstrip("/")
DEVICE_API_TOKEN = os.environ.get("DEVICE_API_TOKEN", "")
try:
    REQUEST_TIMEOUT = max(0.1, float(os.environ.get("API_TIMEOUT_SECONDS", "5")))
except ValueError:
    REQUEST_TIMEOUT = 5.0


def _headers():
    if not DEVICE_API_TOKEN:
        raise RuntimeError("DEVICE_API_TOKEN is required for device API requests")
    return {"Authorization": f"Bearer {DEVICE_API_TOKEN}"}


def send_to_server(data):
    response = requests.post(
        f"{API_BASE_URL}/api/data", json=data, headers=_headers(), timeout=REQUEST_TIMEOUT
    )
    response.raise_for_status()
    return response.json()


def get_control_commands():
    """Read persisted relay requests. Return connected=False on any API failure."""
    try:
        response = requests.get(
            f"{API_BASE_URL}/api/control", headers=_headers(), timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()
        payload = response.json()
        states = payload.get("appliances") if isinstance(payload, dict) else None
        if not isinstance(states, list) or len(states) != 3 or any(s not in ("ON", "OFF") for s in states):
            raise ValueError("invalid control response")
        payload["connected"] = True
        payload.setdefault("preset_limit", DEFAULT_PRESET_LIMIT)
        payload.setdefault("appliance_names", DEFAULT_APPLIANCE_NAMES)
        return payload
    except (requests.RequestException, ValueError, RuntimeError) as exc:
        print(f"Control API unavailable: {exc}")
        return {"appliances": ["OFF"] * 3, "connected": False,
                "preset_limit": DEFAULT_PRESET_LIMIT,
                "appliance_names": DEFAULT_APPLIANCE_NAMES}


def get_appliance_configs():
    try:
        response = requests.get(
            f"{API_BASE_URL}/api/appliance-config", headers=_headers(), timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()
        payload = response.json()
        configs = payload.get("configs") if isinstance(payload, dict) else None
        if isinstance(configs, dict):
            return configs
        raise ValueError("invalid appliance-config response")
    except (requests.RequestException, ValueError, RuntimeError) as exc:
        print(f"Appliance config API unavailable: {exc}")
        return None


def report_trip(index, value_watts, limit_watts, reason="power_limit"):
    try:
        response = requests.post(
            f"{API_BASE_URL}/api/device-trip",
            json={"index": index, "value_watts": value_watts,
                  "limit_watts": limit_watts, "reason": reason},
            headers=_headers(), timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return True
    except (requests.RequestException, RuntimeError) as exc:
        print(f"Trip-state upload failed: {exc}")
        return False


def set_appliance_names(names):
    try:
        response = requests.post(
            f"{API_BASE_URL}/api/config", json={"appliance_names": names},
            headers=_headers(), timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, RuntimeError) as exc:
        print(f"Error setting appliance names: {exc}")
        return {"error": str(exc)}
