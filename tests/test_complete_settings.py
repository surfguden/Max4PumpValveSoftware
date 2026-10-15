import json
import pytest
from PySide6.QtWidgets import QApplication
from pumpvalve.application import Application
from pumpvalve.commands import Commands
from pumpvalve.storage import load, save, defaults
from pumpvalve.ui import MainWindow


def test_shipped_luer_lok_profiles_import_without_hardware(tmp_path, monkeypatch):
    from pathlib import Path
    from pumpvalve.ui import SyringeDialog
    monkeypatch.setenv('PUMPVALVE_DATA_DIR', str(tmp_path))
    qt = QApplication.instance() or QApplication([])
    commands = Commands()
    path = Path(__file__).resolve().parents[1] / 'settings' / 'BD-Plastipak-Luer-Lok.json'
    try:
        response = commands.execute('settings.load', dict(path=str(path)))
        assert response['ok'], response
        profiles = commands.settings['syringes']
        expected = {1: 4.70, 3: 8.57, 5: 11.98, 10: 14.43}
        assert len(profiles) == len(expected)
        for volume, diameter in expected.items():
            name = f'BD Plastipak {volume} mL Luer-Lok'
            dialog = SyringeDialog(profiles)
            try:
                dialog.profiles.setCurrentText(name)
                dialog.apply()
                assert dialog.profile is not None
                assert dialog.profile.name == name
                assert dialog.profile.inner_diameter_mm == diameter
                assert dialog.profile.maximum_ml == volume
            finally:
                dialog.close()
        assert commands.app.pumps is None and commands.app.valves is None
    finally:
        commands.close()
        qt.processEvents()


def test_hardware_defaults_and_simulation_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv('PUMPVALVE_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path/'local'))
    monkeypatch.delenv('PUMPVALVE_TEST_SIMULATION', raising=False)
    settings = load()
    assert settings['config'].endswith(r'Configurations\dual')
    assert settings['sdk'] == str(tmp_path/'local'/'CETONI_SDK')
    assert settings['port'] == 'COM3' and settings['baud'] == 9600
    app = Application()
    try:
        with pytest.raises(ValueError, match='disabled'):
            app.connect_pumps(True)
        with pytest.raises(ValueError, match='disabled'):
            app.connect_valves(True)
        assert app.pumps is None and app.valves is None
    finally:
        app.shutdown()


def test_connection_commands_default_to_saved_hardware_paths(tmp_path, monkeypatch):
    monkeypatch.setenv('PUMPVALVE_DATA_DIR', str(tmp_path))
    monkeypatch.delenv('PUMPVALVE_TEST_SIMULATION', raising=False)
    commands = Commands()
    calls = []
    monkeypatch.setattr(commands.app, 'connect_pumps', lambda simulation, **args: calls.append((simulation, args)) or 2)
    monkeypatch.setattr(commands.app, 'connect_valves', lambda simulation, **args: calls.append((simulation, args)) or [])
    try:
        assert commands.execute('pumps.connect')['ok']
        assert commands.execute('valves.connect')['ok']
        assert calls == [(False, dict(configuration=defaults()['config'], sdk=defaults()['sdk'])),
                         (False, dict(port='COM3', baud=9600))]
    finally:
        commands.close()


def test_save_load_every_control_without_device_actions(tmp_path, monkeypatch):
    monkeypatch.setenv('PUMPVALVE_DATA_DIR', str(tmp_path/'all settings'))
    qt = QApplication.instance() or QApplication([])
    commands = Commands()
    window = MainWindow(commands)
    window.timer.stop()
    try:
        commands.app.connect_pumps(True, 2)
        commands.app.connect_valves(True)
        window.set_cards(2)
        window.show_valves(list(commands.app.units.values()))
        window.config.setText('C:/configuration')
        window.sdk.setText('C:/runtime')
        window.port.setCurrentText('COM42')
        window.baud.setCurrentText('38400')
        window.cards[0].mode.setCurrentText('Flow')
        window.cards[0].volume.setValue(-0.3)
        window.cards[0].flow.setValue(1234)
        window.cards[0].level.setValue(0.75)
        window.cards[0].energized.setCurrentIndex(2)
        window.cards[0].deenergized.setCurrentIndex(1)
        window.speed.setCurrentText('Defined flow rate')
        window.stir_rate.setValue(4321)
        window.refill.setCurrentText('Defined volume')
        window.refill_volume.setValue(0.4)
        window.stir_pump.setCurrentIndex(1)
        window.inject_pump.setCurrentIndex(1)
        window.inject_valve.setCurrentIndex(window.inject_valve.findData(2))
        window.before.setCurrentIndex(window.before.findData('input'))
        window.after.setCurrentIndex(window.after.findData('extra'))
        window.inject_volume.setValue(0.2)
        window.inject_rate.setValue(2345)
        window.deadline.setValue(90)
        window.names[2] = 'Loop valve'
        window.tabs.setCurrentIndex(2)
        window.persist()
        saved = load()
        assert saved['ui']['helpers']['after'] == 'extra'
        assert saved['ui']['pumps']['1']['flow'] == 1234
        window.cards[0].flow.setValue(999)
        window.inject_rate.setValue(888)
        window.sdk.setText('changed')
        moves = list(commands.app.valves.moves)
        fill = commands.app.pumps.read_fill_level(0)
        window.load_settings()
        assert window.cards[0].flow.value() == 1234
        assert window.sdk.text() == 'C:/runtime'
        assert window.inject_rate.value() == 2345
        assert window.after.currentData() == 'extra'
        assert window.tabs.currentIndex() == 2
        assert commands.app.mapping == {}
        assert commands.app.valves.moves == moves
        assert commands.app.pumps.read_fill_level(0) == fill
        # Disconnection hides device selectors but must retain their settings.
        commands.app.disconnect_pumps()
        commands.app.disconnect_valves()
        window.set_cards(0)
        window.show_valves(list(commands.app.units.values()))
        window.persist()
        again = load()
        assert again['ui']['helpers']['inject_valve'] == 2
        assert again['ui']['helpers']['after'] == 'extra'
        assert again['ui']['pumps']['1']['flow'] == 1234
    finally:
        window.close()
        commands.close()
        qt.processEvents()
    # A new window restores file settings while staying disconnected.
    commands = Commands()
    window = MainWindow(commands)
    try:
        assert window.sdk.text() == 'C:/runtime'
        assert window.cards[0].flow.value() == 1234
        assert window.inject_rate.value() == 2345
        assert commands.app.pumps is None and commands.app.valves is None
    finally:
        window.close()
        commands.close()
        qt.processEvents()


def test_invalid_settings_load_keeps_current_state(tmp_path, monkeypatch):
    monkeypatch.setenv('PUMPVALVE_DATA_DIR', str(tmp_path))
    commands = Commands()
    try:
        original = dict(commands.settings)
        (tmp_path/'settings.json').write_text(json.dumps({'ui': {'pumps': {'1': {'flow': float('nan')}}}}))
        response = commands.execute('settings.load')
        assert not response['ok']
        assert commands.settings == original
        assert commands.app.pumps is None and commands.app.valves is None
    finally:
        commands.close()
