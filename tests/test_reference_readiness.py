import time
from types import SimpleNamespace
import pytest
from pumpvalve.application import Application
from pumpvalve.drivers.cetoni import CetoniPump, CetoniPumpError


def test_unreferenced_connection_keeps_drive_disabled_without_homing():
    calls = []
    pump = SimpleNamespace(is_in_fault_state=lambda: False,
                           is_position_sensing_initialized=lambda: False,
                           is_enabled=lambda: False,
                           enable=lambda value: calls.append(('enable', value)),
                           calibrate=lambda: calls.append('calibrate'))
    driver = CetoniPump(pump=pump)
    driver.prepare_connected_drive()
    assert calls == []


def test_fault_still_blocks_reference_ready_connection():
    pump = SimpleNamespace(is_in_fault_state=lambda: True)
    with pytest.raises(CetoniPumpError, match='fault state'):
        CetoniPump(pump=pump).prepare_connected_drive()


@pytest.mark.parametrize('finish_initialized',[True,False])
def test_reference_available_before_position_and_syringe_setup(monkeypatch, finish_initialized):
    app = Application()
    app.connect_pumps(True,count=1)
    position = {'initialized':False}
    monkeypatch.setattr(app.pumps,'read_position_initialized',lambda _:position['initialized'])
    monkeypatch.setattr(app.pumps,'start_reference_move',lambda _:position.update(initialized=finish_initialized))
    monkeypatch.setattr(app.pumps,'reference_move_finished',lambda _:True)
    try:
        with pytest.raises(RuntimeError,match='Reference required'):
            app.command_pump(0,'level',value=.5,rate=1000)
        if finish_initialized:
            app.command_pump(0,'reference',syringe_disconnected=True)
            assert app.read_pumps()[0]['position_initialized']
            app.command_pump(0,'level',value=.5,rate=1000)
        else:
            with pytest.raises(RuntimeError,match='still uninitialized'):
                app.command_pump(0,'reference',syringe_disconnected=True)
        assert not app.reserved
    finally:
        app.shutdown()


def test_unreferenced_gui_enables_reference_but_blocks_dosing(tmp_path,monkeypatch):
    from PySide6.QtWidgets import QApplication
    from pumpvalve.commands import Commands
    from pumpvalve.ui import MainWindow
    monkeypatch.setenv('PUMPVALVE_DATA_DIR',str(tmp_path))
    qt=QApplication.instance() or QApplication([])
    commands=Commands()
    ui=MainWindow(commands)
    try:
        commands.execute('pumps.connect',{'simulation':True,'count':1})
        monkeypatch.setattr(commands.app.pumps,'read_position_initialized',lambda _:False)
        expires=time.monotonic()+2
        while time.monotonic()<expires:
            qt.processEvents()
            ui.poll()
            if ui.cards and ui.cards[0].syringe.state.get('position_initialized') is False:
                break
            time.sleep(.01)
        card=ui.cards[0]
        assert 'REFERENCE REQUIRED' in card.status.text()
        assert card.reference_button.isEnabled()
        assert card.configure_button.isEnabled()
        assert card.stop_button.isEnabled()
        assert not card.motion_buttons['empty'].isEnabled()
        assert not card.device_buttons[0].isEnabled()
        assert not ui.stir_start.isEnabled()
    finally:
        ui.close()
        commands.close()
        qt.processEvents()


def test_reference_requires_explicit_syringe_confirmation(monkeypatch):
    app=Application()
    calls=[]
    monkeypatch.setattr(app,'_reference',lambda index:calls.append(index))
    try:
        for confirmation in (False,None,1,'yes'):
            with pytest.raises(ValueError,match='Disconnect the syringe'):
                app.command_pump(0,'reference',syringe_disconnected=confirmation)
        assert calls==[]
        app.command_pump(0,'reference',syringe_disconnected=True)
        assert calls==[0]
    finally:
        app.shutdown()


@pytest.mark.parametrize('accept',[False,True])
def test_reference_popup_precedes_command_and_defaults_to_cancel(tmp_path,monkeypatch,accept):
    from PySide6.QtWidgets import QApplication,QMessageBox
    from pumpvalve.commands import Commands
    from pumpvalve.ui import MainWindow
    monkeypatch.setenv('PUMPVALVE_DATA_DIR',str(tmp_path))
    qt=QApplication.instance() or QApplication([])
    commands=Commands()
    ui=MainWindow(commands)
    calls=[]
    warnings=[]
    def warn(parent,title,text,buttons,default):
        assert not calls
        warnings.append((text,default))
        return QMessageBox.Yes if accept else QMessageBox.Cancel
    monkeypatch.setattr(QMessageBox,'warning',warn)
    monkeypatch.setattr(ui,'issue',lambda command,args:calls.append((command,args)))
    try:
        ui.cards[0].command('reference')
        assert len(warnings)==1 and 'Disconnect the syringe' in warnings[0][0]
        assert warnings[0][1]==QMessageBox.Cancel
        if accept:
            assert calls[0][0]=='pump.command' and calls[0][1]['syringe_disconnected'] is True
        else:
            assert calls==[]
    finally:
        ui.close()
        commands.close()
        qt.processEvents()
