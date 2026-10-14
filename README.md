# Max4 Pump & Valve

PySide6 controller for one to four CETONI pumps and TriContinent serial valves.
Always starts disconnected. Hardware actions and helper sequences are implemented
in `pumpvalve/application.py`; the UI only gathers inputs and displays results.
Qmix and serial traffic have separate serialized owners. Simulation never opens
either physical bus. Simulation is disabled in normal operation; only local
offline tests opt into it. Connections default to hardware, with the public
Qmix Elements dual configuration, %LOCALAPPDATA%/CETONI_SDK, and COM3/9600 baud.
Save all settings / Load settings persist and restore the complete control setup
without automatically connecting devices, applying syringe profiles or moving them.

Version 1.1 adds one shared command dispatcher for GUI and terminal, an attached
console with a simultaneous GUI, a headless owner, authenticated local clients,
and live/replayable JSON-line events. See `docs/TERMINAL.md` for PowerShell/SSH
examples, command schemas and hardware authorization.

```powershell
./Max4PumpValveCtl.exe run --gui
# In another terminal while that owner runs:
./Max4PumpValveCtl.exe status
./Max4PumpValveCtl.exe watch
```

Download/extract `dist/Max4PumpValve-1.1.0-windows-x64-portable.zip` and launch the
executable. See `PORTABLE_README.txt` for operation and runtime setup.

## Source and repeatable Windows build

Use Python 3.12 x64 on Windows:

```powershell
./build_portable.ps1
```

The script creates an isolated environment, installs pinned dependencies, runs
simulation/fake-device tests, packages folder mode, runs the packaged smoke test,
retains license notices, and writes the versioned ZIP and SHA256. A complete source
checkout is only required for rebuilding. Source launch: `.venv/Scripts/python.exe main.py`.

For an existing prepared build environment: `./build_portable.ps1 -SkipDependencyInstall`.
Tests: `.venv/Scripts/python.exe tools/run_tests.py -q -p no:cacheprovider --basetemp build/test-temp`.

Syringe setup follows CETONI Elements manual sections 7.9 and 7.11. Maximum pressure
is profile metadata, not an active pressure monitor. SDK runtime redistribution
rights were not established locally; native vendor DLLs are external. The Python
wrapper is included with original source notices intact. See `docs/SDK_RUNTIME.md`
and `docs/SOURCE_PROVENANCE.md`. Clean Windows and physical device validation remain
separate acceptance steps; no hardware testing was authorized or performed.
