# BD Plastipak Luer-Lok profiles

`BD-Plastipak-Luer-Lok.json` is an importable settings file containing only four
saved syringe profiles. Load it through **File > Load settings**, then explicitly
apply the profile for the mounted syringe. Importing does not move or connect
devices. Export existing settings first: loading replaces settings, including
connection fields and pump assignments, with the imported values and app defaults.

| Profile | Internal diameter (mm) | Configured stroke (mm) | Maximum fill (mL) |
| --- | ---: | ---: | ---: |
| BD Plastipak 1 mL Luer-Lok | 4.70 | 57.6388 | 1 |
| BD Plastipak 3 mL Luer-Lok | 8.57 | 52.0080 | 3 |
| BD Plastipak 5 mL Luer-Lok | 11.98 | 55.0000 | 5 |
| BD Plastipak 10 mL Luer-Lok | 14.43 | 61.1474 | 10 |

Nominal internal diameters are from Aurora Pro Scientific's
[BD Plastipak Luer Lock table](https://www.auroraprosci.com/Syringe-Pump-Flow-Rate-Calculation),
checked on 2026-10-15. They differ from the app's older generic BD plastic presets.
Select the full Plastipak Luer-Lok name when applying these saved profiles.

The 1, 3 and 10 mL strokes are calculated nominal scale travel, using
`stroke = volume * 1000 / (pi * diameter^2 / 4)`, rounded upward to four decimals.
They are not manufacturer-certified maximum mechanical travel. The 5 mL profile
retains the previously saved 55 mm stroke; this value is also unverified. Its
calculated 0-to-5 mL scale travel is 44.3574 mm, consistent with the user's
approximate 44 mm measurement. Verify travel against each mounted syringe.

All profiles use Plastic, minimum fill 0 mL and maximum fill equal to nominal size.
The inherited 1 bar field is metadata, not a verified pressure rating or an
actively monitored pressure limit. No new physical dosing calibration was run
as part of this settings-only release.
