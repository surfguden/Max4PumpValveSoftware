"""Regressions for observations from the physical 5 mL test."""
import time
from types import SimpleNamespace
import pytest
from pumpvalve.application import Application, Syringe
from pumpvalve.commands import Commands
from pumpvalve.drivers.cetoni import CetoniPump, CetoniPumpError


@pytest.fixture
def app():
    app = Application()
    app.connect_pumps(True, count=1)
    yield app
    app.shutdown()


@pytest.mark.parametrize('kind', ['stir', 'inject'])
def test_disconnected_helper_rejected_without_started_event(kind):
    a = Application()
    try:
        with pytest.raises(RuntimeError, match='pump disconnected'):
            a.start(kind, 0)
        assert a.snapshot()['step'] == 'Idle'
        assert not a.reserved
    finally:
        a.shutdown()


def test_disconnected_valve_preflight_does_not_touch_pump(app):
    with pytest.raises(RuntimeError, match='valve disconnected'):
        app.start('inject', 0, volume=.1, address=2)
    assert app.snapshot()['step'] == 'Idle'
    assert not app.pumps.read_status(0)


def test_stop_waits_for_settling_without_occupying_bus(app, monkeypatch):
    expires = time.monotonic() + .12
    monkeypatch.setattr(app.pumps, 'read_status', lambda _: time.monotonic() < expires)
    monkeypatch.setattr(app.pumps, 'read_flow', lambda _: 0)
    assert app.verify_stopped(0)['pumping'] is False
    assert not app.qmix.stalled


def test_stop_reports_busy_separately_from_zero_flow(app, monkeypatch):
    monkeypatch.setattr(app.pumps, 'read_status', lambda _: True)
    monkeypatch.setattr(app.pumps, 'read_flow', lambda _: 0)
    with pytest.raises(RuntimeError, match='pumping=True, flow=0'):
        app.verify_stopped(0, timeout=.05)
    assert not app.qmix.stalled


def test_final_helper_volume_uses_stable_idle_readback(app, monkeypatch):
    readings = iter([.41, .4, .4])
    monkeypatch.setattr(app.pumps, 'set_fill_level', lambda *args: None)
    monkeypatch.setattr(app.pumps, 'read_status', lambda _: False)
    monkeypatch.setattr(app.pumps, 'read_flow', lambda _: 0)
    monkeypatch.setattr(app.pumps, 'read_fill_level', lambda _: next(readings))
    app._dose(0,.6,.4,1000,time.monotonic()+2,'Injecting')
    assert app.snapshot()['remaining'] == .4
    assert app.snapshot()['injected'] == pytest.approx(.2)


def test_stop_returns_settled_fill(app, monkeypatch):
    fills = iter([.51,.5,.5])
    monkeypatch.setattr(app.pumps, 'read_fill_level', lambda _: next(fills))
    assert app.verify_stopped(0)['fill'] == .5


def test_completion_refreshes_volume_after_final_valve(app, monkeypatch):
    app.connect_valves(True)
    original = app._move_valve
    def move(address, position):
        observed = original(address, position)
        if position == 'output':
            app.pumps._pumps[0].fill_level_ml -= .000002
        return observed
    monkeypatch.setattr(app, '_move_valve', move)
    app.start('inject', 0, volume=.2, rate=5000, address=2, before='input', after='output')
    app.helper.join(3)
    assert app.snapshot()['step'] == 'Completed'
    assert app.snapshot()['remaining'] == pytest.approx(.399998)
    assert app.snapshot()['injected'] == pytest.approx(.200002)


def test_saved_profile_blocks_motion_after_reconnect(app):
    profile = Syringe(name='Saved 5 mL', material='Plastic', inner_diameter_mm=12.07,
                      max_piston_stroke_mm=43.6985, maximum_ml=5)
    app.expected_profiles[0] = profile
    with pytest.raises(ValueError, match='not applied'):
        app.command_pump(0, 'level', value=.5, rate=1000)
    app.configure_syringe(0, profile)
    assert app.read_pumps()[0]['syringe_applied']
    app.disconnect_pumps()
    app.connect_pumps(True, count=1)
    state = app.read_pumps()[0]
    assert state['syringe_required'] and not state['syringe_applied']
    with pytest.raises(ValueError, match='not applied'):
        app.command_pump(0, 'empty', rate=1000)


def test_geometry_mismatch_does_not_mark_profile_applied(app, monkeypatch):
    monkeypatch.setattr(app.pumps, 'read_syringe', lambda _: (5, 60))
    with pytest.raises(RuntimeError, match='readback'):
        app.configure_syringe(0, Syringe())
    assert 0 not in app.profiles


def test_recovery_never_enables_persistent_fault():
    calls = []
    pump = SimpleNamespace(is_in_fault_state=lambda: True,
                           clear_fault=lambda: calls.append('clear'),
                           stop_pumping=lambda: calls.append('stop'),
                           enable=lambda value: calls.append(('enable', value)))
    driver = CetoniPump(pump=pump)
    with pytest.raises(CetoniPumpError, match='not enabled'):
        driver.recover_fault()
    assert calls == ['stop', 'clear']
    assert driver.last_diagnostics['fault']


def test_recovery_acknowledges_delayed_clear():
    state = dict(fault=True, enabled=False, clears=0)
    def fault():
        if state['clears']:
            state['clears'] += 1
        return state['clears'] < 4
    pump = SimpleNamespace(is_in_fault_state=fault, clear_fault=lambda: state.update(clears=1),
                           stop_pumping=lambda: None, is_position_sensing_initialized=lambda: True,
                           is_enabled=lambda: state['enabled'], enable=lambda value: state.update(enabled=value))
    driver = CetoniPump(pump=pump)
    result = driver.recover_fault()
    assert state['enabled'] and not result['after']['fault']


def test_failed_connection_diagnostics_are_available(tmp_path, monkeypatch):
    monkeypatch.setenv('PUMPVALVE_DATA_DIR', str(tmp_path))
    c = Commands()
    try:
        c.app.pump_connection_error = 'Persistent drive fault'
        c.app.pump_diagnostics[0] = {'fault': True, 'enabled': False}
        response = c.execute('pump.diagnose', {'pump': 1})
        assert response['ok'] and response['result']['last_observation']['fault']
        rejected = c.execute('helper.start', {'kind': 'stir', 'pump': 1})
        assert not rejected['ok']
        assert not c.app.reserved
    finally:
        c.close()


def test_recovery_keeps_reservation_until_stop_verified(app, monkeypatch):
    def verify(index):
        assert ('pump', index) in app.reserved
        return {}
    monkeypatch.setattr(app, 'verify_stopped', verify)
    app.recover_pump(0)
    assert not app.reserved


def test_gui_shows_profile_reapply_and_recovery_controls(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from pumpvalve.ui import MainWindow
    monkeypatch.setenv('PUMPVALVE_DATA_DIR', str(tmp_path))
    qt = QApplication.instance() or QApplication([])
    c = Commands()
    ui = MainWindow(c)
    try:
        c.execute('pumps.connect', {'simulation': True, 'count': 1})
        c.execute('pump.syringe', {'pump': 1, 'profile': {'name': 'Saved'}})
        c.execute('pumps.disconnect')
        c.execute('pumps.connect', {'simulation': True, 'count': 1})
        deadline = time.monotonic()+2
        while time.monotonic()<deadline:
            qt.processEvents()
            ui.poll()
            if ui.cards and ui.cards[0].syringe.state.get('syringe_required'):
                break
            time.sleep(.01)
        card = ui.cards[0]
        assert 'APPLY SYRINGE' in card.status.text()
        assert not card.motion_buttons['empty'].isEnabled()
        assert not card.device_buttons[0].isEnabled()
        assert card.diagnose_button.isEnabled() and card.recover_button.isEnabled()
        assert card.configure_button.isEnabled()
    finally:
        ui.close()
        c.close()
        qt.processEvents()
