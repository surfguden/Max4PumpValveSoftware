# Reused implementations

- CETONI: reference branch `ola-branch`, commit
  `5014fc8f479c058904e7e69f2f67384851ab88ce`. Copied only pump/cetoni.py,
  pump/bank.py and pump/simulated.py. Adapted configuration defaults, SDK wrapper
  discovery and logging imports. Cleanup runs on the owning bus instead of
  creating concurrent vendor cleanup threads. The bank's session and device
  selection design are retained.
- TriContinent: valve_session.py, tricontinent_valve.py and valve_settings.py from
  the supplied reference. The reference has no committed HEAD. Relative imports
  are adapted; serial protocol, profile detection and verified movement are
  retained. The combined app stores configuration in writable user data rather
  than beside the executable. No original Tk UI or unrelated instruments copied.
- Qmix: Python wrapper copied from the Cetoni reference. DLL directory handle
  retention corrected for Python 3.12. No native vendor DLLs copied.
- Layout: user-provided Qmix Elements screenshot; original vector syringe drawing
  implemented with Qt, with no vendor image assets copied.
- Syringe configuration: CETONI Elements manual sections 7.9/7.11,
  https://cetoni.com/downloads/manuals/CETONI_Elements_Manual_EN.pdf.

Both reference projects, their working files and branches remain unchanged.
