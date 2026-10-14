"""Time-based extensions of the reused simulators; never import native SDKs."""
import math
import time
import threading
from .drivers.simulated import SimulatedPump
from .drivers.bank import SimulatedPumpBank
from .drivers.valve_session import DetectedUnit, VALVE_TYPES


class MovingPump(SimulatedPump):
    def __init__(self):
        super().__init__()
        self.fill_level_ml = 0.6
        self.target = None
        self.last = time.monotonic()
        self.time_scale = 30
        self.fault = False
        self.frozen = False

    def _update(self):
        now = time.monotonic()
        if self.flow_ul_min and not self.frozen:
            self.fill_level_ml -= self.flow_ul_min / 60000 * (now - self.last) * self.time_scale
            target = self.target if self.target is not None else (0 if self.flow_ul_min > 0 else self.max_volume_ml)
            if (self.flow_ul_min > 0 and self.fill_level_ml <= target) or (self.flow_ul_min < 0 and self.fill_level_ml >= target):
                self.fill_level_ml = target
                self.flow_ul_min = 0
        self.last = now

    def set_fill_level(self, fill_level, flow_rate=None):
        self._update()
        self.target = fill_level
        self.generate_flow(math.copysign(flow_rate or self.max_flow_rate_ul_min, self.fill_level_ml - fill_level))

    def generate_flow(self, flow_rate):
        self._update()
        super().generate_flow(flow_rate)

    def stop(self):
        self._update()
        super().stop()

    def read_fill_level(self):
        self._update()
        return super().read_fill_level()

    def read_status(self):
        self._update()
        return super().read_status()

    def read_flow(self):
        self._update()
        return super().read_flow()

    def read_fault(self):
        return self.fault

    def configure_syringe(self, config):
        super().configure_syringe(config)
        self.fill_level_ml = min(self.fill_level_ml, self.max_volume_ml)


class MovingPumpBank(SimulatedPumpBank):
    def __init__(self, count=2):
        super().__init__(count)
        self._pumps = [MovingPump() for _ in range(count)]


class SimulatedValves:
    def __init__(self):
        self.connected = False
        self.cancel = threading.Event()
        self.positions = {2: 'output', 3: 'input'}
        self.verified_units = {}
        self.moves = []

    def open_bus(self, port_name='', baudrate=9600):
        self.connected = True
        self.cancel.clear()

    def disconnect(self):
        self.connected = False
        self.verified_units.clear()

    def detect(self, address, **kwargs):
        if not self.connected:
            raise RuntimeError('Valves disconnected')
        kind = '4-port loop' if address == 2 else '3-port valve'
        if address not in self.positions:
            raise RuntimeError('Saved valve missing')
        self.verified_units[address] = kind
        return DetectedUnit(address, 'VSeries:SIM', '4PORT-LOOP' if address == 2 else '3PORT-Y',
                            self.positions[address], kind)

    def scan(self, **kwargs):
        return [self.detect(i) for i in self.positions]

    def query_position(self, *, address):
        if not self.connected:
            raise RuntimeError('Valves disconnected')
        return self.positions[address]

    def move(self, position, timeout=10, *, address, **kwargs):
        if position not in VALVE_TYPES[self.verified_units.get(address, 'unknown')]:
            raise ValueError('Unsupported or unverified valve position')
        if self.cancel.wait(0.05):
            raise RuntimeError('Cancelled; valve position unverified')
        self.moves.append((address, position))
        self.positions[address] = position
        return self.query_position(address=address)
