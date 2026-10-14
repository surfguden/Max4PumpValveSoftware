# Verification — 2026-10-14

Build host: Windows 11 x64 (10.0.26200), Python 3.12.14, PySide6 6.11.1,
PyInstaller 6.22.3. All checks below are simulation or fake-device checks.
No physical Qmix or serial connection was opened.

## Automated acceptance

88 tests passed, with 18 additional unittest subtests. Tests cover:

- All three refill targets with both maximum-speed and defined-rate Stirring.
- All 16 four-port and all 9 three-port Before/After injection combinations.
- Finite target-fill dosing, final stopped-flow checks, injected volume,
  remaining volume and progress from fill-level readback.
- Invalid/nonfinite rates and volumes, insufficient volume, unsupported and
  query-only valves, required Cetoni valve mapping, syringe geometry and limits.
- Faults before/during delivery, premature stop, wrong valve readback, timeouts,
  cancellation with no later transition, prompt stop and reservation release.
- Pump and serial-bus reservations, serialized thread ownership, native I/O
  timeout quarantine, nonblocking reference polling and stop requests.
  Status polling also remains nonblocking while a connection is pending.
- Disconnected startup, all three Qt tabs, saved settings and paths with spaces,
  supported-position dropdowns, responsive Qt event processing, syringe dialog.
- Reused serial driver's fake transport: framing, errors, baud/flow-control setup,
  identification, scanning, missing saved devices, profile support, verified
  moves, transport cancellation and serial failures. No COM port is opened.

Repeatable command:

```powershell
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --basetemp build/test-temp
```

## Packaged executable

The actual folder-mode executable passes the opt-in `--smoke-test REPORT_PATH`
check. It renders the three tabs and syringe dialog, verifies disconnected
startup, connects simulations, runs Stirring and Injection through the UI,
checks final progress/readback, persists a configuration path containing spaces,
and checks actionable missing-SDK/XML errors without loading a real bus.
The Qmix Python wrapper's packaged location is checked explicitly.
The ZIP includes the machine-readable result as `verification.json`.
Screenshots under `build/packaged smoke results` were visually inspected.

`tools/test_extracted.ps1` additionally extracts the delivered ZIP into a path
with spaces, marks distribution files read-only, removes Python directories from
PATH/PYTHONPATH/PYTHONHOME, and repeats the executable smoke check using an external
user-data folder. This passes. It does not uninstall Python or create a clean OS.

A build-host Poppler ICU DLL initially caused a Qt import failure. The final
specification excludes incompatible ICU DLLs and uses Windows' ICU API (Windows
10 1903+). The corrected executable was rebuilt and smoke-tested successfully.

## Outstanding physical/deployment acceptance

- A clean Windows x64 machine with no Python installed was unavailable. Launch
  on that environment remains unverified, despite bundled Python and the
  isolated-PATH executable check.
- Real Qmix connection, syringe geometry/limits, built-in valve mapping, reference
  movement, dosing accuracy and TriContinent motion require separate authorization.
- Vendor native runtime redistribution rights were not established locally.
  An official external CETONI SDK runtime and hardware drivers are required.
- Maximum syringe pressure is stored metadata, not active pressure monitoring.
- In-process native SDK calls cannot be forcibly interrupted safely. If a native
  call hangs, the owner is quarantined and the priority stop remains unverified
  until that call returns. Sequence deadlines and UI responsiveness remain bounded.

The source references were read/copied only; their files and branches were not
modified. Their prior hardware-test reports do not validate this combined app.
