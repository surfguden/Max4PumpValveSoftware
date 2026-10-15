# GUI, PowerShell and SSH command interface — 1.1.0

Extract the portable ZIP. It contains Max4PumpValve.exe (GUI) and
Max4PumpValveCtl.exe (console/client), sharing the same bundled runtime.
Neither executable needs Python installed.
Connections default to real hardware, using saved paths and COM settings when
arguments are omitted. Simulation is disabled during normal operation. The GUI's
Save all settings and Load settings cover connection settings, profiles, pump
controls and mappings, helper controls, and layout. Loading settings does not
connect or move devices; syringe profiles must be applied explicitly.

## Same terminal, GUI open

```powershell
./Max4PumpValveCtl.exe run --gui
```

The console stays attached. Type commands there while using the GUI. Both sources
produce JSON-line command/result events in that console. Example input lines:

```text
pumps connect
valves connect
pump map --pump 1 --energized 2 --deenergized 1
helper stir --pump 1
helper stop
status
shutdown
```

Wait for each command's response before issuing dependent commands. Input is
handled asynchronously so Stop remains available during a reference operation or
scan. Commands from separate clients can also execute concurrently; bus owners
serialize I/O and the application rejects conflicts. GUI input fields only submit
commands when you click their action buttons; typing values does not move devices.
Ctrl+C, `shutdown`, closing the GUI, or stdin EOF stops this owner and requests
device cleanup. Use `--no-stdin` when keeping an owner running without interactive
input; client disconnection alone then leaves the owner and GUI running.

## Separate terminal / agent tool calls

Open the GUI normally, or keep an attached owner running. Then use another
PowerShell/SSH session under the same Windows account:

```powershell
./Max4PumpValveCtl.exe status
./Max4PumpValveCtl.exe pumps connect
./Max4PumpValveCtl.exe valves connect
./Max4PumpValveCtl.exe pump map --pump 1 --energized 2 --deenergized 1
./Max4PumpValveCtl.exe helper inject --pump 1 --address 2 --volume 0.1 --rate 1000 --before input --after output
./Max4PumpValveCtl.exe helper stop
./Max4PumpValveCtl.exe pump stop --pump 1
./Max4PumpValveCtl.exe watch
```

`watch` includes GUI and terminal commands, results, helper steps/progress, and
observed state. Use `watch --once` for a finite replay, or `watch --since 123
--session SESSION_ID` to resume from a returned event cursor. Sequence numbers
are per application session. A changed session rejects an old cursor; reset to
0. A bounded replay-buffer gap is reported as `events.gap`. The rotating durable
event journal is `%LOCALAPPDATA%/Max4PumpValve/events.jsonl` (plus up to 3 backups).
Command results and observed states are separate: a successful helper.start
means accepted/started, not that the sequence delivered liquid. Confirm its
matching helper.state with running=false and step=Completed. Inspect Error or
Cancelled states and their verification details before issuing further motion.

If an agent's shell runner closes stdin after launch, use `run --gui --no-stdin`
or `run --headless --no-stdin` in a background process, and issue separate client
commands. An interactive attached console requires a persistent terminal session.

## Headless owner over SSH

```powershell
./Max4PumpValveCtl.exe run --headless --no-stdin
```

Run that in the owning SSH session. Send commands from another SSH session, or
start it using PowerShell Start-Process with `-WindowStyle Hidden` and redirected
stdout/stderr. `--gui` needs an interactive Windows desktop; headless mode has
no Qt event loop or widgets and is appropriate for an unattended SSH session.
Clients discover an authenticated loopback endpoint from the per-user data
folder. The endpoint never binds to an external network interface; SSH executes
the client on the workstation. Protect the Windows account/data folder as you
would any account authorized to control the instruments. Do not expose the token
in endpoint.json. PUMPVALVE_DATA_DIR must match between owner and clients.
An owner lock rejects a second host in that same folder. Deliberately using
different data folders creates independent sessions; do not connect two such
sessions to the same physical buses.

## Canonical JSON requests

GUI actions and CLI shorthand both create the exact same command envelopes.
For `pump.command` in `refill` or `empty` mode, JSON `"rate":"max"` resolves the
current device-reported maximum on the device worker when the command executes.
Numeric rates remain µL/min. The GUI stores a separate rate for each action.
For scripting, use an argument file to avoid PowerShell JSON quoting issues:

```powershell
'{"pump":1,"mode":"level","value":0.5,"rate":1000}' | Set-Content -Encoding utf8 arguments.json
./Max4PumpValveCtl.exe send pump.command --args-file arguments.json --id experiment-42
```

Or provide a complete request on stdin:

```powershell
'{"id":"stop-42","command":"pump.stop","args":{"pump":1}}' | ./Max4PumpValveCtl.exe json
```

Responses contain ok, command, id, and result or error/code. Exit 0 means the
command succeeded; exit 1 means failure, and Ctrl+C returns 130 for a client.
Command IDs suppress identical retries during the live session (last 1024
completed/active requests retained); reusing an ID with different arguments is
rejected. Do not automatically retry motion after an owner restart: the new
session has no old retry history. Events include command_id, source (gui or
terminal), UTC time, session, and sequence. Helper events carry the initiating ID.

Run `help` or `send help` for command names and `--help` for CLI options.
Pump numbers are 1–4 throughout the public interface. TriContinent addresses are
physical switch integers 0–14, not DT wire addresses. Volumes are mL; rates are
µL/min. Syringe configuration:

```json
{"command":"pump.syringe","args":{"pump":1,"profile":{"name":"Custom","material":"Glass","inner_diameter_mm":4.78,"max_piston_stroke_mm":55,"minimum_ml":0,"maximum_ml":0.98,"max_pressure_bar":1}}}
```

`settings.save` accepts config/sdk/port/baud/valves/syringes/pump_profiles/ui using the
settings schema. Syringe commands save the profile after a successful application.
`settings.load` accepts no arguments for the per-user file, or `{"path":"C:/settings.json"}`
to import a selected settings file. Imported settings are validated before use.
Stirring accepts `wait_empty` and `wait_full` in seconds (0–3600, default zero),
and `valve_begin` / `valve_end` as `Energized` or `Not energized`. CLI equivalents
are `--wait-empty`, `--wait-full`, `--valve-begin` and `--valve-end`.
The sequence deadline includes motion and both cancellable, fault-monitored waits.
No saved profile or configuration triggers automatic connection or motion.

### Diagnostics, recovery and syringe state

`pump diagnose --pump 1` captures pump/drive error records, enabled and
position-sensing state, fill/flow and SDK emergency events. A newly observed
fault is captured before error recovery; failed connections retain their last
diagnostic observation. Diagnostics are available with pumps disconnected.
The GUI's Diagnostics button displays the same result. Fault records are also
included in helper failure events and the application log.

`pump recover --pump 1` explicitly stops, requests one fault clear, waits up to
one second for acknowledgement and checks position sensing before enabling.
A persistent fault is never force-enabled. The result requires verified stopped
readback. Use Clear fault & verify in the GUI for the same command. If connection
failed, inspect retained diagnostics and the hardware before reconnecting; a
fresh owner process may be needed for stale SDK state.

Stop polls readback for up to one second to allow deceleration. Failure reports
`pumping`, `flow` and `fault` separately; zero flow with pumping=true is still
unverified. Polling releases the bus between reads so priority Stop stays usable.
Stop and helper completion require consecutive stable idle fill readings so a
fill sample taken before the SDK idle transition is not reported as final volume.

Status includes `syringe_expected`, `syringe_required` and `syringe_applied`.
A saved syringe assignment requires explicit application on every connection,
including after reconnect, even when XML geometry happens to match. Dosing is
blocked until the profile is applied and geometry readback matches. The GUI
displays APPLY SYRINGE BEFORE DOSING and leaves Configure syringe available.
Loading settings alone never applies a syringe or causes movement.

### Reference after power loss

An unreferenced pump connects with its drive disabled and status
`position_initialized=false`. The GUI shows REFERENCE REQUIRED and enables
Reference move and Configure syringe. Dosing and helpers remain blocked.
Connection never starts homing automatically.

Disconnect the syringe first. Reference move displays a warning popup and only
proceeds after confirmation; Cancel is the default. From the terminal, use
`pump reference --pump 1 --syringe-disconnected` to explicitly acknowledge the
disconnected syringe and initialize the encoder. JSON requests must include
`"syringe_disconnected":true`. Missing acknowledgement rejects the command before
motion. Reference does not require an already applied syringe profile. It
uses the serialized bus, leaves priority Stop available during polling, and
requires both completed calibration and initialized position sensing before
reporting success. Stop can cancel reference. Apply the saved syringe profile
before subsequent dosing. Persistent faults still prevent connection/enable.

Known disconnected pump/valve dependencies reject helper.start immediately;
accepted helpers still require their final Completed event to establish success.

## Hardware authorization

Connections use real hardware in normal operation. Simulation is disabled.
These commands explicitly select hardware paths and serial settings:

```powershell
./Max4PumpValveCtl.exe pumps connect --hardware --configuration 'C:\Qmix configuration' --sdk 'C:\CETONI SDK'
./Max4PumpValveCtl.exe valves connect --hardware --port COM10 --baud 9600
```

Only run these after the human authorizes the hardware test and its intended
motion. There is no automatic hardware probe, calibration, or resumption at
startup. The existing readiness checks, reservations, target-fill operations,
cancellation rules and bounded deadlines apply identically to both interfaces.
Native SDK I/O can still hang: a priority Stop cannot physically execute until a
hung vendor call returns. A timed-out bus is quarantined and stop state is
reported as unverified. All development verification for this release uses
simulations/fake devices; physical testing remains separately authorized.
