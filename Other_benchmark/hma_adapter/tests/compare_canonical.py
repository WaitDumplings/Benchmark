from pathlib import Path
import sys,json,subprocess,math
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
from run import export_native
fixture_index=root.parent/'tests/fixtures/index.json'
out=root/'evidence/canonical_comparison';out.mkdir(parents=True,exist_ok=True)
results=[]
for group in json.loads(fixture_index.read_text()):
 directory=Path(group['directory']);target=out/directory.name;target.mkdir(exist_ok=True)
 export_native(directory/'instance.json',target/'native_input.txt')
 expected=json.loads((directory/'expected.json').read_text())
 for case in expected['cases']:
  base=target/case['name'];routes=base.with_suffix('.routes.txt');result=base.with_suffix('.json')
  routes.write_text('\n'.join(' '.join(map(str,route)) for route in case['routes'])+'\n')
  with base.with_suffix('.native.log').open('w') as log:
   subprocess.run([str(root/'bin/route_probe'),str(target/'native_input.txt'),str(routes),str(result)],check=True,stdout=log,stderr=subprocess.STDOUT)
  actual=json.loads(result.read_text());valid=case['canonical_verifier']['passed']
  passed=actual['complete_and_feasible']==valid
  delta=None
  if valid:
   delta=actual['cost']-case['canonical_objective']['objective_cost_usd'];passed=passed and abs(delta)<1e-7
  else:passed=passed and actual['cost'] is None
  results.append(dict(instance=directory.name,case=case['name'],passed=passed,canonical_feasible=valid,native_feasible=actual['complete_and_feasible'],cost_delta=delta))
summary={'passed':all(x['passed'] for x in results),'case_count':len(results),'results':results}
(root/'evidence/canonical_comparison.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps(summary,indent=2));assert summary['passed']
