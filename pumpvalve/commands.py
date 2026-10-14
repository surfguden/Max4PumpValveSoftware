"""Canonical commands and event journal, shared by GUI, console and IPC."""
from collections import deque, OrderedDict
from concurrent.futures import Future
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
import json
import logging
import threading
import uuid
from .application import Application, Syringe
from .storage import data_dir, load, save, VERSION


def plain(value):
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain(v) for v in value]
    return value


class Events:
    def __init__(self, path=None):
        self.session = uuid.uuid4().hex
        self.condition = threading.Condition()
        self.items = deque(maxlen=4096)
        self.sequence = 0
        self.handler = RotatingFileHandler(path or data_dir() / 'events.jsonl',
                                          maxBytes=5_000_000, backupCount=3, encoding='utf-8')

    def emit(self, event, **data):
        with self.condition:
            self.sequence += 1
            record = plain(dict(session=self.session, sequence=self.sequence,
                time=datetime.now(timezone.utc).isoformat(), event=event, **data))
            self.items.append(record)
            self.handler.emit(logging.makeLogRecord({'msg': json.dumps(record, allow_nan=False), 'levelno': 20}))
            self.condition.notify_all()
            return record

    def read(self, since=0, wait=0, session=None):
        if type(since) is not int or since < 0 or not isinstance(wait, (int, float)) or not 0 <= wait <= 1:
            raise ValueError('Events require a nonnegative integer cursor and wait in 0–1 seconds')
        if session and session != self.session:
            raise ValueError('Application session changed; reset event cursor to 0')
        with self.condition:
            if since > self.sequence:
                raise ValueError('Event cursor is ahead of this session')
            if self.sequence <= since and wait:
                self.condition.wait(wait)
            oldest = self.items[0]['sequence'] if self.items else 1
            return dict(session=self.session, cursor=self.sequence, gap=since < oldest-1,
                        events=[dict(i) for i in self.items if i['sequence'] > since])

    def close(self):
        self.handler.close()


class Commands:
    def __init__(self, app=None):
        self.app = app or Application()
        self.events = Events()
        self.lock = threading.RLock()
        self.starts = threading.Lock()
        self.closed = threading.Event()
        self.requests = OrderedDict()
        self.latest = {'pumps': [], 'pump_connected': False, 'valve_connected': False,
                       'valves': [], 'mapping': {}, 'helpers': {}, 'reservations': [], 'settings': {}}
        self.app.observe = self.sequence_event
        try:
            self.settings = load()
        except Exception as exc:
            self.settings = {}
            self.events.emit('settings.error', error=str(exc))
        from .drivers.valve_session import DetectedUnit
        self.app.units = {v['address']: DetectedUnit(v['address'], error='Disconnected; position unverified')
                          for v in self.settings.get('valves', [])}
        self.events.emit('session.started', version=VERSION, disconnected=True)
        self.monitor = threading.Thread(target=self._monitor, name='State monitor', daemon=True)
        self.monitor.start()

    def sequence_event(self, state):
        state = dict(state)
        if state.get('pump') is not None:
            state['pump'] += 1
        self.events.emit('helper.state', state=state, command_id=state.get('command_id'))

    def _monitor(self):
        while not self.closed.is_set():
            try:
                state = self._snapshot()
                with self.lock:
                    changed = state != self.latest
                    self.latest = state
                if changed:
                    self.events.emit('state', state=state)
            except Exception as exc:
                with self.lock:
                    self.latest = {**self.latest, 'readback_error': str(exc)}
                self.events.emit('readback.error', error=str(exc), verified=False)
                self.closed.wait(1)
            self.closed.wait(0.25)

    def _snapshot(self):
        a = self.app
        pumps = a.read_pumps() if not a.qmix.stalled else []
        pumps = [dict(state, pump=index+1) for index, state in enumerate(pumps)]
        with a.lock:
            reservations = list(a.reserved)
        with self.lock:
            settings = json.loads(json.dumps(self.settings))
        from .simulation import MovingPumpBank, SimulatedValves
        helpers = {k: a.helper_snapshot(k) for k in ('stir', 'inject')}
        for state in helpers.values():
            if state.get('pump') is not None:
                state['pump'] += 1
        return plain(dict(pumps=pumps, pump_connected=a.pumps is not None,
            pump_simulation=isinstance(a.pumps, MovingPumpBank), valve_simulation=isinstance(a.valves, SimulatedValves),
            pump_count=a.pumps.unit_count if a.pumps else 0, valve_connected=bool(a.valves and a.valves.connected),
            valves=list(a.units.values()), mapping={i+1: positions for i, positions in a.mapping.items()},
            helpers=helpers,
            reservations=sorted((bus, value+1 if bus == 'pump' else value) for bus, value in reservations), settings=settings,
            buses={'qmix_stalled': a.qmix.stalled, 'serial_stalled': a.serial.stalled}))

    def cached_state(self):
        with self.lock:
            return json.loads(json.dumps(self.latest))

    def execute(self, command, args=None, *, source='terminal', request_id=None):
        args = {} if args is None else args
        request_id = request_id or uuid.uuid4().hex
        if not isinstance(command, str) or not isinstance(args, dict) or not isinstance(request_id, str) or len(request_id) > 128:
            return dict(ok=False, error='Invalid command envelope', code='invalid_request')
        # Read-only stream/status polling does not flood the command journal.
        try:
            if command == 'events':
                return dict(ok=True, result=self.events.read(**args))
            if command == 'state.cached':
                if args:
                    raise ValueError('state.cached accepts no arguments')
                return dict(ok=True, result=self.cached_state())
            fingerprint = json.dumps([command, args], sort_keys=True, allow_nan=False)
        except (ValueError, TypeError) as exc:
            return dict(ok=False, error=str(exc), code='invalid_request', id=request_id)
        with self.lock:
            if request_id in self.requests:
                previous, future = self.requests[request_id]
                if previous != fingerprint:
                    return dict(ok=False, error='Request ID reused for a different command', code='id_conflict', id=request_id)
                owner = False
            else:
                future = Future()
                self.requests[request_id] = (fingerprint, future)
                owner = True
        if not owner:
            return future.result(90)
        self.events.emit('command.received', command_id=request_id, source=source, command=command, args=args)
        try:
            result = plain(self._dispatch(command, args, request_id))
            response = dict(ok=True, id=request_id, command=command, result=result)
            self.events.emit('command.completed', command_id=request_id, source=source, command=command, result=result)
        except Exception as exc:
            response = dict(ok=False, id=request_id, command=command, error=str(exc), code=type(exc).__name__)
            self.events.emit('command.failed', command_id=request_id, source=source, command=command,
                             error=str(exc), code=type(exc).__name__)
        future.set_result(response)
        with self.lock:
            for key in list(self.requests):
                if len(self.requests) <= 1024:
                    break
                if self.requests[key][1].done():
                    self.requests.pop(key)
        return response

    def _dispatch(self, command, args, request_id):
        a = self.app
        if self.closed.is_set() and command != 'shutdown':
            raise RuntimeError('Application is shutting down')
        # Strict argument names and Python signatures reject malformed requests.
        if command == 'help':
            if args:
                raise ValueError('help accepts no arguments')
            return dict(commands=['status', 'ports', 'pumps.connect', 'pumps.disconnect', 'pump.command',
                'pump.stop', 'pump.syringe', 'pump.map', 'valves.connect', 'valves.scan',
                'valves.disconnect', 'valve.query', 'valve.move', 'helper.start', 'helper.stop',
                'settings.save', 'events', 'shutdown'], pump_numbers='1–4', valve_addresses='physical switch 0–14',
                units=dict(volume='mL', rate='µL/min'))
        if command == 'status':
            if args:
                raise ValueError('status accepts no arguments')
            return self._snapshot()
        if command == 'ports':
            if args:
                raise ValueError('ports accepts no arguments')
            from serial.tools.list_ports import comports
            return [p.device for p in comports()]
        if command == 'pumps.connect':
            args = dict(args)
            simulation = args.pop('simulation', True)
            if type(simulation) is not bool:
                raise ValueError('simulation must be true or false')
            if type(args.get('count', 2)) is not int or not 1 <= args.get('count', 2) <= 4:
                raise ValueError('count must be an integer from 1 to 4')
            count = a.connect_pumps(simulation, **args)
            return {'count': count, 'simulation': simulation}
        if command == 'pumps.disconnect':
            if args:
                raise ValueError('disconnect accepts no arguments')
            return a.disconnect_pumps()
        if command.startswith('pump.'):
            args = dict(args)
            pump = args.pop('pump', None)
            if type(pump) is not int or not 1 <= pump <= 4:
                raise ValueError('pump must be an integer from 1 to 4')
            index = pump-1
            if command == 'pump.command':
                return a.command_pump(index, **args)
            if command == 'pump.map':
                return a.map_valve(index, **args)
            if command == 'pump.syringe':
                profile = Syringe(**args.pop('profile'))
                if args:
                    raise ValueError('Unexpected syringe arguments')
                result = a.configure_syringe(index, profile)
                with self.lock:
                    self.settings.setdefault('syringes', {})[profile.name] = asdict(profile)
                    save(self.settings)
                return result
            if command == 'pump.stop':
                if args:
                    raise ValueError('Unexpected stop arguments')
                a.stop_pump(index).result(3)
                state = a.qmix.call(lambda: a._read_pump(index), recovery=True, timeout=2)
                if state['pumping'] or abs(state['flow']) > 1e-6:
                    raise RuntimeError('Pump stop unverified; device still reports flow')
                return {'stopped': True, 'verified': True, 'state': state}
        if command == 'valves.connect':
            args = dict(args)
            simulation = args.pop('simulation', True)
            if type(simulation) is not bool:
                raise ValueError('simulation must be true or false')
            return a.connect_valves(simulation, **args)
        if command in ('valves.scan', 'valves.disconnect', 'helper.stop'):
            if args:
                raise ValueError(f'{command} accepts no arguments')
            if command == 'valves.scan':
                return a.scan_valves()
            if command == 'valves.disconnect':
                return a.disconnect_valves()
            running = a.snapshot()['running']
            a.stop_helper()
            return {'stop_requested': running, 'completion': 'Observe helper.state events'}
        if command == 'valve.query':
            self._address(args.get('address'))
            return a.query_valve(**args)
        if command == 'valve.move':
            self._address(args.get('address'))
            return a.move_valve(**args)
        if command == 'helper.start':
            args = dict(args)
            pump = args.pop('pump', None)
            if type(pump) is not int or not 1 <= pump <= 4:
                raise ValueError('pump must be an integer from 1 to 4')
            if args.get('kind') == 'inject':
                self._address(args.get('address'))
            with self.starts:
                a.start(index=pump-1, command_id=request_id, **args)
            return {'started': True, 'completion': 'Observe helper.state events', 'command_id': request_id}
        if command == 'settings.save':
            with self.lock:
                updated = {**self.settings, **args}
                # Validate with the same schema used on startup before committing.
                from .storage import validate
                validate(updated)
                save(updated)
                self.settings = updated
            return updated
        if command == 'shutdown':
            if args:
                raise ValueError('shutdown accepts no arguments')
            self.closed.set()
            return {'shutdown_requested': True}
        raise ValueError(f'Unknown command: {command}')

    @staticmethod
    def _address(address):
        if type(address) is not int or not 0 <= address <= 14:
            raise ValueError('address must be a physical switch integer from 0 to 14')

    def close(self):
        self.closed.set()
        self.monitor.join(6)
        self.app.shutdown()
        self.events.emit('session.stopped')
        self.events.close()
