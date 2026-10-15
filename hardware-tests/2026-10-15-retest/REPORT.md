# Implemented fixes and physical retest — 15 October 2026

**Final result: all 50 recorded physical acceptance checks passed after power
cycling, reference initialization and the final software fixes.** Counts include
setup and failure-handling checks, not 50 full stirring/injection sequences.

This run was performed on `main` before the implementation was committed.
The pre-existing root `.gitignore` edit was preserved. The tested application is in
`dist/hardware-accepted-20261015/Max4PumpValve-1.1.0`.

## Implemented

- Capture pump and drive error records, SDK emergency events, enabled state,
  position sensing, fill and flow at a newly observed fault and before clearing
  faults on connection. Retain diagnostic history after failed connections.
- Add `pump diagnose --pump 1` and `pump recover --pump 1`, with matching GUI
  buttons. Recovery stops first, clears once, checks position sensing and never
  forces a persistent fault into enabled state.
- Poll Stop readback while leaving the serialized bus available for priority
  commands. Report pumping, flow and fault separately. Require stable idle fill
  samples rather than using one reading from the deceleration interval.
- Track saved versus actively applied syringe geometry. Block motion after
  reconnect until explicit syringe application; verify geometry readback.
- Reject disconnected pump/valve helper dependencies synchronously.
- Refresh helper final volume after the final valve action. Consecutive idle
  samples avoid treating a pre-idle fill reading as the completed volume.
- Show connection failures, syringe application state and diagnostics in the GUI.
  A visible desktop launch replaced the earlier hidden console launch.

## Recovery and test method

The original persistent fault survived normal reconnection and an application
restart. The operator then power-cycled the pump. That cleared the fault but
invalidated the incremental encoder reference. A supporting terminal SDK script
performed an explicit reference move while the application's Qmix bus was closed.
Reference completed with position sensing initialized and no fault. No position
counter value was fabricated or restored.

All subsequent motion tests used the public terminal interface of the packaged
application, sharing the visible GUI owner. Real pump 1 used the supplied public
`script test/Configurations/single` configuration. The BD 5ml Plastic profile
was applied explicitly: 12.07 mm bore, 43.6984047329 mm stroke, capacity 5.0 mL.
TriContinent was COM6/9600, address 2, VSeries four-port loop. Simulation flags
were false throughout the physical tests.

Early hardware runs exposed stale completion values. One early harness check
also compared independent read times within 1 nL, below the roughly 4 nL encoder
increment observed here. That consistency tolerance was corrected to 0.01 µL;
the displacement acceptance remained max(1 µL, 0.1% of requested volume).
Earlier failures remain in the raw logs; the final run is summarized separately.

## Automated verification

149 tests passed, with 18 additional subtests. Coverage includes transient and
persistent Stop state, delayed and failed fault recovery, syringe reapplication
after reconnect, disconnected helper preflight, and volume refresh after the
ending valve action. The full suite also includes GUI layout and IPC checks.
Packaged terminal/GUI verification is saved under
`build/hardware-accepted-console/report.json`.

## Final measured results

All three stirring modes completed: Defined volume to 0.7 mL at 5,000 µL/min,
Volume before emptying at 10,000 µL/min, and Full syringe to 5 mL at the
device-reported maximum of 43,449.3359 µL/min. Beginning/end valve readback and
final stopped state passed. The requested one-second empty/full dwells measured
0.9998/0.9988 seconds between journal events; the half-second dwells measured
0.5051/0.5061 seconds. Event timestamps are not precision timing instrumentation.

| Requested injection | Rate (µL/min) | Encoder displacement (µL) | Error (µL) |
|---|---:|---:|---:|
| 10 µL | 1,000 | 9.999888 | -0.000112 |
| 100 µL | 5,000 | 99.998883 | -0.001117 |
| 500 µL | 10,000 | 499.998388 | -0.001612 |

The helper's final injected/remaining volume agreed with settled readback within
the 0.01 µL comparison tolerance. Each injection ended at zero flow with the
requested final TriContinent position. These numbers use the configured syringe
geometry and encoder; they are not independently calibrated liquid volumes.

Manual Stop passed during positive and negative 5,000 µL/min flow. Complete
terminal round trips were 0.509 and 0.499 seconds. Subsequent two-second checks
found zero reported flow and no displacement exceeding the 1 µL stability limit.

Cancellation passed during Emptying, Refilling, Waiting after empty, Waiting
after full, and active injection using both helper.stop and pump.stop. Later
valve transitions were skipped, reservations released, and stopped state verified.
A forced one-second deadline stopped the pump; out-of-range/zero requests and
insufficient injection volume were rejected; a subsequent valid injection passed.
Both Cetoni positions and all 16 TriContinent before/after combinations passed.

Final GUI owner remains open with both buses connected, no helper running,
no reservations, no pump fault and zero reported pump flow. TriContinent is
at verified `input`. Final exact state is in `final-status.json`.

## Remaining improvements and practical limits

1. At the time of this run, position initialization after power loss needed the
   separate authorized terminal reference script. The subsequent in-app workflow
   is recorded in `../2026-10-15-reference-warning/REPORT.md`.
2. Investigate the original persistent drive fault and the mismatch between
   fault=true and the vendor's resolved-error code 0. Power cycling and homing
   restored operation, but did not establish the original root cause.
3. Validate delivered liquid volume and stop-flow leakage using a balance or
   independent flow sensor. These tests establish encoder displacement and SDK
   flow/idle feedback; they do not certify liquid delivery accuracy or leakage.
4. Preserve fault/position readiness evidence across application restarts in a
   dedicated diagnostic export. Current live history is bounded and journals
   rotate; retain the test evidence before long-term reuse.

Native SDK calls remain noninterruptible in-process. Terminal Stop round trips
include client startup and IPC; they are not direct measurements of motor-stop
latency or hard real-time guarantees.

## Evidence and reuse

- `summary.json`: most recent run outcomes, injection errors, Stop timings and
  dwell timing derived from command-correlated events.
- `results.jsonl` and `cli-transcript.jsonl`: all runs, including earlier failures.
- `accepted-build-run-output.jsonl`: final packaged physical test run.
- `session/events.jsonl*`: actual GUI/terminal owner journals.
- `reference-log-after-clear.txt`: successful reference initialization.
- `gui-window-state.json`: visible window handle and foreground activation.
- `build-manifest.json`: branch, base commit and source/executable SHA256 values.
- `retest.py`: full matrix. `Launch-TestApp.ps1` reuses/opens this test GUI with
  the correct saved data folder. Neither launch nor settings load causes motion.

The authenticated endpoint descriptor and owner lock are ignored runtime files.
