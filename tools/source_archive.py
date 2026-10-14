from pathlib import Path
import zipfile
root = Path(__file__).resolve().parents[1]
output = root / 'dist/Max4PumpValve-1.1.0-source.zip'
entries = ['pumpvalve', 'vendor', 'tests', 'tools', 'docs', 'licenses', 'main.py', 'console_main.py', 'README.md',
           'PORTABLE_README.txt', 'Max4PumpValve.spec', 'build_portable.ps1',
           'requirements.txt', 'requirements-build.txt', '.gitignore', 'version_info.txt']
with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
    for name in entries:
        target = root/name
        for file in target.rglob('*') if target.is_dir() else [target]:
            if file.is_file() and '__pycache__' not in file.parts and file.suffix != '.pyc':
                archive.write(file, 'Max4PumpValve-1.1.0-source/' + file.relative_to(root).as_posix())
print(output)
