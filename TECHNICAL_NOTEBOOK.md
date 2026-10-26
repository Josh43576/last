# Raspberry Pi Power Monitor — Technical Notebook

**Purpose:** Master operational reference for the Flask dashboard and Raspberry Pi monitoring agent. This notebook records verified software behavior, configuration, startup and recovery procedures, and the hardware/network facts needed for deployment. Physical specifications that have not been verified against the installed equipment are marked **Confirm on site** rather than assumed.

**Source of truth:** repository source and configuration templates as of 26 October 2026. Review this notebook after changes to `app.py`, `main.py`, `sensors/`, `controls/`, or deployment configuration.

## 1. System overview

The system has two runtime roles:

| Role | Runtime | Responsibility |
| --- | --- | --- |
| Web/API server | `app.py` (Flask, SQLAlchemy) | Login and administration, SQLite persistence, telemetry API, configuration and relay requests |
| Device agent | `main.py` (Raspberry Pi) | Poll three PZEM meters, upload readings, retrieve control/configuration, enforce limits, operate three relay GPIO outputs |

The Pi polls every 5 seconds. The Flask app does not directly drive GPIO. In baseline mode, the Pi reads and uploads measurements while holding all relays OFF. In active mode, missing API commands, invalid limits, or invalid sensor measurements cause affected outputs to remain OFF. On normal shutdown, all outputs are commanded LOW before GPIO cleanup.

```text
PZEM meters (Modbus RTU, IDs 1–3) → Raspberry Pi agent → Flask API → SQLite
                                                  ↑                 ↓
                                      SSR GPIO outputs ← requests/config
```

## 2. Repository map

| Path | Purpose |
| --- | --- |
| `app.py` | Flask web app, database models, migrations, device and browser routes |
| `main.py` | Pi polling/control loop, daily energy accounting, event history |
| `sensors/pzem.py` | Modbus RTU framing and PZEM serial reader |
| `controls/relay.py` | GPIO initialization and relay output control |
| `utils/server.py` | Authenticated HTTP client used by the Pi |
| `auth/rfid.py`, `manual_interface.py` | RFID helper and separate read-only manual interface; not part of `main.py` runtime |
| `templates/`, `static/` | Dashboard and browser assets |
| `instance/users.db` | Default Flask SQLite database |
| `daily_usage_state.json` | Pi local daily energy and last cumulative meter counters |
| `daily_usage_history.json` | Pi local event history, capped at 10,000 entries |
| `appliance_config.json` | Pi local appliance display names, created interactively if absent |
| `QA_HARDWARE_DIAGNOSTIC_PROCEDURE.md` | Detailed hardware commissioning gates and diagnostics |

## 3. Software and dependencies

Python dependencies are pinned or bounded in `requirements.txt`:

| Component | Version/range | Use |
| --- | --- | --- |
| Flask | 3.0.0 | HTTP application and CLI |
| Flask-Login | 0.6.3 | User sessions |
| Flask-SQLAlchemy | 3.1.1 | ORM and persistence |
| Werkzeug | 3.0.1 | Password hashing and Flask support |
| requests | 2.31.0 | Pi HTTP client |
| python-dotenv | >=1.0,<2 | `.env` loading |
| pyserial | 3.5 | Serial Modbus transport |
| RPi.GPIO | 0.7.1 | Raspberry Pi GPIO |
| spidev | 3.6 | SPI support for RFID helper |
| mfrc522 | 0.0.7 | RC522 RFID helper |
| smbus2 | 0.4.2 | I²C support/helper dependency |

Use a Python version supported by these packages and the target Raspberry Pi OS. **Confirm on site:** exact Python version, OS release/architecture, and successful install availability for the legacy Raspberry Pi hardware packages. Use a virtual environment on each host. Install `RPi.GPIO` and hardware dependencies on the Pi; the server does not need GPIO hardware, but the current shared requirements file includes them.

## 4. Hardware inventory and interface assumptions

The QA procedure and project design identify a Raspberry Pi 4 Model B, three PZEM-004T V4 meters with current transformers, three SSR-controlled appliance branches, double-pole circuit protection, Wi-Fi, an MPI3508 HDMI display, and an RC522 RFID reader. Treat these as design references, not proof of the installed build.

| Subsystem | Software assumption | Physical confirmation required |
| --- | --- | --- |
| Meter bus | One serial bus, Modbus RTU, 9600 baud, 8-N-1, IDs 1, 2, 3; function 0x04 | Exact PZEM revision and whether installed interface is TTL or RS-485; adapter, voltage levels, wiring, CT orientation |
| Serial port | Prefers `/dev/ttyUSB0`; UART alternatives `/dev/serial0`, `/dev/ttyAMA0`, `/dev/ttyS0` | Stable device path and ownership/permissions on target Pi |
| Relay GPIO | BCM 17, 27, 22; physical header pins 11, 13, 15; HIGH means ON, LOW means OFF | SSR input compatibility/polarity, branch mapping, load ratings, heat sinking and electrical protection |
| Channel order | Meter IDs 1–3 map by index to `acu`, `outlet`, `lighting` | Label and verify actual sensor → SSR → branch map before operation |
| RFID | RC522 helper uses SPI when invoked by supported interface | `main.py` does not invoke RFID; verify separate workflow against project requirements |
| Display | HDMI display; app emits terminal output and serves browser dashboard | Confirm display/browser setup; no GPIO character LCD driver is used by the active agent |

The QA procedure documents unresolved interface/model mismatches. Complete its commissioning gates before connecting or energizing loads. SSR LOW is a software command, not a maintenance disconnect or proof of galvanic isolation.

## 5. Network and API parameters

The Pi sends requests to `API_BASE_URL` and authenticates device routes with a bearer token. Set the same strong `DEVICE_API_TOKEN` on Flask and Pi. `API_TIMEOUT_SECONDS` defaults to 5 seconds. The example URL uses HTTP; bearer tokens and telemetry are unencrypted over HTTP. Restrict HTTP to an isolated trusted LAN, or put the server behind HTTPS and configure a trusted HTTPS URL. For remote operation use a production WSGI server and HTTPS reverse proxy or private VPN; `python app.py` is for local/development startup.

Open only the network paths needed between the Pi and Flask host. Do not expose the Flask development server or token-protected API directly to the public internet. Record hostnames/IPs and ports in the deployment record, but never record the token.

## 6. Environment configuration

Copy `.env.example` to a private `.env` for each runtime role. The app loads the project `.env`; environment variables set by the service manager take precedence. The file is ignored by Git. Generate unique secrets and keep the device token identical across the two roles.

| Variable | Required/meaning | Deployment guidance |
| --- | --- | --- |
| `FLASK_SECRET_KEY` | Flask session signing key | Set a long random stable value on the web host |
| `DEVICE_API_TOKEN` | Device bearer token | Long random value, same on Flask and Pi |
| `API_BASE_URL` | Pi's Flask base URL | Use reachable server address; loopback only if co-located |
| `API_TIMEOUT_SECONDS` | Pi HTTP timeout | Defaults to 5 seconds |
| `DATABASE_URL` | SQLAlchemy URI | Defaults in app to SQLite under `instance/users.db`; example currently says `sqlite:///users.db`, so set deliberately and verify resolved path |
| `FLASK_DEBUG` | Debug setting | Keep `0` in deployment; app debug is off by default |
| `BASELINE_MODE` | Telemetry-only mode | `1` holds relays OFF; use for initial commissioning |
| `SIMULATE_HARDWARE` | Explicit software mock mode | `1` simulates PZEM/GPIO/RFID behavior; it is not electrical validation |
| `SIMULATED_RFID_ID` | Mock card ID | Simulation only |
| `ALLOW_DEMO_CODES` | Local verification/reset code display | `0` in deployment |
| `MAIL_USERNAME`, `MAIL_PASSWORD` | SMTP account values | Configure privately if verification mail is used; use provider app password where required |
| `SESSION_COOKIE_SECURE` | Secure session-cookie flag | Set `1` when serving over HTTPS |
| `RS485_DE_RE_PIN` | Optional RS-485 direction GPIO, BCM numbering | Set only for a transceiver requiring direction control; must not conflict with relay pins 17, 27, 22 |

Configuration values can differ between `.env.example` and code defaults. In particular, `DATABASE_URL` must be checked: the app code defaults to its Flask instance database, while the template explicitly specifies `sqlite:///users.db`. Resolve the chosen absolute database location before migration and back it up.

## 7. Installation and initialization

Commands below are run from the project root on a Debian-based Raspberry Pi OS or compatible Linux host. Adapt package names for the selected OS.

### 7.1 Prepare each host

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip
cd ~/last   # replace with the actual deployed project directory
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp .env.example .env
chmod 600 .env
```

Edit `.env` with host-specific values. On Flask, set `FLASK_SECRET_KEY`, the shared device token, database location, SMTP values if needed, and deployment cookie settings. On the Pi, set the same token, reachable `API_BASE_URL`, timeout, `SIMULATE_HARDWARE=0`, and initially `BASELINE_MODE=1`. Do not paste secrets into tickets, logs, or source control.

### 7.2 Initialize Flask database

Back up the selected database first if upgrading. Then from the app directory with the environment activated:

```bash
flask --app app init-db
```

For local browser testing without SMTP, create a verified test admin interactively:

```bash
flask --app app create-test-admin
```

For a fresh browser deployment, register the first account and verify it. Existing accounts may be migrated with administrator access to preserve control; audit user roles after upgrade.

### 7.3 Start in simulation or baseline mode

Software-only simulation, before starting the relevant process:

```bash
SIMULATE_HARDWARE=1 BASELINE_MODE=1 python main.py
```

This validates application paths with mocks; it does not test wiring or meter accuracy. For a real Pi baseline run, set `SIMULATE_HARDWARE=0`, `BASELINE_MODE=1` in its environment and run:

```bash
python main.py
```

The first run asks for three appliance names if `appliance_config.json` is absent, then synchronizes them to Flask. Start Flask locally for development with:

```bash
python app.py
```

The Pi prints sensor readings, daily energy, API failures, and mode status to standard output. Stop with Ctrl+C; SIGINT/SIGTERM handling commands relay outputs OFF and cleans up GPIO.

## 8. Optional systemd service examples

These are templates; replace `pi`, `/home/pi/last`, and executable paths with the installed account and project path. Run the web service behind a production WSGI server for deployment; this example illustrates the Pi agent only.

Create `/etc/systemd/system/power-monitor.service`:

```ini
[Unit]
Description=Raspberry Pi power monitor agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/last
EnvironmentFile=/home/pi/last/.env
ExecStart=/home/pi/last/.venv/bin/python /home/pi/last/main.py
Restart=on-failure
RestartSec=5
UMask=0077

[Install]
WantedBy=multi-user.target
```

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now power-monitor.service
sudo systemctl status power-monitor.service
```

Systemd environment values override `.env`. Set `BASELINE_MODE=1` in the service environment during commissioning. Confirm file ownership allows the service account to access its environment, serial device, and JSON state files. Do not enable active control until hardware commissioning has passed.

## 9. Operating procedure

1. Confirm the correct revision is deployed and `.env` is private; check the Flask URL and matching token without printing token values.
2. Confirm the Flask service/database is available and run `flask --app app init-db` after a planned backup when initializing/upgrading.
3. Start Flask and sign in to the dashboard. Confirm sensor data is not stale once the Pi starts.
4. On the Pi, check serial adapter path and permissions; ensure only one process owns the meter bus.
5. Start `main.py` with `BASELINE_MODE=1`; confirm all three expected sensor IDs, channel mapping, and telemetry update. Relays stay OFF.
6. Stop and verify OFF behavior. Complete the hardware QA gates with loads disconnected and qualified electrical oversight.
7. Only after documented approval, set `BASELINE_MODE=0` and start the agent. Request one channel at a time from the authenticated admin dashboard and observe the corresponding branch.
8. To stop, use `sudo systemctl stop power-monitor.service` or Ctrl+C. Verify outputs are commanded OFF and apply physical isolation as needed.

## 10. Security constraints

- Keep `.env`, database files, backups, and test credentials out of source control and shared screenshots.
- Use distinct random `FLASK_SECRET_KEY` and `DEVICE_API_TOKEN`; only the device token is shared between Flask and Pi.
- Disable `ALLOW_DEMO_CODES`; do not use default/test credentials in deployment.
- Use HTTPS for traffic beyond an isolated trusted test network. The token is exposed to network observers over plain HTTP.
- Use a production WSGI server and HTTPS termination for remote service; disable debug mode.
- Keep the Pi and server on a restricted network, patch the OS/dependencies, limit service account permissions, and back up the database before migrations.
- Verify administrator accounts after an upgrade. Browser control/configuration writes require an authenticated admin session and CSRF token; device routes require the bearer token.
- Software safety behavior does not replace overcurrent protection, approved isolation, electrical code, or supervised commissioning.

## 11. Diagnostics and logs

There is no configured rotating application log file. The Pi agent writes operational messages and errors to stdout/stderr. Under systemd, inspect the journal:

```bash
sudo journalctl -u power-monitor.service -n 100 --no-pager
sudo journalctl -u power-monitor.service -f
```

For interactive runs, capture output to a restricted diagnostic file if required (avoid exposing secrets):

```bash
python main.py 2>&1 | tee -a monitor-console.log
```

Useful checks:

```bash
python -m pip check
ls -l /dev/serial/by-id/ /dev/ttyUSB0
sudo systemctl status power-monitor.service
curl -I http://SERVER_HOST:5000/login
```

`curl` checks reachability only; protected API routes require the configured token. Never add that token to command history or paste it into support output.

| Symptom | Checks |
| --- | --- |
| No serial data / all PZEM reads fail | Correct device path and permissions; one reader process only; adapter/interface compatibility; baud 9600 8-N-1; unique IDs 1–3; CRC/timeouts; actual meter revision |
| Pi cannot reach API | `API_BASE_URL`, DNS/IP, route/firewall, Flask health/reverse proxy, timeout; compare token configuration privately without printing it |
| Telemetry posts fail | Journal/console exception, server availability, JSON shape, token mismatch, API response; keep relays OFF while connectivity is unresolved |
| Relay unexpectedly OFF | Baseline mode; command connectivity; valid limit configuration; valid channel sensor data; trip/cutoff status; GPIO initialization; wiring and SSR input state |
| Daily total resets or seems implausible | Pi local date rollover, `daily_usage_state.json`, counter reset/rollover, raw PZEM cumulative Wh; preserve source data before editing state |
| Agent will not start | Python environment/dependencies, GPIO availability, service user/group access to serial/GPIO, appliance name prompt/file, journal traceback |
| Values appear under wrong appliance | Verify physical sensor-to-SSR mapping against IDs 1–3 and software order `acu`, `outlet`, `lighting`; correct records/config only after physical confirmation |

Detailed hardware procedures and acceptance gates are in `QA_HARDWARE_DIAGNOSTIC_PROCEDURE.md`.

## 12. Backup, upgrade, and recovery

Before upgrading, stop the Pi agent and back up `instance/users.db` (or the configured database), `.env` through a secure channel, `daily_usage_state.json`, `daily_usage_history.json`, and `appliance_config.json`. Upgrade code/dependencies in the intended environment, review the changelog, and run:

```bash
flask --app app init-db
```

Start in baseline mode, confirm logins, sensor identity, telemetry, settings, and safe output behavior. Review migrated administrator privileges. Restore from the verified backup if migration or startup fails; keep the agent in baseline mode until the cause is understood.

## 13. Deployment record (complete per installation)

| Field | Recorded value |
| --- | --- |
| Deployment date / operator |  |
| Git revision |  |
| Raspberry Pi model / OS / architecture |  |
| Python version / virtual environment path |  |
| Server host / TLS endpoint |  |
| Database URI and backup location (no credentials) |  |
| PZEM model/revision, adapter, port, IDs |  |
| Relay/SSR models and verified GPIO polarity |  |
| Sensor → SSR → appliance mapping |  |
| Protective device and electrical sign-off reference |  |
| Service names and journal retention policy |  |
| Commissioning QA result and unresolved issues |  |

Do not record secret values in this table.
