"""Persistent, validated DT serial transport for the valve UI."""
from dataclasses import dataclass
import threading
import time

import serial
from .tricontinent_valve import build_command


class ValveError(Exception):
    pass


class ReplyTimeout(ValveError):
    """No complete reply within the deadline (normal for absent scan addresses)."""


ERRORS = {1: 'Initialization failure', 2: 'Invalid command', 3: 'Invalid operand',
          4: 'Invalid checksum', 6: 'EEPROM failure', 7: 'Device not initialized',
          8: 'CAN bus failure', 9: 'Plunger overload', 10: 'Valve overload',
          11: 'Plunger move not allowed', 15: 'Command overflow'}


@dataclass(frozen=True)
class Reply:
    ready: bool
    data: str


def parse_reply(raw):
    # CR/LF can arrive after ETX and precede the next reply on a real bus.
    raw = raw.lstrip(b'\r\n')
    if not raw:
        raise ValveError('No reply received. Check power, address and baud rate.')
    end = raw.find(b'\x03')
    if not raw.startswith(b'/0') or end < 3 or raw[end + 1:].strip(b'\r\n'):
        raise ValveError(f'Malformed DT reply: {raw!r}')
    status = raw[2]
    if status & 0xD0 != 0x40:
        raise ValveError(f'Invalid status byte: 0x{status:02X}')
    error = status & 15
    if error:
        raise ValveError(f'Device error {error}: {ERRORS.get(error, "Unknown error")}')
    try:
        data = raw[3:end].decode('ascii')
    except UnicodeDecodeError as exc:
        raise ValveError('Non-ASCII reply data.') from exc
    return Reply(bool(status & 0x20), data)


VALVE_TYPES = {'4-port loop': ('input', 'output', 'bypass', 'extra'),
               '3-port valve': ('input', 'output', 'bypass'), 'unknown': ()}


@dataclass(frozen=True)
class DetectedUnit:
    address: int
    firmware: str = ''
    configuration: str = ''
    position: str = 'unknown'
    valve_type: str = 'unknown'
    error: str = ''


def detected_valve_type(firmware, configuration):
    profile = configuration.split('/')[0]
    if firmware.startswith('VSeries:'):
        return {'4PORT-LOOP': '4-port loop', '3PORT-Y': '3-port valve',
                '3P-Y': '3-port valve'}.get(profile, 'unknown')
    if firmware.startswith(('C3000:', 'C3000MP:')):
        return {'LOOP': '4-port loop', '3P-Y': '3-port valve'}.get(profile, 'unknown')
    return 'unknown'


def validate_mappings(open_position, close_position, valve_type='4-port loop'):
    positions = VALVE_TYPES[valve_type]
    if open_position not in positions or close_position not in positions:
        raise ValueError('Choose positions for both Open and Close.')
    if open_position == close_position:
        raise ValueError('Open and Close must use different positions.')


class ValveSession:
    def __init__(self, factory=serial.Serial):
        self.factory = factory
        self.port = None
        self.address = 2
        self.verified_units = {}
        self.cancel = threading.Event()

    @property
    def connected(self):
        return self.port is not None and self.port.is_open

    @property
    def verified(self):
        return self.address in self.verified_units

    def disconnect(self):
        self.verified_units.clear()
        port, self.port = self.port, None
        if port is not None:
            port.close()

    def exchange(self, command, timeout=1.0, *, address=None):
        address = self.address if address is None else address
        if type(address) is not int or address not in range(15):
            raise ValueError('Address switch must be between 0 and 14 (0-E).')
        if self.cancel.is_set():
            raise ValveError('Operation cancelled. Valve motion may still be in progress.')
        if not self.connected:
            raise ValveError('Connect to a serial port first.')
        try:
            self.port.reset_input_buffer()
            self.port.write(f'/{address + 1:X}{command}\r'.encode('ascii'))
            # Writes have a bounded timeout; avoid an unbounded serial flush.
            deadline = time.monotonic() + timeout
            raw = bytearray()
            while time.monotonic() < deadline and not self.cancel.is_set():
                raw.extend(self.port.read(self.port.in_waiting or 1))
                if b'\x03' in raw:
                    return parse_reply(bytes(raw))
                if len(raw) > 4096:
                    raise ValveError('Reply exceeded the maximum length.')
            if self.cancel.is_set():
                raise ValveError('Operation cancelled. Valve motion may still be in progress.')
            raise ReplyTimeout('Reply timed out or was incomplete. Check the connection.')
        except (serial.SerialException, OSError):
            self.disconnect()
            raise

    def connect(self, port_name, address=2, baudrate=9600):
        """Compatibility convenience for one loop-valve unit."""
        if address not in range(15) or baudrate not in (9600, 38400):
            raise ValueError('Invalid address or baud rate.')
        self.address = address
        self.open_bus(port_name, baudrate)
        try:
            return self.identify(address, '4-port loop')
        except Exception:
            self.disconnect()
            raise

    def open_bus(self, port_name, baudrate=9600):
        """Open the shared port once. Identification is per address."""
        if baudrate not in (9600, 38400):
            raise ValueError('Baud rate must be 9600 or 38400.')
        self.disconnect()
        self.cancel.clear()
        try:
            self.port = self.factory(port=None, baudrate=baudrate, bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE, timeout=0.05,
                write_timeout=1, rtscts=False, dsrdtr=False, xonxoff=False)
            self.port.dtr = False
            self.port.rts = False
            self.port.port = port_name
            self.port.open()
        except Exception:
            self.disconnect()
            raise

    def identify(self, address, valve_type):
        """Compatibility API: additionally require the caller's expected type."""
        if valve_type not in VALVE_TYPES:
            raise ValueError('Unknown valve type.')
        result = self.detect(address)
        if result.error:
            raise ValveError(result.error)
        if result.valve_type != valve_type:
            self.verified_units.pop(address, None)
        return result.firmware, result.configuration, result.position

    def detect(self, address, *, probe=True):
        """Infer capabilities only from firmware and the active device profile."""
        self.verified_units.pop(address, None)
        if probe:
            self.exchange('Q', address=address)
        firmware = self.exchange('&', address=address).data
        configuration = self.exchange('?76', address=address).data
        valve_type = detected_valve_type(firmware, configuration)
        try:
            position = self.query_position(address=address)
        except ValveError as exc:
            return DetectedUnit(address, firmware, configuration, valve_type=valve_type, error=str(exc))
        error = ''
        if valve_type != 'unknown' and position in VALVE_TYPES[valve_type]:
            self.verified_units[address] = valve_type
        elif valve_type != 'unknown':
            error = f'Unexpected position for detected {valve_type}: {position}.'
        return DetectedUnit(address, firmware, configuration, position, valve_type, error)

    def scan(self, probe_timeout=0.3):
        """Read-only scan of switches 0-E, transmitting wire addresses 1-F."""
        self.verified_units.clear()
        units = []
        for address in range(15):
            if self.cancel.is_set():
                raise ValveError('Scan cancelled.')
            try:
                self.exchange('Q', address=address, timeout=probe_timeout)
            except ReplyTimeout:
                continue
            except ValveError as exc:
                units.append(DetectedUnit(address, error=str(exc)))
                continue
            try:
                units.append(self.detect(address, probe=False))
            except ValveError as exc:
                units.append(DetectedUnit(address, error=str(exc)))
        return units

    def query_position(self, *, address=None):
        data = self.exchange('?6', address=address).data
        positions = {'i': 'input', 'o': 'output', 'b': 'bypass', 'e': 'extra'}
        if data not in positions:
            raise ValveError(f'Unrecognized valve position: {data!r}')
        return positions[data]

    def move(self, position, timeout=10.0, *, address=None, valve_type=None):
        address = self.address if address is None else address
        verified_type = self.verified_units.get(address)
        if not verified_type or (valve_type is not None and valve_type != verified_type):
            raise ValveError('Movement requires identification matching this unit’s selected valve type.')
        if position not in VALVE_TYPES[verified_type]:
            raise ValveError(f'{position.title()} is not supported by a {verified_type}.')
        if not self.exchange('Q', address=address).ready:
            raise ValveError('Device is busy. Query again when motion has finished.')
        command = build_command(address, position).decode('ascii')[2:-1]
        self.exchange(command, address=address)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.cancel.wait(min(0.1, max(0, deadline - time.monotonic()))):
                raise ValveError('Operation cancelled. Valve motion may still be in progress.')
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if self.exchange('Q', timeout=min(1.0, remaining), address=address).ready:
                actual = self.query_position(address=address)
                if actual != position:
                    raise ValveError(f'Position mismatch: requested {position}, reported {actual}.')
                return actual
        raise ValveError(f'Motion timed out after {timeout:g} seconds; final position is unverified.')
