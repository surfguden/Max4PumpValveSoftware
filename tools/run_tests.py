"""Run offline tests with Windows sandbox-compatible temporary directories."""
import os
from pathlib import Path
import sys

if sys.platform == 'win32':
    # Windows mkdir(0700) removes inherited sandbox ACL entries. These isolated
    # test directories should inherit the workspace's existing access rules.
    mkdir = os.mkdir

    def workspace_mkdir(path, mode=0o777, **kwargs):
        return mkdir(path, 0o777 if mode == 0o700 else mode, **kwargs)

    os.mkdir = workspace_mkdir

root = Path(__file__).resolve().parents[1]
temporary = root / 'build' / 'test-system-temp'
temporary.mkdir(parents=True, exist_ok=True)
os.environ['TEMP'] = os.environ['TMP'] = str(temporary)
sys.path.insert(0, str(root))
import pytest
raise SystemExit(pytest.main(sys.argv[1:]))
