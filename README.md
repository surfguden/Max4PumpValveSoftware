# Max4 Pump & Valve

PySide6 controller for one to four CETONI pumps and TriContinent serial valves.
Always starts disconnected. Hardware actions and helper sequences are implemented
in `pumpvalve/application.py`; the UI only gathers inputs and displays results.
Qmix and serial traffic have separate serialized owners. Simulation never opens
either physical bus.

Download/extract `dist/Max4PumpValve-1.0.0-windows-x64-portable.zip` and launch the
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
Tests: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --basetemp build/test-temp`.

Syringe setup follows CETONI Elements manual sections 7.9 and 7.11. Maximum pressure
is profile metadata, not an active pressure monitor. SDK runtime redistribution
rights were not established locally; native vendor DLLs are external. The Python
wrapper is included with original source notices intact. See `docs/SDK_RUNTIME.md`
and `docs/SOURCE_PROVENANCE.md`. Clean Windows and physical device validation remain
separate acceptance steps; no hardware testing was authorized or performed.
