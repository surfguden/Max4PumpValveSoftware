# Physical hardware test report — 15 October 2026

**Result: partial execution; pump acceptance failed and remaining pump tests
are blocked by a persistent hardware/SDK fault.** This is not a successful
stirring or injection acceptance run.

The packaged application was controlled through `Max4PumpValveCtl.exe`, with its
GUI open on the same owner. No application source was changed. The test scripts
and report are new. The pre-existing root `.gitignore` modification was preserved.

## Setup

- Build: `dist/Max4PumpValve-1.1.0-1f82847`, source commit
  `1f828479b10ca7155e58500203d119fc99b4f07a`.
- One real Qmix pump, public `script test/Configurations/single` configuration.
- Pump 1: app BD 5ml Plastic profile, bore 12.07 mm, calculated stroke
  43.6984047329 mm, 0–5 mL limits. SDK readback confirmed 5.0 mL capacity and
  43,449.3359 µL/min maximum flow. The physical syringe brand/geometry was not
  independently inspected; this is the app's 5 mL preset.
- Cetoni mapping: Energized=2, Not energized=1. Both positions tested.
- TriContinent: COM6, 9600 baud, physical address 2,
  firmware `VSeries: 051215`, configuration
  `4PORT-LOOP/9600/100K/AUTOINIT-ON`.
- Both simulation flags were false.

## Executed checks

| Check | Result | Evidence |
|---|---|---|
| Real Qmix and serial connection | Passed initially | One pump and supported four-port valve discovered |
| Apply 5 mL profile | Passed | SDK capacity 5.0 mL, geometry read back |
| Pump target level 0.5 mL at 20,000 µL/min | Passed | End fill 0.4999983885 mL, idle and fault-free |
| Both Cetoni valve positions | Passed | Readback 2 and 1 |
| All four TriContinent positions | Passed | input/output/bypass/extra queried successfully |
| All 16 ordered TriContinent position combinations | Passed | Every requested final position read back |
| First stirring cycle | **Failed** | Fault during Emptying, before dwell/refill |
| Fault-triggered stop and skipped transitions | Passed for this incident | Zero flow, pumping=false; Cetoni remained 2 |
| In-process reconnect and Stop | **Failed recovery** | pumping=true with flow=0 and stable fill; Stop unverified twice |
| Fresh process reconnect | **Failed recovery** | Active fault remained after clear; connection refused |
| SDK fault-clear diagnostic | **Failed recovery** | Fault remained true across 20 samples over 4.75 seconds; drive disabled |
| Stir/inject requested with disconnected pump | Passed failure handling | Error, released reservations, no serial-valve transition |

`results.jsonl` contains 28 recorded outcomes: 26 passing checks and 2 failures.
Those counts include setup and failure-handling checks; they do not mean 26
completed pump sequences. Recovery diagnostics and manual Stop failures are
also preserved in the owner journals and diagnostic files.

## Stirring incident

Request `956b0f85022e4a8e9122bbe770172d6c` selected Defined volume, refill
0.7 mL, rate 5,000 µL/min, one-second waits after emptying and refilling,
Energized beginning and Not energized ending.

It started from 0.4999983885 mL, set Cetoni position 2, and reported a pump fault
during Emptying. Last helper progress was 28.3208%, displaced volume
0.1416035038 mL and fill 0.3583948846 mL. The application ended in Error,
reported a verified pump stop, released its reservation and skipped later
transitions. Subsequent stopped fill was 0.3512475668 mL, with flow zero and
fault=true. The terminal success response to `helper.start` only meant the
request started; helper events correctly reported failure.

The root cause is **undetermined**. These observations do not establish whether
the trigger was mechanical resistance, fluid routing, drive state or an SDK
issue. Both pump and drive last-error records later returned code 0 and a
resolved-error message despite fault=true. Reopening the configuration returned
the configured XML geometry, 5 mm bore / 60 mm stroke / 1.1780972451 mL capacity,
rather than the previously applied 5 mL profile.

After the first reconnect, Stop returned `Pump stop unverified; device still
reports flow` twice, although status actually showed **flow=0**, **pumping=true**,
and unchanged fill 0.0602752184 mL in the reset XML geometry. This is an
inconsistent SDK status, not evidence that nonzero liquid flow was measured.
The GUI owner was restarted; subsequent connections refused to enable the
persistent fault. A separate SDK diagnostic ran only while the application's
Qmix connection was closed. It issued stop/clear and polled fault state; it
did not force enable while faulted or alter position counters.

## Tests that remain blocked

- Successful stirring: defined/previous/full refill modes, maximum speed,
  beginning/end states, both dwell durations and full-cycle repeatability.
- Injection displacement at 0.01, 0.1 and 0.5 mL and multiple rates.
- Stop during positive/negative manual flow; stop latency and post-stop drift.
- Cancellation during emptying, refilling, each dwell and active injection.
- Deadline handling and recovery after timeout on a moving pump.
- Connected-pump invalid-volume/rate and insufficient-volume checks.

Disconnected-helper failures are not substitutes for these physical tests.
No balance or independent flow sensor was used: even a successful displacement
test would not certify actual delivered liquid volume or stop-flow leakage.

## Improvements, in priority order

1. **Capture useful fault diagnostics at the instant of failure.** Record drive
   identity, SDK emergency events, enabled/position-sensing state, fill and
   motion state before reconnecting or clearing errors. Code 0 plus an active
   fault currently leaves the operator without a cause. Resolve this hardware
   condition before completing pump acceptance.
2. **Improve Stop verification and its message.** Report pumping and flow
   separately. Use bounded polling to distinguish deceleration from persistent
   contradictory SDK state. Never claim verified Stop while pumping=true.
   The current message incorrectly says the device reports flow when flow=0.
3. **Make reconnect syringe state unmistakable.** The saved BD 5ml selection
   survived, but active SDK geometry reverted to the XML's 1.178 mL capacity.
   Show a prominent applied/unapplied indicator and require explicit profile
   application before motion when saved and active geometries differ.
4. **Provide a clear recovery workflow in the terminal and GUI.** Surface the
   persistent fault, preserve its diagnostics, distinguish reconnect from a
   fresh owner restart, and expose supported diagnosis/recovery commands.
   Avoid implying that reconnect alone will recover every fault.
5. **Reject disconnected helpers synchronously.** Both helpers currently
   acknowledge started=true, then fail asynchronously with `Selected pump
   disconnected` and a secondary unverified-stop warning. Preflight known
   disconnected dependencies before accepting a sequence.
6. **Complete physical volume and Stop acceptance after recovery.** Run the
   saved matrix, measure displacement and stop settling, and use an independent
   balance/flow measurement for delivered volume and leakage. Do not mark
   stirring/injection ready based on valve or simulation checks alone.

## Final state and retained evidence

GUI left open. Pump disconnected in the application; last direct hardware
diagnostic showed fault=true, drive disabled and pumping=false. No helper is
running; reservations are empty. TriContinent remains connected at verified
`input`. No reference move or position-counter rewrite was performed.

- `PLAN.md`: intended matrix and acceptance criteria.
- `run_hardware.py`: terminal-driven setup, sequence, stop and failure checks.
- `cli-transcript.jsonl`: 89 scripted request/response records with timings.
- `results.jsonl`: recorded outcomes and snapshots.
- `owner-stdout.jsonl`: original session, including the physical stirring fault
  and manual reconnect/Stop responses.
- `owner-restart-stdout.jsonl`: fresh GUI session and recovery attempts.
- `fault-diagnostic.txt`, `recovery-diagnostic.txt`, `drive-diagnostic.txt`:
  separate SDK observations; `read_fault.py` contains the diagnostic code.
- `session/events.jsonl`: durable application event journal.

The live authenticated endpoint descriptor is runtime-only and excluded from Git.
