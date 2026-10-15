# Physical hardware test plan — 15 October 2026

Authorized by the user for unrestricted pump and valve motion. Run the packaged
1f82847 application through its terminal interface, with the GUI observing the
same owner. Use pump 1, the supplied `single` Qmix configuration and BD 5ml
application preset (12.07 mm bore, derived 43.6984 mm stroke, 0–5 mL limits).
This is a configured geometry, not an independently identified syringe model.

1. Connect real Qmix and available serial hardware; record device identity,
   limits, fault state, syringe readback, and baseline positions.
2. Apply 5 mL geometry; map Cetoni energized=2/deenergized=1; exercise both
   Cetoni positions and every supported TriContinent position with readback.
3. Stir: defined volume, previous volume and full syringe; defined rate and
   device maximum; both beginning/end valve choices; verify idle, resulting
   volume, dwell durations and no device faults.
4. Inject 0.01, 0.1 and 0.5 mL at multiple rates and before/after positions.
   Compare pump fill delta to requested volume and helper reported injected
   volume. Confirm zero flow and final valve readback.
5. Stop manual positive and negative flow; measure terminal round-trip and
   stopped readback, then monitor for further movement.
6. Cancel stirring during emptying, refilling, and each dwell. Cancel injection
   during delivery with both helper.stop and pump.stop. Check later transitions
   are skipped, zero flow, stable volume and released reservations.
7. Force a short sequence deadline and reject invalid volume/rate requests;
   check recovery by completing a subsequent valid operation.
8. Leave pump stopped, no helper running, both buses responsive. Retain GUI.

Acceptance: no fault; stopped means pumping=false and flow=0; normal volume
error <= max(0.001 mL, requested volume*0.001), matching application tolerance;
valve readback matches command. Record timing observations without claiming a
hard real-time guarantee. Retain all CLI requests/responses and event stream.
No balance or external flow sensor is connected: fill feedback establishes
device displacement only, not calibrated liquid delivery or leakage accuracy.
