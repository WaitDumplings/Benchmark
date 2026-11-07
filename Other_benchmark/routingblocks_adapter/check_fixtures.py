"""Compare native route resources to fixtures from the existing ICLR verifier."""
import json
from collections import Counter
from pathlib import Path
import routingblocks as rb
from run import load_instance,create_instance

base=Path(__file__).resolve().parent
fixtures=base.parent/"tests/fixtures"
reports=[]
for expected_path in sorted(fixtures.glob("*/expected.json")):
    cfg,mats=load_instance(expected_path.parent)
    _,instance,evaluation=create_instance(cfg,mats)
    expected=json.loads(expected_path.read_text())
    for case in expected["cases"]:
        paths=case["routes"]
        resource=[]
        n=cfg["num_customers"]
        counts=Counter(v for r in paths for v in r if 1<=v<=n)
        topology=counts==Counter(range(1,n+1)) and all(len(r)>=3 and r[0]==r[-1]==0
            and 0 not in r[1:-1] and any(1<=v<=n for v in r) for r in paths)
        for r in paths:
            resource.append(evaluation.replay(instance,r))
        native_pass=topology and all(rb.create_route(evaluation,instance,r[1:-1]).feasible for r in paths)
        canonical=case["canonical_verifier"]
        assert native_pass==canonical["passed"],(expected_path.parent.name,case["name"],resource,canonical)
        result=dict(fixture=expected_path.parent.name,case=case["name"],passed=True,
            native_solution_feasible=native_pass,canonical_feasible=canonical["passed"],
            native_route_resources=resource)
        if native_pass:
            distance=sum(x[1] for x in resource)
            charging=sum(x[7] for x in resource)
            cost=sum(x[0] for x in resource)
            assert abs(distance-canonical["objective_distance_km"])<1e-8
            assert abs(charging-canonical["total_charging_time_s"])<1e-5
            assert abs(cost-case["canonical_objective"]["objective_value"])<1e-8
            result.update(canonical_cost_usd=cost,absolute_cost_error=abs(cost-case["canonical_objective"]["objective_value"]))
        reports.append(result)
output=base/"evidence/canonical_fixture_comparison.json"
output.write_text(json.dumps(dict(passed=True,cases=len(reports),results=reports),indent=2)+"\n")
print(json.dumps(dict(passed=True,cases=len(reports),output=str(output))))
