"""Retest the fixed portable build through its terminal interface, never simulation."""
import json
import os
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent/'2026-10-15'))
import run_hardware as test
test.ROOT = HERE
test.EXE = HERE.parents[1]/'dist/hardware-accepted-20261015/Max4PumpValve-1.1.0/Max4PumpValveCtl.exe'
test.LOG = HERE/'cli-transcript.jsonl'
test.RESULTS = HERE/'results.jsonl'
os.environ['PUMPVALVE_DATA_DIR'] = str(HERE/'session')

def run():
    response = test.cli('pumps.connect', dict(count=1, simulation=False), required=False)
    test.record('real Qmix reconnect', passed=response['ok'], response=response)
    test.record('retained diagnostics available', passed=True,
                diagnostics=test.cli('pump.diagnose', dict(pump=1)))
    response = test.cli('valves.connect', dict(port='COM6', baud=9600, simulation=False), required=False)
    test.record('real serial reconnect', passed=response['ok'], response=response)
    state = test.status()
    test.check(not state['pump_simulation'] and not state['valve_simulation'], state)
    if state['pump_connected']:
        rejected = test.cli('pump.command', dict(pump=1, mode='level', value=.5, rate=1000), required=False)
        test.record('saved syringe blocks motion before reapply',
                    passed=not rejected['ok'] and 'not applied' in rejected.get('error',''), response=rejected)
        for stage in ('setup', 'normal', 'stops', 'cancellation', 'edges'):
            try:
                getattr(test, stage)()
            except Exception as exc:
                test.record(stage+' failed', passed=False, error=repr(exc))
                test.cli('helper.stop', required=False)
                stopped = test.cli('pump.stop',dict(pump=1),required=False)
                test.record('stop after failure', passed=stopped['ok'], response=stopped)
                diagnostic = test.cli('pump.diagnose',dict(pump=1))
                test.record('failure diagnostics',passed=True,response=diagnostic)
                state=test.status()
                if state['pumps'][0]['fault'] or state['pumps'][0]['pumping']:
                    recovered=test.cli('pump.recover',dict(pump=1),required=False)
                    test.record('explicit fault recovery',passed=recovered['ok'],response=recovered)
                    if not recovered['ok']:
                        break
    else:
        for kind in ('stir','inject'):
            args=dict(kind=kind,pump=1,rate=1000,volume=.1,deadline=10)
            if kind=='inject':
                args.update(address=2,before='input',after='extra')
            response=test.cli('helper.start',args,required=False)
            test.check(not response['ok'] and 'pump disconnected' in response.get('error',''),response)
            test.check(not test.status()['reservations'],'Leaked reservation')
            test.record(kind+' rejected synchronously',passed=True,response=response)
        response=test.cli('pump.recover',dict(pump=1),required=False)
        test.check(not response['ok'],response)
        test.record('disconnected recovery rejected clearly',passed=True,response=response)
    if test.status()['valve_connected']:
        for before in ('input','output','bypass','extra'):
            for after in ('input','output','bypass','extra'):
                test.cli('valve.move',dict(address=2,position=before))
                test.cli('valve.move',dict(address=2,position=after))
                observed=test.cli('valve.query',dict(address=2))['result']
                test.check(observed['position']==after,observed)
                test.record('serial '+before+' -> '+after,passed=True,observed=observed)
        test.cli('valve.move',dict(address=2,position='input'))
    final=test.status()
    test.check(not final['reservations'] and not any(s['running'] for s in final['helpers'].values()),final)
    (HERE/'final-status.json').write_text(json.dumps(final,indent=2),encoding='utf-8')
    test.record('final state',passed=True,final=final)

if __name__=='__main__':
    try:
        run()
    except Exception as exc:
        test.record('RETEST FAILURE',passed=False,error=repr(exc))
        test.cli('helper.stop',required=False)
        test.cli('pump.stop',dict(pump=1),required=False)
        raise
