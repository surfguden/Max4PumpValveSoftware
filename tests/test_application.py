import math
import threading
import time
from dataclasses import asdict
import pytest
from pumpvalve.application import Application, Syringe
from pumpvalve.workers import BusWorker
from pumpvalve.drivers.valve_session import DetectedUnit


@pytest.fixture
def app():
    a = Application()
    a.connect_pumps(True)
    a.connect_valves(True)
    a.map_valve(0, 2, 1)
    yield a
    a.shutdown()


def wait(app):
    app.helper.join(5)
    assert not app.snapshot()['running']
    return app.snapshot()


@pytest.mark.parametrize('rate', [None, 5000])
@pytest.mark.parametrize('refill,target', [('Full syringe', 1), ('Defined volume', 0.3), ('Volume before emptying', 0.6)])
def test_stir_modes(app, rate, refill, target):
    app.start('stir', 0, rate=rate, refill=refill, volume=0.3)
    assert wait(app)['step'] == 'Completed'
    assert app.pumps.read_fill_level(0) == pytest.approx(target)
    assert app.pumps.read_valve_position(0) == 1
    assert not app.valves.moves
    assert app.helper_snapshot('stir')['step'] == 'Completed'


@pytest.mark.parametrize('fill_matches_target', [False, True])
def test_stir_waits_for_idle_not_fill(app, monkeypatch, fill_matches_target):
    motion = dict(target=None, polls=0)
    transitions = []

    def set_target(index, target, flow):
        if motion['target'] is not None:
            assert motion['polls'] >= 3, 'Next motion started while pump was busy'
        transitions.append(('target', target))
        motion.update(target=target, polls=0)

    def fill(index):
        if motion['target'] is not None:
            motion['polls'] += 1
        return motion['target'] if fill_matches_target and motion['target'] is not None else 0.6

    def valve(index, position):
        if position == 1:
            assert motion['polls'] >= 3, 'Valve switched while pump was busy'
        transitions.append(('valve', position))
        return position

    monkeypatch.setattr(app.pumps, 'set_fill_level', set_target)
    monkeypatch.setattr(app.pumps, 'read_fill_level', fill)
    monkeypatch.setattr(app.pumps, 'read_status', lambda index: motion['target'] is not None and motion['polls'] < 3)
    monkeypatch.setattr(app.pumps, 'read_flow', lambda index: 1000 if motion['target'] is not None and motion['polls'] < 3 else 0)
    monkeypatch.setattr(app.pumps, 'set_valve_position', valve)
    app.start('stir', 0, rate=1000)
    assert wait(app)['step'] == 'Completed'
    assert transitions == [('valve', 2), ('target', 0), ('target', 1), ('valve', 1)]


@pytest.mark.parametrize('mode,target', [('refill', 1), ('empty', 0)])
def test_manual_max_uses_live_device_limit(app, monkeypatch, mode, target):
    commands = []
    monkeypatch.setattr(app.pumps, 'set_fill_level', lambda *args: commands.append(args))
    app.pumps._pumps[0].max_flow_rate_ul_min = 4321
    app.command_pump(0, mode, rate='max')
    app.pumps._pumps[0].max_flow_rate_ul_min = 2345
    app.command_pump(0, mode, rate='max')
    assert commands == [(0, target, 4321), (0, target, 2345)]


def test_stir_busy_deadline_stops_and_skips_later_transitions(app):
    app.pumps._pumps[0].frozen = True
    app.start('stir', 0, deadline=0.2)
    state = wait(app)
    assert state['step'] == 'Error'
    assert 'deadline' in state['message'].lower()
    assert app.pumps.read_valve_position(0) == 2
    assert app.pumps.read_flow(0) == 0
    assert app.pumps.read_fill_level(0) == pytest.approx(0.6)


@pytest.mark.parametrize('begin,end,positions', [('Not energized', 'Energized', [1, 2]),
                                                ('Energized', 'Energized', [2, 2])])
def test_stir_waits_after_idle_and_selected_valve_states(app, monkeypatch, begin, end, positions):
    transitions, waits = [], {}
    original_valve, original_target = app.pumps.set_valve_position, app.pumps.set_fill_level
    original_publish = app._publish
    def valve(index, position):
        transitions.append(('valve', position, time.monotonic()))
        return original_valve(index, position)
    def target(index, level, flow):
        transitions.append(('target', level, time.monotonic()))
        return original_target(index, level, flow)
    def publish(**values):
        if values.get('step', '').startswith('Waiting after'):
            assert not app.pumps.read_status(0)
            waits[values['step']] = time.monotonic()
        return original_publish(**values)
    monkeypatch.setattr(app.pumps, 'set_valve_position', valve)
    monkeypatch.setattr(app.pumps, 'set_fill_level', target)
    monkeypatch.setattr(app, '_publish', publish)
    app.start('stir', 0, wait_empty=0.06, wait_full=0.08, valve_begin=begin, valve_end=end)
    assert wait(app)['step'] == 'Completed'
    assert [(kind, value) for kind, value, _ in transitions] == [('valve', positions[0]), ('target', 0), ('target', 1), ('valve', positions[1])]
    assert transitions[2][2] - waits['Waiting after empty'] >= 0.055
    assert transitions[3][2] - waits['Waiting after full'] >= 0.075


@pytest.mark.parametrize('failure', ['cancel', 'fault', 'timeout'])
def test_stir_wait_interruptions_skip_refill_and_end_valve(app, monkeypatch, failure):
    targets = []
    original = app.pumps.set_fill_level
    monkeypatch.setattr(app.pumps, 'set_fill_level', lambda *args: (targets.append(args[1]), original(*args))[1])
    app.start('stir', 0, wait_empty=1, deadline=0.4 if failure == 'timeout' else 5)
    end = time.monotonic() + 2
    while app.snapshot()['step'] != 'Waiting after empty' and time.monotonic() < end:
        time.sleep(0.005)
    assert app.snapshot()['step'] == 'Waiting after empty'
    if failure == 'cancel':
        app.stop_helper()
    elif failure == 'fault':
        app.pumps._pumps[0].fault = True
    assert wait(app)['step'] == ('Cancelled' if failure == 'cancel' else 'Error')
    assert targets == [0]
    assert app.pumps.read_valve_position(0) == 2
    assert not app.pumps.read_status(0)


@pytest.mark.parametrize('address,before,after', [(a, b, c) for a, positions in
    [(2, ('input', 'output', 'bypass', 'extra')), (3, ('input', 'output', 'bypass'))]
    for b in positions for c in positions])
def test_injection_supported_positions(app, address, before, after):
    app.start('inject', 0, rate=5000, volume=0.2, address=address, before=before, after=after)
    state = wait(app)
    assert state['step'] == 'Completed', state
    assert state['injected'] == pytest.approx(0.2)
    assert state['remaining'] == pytest.approx(0.4)
    assert state['progress'] == 100
    assert app.pumps.read_flow(0) == 0
    assert app.valves.moves == [(address, before), (address, after)]
    assert app.pumps.read_valve_position(0) == 1


@pytest.mark.parametrize('values', [dict(volume=2), dict(volume=-1), dict(volume=float('nan')), dict(rate=20000), dict(rate=0), dict(before='extra', address=3)])
def test_invalid_injection_does_not_move(app, values):
    args = dict(volume=0.1, rate=5000, address=2)
    args.update(values)
    app.start('inject', 0, **args)
    assert wait(app)['step'] == 'Error'
    assert app.valves.moves == []
    assert app.pumps.read_fill_level(0) == pytest.approx(0.6)


def test_mapping_required(app):
    app.mapping.clear()
    app.start('stir', 0)
    assert wait(app)['step'] == 'Error'
    assert app.pumps.read_valve_position(0) == 1
    with pytest.raises(ValueError):
        app.map_valve(0, 1, 1)


def test_cancel_reservations_and_progress(app):
    app.start('inject', 0, rate=100, volume=0.4, address=2)
    limit = time.monotonic()+2
    while app.snapshot()['step'] != 'Injecting' and time.monotonic() < limit:
        time.sleep(0.01)
    time.sleep(0.1)
    state = app.snapshot()
    assert 0 < state['progress'] < 100
    for action in [lambda: app.command_pump(0, 'empty', rate=1000),
                   lambda: app.move_valve(2, 'extra'), app.disconnect_pumps,
                   app.disconnect_valves, app.scan_valves,
                   lambda: app.configure_syringe(0, Syringe())]:
        with pytest.raises(RuntimeError, match='reserved'):
            action()
    started = time.monotonic()
    app.stop_helper()
    state = wait(app)
    assert time.monotonic()-started < 1
    assert state['step'] == 'Cancelled'
    assert 'verified' in state['message']
    assert app.valves.moves == [(2, 'input')]
    assert app.pumps.read_flow(0) == 0
    assert not app.reserved


def test_timeout_skips_after_valve(app):
    app.pumps._pumps[0].frozen = True
    app.start('inject', 0, rate=5000, volume=0.2, address=2, deadline=0.25)
    assert wait(app)['step'] == 'Error'
    assert app.valves.moves == [(2, 'input')]
    assert app.pumps.read_flow(0) == 0


def test_fault_before_start(app):
    app.pumps._pumps[0].fault = True
    app.start('inject', 0, rate=100, volume=0.2, address=2)
    assert wait(app)['step'] == 'Error'
    assert not app.valves.moves


def test_fault_during_delivery(app):
    app.start('inject', 0, rate=100, volume=0.2, address=2)
    time.sleep(0.12)
    app.pumps._pumps[0].fault = True
    assert wait(app)['step'] == 'Error'
    assert app.valves.moves == [(2, 'input')]


def test_premature_stop_rejects_delivery(app):
    original = app.pumps.set_fill_level
    def stop_early(*args):
        original(*args)
        app.pumps.stop(args[0])
    app.pumps.set_fill_level = stop_early
    app.start('inject', 0, rate=100, volume=0.2, address=2)
    assert wait(app)['step'] == 'Error'
    assert app.valves.moves == [(2, 'input')]


def test_valve_mismatch(app):
    app.valves.move = lambda *args, **kwargs: 'bypass'
    app.start('inject', 0, rate=5000, volume=0.2, address=2)
    assert wait(app)['step'] == 'Error'
    assert app.pumps.read_fill_level(0) == pytest.approx(0.6)
    assert 'unverified' in app.units[2].error


def test_custom_syringe_limits(app):
    profile = Syringe(minimum_ml=0.1, maximum_ml=0.8)
    app.configure_syringe(0, profile)
    with pytest.raises(ValueError):
        app.command_pump(0, 'level', value=0.9, rate=1000)
    app.start('stir', 0)
    assert wait(app)['step'] == 'Completed'
    assert app.pumps.read_fill_level(0) == pytest.approx(0.8)
    app.start('inject', 0, volume=0.75, rate=5000, address=2)
    assert wait(app)['step'] == 'Error'


@pytest.mark.parametrize('field,value', [('inner_diameter_mm', float('nan')), ('max_piston_stroke_mm', 66), ('maximum_ml', 5), ('minimum_ml', -1), ('max_pressure_bar', 0)])
def test_bad_syringe(field, value):
    data = asdict(Syringe())
    data[field] = value
    with pytest.raises(ValueError):
        Syringe(**data).validate()


def test_scale_diameter():
    assert Syringe.diameter_from_scale(50, math.pi*4/4*50/1000) == pytest.approx(2)


def test_disconnected_and_invalid_config(tmp_path):
    app = Application()
    try:
        assert app.pumps is None and app.valves is None
        assert app.read_pumps() == []
        with pytest.raises(ValueError, match='XML'):
            app.connect_pumps(False, configuration=str(tmp_path/'config with spaces'))
    finally:
        app.shutdown()


def test_single_bus_owner_and_timeout_quarantine():
    worker = BusWorker('test')
    threads = []
    futures = [worker.submit(lambda: threads.append(threading.get_ident())) for _ in range(10)]
    for f in futures:
        f.result(1)
    assert len(set(threads)) == 1
    with pytest.raises(TimeoutError):
        worker.call(lambda: time.sleep(0.05), timeout=0.01)
    with pytest.raises(RuntimeError):
        worker.call(lambda: None)
    worker.call(lambda: None, recovery=True)
    worker.close()


def test_cancel_before_valve_and_query_only(app):
    app.units[2] = DetectedUnit(2, error='Fault')
    with pytest.raises(ValueError, match='query-only'):
        app.move_valve(2, 'input')
    app.units[2] = DetectedUnit(2, valve_type='unknown')
    with pytest.raises(ValueError, match='query-only'):
        app.move_valve(2, 'input')


def test_reconnect_requires_explicit_mapping(app):
    app.disconnect_pumps()
    app.connect_pumps(True)
    assert not app.mapping


def test_reference_can_stop_without_blocking_bus(app):
    app.pumps.reference_move_finished = lambda i: False
    errors = []
    def run():
        try:
            app.command_pump(0, 'reference')
        except Exception as exc:
            errors.append(exc)
    thread = threading.Thread(target=run)
    thread.start()
    time.sleep(0.1)
    assert app.read_pumps()
    app.stop_pump(0).result(1)
    thread.join(1)
    assert not thread.is_alive()
    assert errors


def test_invalid_settings(tmp_path, monkeypatch):
    from pumpvalve.storage import load
    monkeypatch.setenv('PUMPVALVE_DATA_DIR', str(tmp_path))
    (tmp_path/'settings.json').write_text('{"valves":[{"address":50,"name":"bad"}]}')
    with pytest.raises(ValueError, match='valve'):
        load()
