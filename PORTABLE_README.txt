Max4 Pump & Valve 1.1.0 - portable Windows x64 distribution

TERMINAL / AI AGENTS / SSH
Max4PumpValveCtl.exe run --gui keeps a console attached to the GUI and accepts
commands while printing events from both interfaces. Type "help" for commands.
From another terminal: Max4PumpValveCtl.exe status, send, or watch.
For unattended SSH: Max4PumpValveCtl.exe run --headless --no-stdin.
Use shutdown to end the owner. See TERMINAL.md for complete examples and JSON.
All interfaces use exactly the same application commands and device reservations.

Extract the entire ZIP, then launch Max4PumpValve.exe. Keep _internal beside it.
Requires Windows 10 version 1903 or newer, x64 (Windows 11 recommended).
Python, pip and a source checkout are not required. The application always starts
disconnected. It never restores motion or automatically connects saved devices.

HARDWARE DEFAULTS
Simulation controls are disabled and hidden in normal operation. Both GUI and
terminal connections use hardware. Offline build tests explicitly enable the
internal test simulator; this is not a saved setting or normal operating mode.
Default configuration: C:\Users\Public\Documents\QmixElements\Projects\default_project\Configurations\dual
Default SDK: %LOCALAPPDATA%\CETONI_SDK (resolved for the current Windows user).
Default serial valve connection: COM3, 9600 baud.

REAL DEVICES
Connect only when hardware operation is intended. Select an existing
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
positive flow rates. Flow mode stops at the configured fill limit. Right-click
Refill or Empty to set its separate positive flow rate in a small dialog. Max
selects the current device-reported maximum at the time motion starts. Confirm
with OK; changing a rate does not move the pump. Save all settings retains both
rates independently of the main Flow field. Paired continuous flow is omitted.
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
minimum, wait for idle, refill to the chosen level, wait for idle, then de-energize.
The helper polls the SDK is_pumping flag at approximately 50 ms intervals and
checks faults and zero flow before continuing. Fill readings show progress;
stirring never advances or fails because a volume threshold was crossed or missed.
Targets still set the travel endpoints. Injection additionally verifies dose volume.
Full speed uses the
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
Save all settings remembers connection paths, COM settings, valve names, syringe
profiles and pump assignments, pump mode/volume/flow/level and valve mappings,
helper selections/parameters/deadline, pump count, selected tab and window size.
Load settings restores those saved controls without connecting or moving devices.
Profiles must still be applied explicitly after connecting. Data and logs go to
%LOCALAPPDATA%\Max4PumpValve, never to the extracted distribution.
PUMPVALVE_DATA_DIR can override this location for controlled testing.

VERIFICATION
sdk-import-verification.json checks that the packaged Qmix DLL loader imports
without loading native DLLs or opening a bus. Plastic profiles are supported;
Configure Syringe includes a BD 5ml preset using the inherited BD geometry.
Pressure remains metadata only, and geometry must match a mounted syringe.
verification.json records the packaged simulation-only smoke test. Real hardware
and launch on a clean Windows machine without Python have not been validated.
The application has been packaged with its own Python and Qt runtime. See the
source README and docs/VERIFICATION.md for detailed checks and limitations.
