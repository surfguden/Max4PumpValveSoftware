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
from .storage import test_simulation_enabled


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
        if not self.name.strip() or self.material not in ('Glass', 'Steel', 'Plastic'):
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
        self.expected_profiles = {}
        self.pump_diagnostics = {}
        self.pump_connection_error = ''
        self.mapping = {}
        self.units = {}
        self.lock = Lock()
        self.reserved = set()
        self.helper = None
        self.cancel = Event()
        self.reference_cancels = {i: Event() for i in range(4)}
        self.state = {'running': False, 'step': 'Idle', 'message': '', 'progress': 0}
        self.helper_states = {kind: dict(self.state) for kind in ('stir', 'inject')}

    def _publish(self, **values):
        with self.lock:
            self.state.update(values)
            if self.state.get('kind'):
                self.helper_states[self.state['kind']] = dict(self.state)
        logger.info('Sequence %r', values)
        observer = getattr(self, 'observe', None)
        if observer:
            try:
                observer(self.snapshot())
            except Exception:
                logger.exception('Sequence observer failed')

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
        if simulation and not test_simulation_enabled():
            raise ValueError('Simulation is disabled in normal operation')
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
                except Exception as exc:
                    self.pump_connection_error = str(exc)
                    if hasattr(bank, 'last_diagnostics'):
                        self.pump_diagnostics[0] = bank.last_diagnostics()
                    bank.cleanup()
                    raise
                self.profiles.clear()
                self.mapping.clear()
                self.pumps = bank
                self.pump_connection_error = ''
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
        if simulation and not test_simulation_enabled():
            raise ValueError('Simulation is disabled in normal operation')
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
        syringe = bank.read_syringe(index)
        expected = self.expected_profiles.get(index)
        applied = profile is not None and syringe is not None and all(
            math.isclose(actual, requested, rel_tol=1e-6, abs_tol=1e-5)
            for actual, requested in zip(syringe, (profile.inner_diameter_mm, profile.max_piston_stroke_mm)))
        if expected and profile != expected:
            applied = False
        fault = bank.read_fault(index)
        if fault and hasattr(bank, 'last_diagnostics'):
            self.pump_diagnostics[index] = bank.last_diagnostics()
        return {'fill': bank.read_fill_level(index), 'flow': bank.read_flow(index),
                'pumping': bank.read_status(index), 'fault': fault,
                'position_initialized': bank.read_position_initialized(index),
                'capacity': maximum, 'max_flow': flow,
                'minimum': profile.minimum_ml if profile else 0,
                'maximum': min(maximum, profile.maximum_ml) if profile else maximum,
                'valve': bank.read_valve_position(index), 'syringe': syringe,
                'syringe_expected': expected.name if expected else None,
                'syringe_applied': applied, 'syringe_required': expected is not None}

    def read_pumps(self):
        if self.pump_connecting.is_set():
            return []
        return self.qmix.call(lambda: [self._read_pump(i) for i in range(self.pumps.unit_count)] if self.pumps else [])

    def _ready(self, index, require_syringe=False, allow_unreferenced=False):
        state = self._read_pump(index)
        if state['fault']:
            raise RuntimeError('Pump reports a fault')
        if state['pumping']:
            raise RuntimeError('Stop pump before starting an operation')
        if not state['position_initialized'] and not allow_unreferenced:
            raise RuntimeError('Reference required: run Reference move before dosing or starting helpers')
        if require_syringe and state['syringe_required'] and not state['syringe_applied']:
            raise ValueError('Saved syringe profile is not applied to this connection; configure syringe before motion')
        for name in ('fill', 'max_flow', 'minimum', 'maximum'):
            finite(state[name], name)
        if state['max_flow'] <= 0 or state['maximum'] <= state['minimum']:
            raise RuntimeError('Invalid supported limits')
        if state['position_initialized'] and not state['minimum'] - 0.001 <= state['fill'] <= state['maximum'] + 0.001:
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

    def command_pump(self, index, mode, value=0, rate=0, syringe_disconnected=False):
        if mode == 'reference':
            if syringe_disconnected is not True:
                raise ValueError('Disconnect the syringe before a reference move and confirm syringe_disconnected=true')
            return self._reference(index)
        def command():
            state = self._ready(index, require_syringe=mode != 'valve')
            if mode == 'valve':
                if finite(value, 'Valve position') not in (1, 2):
                    raise ValueError('Valve position must be exactly 1 or 2')
                return self.pumps.set_valve_position(index, int(value))
            if mode not in ('flow', 'volume', 'refill', 'empty', 'level'):
                raise ValueError('Unknown pump mode')
            if mode == 'flow':
                rate_value = finite(rate, 'Flow')
                self._rate(state, abs(rate_value))
                target = state['minimum'] if rate_value > 0 else state['maximum']
            else:
                rate_value = self._rate(state, state['max_flow'] if mode in ('refill', 'empty') and rate == 'max' else rate)
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
        cancelled = self.reference_cancels[index]
        cancelled.clear()
        try:
            def start():
                # Homing establishes the encoder position; it does not dose a
                # syringe volume and must remain available before setup is applied.
                self._ready(index, allow_unreferenced=True)
                self.pumps.start_reference_move(index)
            self.qmix.call(start)
            expires = time.monotonic() + 60
            while True:
                if cancelled.is_set():
                    raise Cancelled('Reference cancelled')
                if time.monotonic() >= expires:
                    raise TimeoutError('Reference operation deadline exceeded')
                finished, fault = self.qmix.call(lambda: (self.pumps.reference_move_finished(index), self.pumps.read_fault(index)))
                if fault:
                    raise RuntimeError('Reference fault')
                if finished:
                    if not self.qmix.call(lambda: self.pumps.read_position_initialized(index)):
                        raise RuntimeError('Reference finished but position sensing is still uninitialized')
                    self.qmix.call(lambda: self.pumps.stop(index))
                    self.verify_stopped(index)
                    return
                cancelled.wait(0.05)
        except Exception:
            self.stop_pump(index)
            raise
        finally:
            self._release(keys)

    def configure_syringe(self, index, profile):
        profile.validate()
        def configure():
            self._ready(index, allow_unreferenced=True)
            self.pumps.configure_syringe(index, asdict(profile))
            maximum, _ = self.pumps.capabilities(index)
            if profile.maximum_ml > maximum + 1e-6:
                raise ValueError('SDK capacity is below configured fill limit; edit syringe limits')
            geometry = self.pumps.read_syringe(index)
            if geometry is None or not all(math.isclose(actual, wanted, rel_tol=1e-6, abs_tol=1e-5)
                    for actual, wanted in zip(geometry, (profile.inner_diameter_mm, profile.max_piston_stroke_mm))):
                self.profiles.pop(index, None)
                raise RuntimeError('Syringe geometry readback does not match requested profile')
            self.profiles[index] = profile
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
        self.reference_cancels[index].set()
        with self.lock:
            if self.state['running'] and self.state.get('pump') == index:
                self.cancel.set()
                if self.state.get('kind') == 'inject' and self.valves:
                    self.valves.cancel.set()
                self.state['step'] = 'Stopping'
        return self.qmix.submit(lambda: self.pumps.stop(index) if self.pumps else None,
                                priority=0, recovery=True)

    def verify_stopped(self, index, timeout=1):
        """Poll settling on the caller; keep the bus available for priority Stop."""
        expires = time.monotonic() + timeout
        idle_fill = None
        while True:
            state = self.qmix.call(lambda: self._read_pump(index), recovery=True, timeout=2, priority=0)
            if not state['pumping'] and abs(state['flow']) <= 1e-6:
                if idle_fill is not None and abs(state['fill']-idle_fill) <= 1e-7:
                    return state
                idle_fill = state['fill']
            else:
                idle_fill = None
            if time.monotonic() >= expires:
                raise RuntimeError(f'Pump stop unverified: pumping={state["pumping"]}, '
                                   f'flow={state["flow"]:g} µL/min, fault={state["fault"]}. '
                                   'Inspect diagnostics before further motion.')
            time.sleep(0.025)

    def diagnose_pump(self, index):
        def capture():
            previous = self.pump_diagnostics.get(index, {})
            if not self.pumps:
                return {'connected': False, 'last_observation': self.pump_diagnostics.get(index, {}),
                        'connection_error': self.pump_connection_error,
                        'recovery': 'Inspect hardware and vendor diagnostics, then reconnect. A fresh application restart may be needed.'}
            if hasattr(self.pumps, 'diagnostics'):
                result = self.pumps.diagnostics(index)
            else:
                result = self._read_pump(index)
            self.pump_diagnostics[index] = result
            return {'connected': True, 'observation': result, 'previous_observation': previous}
        return self.qmix.call(capture, recovery=True)

    def recover_pump(self, index):
        def recover():
            if not self.pumps:
                raise RuntimeError('Pump disconnected; inspect last diagnostics and reconnect first')
            try:
                return self.pumps.recover_fault(index)
            finally:
                if hasattr(self.pumps, 'last_diagnostics'):
                    self.pump_diagnostics[index] = self.pumps.last_diagnostics()
        keys = [('pump', index)]
        self._reserve(keys)
        try:
            result = self.qmix.call(recover)
            self.verify_stopped(index)
            return result
        finally:
            self._release(keys)

    def start(self, kind, index, *, rate=None, refill='Full syringe', volume=0,
              address=None, before='input', after='output', deadline=120, command_id=None,
              wait_full=0, wait_empty=0, valve_begin='Energized', valve_end='Not energized'):
        keys = [('pump', index)] + ([('serial', 0)] if kind == 'inject' else [])
        if kind not in ('stir', 'inject'):
            raise ValueError('Unknown helper')
        if not self.pumps or not 0 <= index < self.pumps.unit_count:
            raise RuntimeError('Selected pump disconnected')
        if kind == 'inject' and not (self.valves and self.valves.connected):
            raise RuntimeError('Selected valve disconnected')
        deadline = finite(deadline, 'Operation deadline')
        if not 0 < deadline <= 3600:
            raise ValueError('Deadline must be within 0–3600 seconds')
        wait_full, wait_empty = finite(wait_full, 'Full wait'), finite(wait_empty, 'Empty wait')
        if not 0 <= wait_full <= 3600 or not 0 <= wait_empty <= 3600:
            raise ValueError('Wait times must be within 0–3600 seconds')
        if valve_begin not in ('Energized', 'Not energized') or valve_end not in ('Energized', 'Not energized'):
            raise ValueError('Choose Energized or Not energized for cycle valve states')
        with self.lock:
            if self.state['running']:
                raise RuntimeError('A helper is already running')
        self._reserve(keys)
        self.cancel.clear()
        self._publish(running=True, kind=kind, pump=index, command_id=command_id, step='Validating', message='', progress=0,
                      injected=0, remaining=0, diagnostics={})
        def run():
            try:
                expires = time.monotonic() + deadline
                state = self._pump_call(lambda: self._ready(index, require_syringe=True), expires)
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
                    begin_position = mapping[0 if valve_begin == 'Energized' else 1]
                    end_position = mapping[0 if valve_end == 'Energized' else 1]
                    self._publish(step=f'Setting beginning valve: {valve_begin}', remaining=initial)
                    observed = self._pump_call(lambda: self.pumps.set_valve_position(index, begin_position), expires)
                    if observed != begin_position:
                        raise RuntimeError('Beginning valve position unverified')
                    self._dose(index, initial, state['minimum'], flow, expires, 'Emptying', verify_target=False)
                    self._dwell(index, wait_empty, expires, 'Waiting after empty')
                    self._dose(index, state['minimum'], target, flow, expires, 'Refilling', verify_target=False)
                    self._dwell(index, wait_full, expires, 'Waiting after full')
                    self._publish(step=f'Setting ending valve: {valve_end}')
                    observed = self._pump_call(lambda: self.pumps.set_valve_position(index, end_position), expires)
                    if observed != end_position:
                        raise RuntimeError('Ending valve position unverified')
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
                final = self._pump_call(lambda: self._read_pump(index), expires)
                if final['fault'] or final['pumping'] or abs(final['flow']) > 1e-6:
                    raise RuntimeError('Final pump idle state unverified')
                if kind == 'inject' and abs(final['fill']-target) > max(0.001, volume_value*.001):
                    raise RuntimeError('Final injection volume unverified')
                self._check(expires)
                self._publish(step='Completed', message='Completion and final positions verified', progress=100,
                              remaining=final['fill'], injected=abs(initial-final['fill']) if kind == 'inject'
                              else abs(final['fill']-state['minimum']))
            except Exception as exc:
                cancelled = self.cancel.is_set() or isinstance(exc, Cancelled)
                future = self.stop_pump(index)
                try:
                    future.result(3)
                    self.verify_stopped(index)
                    detail = 'Pump stopped (readback verified).'
                except Exception as stop_error:
                    detail = str(stop_error)
                try:
                    diagnostics = self.diagnose_pump(index)
                except Exception as diagnostic_error:
                    diagnostics = {'error': str(diagnostic_error)}
                self._publish(step='Cancelled' if cancelled else 'Error',
                              diagnostics=diagnostics,
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

    def _dwell(self, index, seconds, expires, step):
        if not seconds:
            return
        end = time.monotonic() + seconds
        self._publish(step=step)
        while True:
            self._check(expires)
            def verify_idle():
                if self.pumps.read_fault(index):
                    raise RuntimeError('Pump fault during wait')
                if self.pumps.read_status(index) or abs(self.pumps.read_flow(index)) > 1e-6:
                    raise RuntimeError('Pump no longer idle during wait')
            self._pump_call(verify_idle, expires)
            remaining = end - time.monotonic()
            if remaining <= 0:
                return
            if self.cancel.wait(min(0.05, remaining)):
                raise Cancelled('Sequence cancelled during wait')

    def _dose(self, index, initial, target, flow, expires, step, verify_target=True):
        self._publish(step=step)
        self._pump_call(lambda: self.pumps.set_fill_level(index, target, flow), expires)
        idle_fill = None
        while True:
            state = self._pump_call(lambda: self._read_pump(index), expires)
            if state['fault']:
                raise RuntimeError('Pump fault during delivery')
            delivered = abs(initial - state['fill'])
            total = abs(initial-target)
            self._publish(injected=delivered, remaining=state['fill'],
                          progress=min(100, delivered / total * 100) if total else 100)
            if not state['pumping']:
                # Fill can precede the SDK's transition to idle within one read.
                # Require a second stable idle sample before reporting final volume.
                if idle_fill is None or abs(state['fill'] - idle_fill) > 1e-7:
                    idle_fill = state['fill']
                    if self.cancel.wait(0.05):
                        raise Cancelled('Sequence cancelled')
                    continue
                # Stirring advances on the SDK idle flag, never a fill threshold.
                # Injection retains the dose-volume verification it requires.
                if verify_target and abs(state['fill'] - target) > max(0.001, total * 0.001):
                    raise RuntimeError(f'Target fill unverified: observed {state["fill"]:.6g}, expected {target:.6g} mL')
                if abs(state['flow']) > 1e-6:
                    raise RuntimeError('Stopped flow unverified')
                return
            idle_fill = None
            if self.cancel.wait(0.05):
                raise Cancelled('Sequence cancelled')

    def stop_helper(self):
        state = self.snapshot()
        if not state['running']:
            return None
        self.cancel.set()
        if self.valves and state.get('kind') == 'inject':
            self.valves.cancel.set()
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
