#!/usr/bin/env python3
"""Canonical exporter/launcher for the pinned HMA reference-task adaptation.

The canonical independent verifier belongs to benchmark_bridge, not this module.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import time
for thread_env in ("OMP_NUM_THREADS","OPENBLAS_NUM_THREADS","MKL_NUM_THREADS","NUMEXPR_NUM_THREADS","VECLIB_MAXIMUM_THREADS","BLIS_NUM_THREADS"):
    os.environ[thread_env]="1"
import numpy as np

ROOT=Path(__file__).resolve().parent
VENDOR=ROOT.parent/'vendor'/'HMA'
REVISION='0ebbcaa4c2b8385c2c9bff06d60689cb2c64229d'

def inherited_lock_fds() -> tuple[int, ...]:
    """Keep the scheduler's flock alive until the final native child exits."""
    value=os.environ.get('ICLR_BENCHMARK_LOCK_FD')
    if value is None: return ()
    fd=int(value)
    if fd<0: raise ValueError('ICLR_BENCHMARK_LOCK_FD must be a valid open file descriptor')
    os.fstat(fd)  # Fail closed when a scheduler advertises a missing descriptor.
    return (fd,)

def export_native(input_json: Path, target: Path) -> dict:
    cfg=json.loads(input_json.read_text())
    if cfg['schema']!='iclr_external_solver_instance_v1':
        raise ValueError('Unsupported canonical export schema')
    n=int(cfg['num_customers']); m=int(cfg['num_charging_stations']); size=1+n+m
    if n<1 or m<0: raise ValueError('Invalid canonical customer/station roster')
    if cfg['objective_distance_source']!='running_time_path_distance_km':
        raise ValueError('HMA reference profile requires the frozen fastest-time-path distance objective')
    arrays=np.load(input_json.parent/cfg['matrices_file'],allow_pickle=False)
    T,D,E=[arrays[k] for k in ('T_s','D_km','E_kwh')]
    for array in (T,D,E):
        if array.shape!=(size,size) or array.dtype!=np.float32 or not np.isfinite(array).all() or (array<0).any():
            raise ValueError('Expected finite nonnegative canonical square float32 matrices')
    coords=cfg['coordinates']
    if len(coords)!=size: raise ValueError('Coordinate ordering disagrees with node roster')
    start=float(cfg['working_start_s']); end=float(cfg['working_end_s'])
    # This normalization is ONLY an internal energy-to-range coordinate; physical E is imported verbatim.
    consumption=1.0
    with target.open('w') as f:
        f.write(f"NAME : {cfg['instance_id']}\nTYPE : EVRPTW-ICLR-REFERENCE\nDIMENSION : {size}\n")
        values={'VEHICLES':n+1,'DISPATCHINGCOST':cfg['vehicle_fixed_cost_usd'],
                'UNITCOST':cfg['distance_unit_cost_usd_per_km'],'CAPACITY':cfg['cargo_capacity_cm3'],
                'ELECTRIC_POWER':cfg['battery_capacity_kwh'],'CONSUMPTION_RATE':consumption,'RECHARGING_RATE':1.0}
        for key,value in values.items(): f.write(f'{key} : {float(value):.17g}\n')
        f.write('EDGE_WEIGHT_TYPE : EXPLICIT\nNODE_SECTION\nID,type,x,y,delivery,pickup,ready_time,due_date,service_time\n')
        for i,(x,y) in enumerate(coords):
            kind='d' if i==0 else ('c' if i<=n else 'f')
            demand=float(cfg['demands_cm3'][i-1]) if kind=='c' else 0.0
            tw=cfg['tw_s'][i-1] if kind=='c' else [start,end]
            service=float(cfg['service_time_s'][i-1]) if kind=='c' else 0.0
            vals=(float(x),float(y),demand,0.0,float(tw[0]),float(tw[1]),service)
            f.write(f'{i},{kind},'+','.join(f'{v:.17g}' for v in vals)+'\n')
        f.write('DISTANCETIME_SECTION\nID,from_node,to_node,distance,spend_tm,energy_kwh\n')
        for i in range(size):
            for j in range(size):
                f.write(f'{i*size+j},{i},{j},{float(D[i,j]):.17g},{float(T[i,j]):.17g},{float(E[i,j]):.17g}\n')
        f.write('STATION_CHARGING_SECONDS_PER_KWH\n')
        for j,power in enumerate(cfg['effective_charging_power_kw']):
            if not math.isfinite(power) or power<=0: raise ValueError('Invalid effective station power')
            f.write(f'{n+1+j},{3600.0/float(power):.17g}\n')
        f.write('DEPOT_SECTION\n0\n')
    return cfg

def make_command(native_input: Path, output_dir: Path, n: int, seed: int, budget: float) -> list[str]:
    large=n>100
    command=[str(ROOT/'bin'/'hma_reference'),'--problem',str(native_input),'--benchmark_reference',
             '--output',str(output_dir/'native_solution.txt'),'--time',str(math.ceil(budget)),
             '--runs','1','--random_seed',str(seed),'--g_1','20','--pop_size','4',
             '--init','rcrs','--cross_repair','regret','--parent_selection','circle',
             '--replacement','one_on_one','--O_1_eval','--two_opt','--two_opt_star','--or_opt','2',
             '--two_exchange','2','--elo','1','--related_removal',
             '--removal_lower','0.05' if large else '0.1','--removal_upper','0.05' if large else '0.2',
             '--regret_insertion','--individual_search','--population_search','--parallel_insertion',
             '--aggressive_local_search','--station_range','0.1' if large else '0.5',
             '--subproblem_range',str(max(1,n//100))]
    if not large: command.append('--conservative_local_search')
    # Explicit arc TW pruning is disabled because additional Floyd closure is prohibited.
    return command

def main() -> int:
    p=argparse.ArgumentParser()
    p.add_argument('--input',type=Path,required=True);p.add_argument('--output-dir',type=Path,required=True)
    p.add_argument('--seed',type=int,default=1234);p.add_argument('--budget-s',type=float,default=1800)
    p.add_argument('--start-monotonic',type=float);p.add_argument('--ack-file',type=Path)
    args=p.parse_args()
    lock_fds=inherited_lock_fds()
    output=args.output_dir.resolve();output.mkdir(parents=True,exist_ok=True)
    for name in ('events.jsonl','native_stop.json','native_solution.txt'):
        existing=output/name
        if existing.exists() and not (name=='events.jsonl' and existing.stat().st_size==0):
            raise FileExistsError(f'Refusing to reuse existing result: {existing}')
    revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=VENDOR,text=True).strip()
    if revision!=REVISION: raise RuntimeError(f'Unexpected author revision {revision}')
    t=time.monotonic()
    started=args.start_monotonic if args.start_monotonic is not None else t
    native=output/'native_input.txt';cfg=export_native(args.input.resolve(),native)
    export_elapsed=time.monotonic()-t
    command=make_command(native,output,int(cfg['num_customers']),args.seed,args.budget_s)
    env=os.environ.copy();env.update({'HMA_START_MONOTONIC':str(started),'HMA_BUDGET_S':str(args.budget_s),
        'HMA_EVENTS_PATH':str(output/'events.jsonl'),'HMA_STOP_PATH':str(output/'native_stop.json'),
        'HMA_ACK_PATH':str(args.ack_file.resolve()) if args.ack_file else '',
        'OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','MKL_NUM_THREADS':'1'})
    manifest={'solver':'HMA adapted to the benchmark reference task','author_revision':revision,
              'seed':args.seed,'budget_s':args.budget_s,'start_monotonic':started,'command':command,
              'export_elapsed_s':export_elapsed,'input_json':str(args.input.resolve()),
              'native_input_sha256':hashlib.sha256(native.read_bytes()).hexdigest(),
              'binary_sha256':hashlib.sha256((ROOT/'bin'/'hma_reference').read_bytes()).hexdigest(),
              'adaptation_patch_sha256':hashlib.sha256((ROOT/'hma_reference.patch').read_bytes()).hexdigest(),
              'adapter_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'limits':{'g_1_consecutive_nonimprovement':20,'runs':1,'pruning':False,
                        'station_range':0.1 if cfg['num_customers']>100 else 0.5,
                        'subproblem_range':max(1,cfg['num_customers']//100)},
              'external_verifier_required':True}
    manifest['scheduler_lock_inherited']=bool(lock_fds)
    (output/'adapter_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    with (output/'native.log').open('w') as log:
        proc=subprocess.run(command,env=env,cwd=VENDOR,stdout=log,stderr=subprocess.STDOUT,
                            pass_fds=lock_fds)
    (output/'process_result.json').write_text(json.dumps({'returncode':proc.returncode,
           'elapsed_s':time.monotonic()-started},indent=2)+'\n')
    return proc.returncode
if __name__=='__main__': raise SystemExit(main())
