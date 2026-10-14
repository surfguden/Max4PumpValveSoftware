import json
import os
from pathlib import Path
import subprocess
import sys
import time
import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_headless_process_clients_and_events(tmp_path, monkeypatch):
    directory = tmp_path/'SSH user data with spaces'
    monkeypatch.setenv('PUMPVALVE_DATA_DIR', str(directory))
    process = subprocess.Popen([sys.executable, 'console_main.py', 'run', '--headless', '--no-stdin'],
                               cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    def cli(*args):
        result = subprocess.run([sys.executable, 'console_main.py', *args], cwd=ROOT,
                                capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, (result.stdout, result.stderr)
        return json.loads(result.stdout)
    try:
        deadline = time.monotonic()+5
        while not (directory/'endpoint.json').exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert cli('status')['result']['pump_count'] == 0
        cli('pumps', 'connect', '--simulate')
        cli('pump', 'map', '--pump', '1', '--energized', '2', '--deenergized', '1')
        cli('helper', 'stir', '--pump', '1')
        deadline = time.monotonic()+3
        while cli('status')['result']['helpers']['stir']['running'] and time.monotonic() < deadline:
            time.sleep(0.05)
        assert cli('status')['result']['helpers']['stir']['step'] == 'Completed'
        watch = subprocess.run([sys.executable, 'console_main.py', 'watch', '--once'], cwd=ROOT,
                               capture_output=True, text=True, timeout=10)
        records = [json.loads(line) for line in watch.stdout.splitlines()]
        assert any(r['event'] == 'command.received' for r in records)
        assert any(r['event'] == 'helper.state' and r['state']['step'] == 'Completed' for r in records)
        cli('shutdown')
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stderr
        assert any(json.loads(line).get('event') == 'command.received' for line in stdout.splitlines())
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
