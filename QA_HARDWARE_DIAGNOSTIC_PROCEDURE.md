# Hardware QA and Diagnostic Procedure

**System:** Advanced IoT Power Management System for Real-Time Load Analysis and Consumption Control  
**Prepared:** 17 October 2026  
**Basis:** `thesismonkey-semifinal.docx`, especially Chapter III (project-development flow, design requirements, functional decomposition) and current project source code.  
**Use:** Commissioning and fault isolation. This procedure is not an electrical installation design or a substitute for the manufacturers' manuals and local electrical code.

## 1. Study flow and system under test

The thesis describes this development sequence:

1. Planning and design
2. Procurement of components
3. Hardware/software integration
4. Installation and setup
5. Testing and calibration
6. Performance testing and data collection
7. Expert evaluation for design and operation acceptability

The hardware QA gates below follow that order. The thesis identifies a Raspberry Pi 4 Model B, three PZEM-004T V4 meters with CTs, SSRs, double-pole circuit protection, Wi-Fi, a display, and RC522 RFID. The confirmed display setup for this build is an MPI3508 over HDMI, powered from Raspberry Pi physical pin 2 (5V) and pin 6 (GND); it is not a GPIO character LCD. Its intended outputs are live electrical readings and controlled lighting/outlet/ACU loads.

### Known implementation/document mismatches to resolve before energizing

- The thesis lists PZEM-004T V4 with a **TTL UART** interface. The software sends Modbus RTU requests over a serial port (`/dev/ttyUSB0`, 9600 baud, 8 data bits, no parity, 1 stop bit; slave IDs 1, 2, and 3; function code 0x04). Confirm the exact meter revision and the physical adapter/interface. A serial protocol match does not by itself confirm that the electrical interface wiring is compatible.
- The thesis names SSR-40DA in its materials list but SSR-25DA in its essential-parts table. Record the actual installed model and use that exact manufacturer's datasheet for input voltage/current, output rating, heat sinking, and protection requirements.
- The thesis describes lighting, convenience outlet, and special-purpose/ACU categories. Current software maps channel indices to `acu`, `outlet`, `lighting` in that order, and reads sensor IDs 1, 2, 3 by index. Verify and document one authoritative sensor-to-SSR-to-load mapping before testing; do not assume labels imply the wiring order.
- `main.py` reads sensors and controls the SSRs, but it does not use RFID. RFID behavior appears in `manual_interface.py`, which is read-only and accesses the same serial sensor bus. Do not run it at the same time as `main.py`.
- The MPI3508 uses the HDMI display path. The Pi application writes readings to the terminal and serves a web dashboard; it does not drive the HDMI monitor through a custom display driver. Confirm the Linux desktop/browser or terminal view used during demonstrations. Physical header pins 2 and 6 provide power/ground only; the HDMI connection carries the display signal.

Resolve these against the physical build, source code, and final thesis drawings. If the actual hardware differs from a table or drawing, update the records before claiming that the tested system matches the study design.

## 2. Roles, safety boundary, and test states

### Who does what

- A qualified electrician handles the mains panel, breaker, SSR load terminals, branch wiring, protective earth, live measurements, and any re-termination or isolation test on mains circuits.
- A researcher may run the software, record readings, check low-voltage indicators, and operate the dashboard after the electrician has declared the setup safe.
- Do not ask a student/researcher without electrical qualification to open an energized panel, move mains conductors, or use an exposed live circuit as a test fixture.

### Required safe state before touching hardware

1. Press **Ctrl+C** to stop `main.py` and verify it has exited. Stop any other process that reads the PZEM serial device, including `manual_interface.py` or `pzem_multi_reader.py`.
2. Have the electrician isolate the mains supply using the system's approved disconnect/lockout process and verify absence of voltage before any mains wiring is touched.
3. Keep appliances disconnected during initial inspection, GPIO tests, and RS485/serial fault isolation.
4. Keep `SIMULATE_HARDWARE=0` for real hardware checks. Use `BASELINE_MODE=1` for sensor and telemetry checks; it commands relay outputs OFF. Do not switch to active control until all prerequisites in Section 8 pass.
5. Use the breaker and other installed protective devices as designed. Software, an SSR, and this QA document do not replace overcurrent protection or a physical disconnect.

**Immediate stop conditions:** unexpected energization, hot/burning smell, arcing, damaged insulation, exposed conductor, breaker trip, SSR temperature beyond its datasheet limit, any voltage at a location expected to be isolated, unstable Pi power, or repeated sensor/communications errors. Isolate safely and have the qualified electrician investigate.

## 3. Test record and pass/fail rules

Make one record per run. Record the date/time, tester names/qualifications, Pi model/OS, software revision, exact PZEM/adapter/SSR model numbers, serial device path, configured IDs, network endpoint (never the token), ambient conditions if relevant, reference meter model/calibration status, load identity/rating, and every measured result.

Use these result labels:

- **PASS:** measured result meets a pre-approved acceptance criterion and is repeatable.
- **FAIL:** measured result violates a criterion or a protective function fails.
- **BLOCKED:** safe/valid testing cannot proceed because a prerequisite is unknown or unmet.
- **N/T:** not tested; do not describe as passed.

Set numeric acceptance limits before collecting study data using the exact sensor/SSR manufacturer specifications and the research protocol. Do not invent tolerance values after seeing the results.

Suggested record columns:

| Test ID | Channel/load | Expected result/criterion | Actual reading or observation | Instrument/log reference | Pass/Fail/Blocked | Tester/date | Corrective action |
|---|---|---|---|---|---|---|---|

## 4. Gate A — planning, inventory, and design traceability

1. Retrieve the latest thesis wiring diagram, bill of materials, source version, and actual panel labels.
2. Inspect component labels without opening an energized assembly. Record the Pi model; each PZEM revision; the serial/TTL/RS485 adapter model; each SSR model; breaker/power-supply models; MPI3508/HDMI and RFID models; and cable/connector types.
3. Complete a channel map before running control:

   | Software index | Expected sensor ID | Software key | Physical appliance branch | PZEM/CT label | SSR label | Breaker label |
   |---:|---:|---|---|---|---|---|
   | 0 | 1 | `acu` | Confirm from build | Confirm | Confirm | Confirm |
   | 1 | 2 | `outlet` | Confirm from build | Confirm | Confirm | Confirm |
   | 2 | 3 | `lighting` | Confirm from build | Confirm | Confirm | Confirm |

   These are the present code assumptions, not a verified wiring map. Amend the table to match the real assembly and update the code/thesis if they disagree.
4. Confirm the communication design from the exact PZEM and adapter manuals. Record whether the link is TTL UART or differential RS485, logic voltage, transceiver direction requirements, and port. Do not connect Pi GPIO/UART pins directly to an interface unless its voltage levels and wiring are explicitly compatible.
5. Confirm the exact SSR input requirements, output AC/DC type, load voltage/current, heat sink, clearances, fuse/breaker requirements, and whether the SSR's control input can be driven by the Pi output interface. The project code assumes GPIO HIGH = ON and LOW = OFF; this does not prove the SSR input accepts that signal.
6. Mark the gate **BLOCKED** if the installed part numbers, port/interface, channel map, or wiring drawing cannot be reconciled.

## 5. Gate B — de-energized physical inspection (electrician)

With the system isolated and verified de-energized, the electrician checks the assembly against the approved drawing and applicable electrical requirements:

1. Enclosure, strain relief, terminal covers, conductor sizing, terminations, protective earth, separation of mains and low-voltage wiring, and labels.
2. Correct breaker type/rating and double-pole operation as designed; branch protection is appropriate to the conductors and intended load.
3. SSR model/orientation, terminals, input polarity, load-current rating, heat sink, ventilation, and manufacturer's required protection. A “40 A” product label alone does not establish that it is safe for this installation.
4. PZEM voltage-sense/power connections, CT placement/orientation, and the intended side of each SSR being measured. The meter must remain powered and communicative with its controlled load OFF if the software is to obtain a valid zero-load reading and then make a safe ON decision. Confirm this from the exact meter manual and wiring diagram.
5. No assumption that an SSR's off state is a service disconnect. Use the approved physical isolation method for maintenance.
6. Record photos/inspection references and mark any discrepancy **FAIL** or **BLOCKED**. Do not power the assembly until corrected and signed off.

## 6. Gate C — Pi, power, and software startup (no connected load)

1. Confirm stable Pi supply, storage, and boot. Confirm low-voltage power supply output against its label/specification using an appropriate meter.
2. Check the Pi sees the expected serial adapter. On Linux, inspect `/dev/serial/by-id/` if present and confirm the configured path. The current reader defaults to `/dev/ttyUSB0`; do not assume that path belongs to the PZEM adapter if multiple serial devices are attached.
3. Confirm only one program owns the PZEM serial device. `main.py`, `manual_interface.py`, and `pzem_multi_reader.py` must not poll it concurrently.
4. Check `.env` on the Pi without sharing its contents. Confirm `SIMULATE_HARDWARE=0`, `BASELINE_MODE=1`, correct `API_BASE_URL`, and a non-placeholder `DEVICE_API_TOKEN` matching Flask. Never put the token in the test record or screenshots.
5. Start Flask and confirm the dashboard opens from the authorized test device. Start the sensor-reader diagnostic only when the main monitor is stopped.
6. Expected result: Pi remains stable; correct port is selected; there are no permission/serial-open errors. If the port is unknown or another process owns it, mark **BLOCKED** and fix before sensor tests.

## 7. Gate D — sensor and serial-bus diagnostic

This isolates communication before connecting any appliance load. The current source assumes a serial Modbus RTU interface at 9600 8-N-1 and sensor IDs 1–3; verify these assumptions against the exact installed PZEM revision before relying on this test.

1. With `main.py` stopped and the physical system in the approved safe state, run the existing reader from the project directory:

   ```bash
   cd ~/last
   source venv/bin/activate
   python3 pzem_multi_reader.py
   ```

   Stop it with **Ctrl+C** before changing the bus or starting another program. It tests all IDs in a loop; it is not a safe substitute for an interface-specific vendor diagnostic if the physical adapter/protocol differs.
2. Begin with one confirmed-good PZEM and matching adapter. Have hardware changes made only de-energized and per the manufacturer's manual. Confirm its actual slave ID matches the ID under test and that no second device has the same ID.
3. Record at least 10 consecutive polls for the one-meter setup: response/error for each register, voltage, current, power, energy, and timestamp. A pass requires repeatable responses and values within the predeclared meter/reference criteria; CRC errors, timeouts, short frames, or impossible jumps are **FAIL**, not acceptable noise.
4. Add the second and third PZEM one at a time, powering/isolation between wiring changes as required. Repeat the same observations and verify IDs remain unique. If a failure returns only after adding a device, isolate that segment and check addressing, bus topology/termination (if specified for the actual interface), wiring, power, and device condition.
5. For each channel, compare voltage and a controlled load reading with a suitable calibrated reference instrument, with the electrician arranging the safe measurement. Compare current and power at multiple known loads if required by the protocol. Calculate relative error as `abs(system - reference) / abs(reference) * 100%`; do not use this formula near a zero reference reading. Judge against a criterion chosen in advance from specifications and study objectives.
6. With the controlled load OFF, expect the meter to remain online and show its supply voltage (if its model measures/outputs that state); expect load current and power to be near zero within instrument resolution. If the meter disappears when its SSR opens, stop: its supply/sense wiring or system design creates a control deadlock and must be reviewed by the electrician.
7. Treat the previously reported `131072 Wh` and `2097.152 kWh` as suspect until raw register results and accumulated state are validated against the meter and reference. Do not use those values in the thesis dataset.

**Gate D pass:** all three expected IDs communicate repeatably, every channel maps to the correct branch, reference comparisons meet the predeclared criteria, and no unexplained CRC/timeout/implausible-value errors remain. Otherwise keep relays OFF.

## 8. Gate E — baseline telemetry and user interface

1. Keep `BASELINE_MODE=1` so relay outputs are held OFF. Start Flask, then `main.py` on the Pi. Do not run another serial reader at the same time.
2. Observe console output for each sensor, daily-energy calculation, API errors, and cycle continuity. A valid telemetry request should be accepted by Flask; HTTP 400 is a fail. Capture the response body and timestamp (the current helper may print only the status text); do not record the bearer token.
3. Confirm all expected channels appear on the dashboard with the right identity, units, timestamps, and readings. Wait through a predeclared observation interval and count missing/stale samples.
4. Confirm shutdown with **Ctrl+C** leaves all relay outputs commanded LOW. Confirm restart begins in the documented state.
5. Exercise dashboard authentication and RFID separately according to their manuals and thesis requirements. Current code facts: `main.py` does not call RFID; `manual_interface.py` handles RFID but is read-only and shares the serial bus. The MPI3508 is an HDMI monitor, so validate that the Pi's terminal or browser dashboard is visible over HDMI; there is no separate GPIO LCD driver in the active runtime. Record the RFID integration gap if the final study claims RFID is part of the primary control loop.

**Gate E pass:** stable sensor data from all branches is accepted by Flask and visible under correct channel names, with no unexplained 400s, staleness, or unit/mapping errors.

## 9. Gate F — SSR and connected-load operation

### F1. GPIO input check (loads disconnected)

1. Stop `main.py` and all other GPIO/serial processes. Keep appliance loads disconnected and have the electrician confirm the safe setup.
2. On the Pi, run `python3 SSR_Test/ssr_test.py` from `~/last`. Type the requested confirmation only when the loads are in fact disconnected.
3. Test one channel at a time if necessary with `--pin 17`, `--pin 27`, or `--pin 22` (BCM numbering). The script pulses HIGH briefly, then LOW, and commands all relay pins LOW on exit.
4. Observe only the SSR low-voltage input indicator or low-voltage signal using an appropriate instrument and trained person. This verifies GPIO/control-input behavior only, not that the SSR can switch an AC load.

### F2. Supervised functional load check

Proceed only after Gates A–E pass, the electrical inspection is signed off, and SSR/PZEM model/interface discrepancies are resolved.

1. The electrician selects and connects a suitable, known, appropriately rated test load under the approved lab procedure. Do not begin with a person-dependent, heat-producing, motor, compressor, or valuable appliance.
2. Confirm the load is OFF, readings are valid and stable for its branch, limits are configured from the actual system design, network/API is healthy, and the physical disconnect is immediately accessible to the qualified operator.
3. Set `BASELINE_MODE=0`, restart the Pi monitor, and request only that one channel ON from the authenticated dashboard. Do not use a direct GPIO script to energize a connected load.
4. Verify the intended SSR input indication and branch state through approved instruments, and verify matching PZEM voltage/current/power, dashboard update, and correct channel label. Record the observed startup time and reading.
5. Request OFF from the dashboard. Verify current/load stops as expected and software commands the output LOW. Account for the SSR manufacturer's specified off-state leakage; an SSR off state is not necessarily equivalent to galvanic isolation.
6. Repeat one channel at a time. Confirm unrelated channels remain OFF. Stop immediately for unexpected behavior, wrong-channel activation, measurement mismatch, heating, or protection trip.
7. Test fault responses with loads disconnected or through a controlled software/simulation method: unavailable API, invalid/missing sensor sample, invalid limit configuration, and process shutdown should command relays OFF. Do not create an overload on a live appliance to test a cutoff.
8. Finish by turning all requests OFF, stopping the Pi process, and verifying the documented safe output state. The electrician performs final isolation as needed.

**Gate F pass:** only the requested channel switches; measured values are plausible and match reference within the predeclared limits; OFF and fault behavior are verified; no protective or thermal issue occurs. A GPIO pulse alone is not a pass for this gate.

## 10. Gate G — study performance data and expert evaluation

1. After the hardware gates pass, define the measurement plan before collecting data: loads, operating schedule, sample interval, duration, channel mapping, comparison baseline, missing-data treatment, and acceptance/error thresholds.
2. For the thesis's energy-efficiency objective (at least 20%), compare like-for-like before/after conditions. Separate actual energy reduction from changes in occupancy, weather, operating hours, or load; monitoring/control working by itself does not prove a 20% saving.
3. Preserve raw readings, reference measurements, calibration notes, serial/API error logs, configuration version, and analysis steps. Exclude invalid sensor intervals transparently rather than replacing them with guessed values.
4. Only after objective operational results are documented, conduct the thesis's expert acceptability survey for design and operation. The three-point survey is a perception measure; it does not replace electrical inspection, calibration, or reliability results.

## 11. Current known blockers from the project logs

- Modbus reads for sensor IDs 2 and 3 repeatedly timed out or returned malformed/CRC-invalid frames; ID 1 was intermittent. Fix Gate D first.
- Flask received telemetry requests but returned HTTP 400. Inspect the response body after sensor data is stabilized; do not assume the dashboard has valid readings merely because the network connects.
- The displayed daily energy was implausible relative to the other displayed values and should not be used for results until validated.
- The relay has not been verified switching a connected load. The present `SSR_Test` intentionally requires loads disconnected.
- Confirm that the token is the same on both processes and rotate it if the shared transcript or prior displayed value is accessible beyond the authorized project team.
- Resolve the thesis/BOM/software mismatches listed in Section 1, especially meter interface, SSR model, channel order, RFID integration, and LCD integration.

## 12. Final QA sign-off

Do not label the prototype “completed,” “accurate,” “safe,” or “achieved 20% energy savings” solely because the Flask page loads or an SSR input LED lights. Sign-off requires documented passes for the relevant gates, an electrician's approval of mains hardware, repeatable valid measurements, correct telemetry/channel mapping, and supervised system-control evidence.

| Gate | Result | Evidence/reference | Sign-off/date |
|---|---|---|---|
| A — design and inventory |  |  |  |
| B — de-energized inspection |  |  |  |
| C — Pi/software startup |  |  |  |
| D — sensors/serial bus |  |  |  |
| E — baseline telemetry/UI |  |  |  |
| F1 — GPIO input |  |  |  |
| F2 — supervised connected load |  |  |  |
| G — performance data/expert evaluation |  |  |  |
