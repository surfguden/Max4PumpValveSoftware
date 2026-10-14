"""Device application service and cancellable sequences. No Qt dependencies."""
from dataclasses import dataclass, asdict
from pathlib import Path
from threading import Event, Lock, Thread
import math
import time
from .workers import BusWorker
from .simulation import MovingPumpBank, SimulatedValves
from .drivers.bank import CetoniPumpBank
from .drivers.valve_session import ValveSession, VALVE_TYPES, DetectedUnit
from .logging_support import logger


class Cancelled(RuntimeError):
    pass


def finite(value, name):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f'{name} must be finite')
    return value


@dataclass
class Syringe:
    name: str = 'Custom syringe'
    material: str = 'Glass'
    inner_diameter_mm: float = 4.78
    max_piston_stroke_mm: float = 55
    minimum_ml: float = 0
    maximum_ml: float = 0.98
    max_pressure_bar: float = 1

    @property
    def capacity(self):
        return math.pi * self.inner_diameter_mm ** 2 / 4 * self.max_piston_stroke_mm / 1000

    @staticmethod
    def diameter_from_scale(length_mm, volume_ml):
        length_mm = finite(length_mm, 'Scale length')
        volume_ml = finite(volume_ml, 'Scale volume')
        if length_mm <= 0 or volume_ml <= 0:
            raise ValueError('Scale length and volume must be positive')
        return math.sqrt(4 * volume_ml * 1000 / (math.pi * length_mm))

    def validate(self):
        for name, value in asdict(self).items():
            if name not in ('name', 'material'):
                finite(value, name)
        if not self.name.strip() or self.material not in ('Glass', 'Steel'):
            raise ValueError('Enter a name and syringe material')
        # Retain the inherited low-pressure-module geometry bounds.
        if not 1 <= self.inner_diameter_mm <= 35 or not 10 <= self.max_piston_stroke_mm <= 65:
            raise ValueError('Diameter must be 1–35 mm; stroke 10–65 mm (low-pressure pump)')
        if not 0 <= self.minimum_ml < self.maximum_ml <= self.capacity + 1e-8:
            raise ValueError(f'Fill limits must be within calculated capacity {self.capacity:.6g} mL')
        if self.max_pressure_bar <= 0:
            raise ValueError('Maximum pressure must be positive')


class Application:
    def __init__(self):
        self.qmix = BusWorker('Qmix bus')
        self.serial = BusWorker('TriContinent bus')
        self.pumps = None
        self.pump_connecting = Event()
        self.valves = None
        self.profiles = {}
        self.mapping = {}
        self.units = {}
        self.lock = Lock()
        self.reserved = set()
        self.helper = None
        self.cancel = Event()
        self.state = {'running': False, 'step': 'Idle', 'message': '', 'progress': 0}
        self.helper_states = {kind: dict(self.state) for kind in ('stir', 'inject')}

    def _publish(self, **values):
        with self.lock:
            self.state.update(values)
            if self.state.get('kind'):
                self.helper_states[self.state['kind']] = dict(self.state)
        logger.info('Sequence %r', values)

    def snapshot(self):
        with self.lock:
            return dict(self.state)

    def helper_snapshot(self, kind):
        with self.lock:
            return dict(self.helper_states[kind])

    def _reserve(self, keys):
        with self.lock:
            if set(keys) & self.reserved:
                raise RuntimeError('Device reserved by an active operation')
            self.reserved.update(keys)

    def _release(self, keys):
        with self.lock:
            self.reserved.difference_update(keys)

    def connect_pumps(self, simulation, count=2, configuration='', sdk=''):
        keys = [('pump', i) for i in range(4)]
        self._reserve(keys)
        self.pump_connecting.set()
        try:
            def connect():
                if self.pumps:
                    raise RuntimeError('Disconnect pumps before reconnecting')
                if not simulation:
                    path = Path(configuration)
                    if not configuration or not path.is_dir() or not list(path.glob('*.xml')):
                        raise ValueError('Select an existing Qmix Elements folder containing XML files')
                    if sdk:
                        import os
                        if 'qmixsdk.qmixbus' in __import__('sys').modules and os.environ.get('QMIXSDK') != sdk:
                            raise RuntimeError('Restart the application to change an already loaded SDK runtime')
                        os.environ['QMIXSDK'] = sdk
                bank = MovingPumpBank(count) if simulation else CetoniPumpBank()
                if not simulation:
                    bank.set_configuration_path(Path(configuration))
                try:
                    bank.initialize()
                except Exception:
                    bank.cleanup()
                    raise
                self.profiles.clear()
                self.mapping.clear()
                self.pumps = bank
                return bank.unit_count
            return self.qmix.call(connect, timeout=20)
        finally:
            self.pump_connecting.clear()
            self._release(keys)

    def disconnect_pumps(self):
        keys = [('pump', i) for i in range(4)]
        self._reserve(keys)
        try:
            def close():
                if self.pumps:
                    self.pumps.cleanup()
                self.pumps = None
            self.qmix.call(close, recovery=True)
        finally:
            self._release(keys)

    def connect_valves(self, simulation, port='', baud=9600):
        self._reserve([('serial', 0)])
        try:
            def connect():
                if self.valves:
                    raise RuntimeError('Disconnect valves before reconnecting')
                session = SimulatedValves() if simulation else ValveSession()
                session.open_bus(port, baud)
                self.valves = session
                return self._scan()
            return self.serial.call(connect, timeout=25)
        finally:
            self._release([('serial', 0)])

    def _scan(self):
        if not self.valves or not self.valves.connected:
            raise RuntimeError('Valves disconnected')
        self.valves.cancel.clear()
        observed = {u.address: u for u in self.valves.scan()}
        for address in self.units:
            if address not in observed:
                observed[address] = DetectedUnit(address, error='Saved device missing; position unverified')
        self.units = observed
        return list(observed.values())

    def scan_valves(self):
        return self._manual([('serial', 0)], self.serial, self._scan, timeout=25)

    def disconnect_valves(self):
        def close():
            if self.valves:
                self.valves.disconnect()
            self.valves = None
            self.units = {a: DetectedUnit(a, error='Disconnected; position unverified') for a in self.units}
        return self._manual([('serial', 0)], self.serial, close)

    def _manual(self, keys, worker, action, timeout=5):
        self._reserve(keys)
        try:
            return worker.call(action, timeout)
        finally:
            self._release(keys)

    def _read_pump(self, index):
        if not self.pumps or not 0 <= index < self.pumps.unit_count:
            raise RuntimeError('Selected pump disconnected')
        bank = self.pumps
        maximum, flow = bank.capabilities(index)
        profile = self.profiles.get(index)
        return {'fill': bank.read_fill_level(index), 'flow': bank.read_flow(index),
                'pumping': bank.read_status(index), 'fault': bank.read_fault(index),
                'capacity': maximum, 'max_flow': flow,
                'minimum': profile.minimum_ml if profile else 0,
                'maximum': min(maximum, profile.maximum_ml) if profile else maximum,
                'valve': bank.read_valve_position(index), 'syringe': bank.read_syringe(index)}

    def read_pumps(self):
        if self.pump_connecting.is_set():
            return []
        return self.qmix.call(lambda: [self._read_pump(i) for i in range(self.pumps.unit_count)] if self.pumps else [])

    def _ready(self, index):
        state = self._read_pump(index)
        if state['fault']:
            raise RuntimeError('Pump reports a fault')
        if state['pumping']:
            raise RuntimeError('Stop pump before starting an operation')
        for name in ('fill', 'max_flow', 'minimum', 'maximum'):
            finite(state[name], name)
        if state['max_flow'] <= 0 or state['maximum'] <= state['minimum']:
            raise RuntimeError('Invalid supported limits')
        if not state['minimum'] - 0.001 <= state['fill'] <= state['maximum'] + 0.001:
            raise RuntimeError('Current fill is outside configured syringe limits')
        return state

    def _rate(self, state, rate):
        rate = finite(rate, 'Flow')
        if not 0 < rate <= state['max_flow']:
            raise ValueError(f'Flow must be positive and at most {state["max_flow"]:g} µL/min')
        return rate

    def _target(self, state, target):
        target = finite(target, 'Target fill')
        if not state['minimum'] <= target <= state['maximum']:
            raise ValueError('Target fill is outside configured syringe limits')
        return target

    def command_pump(self, index, mode, value=0, rate=0):
        if mode == 'reference':
            return self._reference(index)
        def command():
            state = self._ready(index)
            if mode == 'valve':
                return self.pumps.set_valve_position(index, int(value))
            if mode not in ('flow', 'volume', 'refill', 'empty', 'level'):
                raise ValueError('Unknown pump mode')
            if mode == 'flow':
                rate_value = finite(rate, 'Flow')
                self._rate(state, abs(rate_value))
                target = state['minimum'] if rate_value > 0 else state['maximum']
            else:
                rate_value = self._rate(state, rate)
                if mode == 'volume' and finite(value, 'Volume') == 0:
                    raise ValueError('Dosing volume cannot be zero')
                target = (state['fill'] - finite(value, 'Volume') if mode == 'volume' else
                          state['maximum'] if mode == 'refill' else
                          state['minimum'] if mode == 'empty' else value)
            target = self._target(state, target)
            self.pumps.set_fill_level(index, target, abs(rate_value))
        return self._manual([('pump', index)], self.qmix, command)

    def _reference(self, index):
        keys = [('pump', index)]
        self._reserve(keys)
        self.cancel.clear()
        try:
            def start():
                self._ready(index)
                self.pumps.start_reference_move(index)
            self.qmix.call(start)
            expires = time.monotonic() + 60
            while True:
                self._check(expires)
                finished, fault = self.qmix.call(lambda: (self.pumps.reference_move_finished(index), self.pumps.read_fault(index)))
                if fault:
                    raise RuntimeError('Reference fault')
                if finished:
                    return
                self.cancel.wait(0.05)
        except Exception:
            self.stop_pump(index)
            raise
        finally:
            self._release(keys)

    def configure_syringe(self, index, profile):
        profile.validate()
        def configure():
            self._ready(index)
            self.pumps.configure_syringe(index, asdict(profile))
            self.profiles[index] = profile
            maximum, _ = self.pumps.capabilities(index)
            if profile.maximum_ml > maximum + 1e-6:
                raise ValueError('SDK capacity is below configured fill limit; edit syringe limits')
            return self._read_pump(index)
        return self._manual([('pump', index)], self.qmix, configure)

    def map_valve(self, index, energized, deenergized):
        if {energized, deenergized} != {1, 2}:
            raise ValueError('Explicitly select distinct energized and de-energized positions')
        def apply():
            state = self._ready(index)
            if state['valve'] not in (1, 2):
                raise ValueError('Selected pump has no supported built-in valve')
            self.mapping[index] = (energized, deenergized)
        return self._manual([('pump', index)], self.qmix, apply)

    def query_valve(self, address):
        def query():
            if not self.valves:
                raise RuntimeError('Valves disconnected')
            self.valves.cancel.clear()
            self.units.pop(address, None)
            unit = self.valves.detect(address)
            self.units[address] = unit
            return unit
        return self._manual([('serial', 0)], self.serial, query)

    def _move_valve(self, address, position):
        if not self.valves:
            raise RuntimeError('Valves disconnected')
        unit = self.units.get(address)
        if not unit or unit.error or position not in VALVE_TYPES[unit.valve_type]:
            raise ValueError('Valve is query-only or position is unsupported')
        try:
            result = self.valves.move(position, address=address, timeout=10)
            if result != position:
                raise RuntimeError('Valve position mismatch')
            self.units[address] = DetectedUnit(address, unit.firmware, unit.configuration, result, unit.valve_type)
            return result
        except Exception as exc:
            self.units[address] = DetectedUnit(address, unit.firmware, unit.configuration,
                                              'unknown', unit.valve_type, f'{exc}; final position unverified')
            raise

    def move_valve(self, address, position):
        def move():
            if self.valves:
                self.valves.cancel.clear()
            return self._move_valve(address, position)
        return self._manual([('serial', 0)], self.serial, move, timeout=13)

    def stop_pump(self, index):
        self.cancel.set()  # Also interrupts a reference operation.
        with self.lock:
            if self.state['running'] and self.state.get('pump') == index:
                self.state['step'] = 'Stopping'
        return self.qmix.submit(lambda: self.pumps.stop(index) if self.pumps else None,
                                priority=0, recovery=True)

    def start(self, kind, index, *, rate=None, refill='Full syringe', volume=0,
              address=None, before='input', after='output', deadline=120):
        keys = [('pump', index)] + ([('serial', 0)] if kind == 'inject' else [])
        if kind not in ('stir', 'inject'):
            raise ValueError('Unknown helper')
        deadline = finite(deadline, 'Operation deadline')
        if not 0 < deadline <= 3600:
            raise ValueError('Deadline must be within 0–3600 seconds')
        with self.lock:
            if self.state['running']:
                raise RuntimeError('A helper is already running')
        self._reserve(keys)
        self.cancel.clear()
        self._publish(running=True, kind=kind, pump=index, step='Validating', message='', progress=0,
                      injected=0, remaining=0)
        def run():
            try:
                expires = time.monotonic() + deadline
                state = self._pump_call(lambda: self._ready(index), expires)
                flow = self._rate(state, state['max_flow'] if rate is None else rate)
                initial = state['fill']
                if kind == 'stir':
                    mapping = self.mapping.get(index)
                    if not mapping:
                        raise ValueError('Map energized/de-energized Cetoni valve positions first')
                    if state['valve'] is None:
                        raise ValueError('No built-in Cetoni valve')
                    target = state['maximum'] if refill == 'Full syringe' else initial if refill == 'Volume before emptying' else volume
                    if refill not in ('Full syringe', 'Volume before emptying', 'Defined volume'):
                        raise ValueError('Invalid refill target')
                    target = self._target(state, target)
                    self._publish(step='Energizing Cetoni valve', remaining=initial)
                    observed = self._pump_call(lambda: self.pumps.set_valve_position(index, mapping[0]), expires)
                    if observed != mapping[0]:
                        raise RuntimeError('Energized position unverified')
                    self._dose(index, initial, state['minimum'], flow, expires, 'Emptying')
                    self._dose(index, state['minimum'], target, flow, expires, 'Refilling')
                    self._publish(step='De-energizing Cetoni valve')
                    observed = self._pump_call(lambda: self.pumps.set_valve_position(index, mapping[1]), expires)
                    if observed != mapping[1]:
                        raise RuntimeError('De-energized position unverified')
                else:
                    volume_value = finite(volume, 'Injection volume')
                    if volume_value <= 0:
                        raise ValueError('Injection volume must be positive')
                    target = self._target(state, initial - volume_value)
                    def validate_valve():
                        if not self.valves or not self.valves.connected:
                            raise RuntimeError('Selected valve disconnected')
                        self.valves.cancel.clear()
                        unit = self.valves.detect(address)
                        self.units[address] = unit
                        if unit.error or before not in VALVE_TYPES[unit.valve_type] or after not in VALVE_TYPES[unit.valve_type]:
                            raise ValueError('Valve position unsupported or valve query-only')
                    self._serial_call(validate_valve, expires)
                    self._publish(step='Moving valve before injection', remaining=initial)
                    self._serial_call(lambda: self._move_valve(address, before), expires)
                    self._dose(index, initial, target, flow, expires, 'Injecting')
                    self._publish(step='Moving valve after injection')
                    self._serial_call(lambda: self._move_valve(address, after), expires)
                self._check(expires)
                self._publish(step='Completed', message='Completion and final positions verified', progress=100)
            except Exception as exc:
                cancelled = self.cancel.is_set() or isinstance(exc, Cancelled)
                future = self.stop_pump(index)
                try:
                    future.result(3)
                    stopped = self.qmix.call(lambda: not self.pumps.read_status(index) and abs(self.pumps.read_flow(index)) < 1e-6,
                                             recovery=True, timeout=2)
                    detail = 'Pump stopped (readback verified).' if stopped else 'Pump stop unverified.'
                except Exception:
                    detail = 'Pump stop unverified; inspect device.'
                self._publish(step='Cancelled' if cancelled else 'Error',
                              message=f'{exc}. {detail} Later valve transitions skipped; query positions before reuse.')
            finally:
                self._publish(running=False)
                self._release(keys)
        self.helper = Thread(target=run, name='Helper sequence', daemon=True)
        self.helper.start()

    def _check(self, expires):
        if self.cancel.is_set():
            raise Cancelled('Sequence cancelled')
        if time.monotonic() >= expires:
            raise TimeoutError('Sequence operation deadline exceeded')

    def _pump_call(self, action, expires):
        self._check(expires)
        result = self.qmix.call(lambda: (self._check(expires), action())[1], timeout=min(5, max(0.001, expires-time.monotonic())))
        self._check(expires)
        return result

    def _serial_call(self, action, expires):
        self._check(expires)
        result = self.serial.call(lambda: (self._check(expires), action())[1], timeout=min(13, max(0.001, expires-time.monotonic())))
        self._check(expires)
        return result

    def _dose(self, index, initial, target, flow, expires, step):
        self._publish(step=step)
        self._pump_call(lambda: self.pumps.set_fill_level(index, target, flow), expires)
        while True:
            state = self._pump_call(lambda: self._read_pump(index), expires)
            if state['fault']:
                raise RuntimeError('Pump fault during delivery')
            delivered = abs(initial - state['fill'])
            total = abs(initial-target)
            self._publish(injected=delivered, remaining=state['fill'],
                          progress=min(100, delivered / total * 100) if total else 100)
            if not state['pumping']:
                if abs(state['fill'] - target) > max(0.001, total * 0.001):
                    raise RuntimeError(f'Target fill unverified: observed {state["fill"]:.6g}, expected {target:.6g} mL')
                if abs(state['flow']) > 1e-6:
                    raise RuntimeError('Stopped flow unverified')
                return
            if self.cancel.wait(0.05):
                raise Cancelled('Sequence cancelled')

    def stop_helper(self):
        self.cancel.set()
        if self.valves:
            self.valves.cancel.set()
        state = self.snapshot()
        if state.get('pump') is not None:
            return self.stop_pump(state['pump'])

    def shutdown(self):
        self.stop_helper()
        if self.helper:
            self.helper.join(5)
        for i in range(self.pumps.unit_count if self.pumps else 0):
            self.stop_pump(i)
        for worker, action in [(self.qmix, lambda: self.pumps.cleanup() if self.pumps else None),
                               (self.serial, lambda: self.valves.disconnect() if self.valves else None)]:
            try:
                worker.call(action, timeout=3, recovery=True, priority=0)
            except Exception:
                logger.exception('Shutdown state unverified')
            worker.close()
