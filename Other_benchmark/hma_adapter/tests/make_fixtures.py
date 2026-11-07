import json,sys
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from run import export_native
root=Path(__file__).resolve().parents[1]/'evidence'/'fixtures'
for name,n in [('resources',2),('long_route',120),('psi_1000',1000)]:
 d=root/name;d.mkdir(parents=True,exist_ok=True);size=n+3
 D=np.ones((size,size),np.float32);T=D.copy();E=D.copy()*np.float32(.001)
 for a in(D,T,E):np.fill_diagonal(a,0)
 cfg=dict(schema='iclr_external_solver_instance_v1',instance_id=name,num_customers=n,num_charging_stations=2,working_start_s=10.,working_end_s=10000.,battery_capacity_kwh=10.,cargo_capacity_cm3=100.,vehicle_fixed_cost_usd=100.,distance_unit_cost_usd_per_km=2.,coordinates=[[i,i*.1] for i in range(size)],demands_cm3=[.5]*n,service_time_s=[0.]*n,tw_s=[[10.,10000.]]*n,charging_power_kw=[1800.,900.],charging_power_derating_factor=1.,effective_charging_power_kw=[1800.,900.],matrices_file='matrices.npz',objective_distance_source='running_time_path_distance_km')
 if name=='resources':
  cfg['demands_cm3']=[30.,40.]
  for i,j,distance,energy in [(0,1,3.,3.125),(1,3,2.,2.25),(1,4,2.,2.25),(3,2,4.,4.125),(4,2,4.,4.125),(2,0,4.,4.25)]:D[i,j]=distance;E[i,j]=energy
  # Deliberately nonmetric directed time: matrix preservation is a contract, not a closure.
  T[0,2]=99.;T[2,0]=7.
 if name=='psi_1000':cfg['cargo_capacity_cm3']=1000.
 np.savez(d/'matrices.npz',T_s=T,D_km=D,E_kwh=E)
 (d/'instance.json').write_text(json.dumps(cfg,indent=2))
 export_native(d/'instance.json',d/'native_input.txt')
print(root)
