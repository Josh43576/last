# Raspberry Pi Power Monitor — Status Report

**As of:** 16 October 2026  
**Purpose:** Record today's code/configuration work, observed failures, what is verified, and the remaining effort. Percentages are engineering estimates based on the logs shared in chat, not measured test coverage.

## Executive status

The laptop/Pi configuration and network path have advanced: Flask started on the laptop, the Pi reached Flask, and the failure changed from connection timeout to HTTP 401 and later HTTP 400. HTTP 400 indicates the request reached an application endpoint, but the latest telemetry batch was rejected. The system is **not yet verified end to end**: RS485 readings are intermittent, channels 2 and 3 have repeated Modbus errors, and the SSR has not been verified switching an appliance load.

**Overall completion for a safe, working monitor/control system: approximately 35%.** The website/configuration portion is substantially further along than hardware integration. This score does not mean the equipment is 35% safe or ready for service.

## Work completed today

- Added automatic loading of a project-root `.env` in Flask and the Pi API client, so launching from another working directory does not prevent those processes from finding it.
- Added `python-dotenv` to `requirements.txt`, ignored `.env` files in Git while retaining `.env.example`, and documented the shared `DEVICE_API_TOKEN` and `API_BASE_URL` setup in `README.md`.
- Generated a token in the local ignored `.env`, then replaced it with a grouped, easier-to-type random token. The value is intentionally not reproduced in this report.
- Diagnosed the initial missing-token problem, Windows package-install failure for Pi-only native hardware dependencies, incorrect/stale Flask IP address, and subsequent authentication/network progression.
- Reworked `SSR_Test/ssr_test.py` to use the relay pin map in the main code, require confirmation that appliance loads are disconnected, pulse one or all GPIO outputs for a short interval, and command all outputs LOW on exit. This is a GPIO-input test only; it does not verify load-side SSR switching.
- Explained that the code expects BCM GPIO 17/27/22 (physical pins 11/13/15), HIGH=ON, and fails relay outputs OFF when required readings/API/configuration are unavailable.

## Issue log and current state

| Issue | What was observed | Status / what the evidence says |
|---|---|---|
| Missing `DEVICE_API_TOKEN` | Startup printed that the token was required. | Configuration support was added. The Pi initially returned 401, then later returned 400 on telemetry. That progression suggests authentication was corrected, but confirm both current `.env` files match. |
| Flask unreachable | Pi timed out or reported “No route to host” for different private IPs (`192.168.1.10`, `.125`, `.126`, later `.85`). | Network reachability improved enough to receive HTTP responses. The correct host address depends on where Flask is currently running and may change with DHCP. |
| Windows dependency install | Full requirements attempted to build `RPi.GPIO` and `spidev`, requiring MSVC. | Worked around by installing web dependencies separately on Windows. The requirements are still not split into web and Raspberry Pi files, so a full install on Windows will still fail. |
| Modbus/PZEM readings | Slave 1 sometimes reported 232.4 V, 0 A, 0 W, and 131072 Wh. Slaves 2 and 3 had timeouts, short frames, and other serial errors. | **Unresolved.** Possible causes include bus wiring/polarity, power to meters, duplicate/wrong slave addresses, adapter/direction setup, or a faulty module. Logs alone do not identify which. |
| Telemetry HTTP 400 | Pi posted readings and Flask rejected `/api/data`. | **Unresolved.** The endpoint accepts usable sensor readings and returns 400 when a batch contains none. The request's response body was not shown, so the precise rejection reason is unconfirmed. |
| Implausible daily energy | Program printed 2097.152 kWh while one displayed counter was 131072 Wh amid communication errors. | **Unverified and not trustworthy yet.** Diagnose the raw sensor data and local daily-state history before relying on this total. |
| SSR does not turn on | No successful connected-load switching test was reported. | **Unresolved.** The monitor intentionally fails OFF for missing readings or API/configuration failures. The standalone test only pulses GPIO with appliance loads disconnected. |
| Sensor/SSR power dependency | Concern raised that meters cannot be read while the SSR is open. | Plausible wiring-level startup loop if a meter loses its own supply when its SSR is OFF, but not confirmed. Meter model and wiring were not provided. Meter power/voltage sensing must remain available with the load switched off; have mains wiring checked by a qualified electrician. |

## Estimated progress by area

| Area | Estimate | Basis |
|---|---:|---|
| Flask/Pi environment and token configuration | 80% | `.env` loading and docs are in place; final values/processes on both machines still need confirmation. |
| Flask network reachability | 80% | HTTP responses were received; stable host/IP and current topology still need confirmation. |
| RS485/PZEM communication | 20% | One channel produced some readings; two channels repeatedly failed. |
| Telemetry ingestion | 35% | Requests reach Flask, but latest batch was rejected; successful persistent multi-channel ingestion is not demonstrated. |
| SSR GPIO software test | 60% | Test script is implemented, but has not been run on the Pi or observed at SSR inputs. |
| SSR switching a real load safely | 0% | No verified load-side test; sensor and relay behavior remain unresolved. |
| **Complete safe monitor/control operation** | **~35%** | Software setup has progressed; sensors and real hardware remain the critical path. |

## Next steps, in order

1. Keep appliance relay outputs OFF while debugging. Stop `main.py` before changing serial or GPIO wiring; power down before touching wiring.
2. Confirm where Flask runs and use that host's current LAN IP in the Pi's `API_BASE_URL`. Confirm the Pi and Flask process use the same `DEVICE_API_TOKEN`. Do not post either `.env` file or token in chat.
3. With the appliance loads disconnected and the main monitor stopped, test the RS485 bus with one meter at a time. Confirm the exact meter model, unique Modbus IDs (expected by this code: 1, 2, 3), adapter type, A/B polarity, serial port, and any required RS485 direction control. Add meters back one by one.
4. Confirm stable voltage/current/power responses for every channel and a successful `/api/data` response before enabling relay control. Capture the HTTP response body for any remaining 400.
5. Have a qualified electrician verify the meter supply/sensing and SSR mains wiring. If the meter is powered only from the SSR-switched side, correct the arrangement according to that exact meter's manual so the meter remains available while the load is OFF.
6. Only after stable readings and wiring checks, test the normal dashboard control path using a suitable supervised test load and verified current/power limits. The GPIO test script is not a substitute for this.
7. Rotate `DEVICE_API_TOKEN` before deployment if this conversation or its transcript is accessible to anyone else: the token value was displayed in the conversation. Update both machines together and restart both services.

## Timeline and required skills

These are rough active-work estimates, assuming parts and access are available:

- **Configuration/reachability confirmation:** 15–45 minutes; basic Linux shell, `.env` editing, and LAN troubleshooting.
- **One-meter-at-a-time RS485 diagnosis:** 1–3 hours if wiring/IDs are the cause; 0.5–2 days or longer if a meter, adapter, or power supply must be replaced. Requires intermediate electronics/Modbus troubleshooting and comfort with Raspberry Pi serial devices.
- **Safe full-channel telemetry validation:** 1–3 hours after the bus is stable; Python/API basics and systematic logging.
- **SSR and connected-load verification:** 1–3 hours after sensors and wiring are correct, with a qualified electrician and a controlled test load. Mains switching is not an appropriate beginner-only task.
- **Likely remaining to a supervised working prototype:** about half a day to two days if the hardware is intact and the issue is wiring/configuration; several days or more if replacement parts or wiring changes are needed.

The largest uncertainty is physical hardware, not Python. A software-only estimate cannot promise a completion date until the meter models, bus wiring, and SSR/load arrangement are inspected.

## Limitations of this report

This report is based on source files in the current workspace and terminal output pasted in chat. The Raspberry Pi was not directly inspected, the SSR test was not run on physical hardware, and no new test suite or electrical test was performed for this report. Therefore it records progress and hypotheses, not a certification that the device is safe or operational.

## Follow-up update — 17 October 2026

The user confirmed that the display is an MPI3508 connected over HDMI and powered from Raspberry Pi physical pins 2 (5V) and 6 (GND), rather than a separate GPIO character LCD. Removed the unused GPIO LCD driver calls from `main.py` and `manual_interface.py` and deleted `display/lcd.py`. Sensor data remains available in terminal output and the Flask dashboard; the dashboard can be viewed on the HDMI screen in a browser. The thesis document still needs its LCD/interface descriptions updated to match the actual MPI3508 HDMI setup.
