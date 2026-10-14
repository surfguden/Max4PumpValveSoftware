"""Portable bus configuration, with migration from the single-unit format."""
from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
import sys

from .valve_session import VALVE_TYPES, validate_mappings


@dataclass
class UnitSettings:
    name: str = 'Unit 1'
    address: int = 2
    valve_type: str = '4-port loop'
    open_position: str = ''
    close_position: str = ''
    firmware: str = ''
    configuration: str = ''

    def validate(self):
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError('Each unit needs a name.')
        if type(self.address) is not int or self.address not in range(15):
            raise ValueError(f'{self.name}: address switch must be between 0 and 14 (0-E).')
        if self.valve_type not in VALVE_TYPES:
            raise ValueError(f'{self.name}: unknown valve type.')
        if not isinstance(self.firmware, str) or not isinstance(self.configuration, str):
            raise ValueError(f'{self.name}: invalid saved identification.')
        positions = VALVE_TYPES[self.valve_type]
        if self.open_position not in ('', *positions) or self.close_position not in ('', *positions):
            raise ValueError(f'{self.name}: unsupported position for {self.valve_type}.')
        if self.open_position and self.close_position:
            validate_mappings(self.open_position, self.close_position, self.valve_type)


@dataclass
class Settings:
    port: str = ''
    baudrate: int = 9600
    units: list[UnitSettings] = field(default_factory=list)

    def validate(self):
        if self.baudrate not in (9600, 38400):
            raise ValueError('Baud rate must be 9600 or 38400.')
        if not isinstance(self.port, str) or not isinstance(self.units, list):
            raise ValueError('Invalid bus configuration.')
        addresses = set()
        for unit in self.units:
            if not isinstance(unit, UnitSettings):
                raise ValueError('Invalid unit configuration.')
            unit.validate()
            if unit.address in addresses:
                raise ValueError(f'Duplicate address switch {unit.address:X} (protocol /{unit.address + 1:X}).')
            addresses.add(unit.address)


def settings_path():
    base = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
    return base / 'settings.json'


def load_settings(path):
    if not path.exists():
        return Settings()
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict):
        raise ValueError('Configuration must be an object.')
    if 'units' in data:
        version = data.pop('version', 2)
        if version not in (2, 3):
            raise ValueError('Unsupported configuration version.')
        if not isinstance(data['units'], list):
            raise ValueError('Units must be a list.')
        data['units'] = [UnitSettings(**unit) for unit in data['units']]
        settings = Settings(**data)
    else:
        # Preserve port, address, and routing choices from the original UI.
        port = data.pop('port', '')
        baudrate = data.pop('baudrate', 9600)
        settings = Settings(port, baudrate, [UnitSettings(**data)])
    settings.validate()
    return settings


def save_settings(path, settings):
    settings.validate()
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps({'version': 3, **asdict(settings)}, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)
