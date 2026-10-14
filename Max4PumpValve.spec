# Folder mode: native Qmix runtime is deliberately external (see SDK_RUNTIME.md).
from pathlib import Path
root = Path(SPECPATH)
a = Analysis([str(root / 'main.py')], pathex=[str(root)],
             binaries=[], datas=[(str(root / 'vendor/qmixsdk'), 'vendor/qmixsdk')],
             hiddenimports=['serial.tools.list_ports_windows', 'ctypes.util'],
             excludes=['tkinter', 'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets'],
             noarchive=False)
# Qt 6.11 uses Windows' unversioned ICU API. A conda/Poppler ICU on the build
# host's PATH has a different ABI; it must never shadow the Windows API.
a.binaries = [entry for entry in a.binaries
              if Path(entry[0]).name.lower() not in ('icuuc.dll', 'icuin.dll', 'icudt78.dll')]
# Only the Widgets runtime and Windows platform/style plugins are used.
# Omit auto-collected PDF, QML and virtual-keyboard components.
def needed(entry):
    name = entry[0].replace('\\', '/').lower()
    if '/plugins/' in name:
        return name.endswith(('/platforms/qwindows.dll', '/platforms/qoffscreen.dll',
                              '/styles/qmodernwindowsstyle.dll'))
    if Path(name).name.startswith('qt6'):
        return Path(name).name in ('qt6core.dll', 'qt6gui.dll', 'qt6widgets.dll', 'qt6network.dll')
    return True
a.binaries = [entry for entry in a.binaries if needed(entry)]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='Max4PumpValve',
          debug=False, strip=False, upx=False, console=False,
          version=str(root / 'version_info.txt'))
console_scripts = [entry for entry in a.scripts if entry[0] != 'main']
console_scripts.append(('console_main', str(root / 'console_main.py'), 'PYSOURCE'))
console = EXE(pyz, console_scripts, [], exclude_binaries=True, name='Max4PumpValveCtl',
              debug=False, strip=False, upx=False, console=True,
              version=str(root / 'version_info.txt'))
coll = COLLECT(exe, console, a.binaries, a.datas, strip=False, upx=False, name='Max4PumpValve-1.1.0')
