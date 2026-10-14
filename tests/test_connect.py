import threading
import time
from pumpvalve.application import Application
from pumpvalve.simulation import MovingPumpBank


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
