"""Retain dependency licenses and exact build-version metadata in distribution."""
import importlib.metadata as metadata
from pathlib import Path
import shutil
import sys
target = Path(sys.argv[1]) / 'licenses'
target.mkdir(parents=True, exist_ok=True)
versions = {}
for source in (Path(__file__).resolve().parents[1] / 'licenses').glob('*'):
    if source.is_file():
        shutil.copy2(source, target / source.name)
for name in ['PySide6', 'PySide6_Essentials', 'PySide6_Addons', 'shiboken6', 'pyserial', 'PyInstaller']:
    dist = metadata.distribution(name)
    versions[name] = dist.version
    for entry in dist.files or []:
        if any(word in str(entry).lower() for word in ('license', 'copying', 'copyright')):
            source = Path(dist.locate_file(entry))
            if source.is_file():
                destination = target / name / str(entry).replace('../', '').replace('..\\', '')
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
import json
(target.parent / 'build-versions.json').write_text(json.dumps(versions, indent=2), encoding='utf-8')
