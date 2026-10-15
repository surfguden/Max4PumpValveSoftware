import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PySide6.QtWidgets import QApplication
from pumpvalve.application import Application
from pumpvalve.commands import Commands
from pumpvalve.ui import MainWindow, SyringeDialog, MotionRateDialog
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog


def test_ui_disconnected_responsive(tmp_path, monkeypatch):
    monkeypatch.setenv('PUMPVALVE_DATA_DIR', str(tmp_path/'user data with spaces'))
    qt = QApplication.instance() or QApplication([])
    app = Application()
    commands = Commands(app)
    window = MainWindow(commands)
    try:
        assert window.tabs.count() == 3
        assert app.pumps is None and app.valves is None
        window.config.setText(str(tmp_path/'Qmix configuration with spaces'))
        window.persist()
        app.connect_pumps(True, 4)
        window.set_cards(4)
        app.connect_valves(True)
        window.show_valves(list(app.units.values()))
        assert window.inject_valve.count() == 2
        window.inject_valve.setCurrentIndex(1)
        assert window.before.count() == 3
        app.map_valve(0, 2, 1)
        app.start('stir', 0, rate=100)
        window.poll()
        assert 'RUNNING' in window.helper_status.text()
        assert not window.stir_start.isEnabled()
        qt.processEvents()
        dialog = SyringeDialog({})
        dialog.method.setCurrentIndex(1)
        dialog.apply()
        assert dialog.profile is not None
        app.stop_helper()
        app.helper.join(2)
    finally:
        window.close()
        commands.close()
        qt.processEvents()
    from pumpvalve.storage import load
    assert 'spaces' in load()['config']


def test_bd5_plastic_preset_can_apply_and_reload():
    qt = QApplication.instance() or QApplication([])
    dialog = SyringeDialog({})
    try:
        dialog.profiles.setCurrentText('BD 5ml')
        dialog.apply()
        profile = dialog.profile
        assert profile is not None
        assert profile.material == 'Plastic'
        assert profile.inner_diameter_mm == 12.07
        assert profile.maximum_ml == 5
        assert abs(profile.capacity - 5) < 0.001
        from dataclasses import asdict
        restored = SyringeDialog({profile.name: asdict(profile)})
        try:
            restored.profiles.setCurrentText(profile.name)
            restored.apply()
            assert restored.profile == profile
        finally:
            restored.close()
    finally:
        dialog.close()
        qt.processEvents()


def test_refill_empty_dialog_max_separate_rates_and_persistence(tmp_path, monkeypatch):
    monkeypatch.setenv('PUMPVALVE_DATA_DIR', str(tmp_path))
    qt = QApplication.instance() or QApplication([])
    commands = Commands()
    window = MainWindow(commands)
    window.timer.stop()
    try:
        card = window.cards[0]
        assert card.motion_buttons['refill'].contextMenuPolicy() == Qt.CustomContextMenu
        assert card.motion_buttons['empty'].contextMenuPolicy() == Qt.CustomContextMenu
        dialog = MotionRateDialog('refill', 250, maximum=4321)
        dialog.max_button.click()
        assert dialog.selected_rate() == 'max'
        assert not dialog.rate.isEnabled()
        dialog.max_button.click()
        dialog.rate.setValue(345)
        assert dialog.selected_rate() == 345
        dialog.close()

        def accept_max(self):
            self.max_button.click()
            return QDialog.Accepted
        monkeypatch.setattr(MotionRateDialog, 'exec', accept_max)
        card.motion_buttons['refill'].customContextMenuRequested.emit(card.rect().center())
        assert card.motion_rates == dict(refill='max', empty=1000)
        def cancel_max(self):
            self.max_button.click()
            return QDialog.Rejected
        monkeypatch.setattr(MotionRateDialog, 'exec', cancel_max)
        card.edit_motion_rate('empty')
        assert card.motion_rates['empty'] == 1000
        card.motion_rates['empty'] = 321
        sent = []
        monkeypatch.setattr(window, 'issue', lambda *args, **kwargs: sent.append(args))
        card.flow.setValue(-999)
        card.command('refill')
        card.command('empty')
        assert [args[1]['rate'] for args in sent] == ['max', 321]
        assert commands.app.pumps is None
        window.persist()
        card.motion_rates.update(refill=1, empty=2)
        window.load_settings()
        assert card.motion_rates == dict(refill='max', empty=321)
    finally:
        window.close()
        commands.close()
        qt.processEvents()
