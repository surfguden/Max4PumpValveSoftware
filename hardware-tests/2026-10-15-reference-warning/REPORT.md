# Unreferenced connection and syringe warning — 15 October 2026

Implemented on `main`; existing changes retained. Build:
`dist/reference-warning-20261015/Max4PumpValve-1.1.0`.

Normal startup remains disconnected. A user-requested connection now succeeds
when position sensing is uninitialized, with the drive disabled. The pump card
shows REFERENCE REQUIRED. Reference move and Configure syringe remain available;
dosing, refill/empty and helper starts remain disabled until position is valid.
No connection or startup path calls calibrate/reference automatically.

Clicking Reference move always displays a warning to disconnect the syringe.
The Yes/Cancel dialog defaults to Cancel. The command is submitted only after
Yes. Terminal reference requires `--syringe-disconnected`, and canonical JSON
requires the exact boolean `syringe_disconnected=true`; otherwise the command
fails before any reference action.

Explicit homing uses the existing serialized bus and cancellable polling. It
requires both completed calibration and initialized position sensing, then
verifies stopped feedback. It does not require an already applied syringe profile.
The syringe profile must still be applied before dosing. Persistent faults still
block connection/drive enable.

Verification: 157 tests passed, with 18 subtests. These include no implicit
homing/enable for an unreferenced connection, fault gating, explicit reference
success/failure, acknowledgement validation, GUI availability and popup
confirmation/cancellation. Packaged console/GUI checks are recorded separately
under `build/reference-warning-console/report.json`.

Physical verification on the connected pump confirmed:

- Startup disconnected; no automatic reference.
- Real Qmix connection succeeded with position_initialized=false.
- Drive disabled, pumping=false, flow=0, no active fault.
- Reference request without acknowledgement rejected.
- A dosing request rejected with Reference required.

Final physical confirmation: after the user disconnected the syringe and
confirmed the GUI warning, reference request
`31a8a1540a824dcf8b044dac172d7463` completed successfully. It ran from
2026-10-15 13:43:20.484 to 13:43:22.162 Europe/Stockholm (1.678 seconds).
Position sensing changed from uninitialized to initialized. Final feedback was
fill=0, flow=0, pumping=false and fault=false; reservations were released.
The saved 5 mL profile remains unapplied, so dosing still requires explicit
syringe application after reattachment.

The GUI remains open. `reference-validation.json` contains the concise result;
full state and terminal captures remain local beside this report.
