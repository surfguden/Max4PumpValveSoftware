"""Explicit authorized homing after power loss; application Qmix must be disconnected."""
import json
import math
import os
from pathlib import Path
import sys
import time
import subprocess

ROOT = Path(__file__).resolve().parents[2]
if '--syringe-disconnected' not in sys.argv:
    raise RuntimeError('Disconnect the syringe first, then explicitly pass --syringe-disconnected')
os.environ['PUMPVALVE_DATA_DIR'] = str(Path(__file__).resolve().parent/'session')
cli = ROOT/'dist/hardware-accepted-20261015/Max4PumpValve-1.1.0/Max4PumpValveCtl.exe'
status = json.loads(subprocess.check_output([str(cli),'status'],text=True))
state = status['result']
if state['pump_connected'] or state['reservations'] or state['buses']['qmix_stalled']:
    raise RuntimeError('Close the application Qmix connection and active operations before referencing')
sys.path.insert(0,str(ROOT/'vendor'))
os.environ['QMIXSDK']=str(Path(os.environ['LOCALAPPDATA'])/'CETONI_SDK')
from qmixsdk import qmixbus, qmixpump

bus=qmixbus.Bus()
bus.open(r'C:\Users\Public\Documents\QmixElements\Projects\script test\Configurations\single',0)
pump=None
try:
    pump=qmixpump.Pump()
    pump.lookup_by_device_index(0)
    bus.start()
    print(json.dumps(dict(stage='before clear',fault=pump.is_in_fault_state(),error=str(pump.read_last_error()))),flush=True)
    pump.clear_fault()
    clear_deadline=time.monotonic()+1
    while pump.is_in_fault_state() and time.monotonic()<clear_deadline:
        time.sleep(.05)
    if pump.is_in_fault_state():
        raise RuntimeError('Pump fault present; reference refused')
    pump.set_syringe_param(12.07,5000/(math.pi*12.07**2/4))
    pump.enable(True)
    pump.calibrate()
    expires=time.monotonic()+60
    while True:
        state=dict(fault=pump.is_in_fault_state(), finished=pump.is_calibration_finished(),
                   initialized=pump.is_position_sensing_initialized())
        print(json.dumps(state),flush=True)
        if state['fault']:
            raise RuntimeError('Fault during reference')
        if state['finished']:
            if not state['initialized']:
                raise RuntimeError('Reference finished without position initialization')
            break
        if time.monotonic()>expires:
            raise TimeoutError('Reference deadline exceeded')
        time.sleep(.25)
finally:
    try:
        if pump is not None:
            pump.stop_pumping()
    finally:
        bus.stop()
        bus.close()
