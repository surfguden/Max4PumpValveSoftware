from collections import deque
from concurrent.futures import ThreadPoolExecutor
import json
import socket
import time
import pytest
from pumpvalve.commands import Commands
from pumpvalve.ipc import Host, request
from pumpvalve.cli import parser, envelope


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setenv('PUMPVALVE_DATA_DIR', str(tmp_path/'agent data with spaces'))
    commands = Commands()
    host = Host(commands)
    yield commands, host
    host.close()
    commands.close()


def succeed(command, args=None, rid=None):
    if command in ('pumps.connect', 'valves.connect') and args is None:
        args = {'simulation': True}
    result = request(command, args, rid)
    assert result['ok'], result
    return result['result']


def test_disconnected_start_and_single_owner(service):
    c, host = service
    status = succeed('status')
    assert not status['pump_connected'] and not status['valve_connected']
    with pytest.raises(RuntimeError, match='already owns'):
        Host(c)
    assert succeed('help')['pump_numbers'] == '1–4'


def test_same_command_from_gui_and_terminal(service):
    c, _ = service
    assert c.execute('pumps.connect', {'simulation': True}, source='gui')['ok']
    assert succeed('status')['pumps'][0]['pump'] == 1
    succeed('pump.map', dict(pump=1, energized=2, deenergized=1))
    assert succeed('status')['mapping'] == {'1': [2, 1]}
    succeed('pump.command', dict(pump=1, mode='level', value=0.5, rate=5000))
    time.sleep(0.1)
    result = c.execute('pump.stop', {'pump': 1}, source='gui')
    assert result['ok'] and result['result']['verified']
    events = succeed('events')['events']
    assert any(e.get('source') == 'gui' and e.get('command') == 'pumps.connect' for e in events)
    assert any(e.get('source') == 'terminal' and e.get('command') == 'pump.command' for e in events)
    assert any(e['event'] == 'command.completed' and e['command_id'] == result['id'] for e in events)


def test_idempotent_concurrent_requests(service):
    c, _ = service
    succeed('pumps.connect')
    original = c.app.command_pump
    seen = []
    def command(*args, **kwargs):
        seen.append(1)
        time.sleep(0.05)
        return original(*args, **kwargs)
    c.app.command_pump = command
    arguments = dict(pump=1, mode='level', value=0.5, rate=5000)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: request('pump.command', arguments, 'retry-1'), range(2)))
    assert all(r['ok'] for r in results)
    assert len(seen) == 1
    assert not request('pump.command', {**arguments, 'value': 0.4}, 'retry-1')['ok']


def test_reservations_and_stop_across_clients(service):
    c, _ = service
    succeed('pumps.connect')
    succeed('valves.connect')
    started = succeed('helper.start', dict(kind='inject', pump=1, volume=0.3, rate=100, address=2), 'injection-1')
    deadline = time.monotonic()+2
    while c.app.snapshot()['step'] != 'Injecting' and time.monotonic() < deadline:
        time.sleep(0.01)
    conflict = c.execute('pump.command', dict(pump=1, mode='empty', rate=100), source='gui')
    assert not conflict['ok'] and 'reserved' in conflict['error']
    assert not request('valve.move', dict(address=2, position='extra'))['ok']
    succeed('helper.stop')
    c.app.helper.join(2)
    assert c.app.snapshot()['step'] == 'Cancelled'
    assert c.app.valves.moves == [(2, 'input')]
    events = succeed('events')['events']
    helper_events = [e for e in events if e['event'] == 'helper.state']
    assert all(e['command_id'] == started['command_id'] for e in helper_events)
    assert any(e['state'].get('pump') == 1 for e in helper_events)
    assert any(e['state']['step'] == 'Cancelled' for e in helper_events)


@pytest.mark.parametrize('command,args', [('pumps.connect', {'simulation': 'false'}),
    ('pumps.connect', {'count': True}), ('pump.stop', {'pump': 0}),
    ('valve.move', {'address': 2.0, 'position': 'input'}),
    ('helper.start', {'pump': 1, 'kind': 'inject', 'address': 20}),
    ('settings.save', {'baud': 1}), ('status', {'unexpected': 1}),
    ('not-a-command', {}), ('pump.command', {'pump': 1, 'mode': 'flow', 'rate': float('nan')})])
def test_malformed_requests_never_connect(service, command, args):
    c, _ = service
    response = c.execute(command, args)
    assert not response['ok']
    assert c.app.pumps is None and c.app.valves is None


def test_event_replay_gap_and_session_cursor(service):
    c, _ = service
    c.events.items = deque(maxlen=3)
    for i in range(5):
        c.events.emit('test', number=i)
    page = succeed('events')
    assert page['gap']
    cursor = page['cursor']
    c.events.emit('test', number=6)
    next_page = succeed('events', dict(since=cursor, session=page['session']))
    assert len(next_page['events']) == 1
    assert not request('events', dict(since=cursor, session='old-session'))['ok']
    assert not request('events', dict(since=-1))['ok']


def test_unauthenticated_and_oversized_requests(service):
    c, host = service
    with socket.create_connection(host.server.server_address) as sock:
        sock.sendall(b'{"command":"pumps.connect","token":"wrong"}\n')
        result = json.loads(sock.makefile('rb').readline())
    assert not result['ok'] and 'Authentication' in result['error']
    with socket.create_connection(host.server.server_address) as sock:
        sock.sendall(b'x'*65537 + b'\n')
        result = json.loads(sock.makefile('rb').readline())
    assert not result['ok']
    assert c.app.pumps is None


def test_cli_translates_to_canonical_commands():
    p = parser()
    command, args, _ = envelope(p.parse_args(['pump','level','--pump','1','--value','0.5','--rate','100']))
    assert command == 'pump.command' and args == dict(pump=1, mode='level', value=0.5, rate=100)
    assert envelope(p.parse_args(['pumps','connect']))[1]['simulation'] is False
    assert envelope(p.parse_args(['pumps','connect','--simulate']))[1]['simulation'] is True
    assert envelope(p.parse_args(['pumps','connect','--hardware','--configuration','C:/config folder']))[1]['simulation'] is False


def test_settings_and_syringe_commands(service):
    c, _ = service
    succeed('pumps.connect')
    result = succeed('pump.syringe', dict(pump=1, profile=dict(name='Agent syringe', minimum_ml=0, maximum_ml=0.8)))
    assert result['maximum'] == 0.8
    succeed('settings.save', dict(config='C:/configuration with spaces', port='COM42', baud=38400,
                                  valves=[dict(address=2, name='Injection valve')]))
    assert succeed('status')['settings']['port'] == 'COM42'
    from pumpvalve.storage import load
    assert 'Agent syringe' in load()['syringes']


def test_plastic_profile_applies_through_client_and_persists(service):
    succeed('pumps.connect')
    profile = dict(name='BD 5ml', material='Plastic', inner_diameter_mm=12.07,
                   max_piston_stroke_mm=43.6985, minimum_ml=0, maximum_ml=5)
    result = succeed('pump.syringe', dict(pump=1, profile=profile))
    assert result['maximum'] == 5
    from pumpvalve.storage import load
    assert load()['syringes']['BD 5ml']['material'] == 'Plastic'


def test_stop_is_not_blocked_by_reference_request(service):
    c, _ = service
    succeed('pumps.connect')
    c.app.pumps.reference_move_finished = lambda _: False
    with ThreadPoolExecutor(1) as pool:
        moving = pool.submit(request, 'pump.command', dict(pump=1, mode='reference', syringe_disconnected=True))
        time.sleep(0.1)
        started = time.monotonic()
        succeed('pump.stop', dict(pump=1))
        assert time.monotonic()-started < 1
        assert not moving.result(2)['ok']


def test_gui_and_external_client_share_state_and_echo(service):
    import os
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    from PySide6.QtWidgets import QApplication, QLineEdit
    from pumpvalve.ui import MainWindow
    c, _ = service
    qt = QApplication.instance() or QApplication([])
    window = MainWindow(c)
    def until(predicate):
        deadline = time.monotonic()+3
        while time.monotonic() < deadline:
            qt.processEvents()
            window.poll()
            if predicate():
                return
            time.sleep(0.01)
        raise AssertionError('GUI did not reflect shared state')
    try:
        succeed('pumps.connect', dict(simulation=True, count=4))
        succeed('valves.connect')
        until(lambda: len(window.cards) == 4 and window.cards[0].isEnabled())
        window.cards[0].energized.setCurrentIndex(2)
        window.cards[0].deenergized.setCurrentIndex(1)
        window.cards[0].map()
        until(lambda: 0 in c.app.mapping)
        window.start_stir()
        until(lambda: c.app.helper_snapshot('stir')['step'] == 'Completed')
        event = next(e for e in succeed('events')['events']
                     if e['event'] == 'command.received' and e['command'] == 'helper.start')
        assert event['source'] == 'gui' and event['args']['pump'] == 1
        succeed('settings.save', dict(valves=[dict(address=2, name='Agent renamed valve')]))
        until(lambda: any(edit.text() == 'Agent renamed valve' for card in window.valve_cards for edit in card.findChildren(QLineEdit)))
        succeed('pumps.disconnect')
        until(lambda: all(not card.connected for card in window.cards))
    finally:
        window.close()
        qt.processEvents()


def test_gui_stop_bypasses_busy_normal_request_pool(service):
    import os
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    import threading
    from PySide6.QtWidgets import QApplication
    from pumpvalve.ui import MainWindow
    c, _ = service
    qt = QApplication.instance() or QApplication([])
    succeed('pumps.connect')
    window = MainWindow(c)
    gate = threading.Event()
    try:
        for _ in range(4):
            window.task(lambda: gate.wait(2))
        window.stop_pump(0)
        deadline = time.monotonic()+1
        while time.monotonic() < deadline:
            qt.processEvents()
            events = c.events.read()['events']
            if any(e['event'] == 'command.completed' and e.get('command') == 'pump.stop' for e in events):
                break
            time.sleep(0.01)
        else:
            raise AssertionError('Stop was blocked behind normal GUI requests')
    finally:
        gate.set()
        window.close()
        qt.processEvents()


def test_stop_on_unrelated_pump_does_not_cancel_helper(service):
    c, _ = service
    succeed('pumps.connect')
    succeed('pump.map', dict(pump=1, energized=2, deenergized=1))
    succeed('helper.start', dict(kind='stir', pump=1, rate=100))
    time.sleep(0.05)
    succeed('pump.stop', dict(pump=2))
    assert c.app.snapshot()['running'] and not c.app.cancel.is_set()
    succeed('pump.stop', dict(pump=1))
    c.app.helper.join(2)
    assert c.app.snapshot()['step'] == 'Cancelled'


def test_helper_stop_does_not_stop_unrelated_flow_or_serial_bus(service):
    c, _ = service
    succeed('pumps.connect')
    succeed('valves.connect')
    succeed('pump.command', dict(pump=1, mode='flow', rate=100))
    assert not succeed('helper.stop')['stop_requested']
    assert c.app.pumps.read_status(0)
    succeed('pump.stop', dict(pump=1))
    succeed('pump.map', dict(pump=1, energized=2, deenergized=1))
    succeed('helper.start', dict(kind='stir', pump=1, rate=100))
    succeed('helper.stop')
    c.app.helper.join(2)
    assert not c.app.valves.cancel.is_set()
