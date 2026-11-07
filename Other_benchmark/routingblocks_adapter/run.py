#!/usr/bin/env python3
"""RoutingBlocks examples/evrptw ALNS adapted to the ICLR canonical contract.

The author neighborhood, destroy/repair operators, penalties and thresholds are
retained. Necessary changes are native full recharge, weighted cost instead of
lexicographic fleet reduction, available empty routes, and accountable deadlines.
"""
import argparse
import copy
import hashlib
import json
import math
import os
import resource
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace

for _variable in ("OMP_NUM_THREADS","OPENBLAS_NUM_THREADS","MKL_NUM_THREADS","NUMEXPR_NUM_THREADS",
                  "VECLIB_MAXIMUM_THREADS","BLIS_NUM_THREADS"):
    os.environ[_variable]="1"

ROOT = Path(__file__).resolve().parent
VENDOR = ROOT.parent / "vendor" / "RoutingBlocks"
sys.path.insert(0, str(VENDOR / "examples"))
import numpy as np
import routingblocks as rb
import routingblocks._routingblocks as native
from routingblocks.operators import best_insert, first_move_selector, blink_selector_factory
from evrptw.alns import ALNS
from evrptw.parameters import ALNSParams
from evrptw.utility import distribute_randomly

EXPIRED = "routingblocks_online_budget_expired"


class MatrixArcs(Mapping):
    def __init__(self, names, t, d, e):
        self.ids = {name: i for i, name in enumerate(names)}
        self.t, self.d, self.e = t, d, e
    def __len__(self): return len(self.ids) ** 2
    def __iter__(self):
        return ((i,j) for i in self.ids for j in self.ids)
    def __getitem__(self, ij):
        i,j = (self.ids[x] for x in ij)
        return SimpleNamespace(cost=float(self.d[i,j]), distance=float(self.d[i,j]),
            consumption=float(self.e[i,j]), travel_time=float(self.t[i,j]))


def load_instance(directory):
    cfg = json.loads((directory / "instance.json").read_text())
    if cfg["schema"] != "iclr_external_solver_instance_v1":
        raise ValueError("unsupported canonical instance schema")
    with np.load(directory / cfg["matrices_file"], allow_pickle=False) as z:
        mats = {k: z[k] for k in ("T_s", "D_km", "E_kwh")}
    n, m = cfg["num_customers"], cfg["num_charging_stations"]
    for k,v in mats.items():
        if v.shape != (1+n+m, 1+n+m) or not np.isfinite(v).all() or (v<0).any():
            raise ValueError(f"invalid directed matrix {k}")
    return cfg,mats


def create_instance(cfg,mats):
    n,m = cfg["num_customers"],cfg["num_charging_stations"]
    names=["D0"]+[f"C{i}" for i in range(1,n+1)]+[f"S{i}" for i in range(m)]
    py_vertices={}
    native=[]
    for i,name in enumerate(names):
        customer=0<i<=n
        station=i>n
        demand=cfg["demands_cm3"][i-1] if customer else 0.
        ready=cfg["tw_s"][i-1][0] if customer else cfg["working_start_s"]
        due=cfg["tw_s"][i-1][1] if customer else cfg["working_end_s"]
        service=cfg["service_time_s"][i-1] if customer else 0.
        power=cfg["effective_charging_power_kw"][i-n-1] if station else 0.
        if station and power<=0: raise ValueError("station effective power must be positive")
        x,y=cfg["coordinates"][i]
        py_vertices[name]=SimpleNamespace(vertex_id=name,x_coord=x,y_coord=y,demand=demand,
            ready_time=ready,due_time=due,service_time=service,is_customer=customer,is_station=station,is_depot=i==0)
        native.append(rb.create_fullcharge_vertex(i,name,station,i==0,
            rb.FullRechargeVertexData(demand,ready,due,service,power)))
    t,d,e=(mats[k] for k in ("T_s","D_km","E_kwh"))
    arcs=[[rb.create_fullcharge_arc(rb.FullRechargeArcData(float(d[i,j]),float(e[i,j]),float(t[i,j])))
        for j in range(len(names))] for i in range(len(names))]
    cpp=rb.Instance(native,arcs,n)
    py=SimpleNamespace(vertices=py_vertices,customers=[v for v in py_vertices.values() if v.is_customer],
        stations=[v for v in py_vertices.values() if v.is_station],depot=py_vertices["D0"],
        arcs=MatrixArcs(names,t,d,e),parameters=SimpleNamespace(capacity=cfg["cargo_capacity_cm3"],
        battery_capacity_time=cfg["battery_capacity_kwh"],fleet_size=n))
    evaluation=rb.FullRechargeEvaluation(cfg["battery_capacity_kwh"],cfg["cargo_capacity_cm3"],
        cfg["vehicle_fixed_cost_usd"],cfg["distance_unit_cost_usd_per_km"])
    return py,cpp,evaluation


class AvailableRouteRepair(rb.RepairOperator):
    """Author repair with sufficient empty route slots for every removed customer."""
    def __init__(self, delegate):
        rb.RepairOperator.__init__(self)
        self.delegate=delegate
    def apply(self,evaluation,solution,vertex_ids):
        vertex_ids=list(vertex_ids)
        # Sufficient logical maximum; unused empty routes cost zero. Station
        # nodes are filtered by the original repair implementations.
        empty=sum(r.empty for r in solution)
        for _ in range(max(0,len(vertex_ids)-empty)): solution.add_route()
        self.delegate.apply(evaluation,solution,vertex_ids)
    def can_apply_to(self,solution): return True
    def name(self): return self.delegate.name()+"_unbounded_fleet"


class WeightedALNS(ALNS):
    def __init__(self,evaluation,py_instance,cpp_instance,params,seed,cfg,start,deadline,emit):
        self.online_start,self.deadline,self.emit=start,deadline,emit
        super().__init__(evaluation,py_instance,cpp_instance,params,seed)
        self._fpo=rb.FullRechargeFacilityPlacementOptimizer(cpp_instance,cfg["battery_capacity_kwh"],
            cfg["distance_unit_cost_usd_per_km"],deadline)
        evaluation.deadline_s=deadline
        self.stop_reason=None
        self.stage="initialization"
        self._local_search.solution_callback=lambda solution: self.emit(solution,self.stage+"_local_search",self._iters)
    @property
    def elapsed(self): return time.monotonic()-self.online_start
    def check(self):
        if time.monotonic()>=self.deadline: raise RuntimeError(EXPIRED)
    def _configure_repair_operators(self):
        delegates=[rb.operators.RandomInsertionOperator(self._random),
            best_insert.BestInsertionOperator(self._cpp_instance,first_move_selector),
            best_insert.BestInsertionOperator(self._cpp_instance,
                blink_selector_factory(self._params.best_insertion_blink_probability,self._random))]
        for op in delegates:
            self._adaptive_large_neighborhood.add_repair_operator(AvailableRouteRepair(op))
    def _remove_empty_routes(self,solution):
        clean=super()._remove_empty_routes(solution)
        # A spare route permits fleet-increasing local moves. Repair has enough
        # slots for all removed customers, so there is no lower hard fleet cap.
        clean.add_route()
        return clean
    def _apply_dp(self,solution):
        routes=[]
        for r in solution:
            self.check()
            if any(x.vertex.is_customer for x in r):
                path=self._fpo.optimize([x.vertex_id for x in r])
                routes.append(rb.create_route(self._evaluation,self._cpp_instance,path[1:-1]))
        return self._remove_empty_routes(rb.Solution(self._evaluation,self._cpp_instance,routes))
    def _accept_solution(self,solution):
        self.check()
        score=0
        if solution.cost<self._current_obj:
            self._current_solution=copy.deepcopy(solution)
            score=self._params.new_improvement_score
        if solution.cost<self._best_obj:
            self._best_solution=copy.deepcopy(solution)
            score=self._params.new_best_score
            if not solution.feasible: self._make_feasible(solution)
        # Weighted C alone decides best feasible. No lexicographic K check or
        # forced vehicle-decrease/reset mechanism survives in this variant.
        if solution.feasible and solution.cost<self._best_feasible_obj:
            self._best_feasible_solution=copy.deepcopy(solution)
            score=self._params.new_best_feasible_score
        self.emit(solution,self.stage,self._iters)
        return score
    def run(self,time_limit,max_iterations,max_iterations_since_last_improvement):
        self._start_time=self.online_start
        self._iters=self._iters_since_improvement=1
        # Retain author's random construction and N starting solutions, while
        # exposing complete candidates as soon as each is generated.
        for _ in range(self._params.num_starting_solutions):
            self.check()
            customers=[x.vertex_id for x in self._cpp_instance.customers]
            solution=rb.Solution(self._evaluation,self._cpp_instance,
                [rb.create_route(self._evaluation,self._cpp_instance,r) for r in
                 distribute_randomly(customers,self._cpp_instance.fleet_size,self._random)])
            self.emit(solution,"random_construction",self._iters)
            self._local_search.optimize(solution,self._operators)
            self._accept_solution(self._apply_dp(solution))
        self._current_solution=copy.deepcopy(self._best_solution)
        self._cost_component_tracker.register(self._current_solution)
        self.stage="alns"
        while self._iters<max_iterations and self._iters_since_improvement<max_iterations_since_last_improvement:
            self.check()
            solution,*operators=self._generate_solution_from_lns(self._current_solution)
            candidate_obj=solution.cost_components[0]
            if candidate_obj<self._best_dist*(1+self._params.delta_local_search):
                if self._params.shuffle_operators: self._py_random.shuffle(self._operators)
                self._local_search.optimize(solution,self._operators)
                if candidate_obj<self._best_dist*(1+self._params.delta_fpo):
                    solution=self._apply_dp(solution)
                else: solution=self._remove_empty_routes(solution)
                self._ls_iters+=1
            self._cost_component_tracker.register(self._current_solution)
            score=self._accept_solution(solution)
            self._adaptive_large_neighborhood.collect_score(*operators,score)
            self._iters_since_improvement=1 if score==self._params.new_best_feasible_score else self._iters_since_improvement+1
            self._iters+=1
            if self._iters%self._params.adaptive_period_length==0:
                self._adaptive_large_neighborhood.adapt_operator_weights()
            if self._iters%self._params.penalty_period_length==0:
                actual=self._cost_component_tracker.window_feasibility_ratios
                penalties=[self._evaluation.overload_penalty_factor,self._evaluation.resource_penalty_factor,
                    self._evaluation.time_shift_penalty_factor]
                factors=[max(.1,min(10000.,p*(self._params.penalty_increase_factor if a<t else self._params.penalty_decrease_factor)))
                    for a,t,p in zip(actual,self._params.target_feasibility_ratios,penalties)]
                self._update_penalty_factors(factors[0],max(factors[1],factors[2]),max(factors[1],factors[2]))
        self.stop_reason="max_iterations" if self._iters>=max_iterations else "max_iterations_since_last_improvement"
        return self._best_feasible_solution or self._best_solution


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--instance-dir",type=Path,required=True)
    p.add_argument("--output-events",type=Path,required=True)
    p.add_argument("--seed",type=int,required=True)
    p.add_argument("--time-limit",type=float,default=1800.)
    p.add_argument("--monotonic-start-s",type=float)
    p.add_argument("--ack-file",type=Path)
    p.add_argument("--max-iterations",type=int,default=sys.maxsize)
    p.add_argument("--max-iterations-since-last-improvement",type=int,default=sys.maxsize)
    args=p.parse_args()
    read_start=time.monotonic()
    cfg,mats=load_instance(args.instance_dir)
    read_seconds=time.monotonic()-read_start
    start=args.monotonic_start_s if args.monotonic_start_s is not None else time.monotonic()
    deadline=start+args.time_limit
    args.output_events.parent.mkdir(parents=True,exist_ok=True)
    count=0
    observed_threads=[]
    status={"schema":"routingblocks_adapted_status_v1","solver_revision":"2d2ee06c79b5d458b1aeeafd95a2b42b5441079a",
        "instance_id":cfg["instance_id"],"seed":args.seed,"input_read_time_s":read_seconds,"online_start_monotonic_s":start}
    params=ALNSParams(**json.loads((VENDOR/"examples/evrptw/config.json").read_text()))
    status.update(config=vars(params),compiled_native_so_path=str(native.__file__),
        compiled_native_so_sha256=hashlib.sha256(Path(native.__file__).read_bytes()).hexdigest())
    status_path=args.output_events.with_suffix(".status.json")
    def persist_status(phase):
        status.update(phase=phase,status_updated_elapsed_s=time.monotonic()-start)
        temporary=status_path.with_name(status_path.name+".tmp")
        temporary.write_text(json.dumps(status,indent=2)+"\n")
        temporary.replace(status_path)
    # The outer hard deadline may kill this process without entering finally.
    # Publish completed stages now so their evidence survives that termination.
    persist_status("input_loaded")
    events=args.output_events.open("w",buffering=1)
    def emit(solution,stage,iteration):
        nonlocal count
        observed_threads.append(len(list(Path("/proc/self/task").iterdir())))
        elapsed=time.monotonic()-start
        if elapsed>=args.time_limit: raise RuntimeError(EXPIRED)
        routes=[[x.vertex_id for x in r] for r in solution if any(x.vertex.is_customer for x in r)]
        event={"schema":"external_solver_candidate_v1","elapsed_s":elapsed,"candidate_index":count+1,
            "routes":routes,"internal_objective":float(solution.cost),"internal_components":list(solution.cost_components),
            "internal_feasible":bool(solution.feasible),"stage":stage,"iteration":iteration}
        events.write(json.dumps(event,separators=(",",":"))+"\n");events.flush();count+=1
        if args.ack_file:
            while True:
                if time.monotonic()>=deadline: raise RuntimeError(EXPIRED)
                try: acknowledged=int(args.ack_file.read_text().strip())
                except (OSError,ValueError): acknowledged=0
                if acknowledged>=count: break
                time.sleep(.005)
    solver=None
    try:
        py,cpp,evaluation=create_instance(cfg,mats)
        status["instance_adaptation_time_s"]=time.monotonic()-start
        persist_status("instance_adapted")
        # Preserve author config. Large volume units remain original cm3;
        # penalty behavior is disclosed rather than silently retuned on test.
        status["config"]=vars(params)
        solver=WeightedALNS(evaluation,py,cpp,params,args.seed,cfg,start,deadline,emit)
        status["preprocessing_time_s"]=time.monotonic()-start
        persist_status("preprocessing_completed")
        solver.run(args.time_limit,args.max_iterations,args.max_iterations_since_last_improvement)
        status["stop_reason"]=solver.stop_reason
    except RuntimeError as exc:
        if EXPIRED not in str(exc):
            status["stop_reason"]="error";status["error"]=repr(exc);raise
        status["stop_reason"]="wall_clock_budget"
    finally:
        events.close()
        status.update(actual_elapsed_s=time.monotonic()-start,candidate_count=count,
            iterations=solver._iters if solver else 0,
            process_peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            process_user_cpu_s=resource.getrusage(resource.RUSAGE_SELF).ru_utime,
            process_system_cpu_s=resource.getrusage(resource.RUSAGE_SELF).ru_stime,
            observed_process_threads_min=min(observed_threads,default=len(list(Path("/proc/self/task").iterdir()))),
            observed_process_threads_max=max(observed_threads,default=len(list(Path("/proc/self/task").iterdir()))))
        persist_status("finished")
    print(json.dumps(status))

if __name__=="__main__": main()
