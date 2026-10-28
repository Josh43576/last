# Embedded System Reliability Changes

## PZEM-004T V4 multi-meter setup

- Documented the V4 TTL-to-RS485 wiring topology, shared bus assumptions, and the Pi's 9600 8N1 serial configuration.
- Added `configure_pzem_address.py` and `set_pzem_address()` to assign Modbus IDs to one isolated meter at a time, using function 0x06 and verifying the new ID.
- Updated the PZEM reader to poll the 10 measurement registers in one Modbus function 0x04 request and documented the procedure in `PZEM_V4_SETUP.md`.
- Updated hardware notes and the diagnostic procedure. Physical address programming and hardware validation remain to be performed on the Pi.

## Summary

The app and Pi agent now use a single command path: an administrator submits a request to Flask, Flask persists it, and the Pi polls it and applies safety checks before touching GPIO. Telemetry includes PZEM identity and energy data, settings survive restarts, and the dashboard renders current server data instead of placeholder readings.

## Code changes

### Flask service and database

- Fixed the missing SQLAlchemy `or_` import used by the dashboard query.
- Added a database migration command (`flask --app app init-db`), legacy singular `user`/`log` table renames, and safe additions for legacy log fields, relay requests, and administrator status.
- Added `slave_id` and `energy` fields to sensor logs, an index on recording time, and 30-day log retention.
- Added persistent per-channel requested relay states, trip flags, and a persistent total system cap.
- Added device-token protected telemetry, configuration, and trip routes. Browser control/configuration writes require an authenticated administrator session and CSRF token.
- Added `api_latest` to return the newest reading for each PZEM sensor. Added persisted trip events when the Pi reports a sensor fault or power cutoff.
- Validated finite positive power limits, device IDs, payload types, duplicate channels, and trip values.
- Verification and reset codes are stored as password hashes, expire after 10 minutes, and allow at most five attempts. Passwords require 10 characters; reset now has a working form with confirmation.
- Added `flask --app app create-test-admin` so local website testing can create a verified login without SMTP; browser registration still uses email verification or explicit local demo-code mode.
- Fixed the local signup/login test path: no-mail requests directly from loopback now show the verification code, registration flashes are visible, and explicit `ALLOW_DEMO_CODES=0` continues to disable the fallback.
- First registration on a new database becomes administrator. Existing accounts are migrated as administrators to preserve access; audit them after updating.
- The app no longer starts Flask debug mode by default. It uses a random development secret if no stable secret is configured; deployment must set `FLASK_SECRET_KEY` to preserve sessions across restarts.

### Raspberry Pi agent and hardware modules

- Reworked `main.py` to use the Flask API for appliance settings rather than opening a project-root `users.db` path that did not match Flask's `instance/users.db` location.
- Baseline mode is controlled by `BASELINE_MODE` and holds relays OFF. Active mode fails OFF when API commands, limits, or sensor data are missing or invalid.
- Added per-channel kW limits and a whole-system watt cap. Trips are persisted in Flask and require a fresh administrator ON request after conditions are checked.
- Fixed daily energy accounting to accumulate differences between each sensor's cumulative Wh counter, survive restarts, handle meter resets, and roll over by the Pi's local date. Corrupt or missing readings no longer overwrite the total with zero.
- Added atomic writes for local state/history, bounded event history, SIGTERM handling, and orderly relay OFF commands during shutdown.
- Sensor reads preserve successful register values when another register fails. RS485 transmit direction is restored in a `finally` block and partial serial writes are rejected.
- Missing RFID hardware now fails closed. `SIMULATE_HARDWARE=1` explicitly enables deterministic sensor, RFID, and relay mocks; real GPIO is bypassed in this mode.
- Consolidated `pzem_multi_reader.py` onto the shared sensor reader. `manual_interface.py` is explicitly documented as read-only and uses the shared sensor code.
- Relay initialization commands LOW/OFF before monitoring and reports unavailable GPIO as a startup failure, except in explicit simulation.

### Dashboard and support files

- Replaced static values, false online states, and hard-coded chart data with live values fetched from Flask.
- Connected channel switches, channel limits, and the total cap to persisted APIs. Non-admin users see disabled controls.
- Added visible sensor stale/no-data states and cutoff indicators.
- Replaced the unusable reset page with a code/password/confirmation form.
- Made `models.py` re-export the app's single SQLAlchemy registry instead of declaring a second divergent registry.
- Updated the API-data test payload for the expanded telemetry schema and improved test isolation. Updated Modbus framing expectations to match function code 0x04.
- Added `.env.example` and expanded `README.md` with migration, simulation, deployment, and hardware assumptions.

## Upgrade and run procedure

1. Back up `instance/users.db`, local JSON state files, and `.env` values.
2. Set `FLASK_SECRET_KEY`, `DEVICE_API_TOKEN`, `API_BASE_URL`, mail configuration, and database URL in the service environments. Keep the same device token on Flask and the Pi.
3. From the project directory, run `flask --app app init-db`. Existing users are given administrator privileges during migration to avoid locking out the current owner; remove admin access from accounts that should not control relays.
4. Start Flask with debug disabled. For remote operation, expose it only through a protected network and HTTPS reverse proxy or VPN.
5. Set `BASELINE_MODE=1` initially and `SIMULATE_HARDWARE=1` only for a software simulation. Confirm dashboard ingestion and channel identity.
6. Before active operation, verify Pi pin numbering, HIGH/LOW SSR polarity, sensor-to-relay channel mapping, protective hardware, and OFF behavior on a disconnected test load. Then set `SIMULATE_HARDWARE=0`; enable active mode only after supervised hardware checks.

For this workspace, the existing SQLite file was backed up to `instance/users.db.pre-implementation.bak` and the migration command was run successfully. Its legacy singular `log` table was renamed, and the current schema/settings were initialized.

## Verification status and remaining constraints

The virtual-environment test suite passed: 15 tests covering telemetry ingestion, local no-SMTP registration and login, dashboard access, limit conversion, device fail-off decisions, energy accounting, and Modbus framing. Physical hardware checks were **not run**. Validate sensor scaling and relay polarity against the actual PZEM modules and SSRs before enabling active control.

The Flask development server is still present for local startup; production hosting requires a WSGI server and HTTPS termination. The Pi's HTTP API requires an environment token but is not encrypted when plain HTTP is used. Email requests have a timeout but no external mail-service retry/rate-limit mechanism. The device loop uses fail-off behavior; the software is not a substitute for independent electrical protection.
