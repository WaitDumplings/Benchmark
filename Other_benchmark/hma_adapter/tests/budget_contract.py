from pathlib import Path
import json,subprocess,time,sys
root=Path(__file__).resolve().parents[1]
base=[sys.executable,str(root/'run.py'),'--input',str(root/'evidence/fixtures/resources/instance.json'),'--seed','1234']
stamp=str(time.time_ns())
checks=[]
for name,budget,backdate in [('ack_wait',.08,0.),('expired_before_preprocessing',.01,1.)]:
 out=root/'evidence'/f'budget_{name}_{stamp}'
 args=base+['--output-dir',str(out),'--budget-s',str(budget),'--ack-file',str(out/'unwritten_ack.txt')]
 if backdate:args+=['--start-monotonic',str(time.monotonic()-backdate)]
 result=subprocess.run(args,check=True)
 stop=json.loads((out/'native_stop.json').read_text())
 events=[json.loads(x) for x in (out/'events.jsonl').read_text().splitlines()] if (out/'events.jsonl').exists() else []
 assert stop['stop_reason']=='wall_budget_exhausted'
 assert all(0<=e['elapsed_s']<budget for e in events)
 if backdate:assert not events
 else:assert len(events)>=1 and stop['elapsed_s']<budget+.1
 checks.append(dict(name=name,stop=stop,candidates=len(events),output=str(out)))
(root/'evidence'/'budget_contract.json').write_text(json.dumps({'passed':True,'checks':checks},indent=2)+'\n')
print(json.dumps(checks,indent=2))
