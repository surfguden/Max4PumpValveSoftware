# Qmix runtime and redistribution review

Inspected the local vendor SDK Windows documentation:
`%LOCALAPPDATA%/CETONI_SDK/doc/html/Page_QmixSDK_Windows.html`,
the SDK root, and the reference's `qmix_sdk_for_codex/python` wrapper.
The Windows documentation says the SDK root holds all DLLs needed for the API;
it does not provide a license grant establishing redistribution of this runtime.
No license/EULA/redistribution notice was found in those local folders. Therefore
this distribution does not bundle native CETONI runtime DLLs. Ask CETONI for
redistribution terms before adding a licensed runtime bundle to future releases.

Install the official Windows x64 CETONI SDK/runtime on the target workstation.
Use the complete vendor runtime folder, including dependencies and bus plugins;
do not copy only the three primary API DLLs. Browse to that folder in the app,
or set `QMIXSDK` before launching. `%LOCALAPPDATA%/CETONI_SDK` is also detected.
The app checks labbCAN_Bus_API.dll, labbCAN_Pump_API.dll and
labbCAN_Valve_API.dll before importing the wrapper and gives actionable errors.
Transitive load errors also include the runtime path and installation guidance.

Locally observed dependencies include labbcan_core/common/device/canopen/local/
service/sim, usl, csi, canoo, Qt5Core, Qt5SerialPort, qtcoreaddons,
qstatemachineex, libboost*, libgcc_s_seh-1, libstdc++-6, libwinpthread-1,
liblog4cplus and libserialport. This list is evidence from the local SDK root,
not a guaranteed complete dependency manifest for other SDK versions. Preserve
the official installation's dependencies and drivers. Do not use Qmix Elements'
application directory as a substitute for the SDK runtime.

Qmix Python wrapper sources are bundled; their original copyright notices are
retained. No local grant for the wrapper was found either: distribution outside
the owner's authorized vendor/license context requires checking CETONI terms.
Simulation and TriContinent use do not require the Qmix native runtime.

PySide6/Qt is dynamically bundled; license texts are retained in `licenses/`,
with source availability through the official Qt/PySide project. Application
source is delivered alongside the executable, and Qt DLLs remain replaceable.
