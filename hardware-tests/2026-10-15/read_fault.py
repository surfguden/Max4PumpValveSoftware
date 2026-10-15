"""Read-only SDK diagnostic. Run only with application Qmix disconnected."""
import json
import os
from pathlib import Path
import sys
import time
import ctypes
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'vendor'))
os.environ['QMIXSDK'] = str(Path(os.environ['LOCALAPPDATA']) / 'CETONI_SDK')
from qmixsdk import qmixbus, qmixpump
bus = qmixbus.Bus()
bus.open(r'C:\Users\Public\Documents\QmixElements\Projects\script test\Configurations\single', 0)
try:
    pump = qmixpump.Pump()
    pump.lookup_by_device_index(0)
    bus.start()
    error = pump.read_last_error()
    print(json.dumps(dict(fault=pump.is_in_fault_state(), error_code=error.code, error_message=error.message, position_initialized=pump.is_position_sensing_initialized(), enabled=pump.is_enabled())))
    qmixpump.pump_api.LCP_GetPumpDriveHandle.restype = ctypes.c_longlong
    drive_handle = qmixpump.pump_api.LCP_GetPumpDriveHandle(pump.handle)
    drive = qmixbus.Device(ctypes.c_longlong(drive_handle))
    drive_error = drive.read_last_error()
    print(json.dumps(dict(drive_name=drive.get_device_name(), error_code=drive_error.code, error_message=drive_error.message)), flush=True)
    for i in range(100):
        event = bus.read_event()
        if not event.is_valid():
            break
        print(json.dumps(dict(event=event.event_id, data=event.data, message=event.string)), flush=True)
    if '--recover' in sys.argv:
        pump.stop_pumping()
        pump.clear_fault()
        for i in range(20):
            print(json.dumps(dict(stage='after clear', seconds=i*.25, fault=pump.is_in_fault_state(), pumping=pump.is_pumping(), enabled=pump.is_enabled())), flush=True)
            if not pump.is_in_fault_state():
                break
            time.sleep(.25)
        if not pump.is_in_fault_state() and pump.is_position_sensing_initialized():
            pump.enable(True)
            time.sleep(.5)
            pump.stop_pumping()
            time.sleep(.5)
            print(json.dumps(dict(stage='after enable and stop', fault=pump.is_in_fault_state(), pumping=pump.is_pumping(), enabled=pump.is_enabled())), flush=True)
finally:
    bus.stop()
    bus.close()
