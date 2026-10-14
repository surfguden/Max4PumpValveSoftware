"""Writable user data, independent of the source or executable location."""
import json
import os
from pathlib import Path

VERSION = '1.0.0'


def data_dir():
    path = Path(os.environ.get('PUMPVALVE_DATA_DIR') or
                Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'Max4PumpValve')
    path.mkdir(parents=True, exist_ok=True)
    return path


def load():
    path = data_dir() / 'settings.json'
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError('Settings must contain an object')
    for key in ('config', 'sdk', 'port'):
        if key in data and not isinstance(data[key], str):
            raise ValueError(f'Settings {key} must be text')
    if data.get('baud', 9600) not in (9600, 38400):
        raise ValueError('Saved baud must be 9600 or 38400')
    units = data.get('valves', [])
    if not isinstance(units, list) or any(not isinstance(u, dict) or
        type(u.get('address')) is not int or u['address'] not in range(15) or
        not isinstance(u.get('name'), str) for u in units):
        raise ValueError('Invalid saved valve configuration')
    if len({u['address'] for u in units}) != len(units):
        raise ValueError('Duplicate saved valve addresses')
    profiles = data.get('syringes', {})
    if not isinstance(profiles, dict):
        raise ValueError('Invalid saved syringe profiles')
    # Import lazily to avoid the storage/logger/application import cycle.
    from .application import Syringe
    for name, profile in profiles.items():
        if not isinstance(profile, dict):
            raise ValueError('Invalid saved syringe profile')
        syringe = Syringe(**profile)
        syringe.validate()
        if syringe.name != name:
            raise ValueError('Saved syringe name mismatch')
    return data


def save(data):
    path = data_dir() / 'settings.json'
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, indent=2, allow_nan=False), encoding='utf-8')
    tmp.replace(path)
