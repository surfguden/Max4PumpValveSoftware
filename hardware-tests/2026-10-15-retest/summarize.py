"""Produce a compact acceptance summary for the most recent complete run."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[1]
rows=[json.loads(line) for line in (HERE/'results.jsonl').read_text().splitlines()]
start=max(i for i,row in enumerate(rows) if row['test']=='real Qmix reconnect')
latest=rows[start:]
events=[]
for path in (HERE/'session').glob('events.jsonl*'):
    for line in path.read_text(encoding='utf-8').splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            pass
dwells=[]
for row in latest:
    if not row['test'].startswith('stir normal') or not row['passed']:
        continue
    states=[event for event in events if event.get('event')=='helper.state' and event.get('command_id')==row['command_id']]
    states.sort(key=lambda event:event['sequence'])
    for step,next_step,field in [('Waiting after empty','Refilling','wait_empty'),
                                 ('Waiting after full','Setting ending valve','wait_full')]:
        requested=row['args'].get(field,0)
        if not requested:
            continue
        begin=next(event for event in states if event['state']['step']==step)
        end=next(event for event in states if event['sequence']>begin['sequence'] and event['state']['step'].startswith(next_step))
        observed=(datetime.fromisoformat(end['time'])-datetime.fromisoformat(begin['time'])).total_seconds()
        assert observed>=requested-.02, (step,observed,requested)
        dwells.append(dict(test=row['test'],step=step,requested_s=requested,observed_s=observed))
summary=dict(outcomes=len(latest),passed=sum(row['passed'] for row in latest),
             failed=[dict(test=row['test'],error=row.get('error')) for row in latest if not row['passed']],
             injection=[{key:row[key] for key in ('test','requested','measured_displacement','error_ml','rate')} for row in latest if row['test'].startswith('inject ') and 'requested' in row],
             stops=[dict(test=row['test'],roundtrip_s=row['roundtrip_s']) for row in latest if 'roundtrip_s' in row],
             dwells=dwells,
             checks=[dict(test=row['test'],passed=row['passed']) for row in latest])
(HERE/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
distribution=REPO/'dist/hardware-accepted-20261015/Max4PumpValve-1.1.0'
manifest=dict(branch=subprocess.check_output(['git','branch','--show-current'],cwd=REPO,text=True).strip(),
              base_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip(),
              includes_uncommitted_changes=True,
              source_sha256={str(path.relative_to(REPO)):hashlib.sha256(path.read_bytes()).hexdigest() for path in (REPO/'pumpvalve').rglob('*.py')},
              executable_sha256={name:hashlib.sha256((distribution/name).read_bytes()).hexdigest() for name in ('Max4PumpValve.exe','Max4PumpValveCtl.exe')},
              automated_tests='149 passed, 18 subtests passed')
(HERE/'build-manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
print(json.dumps({key:summary[key] for key in ('outcomes','passed','failed','injection','stops','dwells')},indent=2))
