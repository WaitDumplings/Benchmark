from pathlib import Path
import importlib.util,json,subprocess,sys
root=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('canonical_tests',root.parent/'tests/test_bridge.py')
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
# Import our adapter under a distinct name: canonical test loader has imported root's run already.
spec=importlib.util.spec_from_file_location('hma_transport',root/'run.py');adapter=importlib.util.module_from_spec(spec);spec.loader.exec_module(adapter)
np=mod.np;out=root/'evidence/tolerance_cases';out.mkdir(exist_ok=True)
records=[]
for resource in ('battery','customer_tw','return_horizon','capacity'):
 for extra in (5e-6,2e-5):
  name=f'{resource}_{extra:g}';kwargs={};route=[0,1,0]
  if resource=='battery':
   E=np.full((3,3),.1,dtype=np.float32);E[0,2]=1.+extra
   kwargs=dict(n=1,m=1,battery=1.,energy=E,powers=[3600.],derating=1.);route=[0,2,1,0]
  elif resource=='customer_tw':kwargs=dict(n=1,travel=[[0,1.+extra],[1.,0]],tw=[[0.,1.]])
  elif resource=='return_horizon':kwargs=dict(n=1,travel=[[0,1.],[1.+extra,0]],end=2)
  else:kwargs=dict(n=1,demands=[1.+extra],capacity=1.)
  instance=mod.make_instance(name,**kwargs);folder=out/name;mod.common.export_instance(instance,folder)
  expected=mod.common.validate_routes(instance,[route]);adapter.export_native(folder/'instance.json',folder/'native.txt')
  (folder/'routes.txt').write_text(' '.join(map(str,route))+'\n')
  with (folder/'native.log').open('w') as log:subprocess.run([str(root/'bin/route_probe'),str(folder/'native.txt'),str(folder/'routes.txt'),str(folder/'actual.json')],stdout=log,stderr=subprocess.STDOUT,check=True)
  actual=json.loads((folder/'actual.json').read_text());passed=actual['complete_and_feasible']==expected['passed']
  if resource=='battery' and expected['passed']:
   states=actual['routes'][0]['states'];native_charge=states[1]['departure_s']-states[1]['arrival_s'];passed &= abs(native_charge-expected['total_charging_time_s'])<1e-12
  record=dict(resource=resource,extra=extra,passed=bool(passed),canonical_feasible=expected['passed'],native_feasible=actual['complete_and_feasible']);records.append(record)
  (folder/'expected.json').write_text(json.dumps(expected,indent=2))
summary=dict(passed=all(r['passed'] for r in records),cases=records)
(root/'evidence/tolerance_contract.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary,indent=2));assert summary['passed']
