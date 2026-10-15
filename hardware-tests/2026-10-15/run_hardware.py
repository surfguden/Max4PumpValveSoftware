"""Physical acceptance tests using only the packaged public terminal interface."""
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent.parent
EXE = REPO / 'dist/Max4PumpValve-1.1.0-1f82847/Max4PumpValveCtl.exe'
os.environ['PUMPVALVE_DATA_DIR'] = str(ROOT / 'session')
LOG = ROOT / 'cli-transcript.jsonl'
RESULTS = ROOT / 'results.jsonl'

def append(path, value):
    with path.open('a', encoding='utf-8') as f:
        f.write(json.dumps(value, allow_nan=False) + '\n')

def cli(command, args=None, required=True):
    payload = dict(command=command, args=args or {}, id=uuid.uuid4().hex)
    start = time.monotonic()
    p = subprocess.run([str(EXE), 'json'], input=json.dumps(payload), capture_output=True, text=True, timeout=35)
    response = json.loads(p.stdout)
    append(LOG, dict(time=time.time(), request=payload, response=response, elapsed=time.monotonic()-start, stderr=p.stderr))
    if required and not response['ok']:
        raise RuntimeError(response)
    return response

def status():
    return cli('status')['result']

def check(condition, detail):
    if not condition:
        raise AssertionError(detail)

def record(name, **details):
    item = dict(test=name, **details)
    append(RESULTS, item)
    print(json.dumps(item), flush=True)

def idle(timeout=100):
    expires = time.monotonic()+timeout
    while time.monotonic() < expires:
        s = status()
        p = s['pumps'][0]
        check(not p['fault'], p)
        if not p['pumping'] and abs(p['flow']) < 1e-6:
            return s
        time.sleep(.15)
    raise TimeoutError('Pump did not become idle')

def level(value, rate=20000):
    cli('pump.command', dict(pump=1, mode='level', value=value, rate=rate))
    s = idle()
    check(abs(s['pumps'][0]['fill']-value) <= .001, s)
    return s

def helper(kind, **kwargs):
    args = dict(kind=kind, pump=1, deadline=100, **kwargs)
    started = cli('helper.start', args)
    return started['id']

def wait_helper(kind, expected='Completed', timeout=110):
    expires = time.monotonic()+timeout
    while time.monotonic() < expires:
        s = status()
        h = s['helpers'][kind]
        if not h['running']:
            check(h['step'] == expected, h)
            check(not s['pumps'][0]['pumping'] and abs(s['pumps'][0]['flow']) < 1e-6, s)
            check(not s['pumps'][0]['fault'], s)
            # Release is immediately after publishing running=false.
            if s['reservations']:
                time.sleep(.2)
                s = status()
            check(not s['reservations'], s)
            return s
        time.sleep(.12)
    raise TimeoutError('Helper did not terminate')

def stable(s, seconds=1):
    before = s['pumps'][0]['fill']
    time.sleep(seconds)
    after = status()
    check(abs(after['pumps'][0]['fill']-before) < .001, after)
    check(not after['pumps'][0]['pumping'] and after['pumps'][0]['flow'] == 0, after)
    return after

def setup():
    s = status()
    record('real hardware baseline', passed=not s['pump_simulation'] and not s['valve_simulation'], state=s)
    cli('settings.save', dict(config=r'C:\Users\Public\Documents\QmixElements\Projects\script test\Configurations\single', port='COM6', ui=dict(count=1)))
    profile = dict(name='BD 5ml', material='Plastic', inner_diameter_mm=12.07,
                   max_piston_stroke_mm=5000/(math.pi*12.07**2/4), minimum_ml=0, maximum_ml=5, max_pressure_bar=1)
    p = cli('pump.syringe', dict(pump=1, profile=profile))['result']
    check(abs(p['capacity']-5)<1e-6, p)
    record('5 mL syringe configured', passed=True, state=p)
    cli('pump.map', dict(pump=1, energized=2, deenergized=1))
    for v in (2,1):
        cli('pump.command', dict(pump=1, mode='valve', value=v))
        check(status()['pumps'][0]['valve']==v, 'Cetoni mismatch')
    record('Cetoni valve both positions', passed=True)
    for position in ('input','output','bypass','extra'):
        cli('valve.move', dict(address=2, position=position))
        observed = cli('valve.query', dict(address=2))['result']
        check(observed['position']==position, observed)
        record('TriContinent '+position, passed=True, observed=observed)
    level(.5)

def normal():
    cases = [dict(refill='Defined volume', volume=.7, rate=5000, wait_empty=1, wait_full=1),
             dict(refill='Volume before emptying', rate=10000, valve_begin='Not energized', valve_end='Energized'),
             dict(refill='Full syringe', rate=None, wait_empty=.5, wait_full=.5)]
    for i, args in enumerate(cases):
        initial=status()['pumps'][0]['fill']
        start=time.monotonic()
        rid=helper('stir', **args)
        s=wait_helper('stir')
        expected=5 if args['refill']=='Full syringe' else initial if args['refill']=='Volume before emptying' else args['volume']
        check(abs(s['pumps'][0]['fill']-expected)<.001, s)
        check(s['pumps'][0]['valve']==(2 if args.get('valve_end')=='Energized' else 1), s)
        record('stir normal '+str(i+1), passed=True, args=args, command_id=rid, elapsed=time.monotonic()-start, initial=initial, final=s)
    for volume,rate,before,after in [(.01,1000,'input','output'),(.1,5000,'bypass','extra'),(.5,10000,'output','input')]:
        initial=status()['pumps'][0]['fill']
        rid=helper('inject', volume=volume, rate=rate, address=2, before=before, after=after)
        s=wait_helper('inject')
        delta=initial-s['pumps'][0]['fill']
        check(abs(delta-volume)<=max(.001,volume*.001), s)
        # Compare independent read times within 0.01 uL, above encoder quantization.
        check(abs(s['helpers']['inject']['injected']-delta)<1e-5, s)
        check(s['valves'][0]['position']==after, s)
        record('inject '+str(volume)+' mL', passed=True, command_id=rid, rate=rate, requested=volume, measured_displacement=delta, error_ml=delta-volume, display_error_ml=s['helpers']['inject']['injected']-delta, final=s)

def stops():
    level(1)
    for rate in (5000,-5000):
        cli('pump.command', dict(pump=1,mode='flow',rate=rate))
        time.sleep(1)
        moving=status()
        check(moving['pumps'][0]['pumping'], moving)
        start=time.monotonic()
        stopped=cli('pump.stop',dict(pump=1))['result']
        elapsed=time.monotonic()-start
        final=stable(status(), 2)
        record('manual flow stop '+str(rate), passed=True, roundtrip_s=elapsed, stop=stopped, final=final)

def cancel_case(kind, step, stop_command='helper.stop'):
    level(.5)
    args=dict(refill='Defined volume', volume=.5, rate=5000, wait_empty=3, wait_full=3, valve_begin='Energized', valve_end='Not energized') if kind=='stir' else dict(volume=.4, rate=1000, address=2, before='input', after='extra')
    rid=helper(kind, **args)
    expires=time.monotonic()+30
    while time.monotonic()<expires:
        s=status()
        if s['helpers'][kind]['step']==step:
            if step in ('Emptying','Refilling','Injecting'):
                time.sleep(.4)
            start=time.monotonic()
            response=cli(stop_command, dict(pump=1) if stop_command=='pump.stop' else {})
            final=wait_helper(kind,'Cancelled')
            latency=time.monotonic()-start
            stable(final,1)
            if kind=='stir':
                check(final['pumps'][0]['valve']==2, final)
            else:
                check(final['valves'][0]['position']=='input', final)
            record(kind+' cancel during '+step+' via '+stop_command, passed=True, command_id=rid, terminal_observed_stop_s=latency, response=response, final=final)
            return
        check(s['helpers'][kind]['running'],s)
        time.sleep(.08)
    raise TimeoutError('Never reached '+step)

def cancellation():
    for step in ('Emptying','Refilling','Waiting after empty','Waiting after full'):
        cancel_case('stir',step)
    for command in ('helper.stop','pump.stop'):
        cancel_case('inject','Injecting',command)

def edges():
    level(.5)
    rid=cli('helper.start',dict(kind='stir',pump=1,refill='Defined volume',volume=.5,rate=1000,deadline=1))['id']
    final=wait_helper('stir','Error')
    stable(final)
    record('deadline stops pump',passed=True,command_id=rid,final=final)
    for args in (dict(mode='volume',value=10,rate=1000),dict(mode='level',value=6,rate=1000),dict(mode='flow',rate=1000000),dict(mode='volume',value=0,rate=1000)):
        response=cli('pump.command',dict(pump=1,**args),required=False)
        check(not response['ok'],response)
        record('reject invalid request',passed=True,args=args,response=response)
    level(.5)
    rid=helper('inject',volume=1,rate=1000,address=2,before='input',after='extra')
    s=wait_helper('inject','Error')
    record('reject insufficient injection volume',passed=True,command_id=rid,final=s)
    rid=helper('inject',volume=.1,rate=5000,address=2,before='input',after='output')
    s=wait_helper('inject')
    record('recovery injection',passed=True,command_id=rid,final=s)
    cli('pump.stop',dict(pump=1))
    stable(status(),2)
    record('final stopped and responsive',passed=True,final=status())

def blocked_checks():
    for before in ('input','output','bypass','extra'):
        for after in ('input','output','bypass','extra'):
            cli('valve.move',dict(address=2,position=before))
            cli('valve.move',dict(address=2,position=after))
            observed=cli('valve.query',dict(address=2))['result']
            check(observed['position']==after,observed)
            record('serial transition '+before+' -> '+after,passed=True,observed=observed)
    cli('valve.move',dict(address=2,position='bypass'))
    response=cli('pumps.connect',dict(count=1,simulation=False),required=False)
    record('pump recovery after valve checks',passed=response['ok'],response=response)
    if response['ok']:
        record('RECOVERY AVAILABLE',passed=True,state=status())
        return
    for kind in ('stir','inject'):
        args=dict(kind=kind,pump=1,rate=1000,volume=.1,deadline=10)
        if kind=='inject':
            args.update(address=2,before='input',after='extra')
        before=cli('valve.query',dict(address=2))['result']
        rid=cli('helper.start',args)['id']
        expires=time.monotonic()+15
        while True:
            s=status()
            if not s['helpers'][kind]['running']:
                break
            check(time.monotonic()<expires,s)
            time.sleep(.2)
        check(s['helpers'][kind]['step']=='Error',s)
        check(not s['reservations'],s)
        after=cli('valve.query',dict(address=2))['result']
        check(before['position']==after['position'],after)
        record(kind+' fails cleanly with disconnected pump',passed=True,command_id=rid,final=s)
    cli('valve.move',dict(address=2,position='input'))
    record('final application state',passed=True,final=status())

if __name__=='__main__':
    try:
        for stage in sys.argv[1:]:
            globals()[stage]()
    except Exception as exc:
        record('RUN FAILURE',passed=False,error=repr(exc))
        try:
            cli('helper.stop',required=False)
            cli('pump.stop',dict(pump=1),required=False)
        finally:
            raise
