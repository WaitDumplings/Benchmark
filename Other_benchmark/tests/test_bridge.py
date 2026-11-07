"""Independent contract tests using the existing ICLR verifier, never a new scorer.

Run with the ICLR maojie Python. --export-fixtures writes transport-format cases
and canonical replay results for the two native adapter evaluator probes.
"""
from __future__ import annotations

import io
import json
import multiprocessing
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ProcessPoolExecutor
from unittest import mock

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / "benchmark_bridge"))
import common
import numpy as np
from evrptw_core.schema import EVRPTWInstance
from evrptw_core.validation import validate_instance_structure

import run as runner


def make_instance(name="toy", n=2, m=0, *, distance=None, travel=None, energy=None,
                  start=0, end=100000, demands=None, capacity=1000., battery=1000.,
                  service=None, tw=None, powers=None, derating=0.9):
    """Small canonical fixtures; matrices remain directed and energy is explicit."""
    count = 1 + n + m
    distance = np.ones((count, count), dtype=np.float32) if distance is None else np.array(distance, dtype=np.float32)
    travel = np.ones((count, count), dtype=np.float32) if travel is None else np.array(travel, dtype=np.float32)
    energy = distance * common.OBJECTIVE.consumption_kwh_per_km if energy is None else np.array(energy, dtype=np.float32)
    for matrix in (distance, travel, energy):
        np.fill_diagonal(matrix, 0.)
    payload = {
        "instance_id": name, "region_id": "synthetic_contract", "day_type": "weekday",
        "working_start_s": start, "working_end_s": end,
        "depot": [0., 0.], "customers": np.zeros((n, 2), dtype=np.float32),
        "charging_stations": np.zeros((m, 2), dtype=np.float32),
        "distance_matrix_km": distance, "running_time_path_distance_km": distance,
        "running_time_shortest_matrix_s": travel, "raw_travel_time_matrix_s": travel,
        "running_time_path_energy_kwh": energy, "energy_matrix_kwh": energy,
        "demands_cm3": np.ones(n) if demands is None else demands,
        "package_counts": np.full(n, 99),  # Deliberately unrelated to volume.
        "service_time_s": np.zeros(n) if service is None else service,
        "tw_s": np.tile([start, end], (n, 1)) if tw is None else tw,
        "cs_time_to_depot_s": np.ones(m),
        "vehicle": {"cargo_capacity_cm3": capacity, "battery_capacity_kwh": battery,
                    "specific_energy_consumption_kwh_per_km": common.OBJECTIVE.consumption_kwh_per_km,
                    "unlimited_fleet": True},
        "charging_power_kw": np.full(m, 30.) if powers is None else powers,
        "charging_policy": {"charging_power_derating_factor": derating,
                            "initial_state_of_charge_fraction": 1.0},
        "metadata": {"objective_distance_source": "running_time_path_distance_km"},
    }
    instance = EVRPTWInstance.from_dict(payload)
    result = validate_instance_structure(instance)
    if not result.success:
        raise ValueError(result.errors)
    return instance


def fixture_cases():
    cases = []
    directed = make_instance("directed_reverse", distance=[[0, 1, 9], [8, 0, 2], [3, 7, 0]])
    cases.append((directed, {"forward": [[0, 1, 2, 0]], "reverse": [[0, 2, 1, 0]]}))
    for end in (140, 139):
        waiting = make_instance(f"service_start_wait_return_{end}", n=1, start=100, end=end,
                                travel=[[0, 5], [5, 0]], service=[15], tw=[[120, 120]])
        cases.append((waiting, {"wait_then_service": [[0, 1, 0]]}))
    missed = make_instance("missed_service_start", n=1, start=100, end=200,
                           travel=[[0, 25], [5, 0]], service=[15], tw=[[120, 120]])
    cases.append((missed, {"late_arrival": [[0, 1, 0]]}))
    energy = np.ones((4, 4), dtype=np.float32)
    energy[0, 2] = energy[0, 3] = 6
    energy[2, 1] = energy[3, 1] = energy[1, 0] = 3
    distance = energy / common.OBJECTIVE.consumption_kwh_per_km
    travel = np.full((4, 4), 10, dtype=np.float32)
    for end in (2000, 1000):
        charging = make_instance(f"full_charge_mixed_power_{end}", n=1, m=2,
            distance=distance, travel=travel, energy=energy, battery=10.,
            powers=[30., 6.], derating=0.5, end=end)
        cases.append((charging, {"fast_station": [[0, 2, 1, 0]], "slow_station": [[0, 3, 1, 0]]}))
    volume = make_instance("volume_and_exactly_once", n=2, m=1, demands=[6, 6], capacity=10)
    cases.append((volume, {"two_routes": [[0, 1, 0], [0, 2, 0]],
        "over_capacity": [[0, 1, 2, 0]], "missing": [[0, 1, 0]],
        "duplicate": [[0, 1, 0], [0, 2, 1, 0]],
        "empty_extra": [[0, 1, 0], [0, 2, 0], [0, 0]],
        "station_only_extra": [[0, 1, 0], [0, 2, 0], [0, 3, 0]],
        "internal_depot": [[0, 1, 0, 2, 0]],
    }))
    long = make_instance("route_over_100_nodes", n=130)
    cases.append((long, {"one_long_route": [[0, *range(1, 131), 0]]}))
    weighted = make_instance("weighted_not_lexicographic", distance=[[0, 1, 1], [1, 0, 4000], [1, 4000, 0]], battery=5000.)
    cases.append((weighted, {"one_vehicle_costlier": [[0, 1, 2, 0]],
                             "two_vehicles_cheaper": [[0, 1, 0], [0, 2, 0]]}))
    fixed = make_instance("weighted_not_distance_only", distance=[[0, 1, 1], [1, 0, 100], [1, 100, 0]])
    cases.append((fixed, {"one_vehicle_cheaper": [[0, 1, 2, 0]],
                          "two_vehicles_shorter": [[0, 1, 0], [0, 2, 0]]}))
    return cases


def export_fixtures(directory=BASE / "tests/fixtures"):
    directory = Path(directory)
    index = []
    for instance, solutions in fixture_cases():
        folder = directory / instance.instance_id
        common.export_instance(instance, folder)
        rows = []
        for name, routes in solutions.items():
            audit = common.validate_routes(instance, routes)
            fields = common.OBJECTIVE.fields(audit["objective_distance_km"], len(routes)) if audit["passed"] else None
            rows.append({"name": name, "routes": routes, "canonical_verifier": audit,
                         "canonical_objective": fields})
        common.atomic_json(folder / "expected.json", {
            "schema": "iclr_native_evaluator_fixture_v1", "instance_id": instance.instance_id,
            "verifier_source": str(common.META / "benchmark_common.py"),
            "verifier_sha256": common.digest_file(common.META / "benchmark_common.py"),
            "objective_config": common.OBJECTIVE.to_dict(), "cases": rows})
        index.append({"instance_id": instance.instance_id, "directory": str(folder), "case_count": len(rows)})
    common.atomic_json(directory / "index.json", index)
    print(json.dumps({"fixtures": len(index), "directory": str(directory)}))


class CanonicalContractTests(unittest.TestCase):
    def test_directional_reversal_is_not_symmetric(self):
        instance, routes = fixture_cases()[0]
        forward = common.validate_routes(instance, routes["forward"])
        reverse = common.validate_routes(instance, routes["reverse"])
        self.assertTrue(forward["passed"] and reverse["passed"])
        self.assertEqual(forward["objective_distance_km"], 6)
        self.assertEqual(reverse["objective_distance_km"], 24)

    def test_service_start_window_waiting_and_return_deadline(self):
        cases = {i.instance_id: (i, r) for i, r in fixture_cases()}
        for key, passed in [("service_start_wait_return_140", True),
                            ("service_start_wait_return_139", False), ("missed_service_start", False)]:
            with self.subTest(key=key):
                instance, solutions = cases[key]
                self.assertEqual(common.validate_routes(instance, next(iter(solutions.values())))["passed"], passed)

    def test_full_charge_station_specific_effective_power(self):
        cases = {i.instance_id: (i, r) for i, r in fixture_cases()}
        instance, routes = cases["full_charge_mixed_power_2000"]
        fast = common.validate_routes(instance, routes["fast_station"])
        slow = common.validate_routes(instance, routes["slow_station"])
        self.assertTrue(fast["passed"])
        self.assertFalse(slow["passed"])
        self.assertAlmostEqual(fast["total_charging_time_s"], 1440.)
        self.assertAlmostEqual(slow["total_charging_time_s"], 7200.)
        tight, routes = cases["full_charge_mixed_power_1000"]
        self.assertFalse(common.validate_routes(tight, routes["fast_station"])["passed"],
                         "Partial recharge would pass this horizon; mandatory full must fail")

    def test_volume_exact_once_and_empty_routes(self):
        instance, routes = next(x for x in fixture_cases() if x[0].instance_id == "volume_and_exactly_once")
        for name, solution in routes.items():
            with self.subTest(case=name):
                self.assertEqual(common.validate_routes(instance, solution)["passed"], name == "two_routes")

    def test_long_route_is_not_truncated_at_100(self):
        instance = make_instance(n=130)
        audit = common.validate_routes(instance, [[0, *range(1, 131), 0]])
        self.assertTrue(audit["passed"], audit["violations"])
        self.assertEqual(audit["objective_distance_km"], 131)

    def test_weighted_cost_can_prefer_more_vehicles(self):
        instance, routes = next(x for x in fixture_cases() if x[0].instance_id == "weighted_not_lexicographic")
        replay = common.CandidateReplay(instance, [1, 2], 2)
        replay.observe({"elapsed_s": 0.1, "routes": routes["one_vehicle_costlier"]}, 0.1)
        replay.observe({"elapsed_s": 0.2, "routes": routes["two_vehicles_cheaper"]}, 0.2)
        self.assertEqual(replay.recorder.best_event["vehicle_count"], 2)

    def test_fixed_vehicle_fee_can_outweigh_distance(self):
        instance, routes = next(x for x in fixture_cases() if x[0].instance_id == "weighted_not_distance_only")
        replay = common.CandidateReplay(instance, [1, 2], 2)
        replay.observe({"elapsed_s": 0.1, "routes": routes["two_vehicles_shorter"]}, 0.1)
        replay.observe({"elapsed_s": 0.2, "routes": routes["one_vehicle_cheaper"]}, 0.2)
        self.assertEqual(replay.recorder.best_event["vehicle_count"], 1)

    def test_transport_preserves_all_directed_matrices_and_power(self):
        instance, _ = next(x for x in fixture_cases() if x[0].instance_id == "full_charge_mixed_power_2000")
        with tempfile.TemporaryDirectory() as tmp:
            path = common.export_instance(instance, tmp)
            payload = json.loads(path.read_text())
            with np.load(Path(tmp) / payload["matrices_file"]) as matrices:
                np.testing.assert_array_equal(matrices["D_km"], instance.distance_matrix_km)
                np.testing.assert_array_equal(matrices["T_s"], common.running_time_matrix_s(instance))
                np.testing.assert_array_equal(matrices["E_kwh"], common.running_time_energy_matrix_kwh(instance))
            self.assertEqual(payload["effective_charging_power_kw"], [15., 3.])


class CandidateReplayTests(unittest.TestCase):
    def test_late_candidate_does_not_backfill_earlier_checkpoint(self):
        instance = make_instance()
        replay = common.CandidateReplay(instance, [0.1, 0.5, 1], 1)
        old = [[0, 1, 0], [0, 2, 0]]
        improved = [[0, 1, 2, 0]]
        replay.observe({"elapsed_s": 0.05, "routes": old}, 0.06)
        replay.observe({"elapsed_s": 0.2, "routes": improved}, 0.22)
        late = replay.observe({"elapsed_s": 1.1, "routes": improved}, 1.2)
        self.assertFalse(late["eligible"])
        self.assertEqual(replay.late_count, 1)
        snapshots = replay.recorder.snapshots(runtime_s=1.2, natural_completion=False, final_status="COMPLETED_WITH_INCUMBENT")
        self.assertEqual([s["vehicle_count"] for s in snapshots], [2, 1, 1])
        self.assertEqual([s["incumbent_event_time_s"] for s in snapshots], [0.05, 0.2, 0.2])

    def test_predeadline_generation_may_be_verified_after_deadline(self):
        replay = common.CandidateReplay(make_instance(n=1), [0.1], 0.1)
        report = replay.observe({"elapsed_s": 0.05, "routes": [[0, 1, 0]]}, 0.2)
        self.assertTrue(report["eligible"])
        self.assertEqual(replay.recorder.best_event["event_time_s"], 0.05)

    def test_rejects_invalid_nodes_future_and_nonmonotonic_stamps(self):
        for event in [
            {"elapsed_s": 0.1, "routes": [[0, True, 0]]},
            {"elapsed_s": 0.1, "routes": [[0, 1.0, 0]]},
            {"elapsed_s": 0.5, "routes": [[0, 1, 0]]},
            {"elapsed_s": float("nan"), "routes": [[0, 1, 0]]},
        ]:
            with self.subTest(event=event):
                replay = common.CandidateReplay(make_instance(n=1), [1], 1)
                self.assertFalse(replay.observe(event, 0.2)["eligible"])
                self.assertIsNone(replay.recorder.best_event)
        replay = common.CandidateReplay(make_instance(n=1), [1], 1)
        replay.observe({"elapsed_s": 0.2, "routes": [[0, 1, 0]]}, 0.2)
        self.assertFalse(replay.observe({"elapsed_s": 0.1, "routes": [[0, 1, 0]]}, 0.3)["eligible"])

    def test_malformed_internal_cost_cannot_invalidate_canonical_route(self):
        replay = common.CandidateReplay(make_instance(n=1), [1], 1)
        report = replay.observe({"elapsed_s": 0.1, "routes": [[0, 1, 0]], "internal_objective": "bad"}, 0.1)
        self.assertTrue(report["eligible"])
        self.assertIsNotNone(replay.recorder.best_event)
        self.assertEqual(replay.invalid_count, 0)

    def test_equal_timestamp_worse_route_does_not_replace_best_audit(self):
        replay = common.CandidateReplay(make_instance(n=1, m=1), [1], 1)
        replay.observe({"elapsed_s": 0.1, "routes": [[0, 1, 0]]}, 0.1)
        replay.observe({"elapsed_s": 0.1, "routes": [[0, 2, 1, 0]]}, 0.1)
        self.assertEqual(replay.recorder.best_event["routes"], [[0, 1, 0]])
        self.assertEqual(replay.audit_by_routes["charging_visit_count"], 0)


class ManifestAndCliTests(unittest.TestCase):
    def _job(self):
        path = BASE / "manifests/iclr_t1_v1/smoke.jsonl"
        return json.loads(path.read_text().splitlines()[0])

    def test_pending_hashes_are_rejected_before_loading(self):
        job = self._job()
        job["dataset_identity"]["current_input_content_status"] = "pending"
        with mock.patch.object(common, "load_input_task") as load:
            with self.assertRaises(ValueError):
                common.load_job(job)
            load.assert_not_called()

    def test_changed_objective_is_rejected_before_loading(self):
        job = self._job()
        job["objective_config"]["vehicle_fixed_cost_usd"] = 1.
        with mock.patch.object(common, "load_input_task") as load:
            with self.assertRaises(ValueError):
                common.load_job(job)
            load.assert_not_called()

    def test_incomplete_file_hash_mapping_is_rejected(self):
        job = self._job()
        job["dataset_identity"]["current_files"] = {}
        # Mock data loading and file hashing so this is a manifest contract test.
        with mock.patch.object(common, "load_input_task", return_value=(make_instance(), {})) as load, \
             mock.patch.object(common, "digest_file", return_value=job["dataset_identity"]["index_sha256"]):
            with self.assertRaises(ValueError):
                common.load_job(job)
            load.assert_not_called()

    def test_nan_budget_and_more_than_30_workers_are_rejected(self):
        for extra in [("--budget", "nan"), ("--checkpoints", "nan"), ("--workers", "31")]:
            with self.subTest(args=extra), mock.patch.object(sys, "argv", [
                "run.py", "--manifest", "/nonexistent.jsonl", "--output", "/tmp/unused-bridge-test", *extra
            ]), mock.patch.object(sys, "stderr", io.StringIO()):
                with self.assertRaises(SystemExit) as exc:
                    runner.main()
                self.assertEqual(exc.exception.code, 2)


def process_running(pid):
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        return stat.rsplit(")", 1)[1].split()[0] != "Z"
    except FileNotFoundError:
        return False


def worker_probe():
    time.sleep(0.1)
    return {"pid": os.getpid(), "affinity": sorted(os.sched_getaffinity(0)),
            "thread_limits": {key: os.environ.get(key) for key in
                ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS")}}


class CpuPoolTests(unittest.TestCase):
    def test_spawn_workers_are_distinct_and_affined_to_unique_physical_cores(self):
        cpus = runner.hardware_snapshot()["physical_cpu_representatives"][:3]
        if len(cpus) < 2:
            self.skipTest("Independent multiprocess check requires at least two available physical cores")
        context = multiprocessing.get_context("spawn")
        queue = context.Queue()
        for cpu in cpus:
            queue.put(cpu)
        try:
            with ProcessPoolExecutor(max_workers=len(cpus), mp_context=context,
                    initializer=runner.initialize_worker, initargs=(queue,)) as pool:
                futures = [pool.submit(worker_probe) for _ in range(2 * len(cpus))]
                results = [future.result() for future in futures]
        finally:
            queue.close()
            queue.join_thread()
        workers = {r["pid"]: r for r in results}
        self.assertEqual(len(workers), len(cpus))
        self.assertNotIn(os.getpid(), workers)
        self.assertEqual({tuple(r["affinity"]) for r in workers.values()}, {(cpu,) for cpu in cpus})
        self.assertTrue(all(value == "1" for r in workers.values() for value in r["thread_limits"].values()))


class SupervisorTests(unittest.TestCase):
    def _run(self, code, *, budget=0.5, instance=None, delay_verify=0.):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        output = Path(temp.name)
        instance = instance or make_instance(n=1)
        replay = common.CandidateReplay(instance, [budget], budget)
        script = output / "fake_adapter.py"
        script.write_text(code)
        start = time.monotonic()
        argv = [sys.executable, str(script), str(output), str(start)]
        if delay_verify:
            original = common.validate_routes
            def slow(*args, **kwargs):
                time.sleep(delay_verify)
                return original(*args, **kwargs)
            with mock.patch.object(common, "validate_routes", slow):
                result = runner.supervise(argv, output, start, budget, replay)
        else:
            result = runner.supervise(argv, output, start, budget, replay)
        return output, replay, result

    def test_complete_jsonl_candidate_survives_native_completion(self):
        output, replay, result = self._run("""
import json,sys,time
from pathlib import Path
p=Path(sys.argv[1]); start=float(sys.argv[2])
with (p/'events.jsonl').open('a') as f:
 f.write(json.dumps({'elapsed_s':time.monotonic()-start,'routes':[[0,1,0]]})+'\\n');f.flush()
time.sleep(0.03)
""")
        self.assertEqual(result["exit_code"], 0)
        self.assertIsNotNone(replay.recorder.best_event)
        self.assertEqual(json.loads((output / "candidate.ack").read_text()), 1)

    def test_incomplete_json_line_never_becomes_incumbent(self):
        _, replay, _ = self._run("""
import sys
from pathlib import Path
(Path(sys.argv[1])/'events.jsonl').write_text('{"elapsed_s":0.01,"routes":[[0,1,0]]}')
""")
        self.assertIsNone(replay.recorder.best_event)
        self.assertGreaterEqual(replay.invalid_count, 1)

    def test_nonfinite_json_event_is_logged_and_next_valid_event_is_kept(self):
        output, replay, result = self._run("""
import json,sys,time
from pathlib import Path
p=Path(sys.argv[1]);start=float(sys.argv[2])
with (p/'events.jsonl').open('a') as f:
 f.write('{"elapsed_s":NaN,"routes":[[0,1,0]]}\\n');f.flush()
 f.write(json.dumps({'elapsed_s':time.monotonic()-start,'routes':[[0,1,0]]})+'\\n');f.flush()
time.sleep(0.03)
""")
        self.assertEqual(result["exit_code"], 0)
        self.assertIsNotNone(replay.recorder.best_event)
        self.assertGreaterEqual(replay.invalid_count, 1)
        audits = [json.loads(x) for x in (output / "candidate_audits.jsonl").read_text().splitlines()]
        self.assertEqual(len(audits), 2)
        self.assertFalse(audits[0]["eligible"])
        self.assertTrue(audits[1]["eligible"])

    def test_ack_wait_and_canonical_replay_consume_online_budget(self):
        output, replay, result = self._run("""
import json,sys,time
from pathlib import Path
p=Path(sys.argv[1]); start=float(sys.argv[2])
for index,routes in enumerate(([[0,1,0],[0,2,0]],[[0,1,2,0]]),1):
 with (p/'events.jsonl').open('a') as f:
  f.write(json.dumps({'elapsed_s':time.monotonic()-start,'routes':routes})+'\\n');f.flush()
 while json.loads((p/'candidate.ack').read_text())<index:time.sleep(0.005)
""", budget=1.5, instance=make_instance(), delay_verify=0.2)
        events = [json.loads(x) for x in (output / "events.jsonl").read_text().splitlines()]
        self.assertEqual(len(events), 2)
        self.assertGreaterEqual(events[1]["elapsed_s"] - events[0]["elapsed_s"], 0.18)
        self.assertGreaterEqual(replay.verification_s, 0.39)
        self.assertGreaterEqual(result["runtime_s"], 0.39)
        self.assertEqual(replay.recorder.best_event["vehicle_count"], 1)

    def test_hard_deadline_kills_native_child_while_parent_busy(self):
        output, _, result = self._run("""
import subprocess,sys,time
from pathlib import Path
p=Path(sys.argv[1])
child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])
(p/'child.pid').write_text(str(child.pid))
time.sleep(30)
""", budget=0.25)
        pid = int((output / "child.pid").read_text())
        self.addCleanup(lambda: os.kill(pid, signal.SIGKILL) if process_running(pid) else None)
        self.assertTrue(result["terminated_by_time_limit"])
        self.assertLess(result["runtime_s"], 1.25)
        deadline = time.monotonic() + 0.5
        while process_running(pid) and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(process_running(pid))

    def test_watchdog_interrupts_native_work_during_slow_verification(self):
        output, replay, result = self._run("""
import json,sys,time
from pathlib import Path
p=Path(sys.argv[1]);start=float(sys.argv[2])
with (p/'events.jsonl').open('a') as f:
 f.write(json.dumps({'elapsed_s':time.monotonic()-start,'routes':[[0,1,0]]})+'\\n');f.flush()
time.sleep(0.3)
(p/'survived_deadline').write_text('native remained alive')
time.sleep(30)
""", budget=0.2, delay_verify=0.5)
        self.assertTrue(result["terminated_by_time_limit"])
        self.assertFalse((output / "survived_deadline").exists())
        self.assertGreaterEqual(result["runtime_s"], 0.5)
        self.assertIsNotNone(replay.recorder.best_event)
        self.assertLess(replay.recorder.best_event["event_time_s"], 0.2)

    def test_export_time_budget_exhaustion_is_not_solver_error(self):
        job = {"stage2_task": {"view_id": "synthetic_export_timeout", "family_id": "synthetic",
                "city_slug": "toy", "split_id": "test", "track_id": "test1_new_seed", "scale_id": "Cus100"},
                "solver_seed": 1234}
        info = dict(job["stage2_task"], file="synthetic")
        instance = make_instance(n=1)
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(runner, "load_job", return_value=(instance, info)), \
             mock.patch.object(runner, "export_instance", side_effect=lambda *args: time.sleep(0.03)):
            result = runner.solve(job, "hma", tmp, 0.01, [0.01],
                                  {"adaptation_sha256": "test-only"}, "test-only")
        row = result["summary_row"]
        self.assertEqual(row["status"], "UNFINISHED_NO_INCUMBENT", row["errors"])
        self.assertEqual(row["early_stop_reason"], "adapter_budget_exhausted")
        self.assertTrue(row["terminated_by_time_limit"])
        self.assertFalse(row["has_incumbent"])

    def test_native_child_cannot_escape_by_wrapper_early_exit(self):
        output, _, result = self._run("""
import subprocess,sys
from pathlib import Path
p=Path(sys.argv[1])
child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])
(p/'child.pid').write_text(str(child.pid))
""", budget=0.3)
        pid = int((output / "child.pid").read_text())
        self.addCleanup(lambda: os.kill(pid, signal.SIGKILL) if process_running(pid) else None)
        deadline = time.monotonic() + 0.5
        while process_running(pid) and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(process_running(pid), "native child survived the adapter and deadline controller")
        self.assertNotEqual(result["early_stop_reason"], "native_completion",
                            "orphaned running native work must not be reported as natural completion")

    def test_final_flush_between_eof_and_process_exit_is_not_lost(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            replay = common.CandidateReplay(make_instance(n=1), [1.], 1.)
            class ExitOnPoll:
                pid = 999999999
                returncode = 0
                emitted = False
                def poll(self):
                    if not self.emitted:
                        with (output / "events.jsonl").open("a") as f:
                            f.write(json.dumps({"elapsed_s": 0., "routes": [[0, 1, 0]]}) + "\n")
                        self.emitted = True
                    return 0
                def wait(self):
                    return 0
            with mock.patch.object(runner.subprocess, "Popen", return_value=ExitOnPoll()):
                result = runner.supervise(["mock-process"], output, time.monotonic(), 1., replay)
            self.assertEqual(result["exit_code"], 0)
            self.assertIsNotNone(replay.recorder.best_event,
                                 "The complete final line appeared between initial EOF and poll(exit)")


if __name__ == "__main__":
    if "--export-fixtures" in sys.argv:
        export_fixtures()
    else:
        unittest.main(verbosity=2)
