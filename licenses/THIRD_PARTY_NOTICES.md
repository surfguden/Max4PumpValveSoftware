# Runtime notices and source availability

This distribution includes Python 3.12.14, pySerial 3.5, and dynamically linked
PySide6/Shiboken/Qt 6.11.1. Original wheel license notices are retained alongside
the Python, BSD and GNU LGPLv3/GPLv3 texts. Only Qt Core, GUI, Widgets and Network
runtime libraries and the Windows platform/style plugins are included.

Qt for Python is offered under LGPLv3/GPLv3 and commercial terms:
https://doc.qt.io/qtforpython-6/
Upstream corresponding source and build instructions:
https://code.qt.io/cgit/pyside/pyside-setup.git/?h=v6.11.1
https://code.qt.io/cgit/qt/qtbase.git/?h=v6.11.1
https://www.python.org/downloads/release/python-31214/
https://github.com/pyserial/pyserial/tree/v3.5

Qt libraries are separate, replaceable DLLs under _internal/PySide6; no Qt or
Python source modifications were made. The application's corresponding source
and repeatable packaging instructions are provided in the accompanying source
ZIP. Dependency source code remains available from the upstream projects above.
Replacing Qt requires an ABI-compatible x64 build with matching PySide bindings.
No application terms restrict reverse engineering for debugging modifications to
the LGPL libraries. Upstream copyrights and notices remain in their source trees.

Qmix wrapper source is supplied for this user's vendor SDK integration; native
Qmix libraries are not redistributed. See SDK_RUNTIME.md for the local runtime
documentation review and unresolved vendor redistribution terms.
