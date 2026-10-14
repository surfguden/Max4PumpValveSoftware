"""Human CLI and JSON-lines console; never create a second device owner to send commands."""
import argparse
import json
import shlex
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from .ipc import request
from .storage import VERSION

OUTPUT_LOCK = threading.Lock()


def parser():
    p = argparse.ArgumentParser(prog='Max4PumpValveCtl')
    p.add_argument('--version', action='version', version=VERSION)
    sub = p.add_subparsers(dest='action', required=True)
    run = sub.add_parser('run', help='Own the buses and accept stdin commands/events')
    run.add_argument('--gui', action='store_true')
    run.add_argument('--headless', action='store_true')
    run.add_argument('--no-stdin', action='store_true', help='Keep running after stdin closes (SSH service)')
    sub.add_parser('status')
    sub.add_parser('help')
    sub.add_parser('shutdown')
    send = sub.add_parser('send', help='Canonical command with JSON arguments')
    send.add_argument('command')
    send.add_argument('--args', default='{}')
    send.add_argument('--args-file')
    send.add_argument('--id', help='Retry ID; identical requests execute once during this session')
    js = sub.add_parser('json', help='One JSON request from stdin')
    watch = sub.add_parser('watch')
    watch.add_argument('--since', type=int, default=0)
    watch.add_argument('--session')
    watch.add_argument('--once', action='store_true')
    pump = sub.add_parser('pump').add_subparsers(dest='operation', required=True)
    for name in ('stop', 'refill', 'empty', 'reference', 'volume', 'flow', 'level', 'valve', 'map'):
        operation = pump.add_parser(name)
        operation.add_argument('--pump', type=int, required=True)
        if name in ('refill', 'empty', 'volume', 'flow', 'level'):
            operation.add_argument('--rate', type=float, required=True, help='µL/min')
        if name in ('volume', 'level', 'valve'):
            operation.add_argument('--value', type=float, required=True, help='mL; valve position is 1 or 2')
        if name == 'map':
            operation.add_argument('--energized', type=int, required=True)
            operation.add_argument('--deenergized', type=int, required=True)
    for bus in ('pumps', 'valves'):
        bus_sub = sub.add_parser(bus).add_subparsers(dest='operation', required=True)
        connect = bus_sub.add_parser('connect')
        connect.add_argument('--hardware', action='store_true', help='Explicit real-hardware connection')
        connect.add_argument('--simulate', action='store_true', help=argparse.SUPPRESS)
        if bus == 'pumps':
            connect.add_argument('--count', type=int, default=2)
            connect.add_argument('--configuration', default='')
            connect.add_argument('--sdk', default='')
        else:
            connect.add_argument('--port', default='')
            connect.add_argument('--baud', type=int, default=9600)
            bus_sub.add_parser('scan')
        bus_sub.add_parser('disconnect')
    valve = sub.add_parser('valve').add_subparsers(dest='operation', required=True)
    for name in ('query', 'move'):
        vp = valve.add_parser(name)
        vp.add_argument('--address', type=int, required=True, help='Physical switch number 0–14')
        if name == 'move':
            vp.add_argument('--position', choices=['input', 'output', 'bypass', 'extra'], required=True)
    helper = sub.add_parser('helper').add_subparsers(dest='operation', required=True)
    helper.add_parser('stop')
    for kind in ('stir', 'inject'):
        hp = helper.add_parser(kind)
        hp.add_argument('--pump', type=int, required=True)
        hp.add_argument('--rate', type=float)
        hp.add_argument('--volume', type=float, default=0)
        hp.add_argument('--deadline', type=float, default=120)
        if kind == 'stir':
            hp.add_argument('--refill', choices=['Full syringe', 'Defined volume', 'Volume before emptying'], default='Full syringe')
        else:
            hp.add_argument('--address', type=int, required=True)
            hp.add_argument('--before', choices=['input', 'output', 'bypass', 'extra'], required=True)
            hp.add_argument('--after', choices=['input', 'output', 'bypass', 'extra'], required=True)
    return p


def envelope(namespace):
    args = vars(namespace).copy()
    action = args.pop('action')
    if action == 'send':
        command = args['command']
        if args.get('args_file'):
            from pathlib import Path
            values = json.loads(Path(args['args_file']).read_text(encoding='utf-8-sig'))
        else:
            values = json.loads(args['args'])
        return command, values, args.get('id')
    if action in ('status', 'help', 'shutdown'):
        return action, {}, None
    operation = args.pop('operation')
    if action == 'pump':
        if operation in ('stop', 'map'):
            return 'pump.'+operation, args, None
        return 'pump.command', dict(mode=operation, **args), None
    if action in ('pumps', 'valves'):
        if operation == 'connect':
            hardware, simulate = args.pop('hardware'), args.pop('simulate')
            if hardware and simulate:
                raise ValueError('Choose hardware or test simulation')
            args['simulation'] = simulate
            # Empty CLI path options use the owner's saved/default paths.
            for field in ('configuration', 'sdk', 'port'):
                if not args.get(field):
                    args.pop(field, None)
        return action+'.'+operation, args, None
    if action == 'helper':
        return ('helper.stop', {}, None) if operation == 'stop' else ('helper.start', dict(kind=operation, **args), None)
    return 'valve.'+operation, args, None


def output(value):
    with OUTPUT_LOCK:
        print(json.dumps(value, ensure_ascii=True, allow_nan=False), flush=True)


def watch(since=0, session=None, once=False, stop=None, emit=output):
    while True:
        finishing = stop is not None and stop.is_set()
        result = request('events', dict(since=since, session=session, wait=0 if once or finishing else 1))
        if not result['ok']:
            raise RuntimeError(result.get('error'))
        page = result['result']
        if page['gap']:
            emit({'event': 'events.gap', 'session': page['session'], 'cursor': page['cursor']})
        for event in page['events']:
            emit(event)
        since, session = page['cursor'], page['session']
        if once or finishing:
            return


def stdin_loop(stop):
    normal = ThreadPoolExecutor(max_workers=4, thread_name_prefix='Console command')
    priority = ThreadPoolExecutor(max_workers=2, thread_name_prefix='Console stop')
    def send(command, args, rid):
        try:
            output({'event': 'console.response', **request(command, args, rid)})
        except Exception as exc:
            output({'event': 'console.error', 'error': str(exc), 'id': rid})
    for line in sys.stdin:
        if stop.is_set():
            break
        line = line.strip()
        if not line:
            continue
        try:
            if line.startswith('{'):
                data = json.loads(line)
                command, args, rid = data['command'], data.get('args', {}), data.get('id')
            else:
                tokens = shlex.split(line, posix=False)
                tokens = [t[1:-1] if len(t) >= 2 and t[0] == t[-1] and t[0] in ('"', "'") else t for t in tokens]
                ns = parser().parse_args(tokens)
                if ns.action in ('run', 'watch', 'json'):
                    raise ValueError('Use commands, status, or JSON requests in the attached console')
                command, args, rid = envelope(ns)
            (priority if command in ('pump.stop', 'helper.stop', 'shutdown') else normal).submit(send, command, args, rid)
        except (Exception, SystemExit) as exc:
            output({'event': 'console.error', 'error': str(exc)})
    normal.shutdown(wait=True)
    priority.shutdown(wait=True)
    if not stop.is_set():
        try:
            request('shutdown')
        except Exception as exc:
            output({'event': 'console.error', 'error': str(exc)})


def main(argv=None):
    # UTF-8-independent ASCII JSON also works under Windows/SSH legacy codepages.
    args = parser().parse_args(argv)
    try:
        if args.action == 'run':
            if args.gui and args.headless:
                raise ValueError('Choose --gui or --headless')
            from .runtime import run
            return run(gui=args.gui, attached=True, stdin=not args.no_stdin)
        if args.action == 'watch':
            watch(args.since, args.session, args.once)
            return 0
        if args.action == 'json':
            data = json.loads(sys.stdin.read(65537))
            response = request(data['command'], data.get('args', {}), data.get('id'))
        else:
            command, values, rid = envelope(args)
            response = request(command, values, rid)
        output(response)
        return 0 if response['ok'] else 1
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        output(dict(ok=False, error=str(exc), code=type(exc).__name__))
        return 1
