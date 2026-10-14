import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PySide6.QtWidgets import QApplication
from pumpvalve.application import Application
from pumpvalve.ui import MainWindow, SyringeDialog


def test_ui_disconnected_responsive(tmp_path, monkeypatch):
    monkeypatch.setenv('PUMPVALVE_DATA_DIR', str(tmp_path/'user data with spaces'))
    qt = QApplication.instance() or QApplication([])
    app = Application()
    window = MainWindow(app)
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
        app.shutdown()
        qt.processEvents()
    from pumpvalve.storage import load
    assert 'spaces' in load()['config']
