Max4 Pump & Valve 1.0.0 - portable Windows x64 distribution

Extract the entire ZIP, then launch Max4PumpValve.exe. Keep _internal beside it.
Requires Windows 10 version 1903 or newer, x64 (Windows 11 recommended).
Python, pip and a source checkout are not required. The application always starts
disconnected. It never restores motion or automatically connects saved devices.

SIMULATION
Simulation is selected by default on both device tabs. Click Connect pumps and
Connect & Scan. Simulated time is accelerated 30x. The helper tab operates on these
devices with visible fill-level progress. Map each pump's energized/de-energized
built-in valve positions explicitly before Stirring Cycle is enabled.

REAL DEVICES
Uncheck Simulation only when hardware operation is intended. Select an existing
Qmix Elements folder containing XML files. Keep this folder external to the ZIP.
Install the vendor CETONI SDK x64 runtime separately and browse to its runtime
folder, or set QMIXSDK to that folder before launch. See SDK_RUNTIME.md.
After changing an already loaded runtime, restart the app.
Select the TriContinent COM port and 9600 or 38400 baud, then Connect & Scan.
Install the appropriate USB serial/CAN device drivers from their manufacturers.
Only identified supported, healthy valve profiles allow movement. Query-only
devices can still be identified. No TriContinent plunger control is included.

PUMPS AND SYRINGES
Start applies Volume, Flow or Syringe Level at the chosen flow rate. Positive
volume/flow dispenses; negative volume/flow aspirates. Volume and Level modes use
positive flow rates. Flow mode stops at the configured fill limit. Refill/Empty
use the configured positive flow field. Paired continuous flow is omitted.
Configure Syringe supports saved custom profiles, inner diameter or scale-based
geometry, piston stroke, fill limits and maximum pressure metadata. Pressure is
not actively monitored. Confirm geometry against the mounted syringe. Geometry
bounds are for neMESYS low-pressure modules (1-35 mm diameter, 10-65 mm stroke).
Applying a profile refreshes live limits and clears the UI flow setting.
Reference Move is an explicit calibration action with a 60-second deadline.
Nothing references or resumes at startup. Syringe profiles are remembered but
must be applied explicitly after connecting; they are never silently reapplied.

HELPERS
Stirring performs one cycle: map/verify Cetoni valve, empty to the configured
minimum, refill to the chosen level, verify, then de-energize. Full speed uses the
current syringe's device-reported maximum. No TriContinent valve is commanded.
Injection verifies the selected TriContinent valve, moves to Before, doses using
the SDK target-fill operation, verifies stopped flow, then moves to After.
The Cetoni built-in valve remains unchanged. Participating devices are reserved.
Stop requests pump stop and skips later transitions; always inspect observed and
unverified state messages. The sequence deadline is adjustable on the helper tab.
Native SDK calls cannot be forcibly interrupted safely in-process. A hung native
call quarantines that bus; a stop is queued with priority, but remains unverified
until the call returns. Resolve hardware state before restarting the application.

SETTINGS AND LOGS
Save Settings / Save Configuration remembers configuration/runtime paths and COM
settings and valve names. Data and rotating logs are written to
%LOCALAPPDATA%\Max4PumpValve, never to the extracted distribution.
PUMPVALVE_DATA_DIR can override this location for controlled testing.

VERIFICATION
verification.json records the packaged simulation-only smoke test. Real hardware
and launch on a clean Windows machine without Python have not been validated.
The application has been packaged with its own Python and Qt runtime. See the
source README and docs/VERIFICATION.md for detailed checks and limitations.
