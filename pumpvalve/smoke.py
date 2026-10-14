"""Opt-in packaged-executable acceptance check. Simulation only."""
import json
import os
import time
from pathlib import Path
from PySide6.QtCore import QTimer
from .storage import VERSION, load
from .drivers.cetoni import CetoniPump, CetoniPumpError


def smoke(qt, app, window, report_path):
    path = Path(report_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    report = {'version': VERSION, 'checks': [], 'hardware_access': False}
    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        report['checks'].append(name)
    stage = 0
    deadline = time.monotonic() + 30
    ticks = 0
    timer = QTimer(window)
    window._smoke_timer = timer

    def finish(error=None):
        timer.stop()
        report['passed'] = error is None
        report['error'] = None if error is None else str(error)
        report['ui_ticks'] = ticks
        path.write_text(json.dumps(report, indent=2), encoding='utf-8')
        window.close()
        qt.exit(0 if error is None else 1)

    def advance():
        nonlocal stage, ticks
        ticks += 1
        try:
            if time.monotonic() > deadline:
                raise TimeoutError(f'Smoke test stage {stage} timed out')
            if stage == 0:
                check('disconnected startup', app.pumps is None and app.valves is None)
                check('all three tabs', window.tabs.count() == 3)
                check('simulation defaults', window.pump_sim.isChecked() and window.valve_sim.isChecked())
                check('Qmix Python wrapper bundled', (CetoniPump().sdk_python_path / 'qmixsdk' / 'qmixpump.py').is_file())
                for i in range(3):
                    window.tabs.setCurrentIndex(i)
                    window.grab().save(str(path.parent / f'tab-{i+1}.png'))
                from .ui import SyringeDialog
                dialog = SyringeDialog({}, window)
                dialog.show()
                dialog.grab().save(str(path.parent / 'syringe-configuration.png'))
                dialog.apply()
                check('syringe configuration dialog', dialog.profile is not None)
                missing = path.parent / 'absent configuration with spaces'
                window.config.setText(str(missing))
                window.persist()
                check('settings persistence with spaces', load()['config'] == str(missing))
                window.task(lambda: app.connect_pumps(True, 2), window.set_cards)
                stage = 1
            elif stage == 1 and app.pumps and len(window.cards) == 2 and window.cards[0].isEnabled():
                window.task(lambda: app.connect_valves(True), window.show_valves)
                stage = 2
            elif stage == 2 and window.inject_valve.count() == 2:
                check('simulation pump and valve connections', app.pumps.unit_count == 2)
                window.task(lambda: app.map_valve(0, 2, 1))
                stage = 3
            elif stage == 3 and 0 in app.mapping:
                window.stir_rate.setValue(5000)
                window.speed.setCurrentIndex(1)
                window.start_stir()
                stage = 4
            elif stage == 4 and not app.snapshot()['running']:
                check('stirring completes', app.snapshot()['step'] == 'Completed')
                window.inject_rate.setValue(5000)
                window.inject_volume.setValue(0.1)
                window.after.setCurrentIndex(1)
                window.start_inject()
                stage = 5
            elif stage == 5 and not app.snapshot()['running']:
                state = app.snapshot()
                check('injection completes', state['step'] == 'Completed')
                check('injection progress readback', state['progress'] == 100 and abs(state['injected']-0.1) < 0.001)
                window.tabs.setCurrentIndex(2)
                window.poll()
                window.grab().save(str(path.parent / 'helpers-completed.png'))
                def diagnostics():
                    try:
                        app.connect_pumps(False, configuration=str(path.parent/'missing'))
                    except Exception:
                        pass  # Existing simulation connection must be disconnected first.
                    old = os.environ.get('QMIXSDK')
                    try:
                        os.environ['QMIXSDK'] = str(path.parent / 'missing SDK with spaces')
                        CetoniPump()._load_sdk()
                    except CetoniPumpError as exc:
                        if 'DLL' not in str(exc):
                            raise
                        report['sdk_error'] = str(exc)
                    finally:
                        if old is None:
                            os.environ.pop('QMIXSDK', None)
                        else:
                            os.environ['QMIXSDK'] = old
                    app.disconnect_pumps()
                    try:
                        app.connect_pumps(False, configuration=str(path.parent/'missing XML with spaces'))
                    except ValueError as exc:
                        report['configuration_error'] = str(exc)
                    else:
                        raise AssertionError('Missing configuration error')
                window.task(diagnostics, lambda _: report.update(diagnostics=True))
                stage = 6
            elif stage == 6 and report.get('diagnostics'):
                check('actionable missing SDK error', 'DLL' in report.get('sdk_error', ''))
                check('actionable missing configuration error', 'XML' in report.get('configuration_error', ''))
                check('responsive UI during sequences', ticks > 10)
                finish()
        except Exception as exc:
            finish(exc)
    timer.timeout.connect(advance)
    timer.start(50)
