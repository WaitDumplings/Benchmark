"""Boundary tests for native full-recharge evaluation and author operators."""
import copy
import itertools
import time
import numpy as np
import pytest
import routingblocks as rb
from run import create_instance


def make(n=3,m=2,**overrides):
    cfg=dict(num_customers=n,num_charging_stations=m,working_start_s=28800.,working_end_s=86400.,
        battery_capacity_kwh=10.,cargo_capacity_cm3=100.,vehicle_fixed_cost_usd=413.6331536717643,
        distance_unit_cost_usd_per_km=.1517509727626459,coordinates=[[0.,0.]]*(1+n+m),
        demands_cm3=[10.]*n,service_time_s=[5.]*n,tw_s=[[28800.,86400.]]*n,
        effective_charging_power_kw=[10.,20.][:m])
    cfg.update(overrides)
    size=1+n+m
    d=np.full((size,size),2.,dtype=float);np.fill_diagonal(d,0)
    e=np.full((size,size),1.,dtype=float);np.fill_diagonal(e,0)
    t=np.full((size,size),30.,dtype=float);np.fill_diagonal(t,0)
    return cfg,dict(T_s=t,D_km=d,E_kwh=e)


def test_full_charge_power_wait_service_and_directed_arcs():
    cfg,mats=make()
    cfg["tw_s"][0]=[28900.,29000.]
    mats["E_kwh"][0,1]=4.;mats["E_kwh"][1,4]=2.
    mats["D_km"][0,1]=7.;mats["D_km"][1,0]=19.
    _,instance,ev=create_instance(cfg,mats)
    values=ev.replay(instance,[0,1,4,2,0])
    # Start 28800, customer wait to28900 +5, station arrival28935;
    # 6kWh at10kW =2160sec, then2 arcs andcustomer5sec.
    assert values[5]==pytest.approx(31160.)
    assert values[7]==pytest.approx(2160.)
    assert values[6]==pytest.approx(8.)
    assert values[1]==pytest.approx(13.)
    assert values[0]==pytest.approx(cfg["vehicle_fixed_cost_usd"]+13*cfg["distance_unit_cost_usd_per_km"])
    fast=ev.replay(instance,[0,1,5,2,0])
    assert fast[7]==pytest.approx(900.) # 5kWh missing at20kW
    forward=ev.replay(instance,[0,1,2,0]);reverse=ev.replay(instance,[0,2,1,0])
    assert forward[1]!=reverse[1]


def test_hard_window_return_battery_capacity_empty():
    cfg,mats=make(cargo_capacity_cm3=15.)
    cfg["tw_s"][0]=[28800.,28829.]
    _,instance,ev=create_instance(cfg,mats)
    assert not rb.create_route(ev,instance,[1]).feasible
    assert not rb.create_route(ev,instance,[2,3]).feasible
    assert rb.create_route(ev,instance,[]).cost==0
    assert rb.create_route(ev,instance,[4]).cost==0
    cfg["working_end_s"]=28850.
    _,instance,ev=create_instance(cfg,mats)
    assert not rb.create_route(ev,instance,[2]).feasible
    mats["E_kwh"][0,2]=11.
    _,instance,ev=create_instance(cfg,mats)
    assert ev.replay(instance,[0,2,0])[3]>=1.


def test_native_cached_insert_splice_and_all_crossroute_moves():
    cfg,mats=make(n=7)
    rng=np.random.default_rng(1234)
    mats["D_km"]=rng.uniform(.1,20.,mats["D_km"].shape)
    np.fill_diagonal(mats["D_km"],0.)
    _,instance,ev=create_instance(cfg,mats)
    route=rb.create_route(ev,instance,[1,2,3])
    for after in range(4):
        nodes=[1,2,3];nodes.insert(after,4)
        assert rb.evaluate_insertion(ev,instance,route,after,4)==pytest.approx(rb.create_route(ev,instance,nodes).cost)
    # Native pred position is inclusive (the upstream pyi description differs).
    assert rb.evaluate_splice(ev,instance,route,1,3)==pytest.approx(rb.create_route(ev,instance,[1,3]).cost)
    solution=rb.Solution(ev,instance,[rb.create_route(ev,instance,x) for x in ([1,2,3],[4,5],[6,7],[])])
    operators=[rb.operators.SwapOperator_0_1(instance,None),rb.operators.SwapOperator_0_2(instance,None),
        rb.operators.SwapOperator_0_3(instance,None),rb.operators.SwapOperator_1_1(instance,None),
        rb.operators.InterRouteTwoOptOperator(instance,None)]
    count=0
    for op in operators:
        op.prepare_search(solution);prev=None
        for _ in range(1000):
            move=op.find_next_improving_move(ev,solution,prev)
            if move is None: break
            candidate=copy.deepcopy(solution)
            delta=move.get_cost_delta(ev,instance,solution)
            move.apply(instance,candidate)
            assert candidate.cost-solution.cost==pytest.approx(delta,abs=1e-8), (type(op).__name__,str(move),[[x.vertex_id for x in r] for r in candidate])
            for r in candidate:
                path=[x.vertex_id for x in r]
                assert r.cost==pytest.approx(ev.replay(instance,path)[0])
            prev=move;count+=1
        op.finalize_search()
    assert count>10


def test_fpo_fullcharge_different_power_and_long_route():
    cfg,mats=make(n=1,battery_capacity_kwh=5.,demands_cm3=[10.],service_time_s=[0.],
        tw_s=[[28800.,40000.]])
    # D->C uses4, direct return uses4; only station3 has reachable return.
    mats["E_kwh"][:]=4.;np.fill_diagonal(mats["E_kwh"],0.)
    mats["E_kwh"][1,2]=1.;mats["E_kwh"][1,3]=1.
    mats["E_kwh"][2,0]=6.;mats["E_kwh"][3,0]=2.
    _,instance,ev=create_instance(cfg,mats)
    assert not rb.create_route(ev,instance,[1]).feasible
    fpo=rb.FullRechargeFacilityPlacementOptimizer(instance,5.,cfg["distance_unit_cost_usd_per_km"],time.monotonic()+5.)
    path=fpo.optimize([0,1,0])
    assert 3 in path
    assert rb.create_route(ev,instance,path[1:-1]).feasible
    assert ev.replay(instance,path)[7]>0
    cfg,mats=make(n=120,cargo_capacity_cm3=10000.,battery_capacity_kwh=1000.,
        demands_cm3=[1.]*120,service_time_s=[0.]*120,tw_s=[[28800.,86400.]]*120)
    _,instance,ev=create_instance(cfg,mats)
    route=rb.create_route(ev,instance,list(range(1,121)))
    assert len(route)==122 and route.feasible
    assert ev.replay(instance,[x.vertex_id for x in route])[-1]==120


def test_native_budget_interrupts_without_accepting_late_results():
    cfg,mats=make()
    _,instance,ev=create_instance(cfg,mats)
    ev.deadline_s=time.monotonic()-1
    with pytest.raises(RuntimeError,match="online_budget_expired"):
        for _ in range(2000): rb.create_route(ev,instance,[1,2,3])
    fpo=rb.FullRechargeFacilityPlacementOptimizer(instance,10.,.1,time.monotonic()-1)
    with pytest.raises(RuntimeError,match="online_budget_expired"): fpo.optimize([0,1,0])


def test_station_insert_remove_delta_and_local_search_stream():
    cfg,mats=make(n=1,demands_cm3=[1.],service_time_s=[0.],tw_s=[[28800.,86400.]],battery_capacity_kwh=5.)
    mats["E_kwh"][0,1]=4.;mats["E_kwh"][1,0]=4.
    _,instance,ev=create_instance(cfg,mats)
    ev.resource_penalty_factor=100.
    sol=rb.Solution(ev,instance,[rb.create_route(ev,instance,[1])])
    insert=rb.operators.InsertStationOperator(instance)
    insert.prepare_search(sol);previous=None;count=0
    while True:
        move=insert.find_next_improving_move(ev,sol,previous)
        if move is None: break
        candidate=copy.deepcopy(sol)
        delta=move.get_cost_delta(ev,instance,sol)
        move.apply(instance,candidate)
        assert candidate.cost-sol.cost==pytest.approx(delta,abs=1e-8)
        previous=move;count+=1
    insert.finalize_search()
    assert count>0
    # Extra stations with ample energy can be removed; exact delta retains full
    # charging time and directed incoming/outgoing arcs.
    cfg["battery_capacity_kwh"]=100.
    _,instance,ev=create_instance(cfg,mats)
    sol=rb.Solution(ev,instance,[rb.create_route(ev,instance,[2,1,3])])
    remove=rb.operators.RemoveStationOperator(instance)
    remove.prepare_search(sol);previous=None;count=0
    while True:
        move=remove.find_next_improving_move(ev,sol,previous)
        if move is None: break
        candidate=copy.deepcopy(sol)
        delta=move.get_cost_delta(ev,instance,sol)
        move.apply(instance,candidate)
        assert candidate.cost-sol.cost==pytest.approx(delta,abs=1e-8)
        previous=move;count+=1
    assert count>0
    ls=rb.LocalSearch(instance,ev,None,rb.BestImprovementPivotingRule())
    emitted=[]
    ls.solution_callback=lambda x: emitted.append([[node.vertex_id for node in r] for r in x])
    ls.optimize(sol,[remove])
    assert emitted and emitted[-1]==[[0,1,0]]


def test_repair_and_removal_cache_deltas_after_invalidation():
    cfg,mats=make(n=7,demands_cm3=[1.]*7,service_time_s=[0.]*7,tw_s=[[28800.,86400.]]*7)
    mats["D_km"][1,2]=15.;mats["D_km"][2,1]=3.
    _,instance,ev=create_instance(cfg,mats)
    sol=rb.Solution(ev,instance,[rb.create_route(ev,instance,r) for r in ([1,8,2],[3,4],[])])
    cache=rb.InsertionCache(instance);cache.rebuild(ev,sol,[5,6,7])
    def check_insertions():
        for vertex in cache.tracked_vertices:
            for move in cache.get_best_insertions_for_vertex(vertex):
                changed=copy.deepcopy(sol)
                changed.insert_vertex_after(move.after_node,move.vertex_id)
                assert changed.cost-sol.cost==pytest.approx(move.delta_cost,abs=1e-8)
    check_insertions()
    chosen=cache.get_best_insertions_for_vertex(5)[0]
    route=chosen.after_node.route
    cache.stop_tracking(5);sol.insert_vertex_after(chosen.after_node,5)
    cache.invalidate_route(sol[route],route)
    check_insertions()
    removals=rb.RemovalCache(instance);removals.rebuild(ev,sol)
    def check_removals():
        for move in removals.moves_in_order:
            changed=copy.deepcopy(sol);changed.remove_vertex(move.node_location)
            assert changed.cost-sol.cost==pytest.approx(move.delta_cost,abs=1e-8)
    check_removals()
    removed=removals.moves_in_order[0];route=removed.node_location.route
    sol.remove_vertex(removed.node_location);removals.invalidate_route(sol[route],route)
    check_removals()


def test_all_operator_deltas_with_infeasible_resources_and_asymmetry():
    for seed in range(10):
        cfg,mats=make(n=5,demands_cm3=[4.,5.,2.,1.,3.],service_time_s=[5.]*5,
            tw_s=[[28800.+i,28900.+3*i] for i in range(5)],cargo_capacity_cm3=7.,battery_capacity_kwh=4.)
        rng=np.random.default_rng(seed)
        for key,lo,hi in [('D_km',.1,30.),('E_kwh',.1,4.),('T_s',1.,80.)]:
            mats[key]=rng.uniform(lo,hi,(8,8));np.fill_diagonal(mats[key],0.)
        _,instance,ev=create_instance(cfg,mats)
        ev.resource_penalty_factor=20.;ev.time_shift_penalty_factor=4.;ev.overload_penalty_factor=10.
        sol=rb.Solution(ev,instance,[rb.create_route(ev,instance,r) for r in ([1,6,2],[3,4,7,5],[])])
        operators=[rb.operators.SwapOperator_0_1(instance,None),rb.operators.SwapOperator_0_2(instance,None),
            rb.operators.SwapOperator_0_3(instance,None),rb.operators.SwapOperator_1_1(instance,None),
            rb.operators.InterRouteTwoOptOperator(instance,None),rb.operators.InsertStationOperator(instance),
            rb.operators.RemoveStationOperator(instance)]
        for op in operators:
            op.prepare_search(sol);prev=None
            for i in range(1000):
                move=op.find_next_improving_move(ev,sol,prev)
                if move is None: break
                delta=move.get_cost_delta(ev,instance,sol)
                changed=copy.deepcopy(sol);move.apply(instance,changed)
                assert changed.cost-sol.cost==pytest.approx(delta,abs=1e-6),(seed,type(op).__name__)
                prev=move
            else: raise AssertionError("operator enumeration did not terminate")
            op.finalize_search()


def test_native_fpo_matches_exhaustive_station_sequences():
    for seed in range(10):
        cfg,mats=make(n=2,demands_cm3=[1.,1.],service_time_s=[1.,1.],
            tw_s=[[28800.,31000.],[28800.,33000.]],working_end_s=34000.,battery_capacity_kwh=5.)
        rng=np.random.default_rng(seed)
        for key,lo,hi in [('D_km',.1,8.),('E_kwh',.1,5.),('T_s',1.,60.)]:
            mats[key]=rng.uniform(lo,hi,(5,5));np.fill_diagonal(mats[key],0.)
        _,instance,ev=create_instance(cfg,mats)
        options=[[],[3],[4],[3,4],[4,3]]
        feasible=[]
        for a,b,c in itertools.product(options,repeat=3):
            path=[0,*a,1,*b,2,*c,0]
            route=rb.create_route(ev,instance,path[1:-1])
            if route.feasible: feasible.append(route.cost)
        fpo=rb.FullRechargeFacilityPlacementOptimizer(instance,5.,cfg['distance_unit_cost_usd_per_km'],time.monotonic()+10)
        path=fpo.optimize([0,1,2,0])
        result=rb.create_route(ev,instance,path[1:-1])
        if feasible:
            assert result.feasible
            assert result.cost==pytest.approx(min(feasible),abs=1e-8)
        else: assert not result.feasible


def test_canonical_tolerance_is_per_resource_event_not_cumulative():
    cfg,mats=make(n=2,battery_capacity_kwh=5.,demands_cm3=[1.,1.],service_time_s=[0.,0.],
        tw_s=[[28800.,28830.-5e-6],[28800.,28860.-5e-6]])
    mats["E_kwh"][0,1]=5.+5e-6
    mats["E_kwh"][1,2]=0.;mats["E_kwh"][2,0]=0.
    _,instance,ev=create_instance(cfg,mats)
    route=rb.create_route(ev,instance,[1,2])
    assert route.feasible
    assert route.cost==pytest.approx(ev.replay(instance,[0,1,2,0])[0],abs=1e-12)
    # At the station the tolerated deficit must still be replenished.
    values=ev.replay(instance,[0,1,3,0])
    assert values[7]==pytest.approx((6.+5e-6)*3600./10.)
    mats["E_kwh"][0,1]=5.+2e-5
    _,instance,ev=create_instance(cfg,mats)
    assert not rb.create_route(ev,instance,[1,2]).feasible
