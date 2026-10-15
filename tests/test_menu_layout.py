import json
from pathlib import Path
import pytest
from PySide6.QtCore import QPoint
from PySide6.QtGui import QFontDatabase, QFont
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QPushButton, QScrollArea, QLineEdit, QComboBox, QDoubleSpinBox
from pumpvalve.commands import Commands
from pumpvalve.ui import MainWindow


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setenv('PUMPVALVE_DATA_DIR', str(tmp_path/'user-data'))
    qt = QApplication.instance() or QApplication([])
    font = Path(r'C:\Windows\Fonts\segoeui.ttf')
    if font.exists():
        QFontDatabase.addApplicationFont(str(font))
    commands = Commands()
    ui = MainWindow(commands)
    ui.show()
    ui.timer.stop()
    qt.processEvents()
    ui.poll()
    yield ui, qt
    ui.close()
    commands.close()
    qt.processEvents()


def test_file_menu_save_load_feedback_and_no_device_actions(window, tmp_path, monkeypatch):
    ui, qt = window
    path = tmp_path/'my-settings.json'
    feedback, errors = [], []
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args: (str(path), ''))
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args: (str(path), ''))
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: feedback.append(args[2]))
    monkeypatch.setattr(QMessageBox, 'critical', lambda *args: errors.append(args[2]))
    ui.wait_empty.setValue(2.5)
    ui.wait_full.setValue(3.5)
    ui.valve_begin.setCurrentText('Not energized')
    ui.valve_end.setCurrentText('Energized')
    ui.save_settings_action.trigger()
    data = json.loads(path.read_text())
    assert data['ui']['helpers']['wait_full'] == 3.5
    assert data['ui']['helpers']['valve_begin'] == 'Not energized'
    assert str(path) in feedback[-1]
    ui.wait_full.setValue(0)
    ui.valve_end.setCurrentText('Not energized')
    ui.load_settings_action.trigger()
    assert ui.wait_full.value() == 3.5
    assert ui.valve_end.currentText() == 'Energized'
    assert str(path) in ui.statusBar().currentMessage()
    assert len(feedback) == 2 and not errors
    assert ui.app.pumps is None and ui.app.valves is None
    path.write_text('{broken')
    ui.load_settings_action.trigger()
    assert errors and ui.wait_full.value() == 3.5
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args: ('', ''))
    ui.save_settings_action.trigger()
    assert len(feedback) == 2


@pytest.mark.parametrize('size', [(1280, 680), (1024, 640)])
def test_laptop_layout_without_vertical_scroll_or_hidden_buttons(window, size):
    ui, qt = window
    ui.resize(*size)
    assert not ui.findChildren(QScrollArea)
    for tab in range(ui.tabs.count()):
        ui.tabs.setCurrentIndex(tab)
        qt.processEvents()
        page = ui.tabs.currentWidget()
        for cls in (QPushButton, QLineEdit, QComboBox, QDoubleSpinBox):
            for control in page.findChildren(cls):
                if control.isVisible():
                    top = control.mapTo(ui, QPoint(0, 0))
                    bottom = control.mapTo(ui, control.rect().bottomRight())
                    assert ui.rect().contains(top) and ui.rect().contains(bottom), control.objectName() or control.__class__.__name__
        for button in page.findChildren(QPushButton):
            if button.isVisible() and not button.isEnabled():
                assert button.toolTip(), button.text()
    ui.tabs.setCurrentIndex(0)
    assert not ui.sdk.isVisible() and not ui.config.isVisible() and not ui.count.isVisible()
    assert ui.pump_connect.isEnabled()
    assert 'No pumps' in ui.pump_disconnect.toolTip()
