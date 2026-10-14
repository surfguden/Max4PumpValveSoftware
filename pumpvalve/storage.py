"""Writable user data, independent of the source or executable location."""
import json
import os
import math
from pathlib import Path

VERSION = '1.1.0'


def defaults():
    local = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local'))
    return dict(config=r'C:\Users\Public\Documents\QmixElements\Projects\default_project\Configurations\dual',
                sdk=str(local / 'CETONI_SDK'), port='COM3', baud=9600)


def test_simulation_enabled():
    return os.environ.get('PUMPVALVE_TEST_SIMULATION') == '1'


def data_dir():
    path = Path(os.environ.get('PUMPVALVE_DATA_DIR') or
                Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'Max4PumpValve')
    path.mkdir(parents=True, exist_ok=True)
    return path


def load():
    path = data_dir() / 'settings.json'
    if not path.exists():
        return defaults()
    data = json.loads(path.read_text(encoding='utf-8'))
    validate(data)
    return {**defaults(), **data}


def validate(data):
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
    assignments = data.get('pump_profiles', {})
    if not isinstance(assignments, dict) or any(k not in ('1', '2', '3', '4') or
            not isinstance(v, str) or v not in profiles for k, v in assignments.items()):
        raise ValueError('Invalid per-pump syringe selection')
    validate_ui(data.get('ui', {}))


def validate_ui(ui):
    if not isinstance(ui, dict):
        raise ValueError('UI settings must contain an object')
    def bounded(value, lower, upper):
        return type(value) in (int, float) and math.isfinite(value) and lower <= value <= upper
    pumps = ui.get('pumps', {})
    if not isinstance(pumps, dict):
        raise ValueError('Invalid saved pump controls')
    for key, values in pumps.items():
        if key not in ('1', '2', '3', '4') or not isinstance(values, dict):
            raise ValueError('Invalid saved pump controls')
        if values.get('mode', 'Volume') not in ('Volume', 'Flow', 'Syringe level'):
            raise ValueError('Invalid saved pump mode')
        for field, limits in dict(volume=(-1000, 1000), flow=(-1000000, 1000000),
                                  level=(0, 1000000), energized=(0, 2), deenergized=(0, 2)).items():
            if field in values and (not bounded(values[field], *limits) or
                    field in ('energized', 'deenergized') and type(values[field]) is not int):
                raise ValueError(f'Invalid saved pump {field}')
    helpers = ui.get('helpers', {})
    if not isinstance(helpers, dict):
        raise ValueError('Invalid helper settings')
    for field, limits in dict(stir_pump=(0, 3), inject_pump=(0, 3), stir_rate=(0.01, 1000000),
            refill_volume=(0, 1000000), inject_volume=(0.0001, 1000000),
            inject_rate=(0, 1000000), deadline=(1, 3600)).items():
        if field in helpers and not bounded(helpers[field], *limits):
            raise ValueError(f'Invalid helper {field}')
    for field, choices in dict(speed=('Full speed', 'Defined flow rate'),
            refill=('Full syringe', 'Defined volume', 'Volume before emptying'),
            before=('input', 'output', 'bypass', 'extra', None),
            after=('input', 'output', 'bypass', 'extra', None)).items():
        if field in helpers and helpers[field] not in choices:
            raise ValueError(f'Invalid helper {field}')
    address = helpers.get('inject_valve')
    for field in ('stir_pump', 'inject_pump'):
        if field in helpers and type(helpers[field]) is not int:
            raise ValueError(f'Invalid helper {field}')
    if address is not None and (type(address) is not int or address not in range(15)):
        raise ValueError('Invalid saved helper valve')
    if 'count' in ui and (type(ui['count']) is not int or ui['count'] not in range(1, 5)):
        raise ValueError('Invalid saved pump count')
    if 'tab' in ui and (type(ui['tab']) is not int or ui['tab'] not in range(3)):
        raise ValueError('Invalid saved tab')
    size = ui.get('window_size')
    if size is not None and (not isinstance(size, list) or len(size) != 2 or
            any(type(v) is not int or not 100 <= v <= 10000 for v in size)):
        raise ValueError('Invalid saved window size')


def save(data):
    validate(data)
    path = data_dir() / 'settings.json'
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, indent=2, allow_nan=False), encoding='utf-8')
    tmp.replace(path)
