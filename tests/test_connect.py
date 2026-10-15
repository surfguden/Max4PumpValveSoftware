import threading
import time
from types import SimpleNamespace
import pytest
from pumpvalve.application import Application
from pumpvalve.simulation import MovingPumpBank
from pumpvalve.drivers.bank import CetoniPumpBank


def test_status_poll_does_not_quarantine_pending_connection(monkeypatch):
    original = MovingPumpBank.initialize
    def slow_initialize(self):
        time.sleep(0.2)
        original(self)
    monkeypatch.setattr(MovingPumpBank, 'initialize', slow_initialize)
    app = Application()
    thread = threading.Thread(target=lambda: app.connect_pumps(True))
    try:
        thread.start()
        deadline = time.monotonic() + 1
        while not app.pump_connecting.is_set() and time.monotonic() < deadline:
            time.sleep(0.001)
        started = time.monotonic()
        assert app.read_pumps() == []
        assert time.monotonic() - started < 0.1
        thread.join(1)
        assert not app.qmix.stalled
        assert app.read_pumps()
    finally:
        thread.join(1)
        app.shutdown()


@pytest.mark.parametrize('blocked', [None, 'fault', 'position'])
def test_every_discovered_pump_is_recovered_and_verified(monkeypatch, tmp_path, blocked):
    monkeypatch.setenv('PUMPVALVE_DATA_DIR', str(tmp_path))
    class Pump:
        def __init__(self, index):
            self.index, self.fault, self.enabled, self.clears = index, True, False, 0
        def lookup_by_device_index(self, index): assert self.index == index
        def is_in_fault_state(self): return self.fault
        def clear_fault(self):
            self.clears += 1
            self.fault = blocked == 'fault' and self.index == 1
        def is_position_sensing_initialized(self): return not (blocked == 'position' and self.index == 1)
        def is_enabled(self): return self.enabled
        def enable(self, enabled): self.enabled = enabled
        def set_flow_unit(self, *args): pass
        def set_volume_unit(self, *args): pass
        def get_volume_max(self): return 5
        def get_flow_rate_max(self): return 40000
    units = [Pump(i) for i in range(3)]
    class Factory:
        next_index = 1
        @staticmethod
        def get_no_of_pumps(): return len(units)
        def __new__(cls):
            pump = units[cls.next_index]
            cls.next_index += 1
            return pump
    bank = CetoniPumpBank()
    session = bank._session
    session.pump = units[0]
    session.qmixpump = SimpleNamespace(Pump=Factory, UnitPrefix=SimpleNamespace(micro=0, milli=0),
        VolumeUnit=SimpleNamespace(litres=0), TimeUnit=SimpleNamespace(per_minute=0))
    def initialize_first():
        session._auto_clear_fault_on_initialize()
        session._require_position_sensing_initialized_before_enable()
        session._enable_pump()
    monkeypatch.setattr(type(session), 'initialize', lambda self: initialize_first())
    if blocked == 'fault':
        with pytest.raises(RuntimeError, match='Pump 2'):
            bank.initialize()
        assert units[1].clears == 1
        assert not units[1].enabled and not units[2].enabled
    else:
        bank.initialize()
        assert bank.unit_count == 3
        assert all(unit.clears == 1 and not unit.fault for unit in units)
        assert all(unit.enabled == (not (blocked == 'position' and unit.index == 1)) for unit in units)
