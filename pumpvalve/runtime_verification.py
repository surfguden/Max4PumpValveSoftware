"""Check the packaged Qmix loader without loading DLLs or opening hardware."""
import importlib
import json
from pathlib import Path
import sys
from .drivers.cetoni import _default_sdk_python_path


def verify_sdk_import(report_path):
    report = dict(passed=False, hardware_access=False, native_dlls_loaded=False)
    try:
        sys.path.insert(0, str(_default_sdk_python_path()))
        loader = importlib.import_module('qmixsdk._qmixloadlib')
        if loader.load_cnt != 0 or loader.libpath is not None:
            raise RuntimeError('Qmix loader unexpectedly loaded a native library')
        report.update(passed=True, checks=['Qmix DLL loader imports', 'ctypes.util available',
                                          'No native DLL loading or bus connection'])
    except Exception as exc:
        report['error'] = str(exc)
    path = Path(report_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    return 0 if report['passed'] else 1
