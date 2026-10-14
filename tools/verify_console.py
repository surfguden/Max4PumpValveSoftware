"""Exercise both packaged executables, attached stdin and separate clients. Simulation only."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time


def verify(distribution, report_path):
    distribution = Path(distribution).resolve()
    report_path = Path(report_path).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = dict(passed=False, hardware_access=False, checks=[])
    exe = distribution / 'Max4PumpValveCtl.exe'
    def check(name, condition=True):
        if not condition:
            raise AssertionError(name)
        report['checks'].append(name)
    try:
        for gui, simulation in ((False, True), (True, True), (False, False)):
            tag = ('attached-gui' if gui else 'headless') if simulation else 'hardware-defaults'
            folder = report_path.parent / (tag+' user data with spaces')
            env = dict(os.environ, PUMPVALVE_DATA_DIR=str(folder), QT_QPA_PLATFORM='offscreen')
            if not simulation:
                env.pop('PUMPVALVE_TEST_SIMULATION', None)
            argv = [str(exe), 'run', '--gui' if gui else '--headless']
            if not gui:
                argv.append('--no-stdin')
            process = subprocess.Popen(argv, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, encoding='utf-8', creationflags=subprocess.CREATE_NO_WINDOW)
            output, errors = [], []
            readers = []
            for stream, target in [(process.stdout, output), (process.stderr, errors)]:
                thread = threading.Thread(target=lambda s=stream, t=target: t.extend(iter(s.readline, '')), daemon=True)
                thread.start()
                readers.append(thread)
            def cli(*args, ok=True):
                result = subprocess.run([str(exe), *args], env=env, capture_output=True, text=True,
                                        encoding='utf-8', timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
                if ok and result.returncode != 0:
                    raise AssertionError((args, result.stdout, result.stderr))
                if not ok and result.returncode == 0:
                    raise AssertionError('Invalid command succeeded')
                return json.loads(result.stdout)
            def until(predicate, timeout=8):
                deadline = time.monotonic()+timeout
                while time.monotonic() < deadline:
                    if predicate():
                        return
                    if process.poll() is not None:
                        raise AssertionError('Owner exited: '+''.join(errors))
                    time.sleep(0.03)
                raise TimeoutError(tag+' condition timed out')
            try:
                def ready():
                    try:
                        return json.loads((folder/'endpoint.json').read_text())['pid'] == process.pid
                    except (OSError, ValueError):
                        return False
                until(ready)
                check(tag+': disconnected startup', cli('status')['result']['pump_count'] == 0)
                if not simulation:
                    for device in ('pumps', 'valves'):
                        response = cli(device, 'connect', '--simulate', ok=False)
                        check(tag+': '+device+' simulation rejected', 'simulation' in json.dumps(response).lower())
                    check(tag+': no simulated pumps connected', cli('status')['result']['pump_count'] == 0)
                    cli('shutdown')
                    process.wait(12)
                    check(tag+': clean owner exit', process.returncode == 0)
                    continue
                if gui:
                    process.stdin.write('{"id":"stdin-connect","command":"pumps.connect","args":{"simulation":true}}\n')
                    process.stdin.flush()
                    until(lambda: cli('status')['result']['pump_count'] == 2)
                    check(tag+': same-terminal stdin command')
                else:
                    cli('pumps', 'connect', '--simulate')
                cli('valves', 'connect', '--simulate')
                cli('pump', 'map', '--pump', '1', '--energized', '2', '--deenergized', '1')
                cli('helper', 'stir', '--pump', '1', '--rate', '100')
                cli('pump', 'empty', '--pump', '1', '--rate', '100', ok=False)
                cli('helper', 'stop')
                until(lambda: not cli('status')['result']['helpers']['stir']['running'])
                check(tag+': mixed-client cancellation', cli('status')['result']['helpers']['stir']['step'] == 'Cancelled')
                cli('helper', 'stir', '--pump', '1')
                until(lambda: not cli('status')['result']['helpers']['stir']['running'])
                check(tag+': stirring completes', cli('status')['result']['helpers']['stir']['step'] == 'Completed')
                cli('helper', 'inject', '--pump', '1', '--address', '2', '--volume', '0.1', '--rate', '5000', '--before', 'input', '--after', 'extra')
                until(lambda: not cli('status')['result']['helpers']['inject']['running'])
                status = cli('status')['result']
                check(tag+': injection completes', status['helpers']['inject']['step'] == 'Completed')
                check(tag+': delivery readback', abs(status['pumps'][0]['fill']-0.9) < 0.001)
                watch = subprocess.run([str(exe), 'watch', '--once'], env=env, capture_output=True,
                                       text=True, encoding='utf-8', timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
                events = [json.loads(line) for line in watch.stdout.splitlines()]
                check(tag+': event replay', any(e['event'] == 'helper.state' for e in events))
                cli('shutdown')
                process.wait(12)
                for thread in readers:
                    thread.join(2)
                check(tag+': console event echo', any(json.loads(line).get('event') == 'command.received' for line in output))
                check(tag+': clean owner exit', process.returncode == 0)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(5)
                for thread in readers:
                    thread.join(2)
                if process.returncode != 0:
                    report.setdefault('diagnostics', {})[tag] = {'stderr': ''.join(errors), 'stdout_tail': output[-5:]}
        report['passed'] = True
    except Exception as exc:
        report['error'] = str(exc)
    report_path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    if not report['passed']:
        raise RuntimeError(report.get('error'))
    print(json.dumps(report))


if __name__ == '__main__':
    verify(sys.argv[1], sys.argv[2])
