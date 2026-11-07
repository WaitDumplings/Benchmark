"""Adapters use the old ICLR loader, objective and independent route verifier."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import time

for _name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
              "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "BLIS_NUM_THREADS"):
    os.environ[_name] = "1"

BASE = Path(__file__).resolve().parents[1]
REPO = BASE.parent / "ICLR/EVRPTW-DB"
META = REPO / "EVRPTW_Benchmark/MetaHeuristics"
sys.path[:0] = [str(REPO / "EVRPTW_Core"),
                str(REPO / "EVRPTW_Dataset_Generator/src"), str(META)]

import numpy as np
from benchmark_common import (IncumbentEventRecorder, charging_profile,
    load_input_task, running_time_energy_matrix_kwh, running_time_matrix_s,
    stable_view_seed, validate_routes)
from evrptw_core.objective import load_objective
from evrptw_core.validation import validate_instance_structure

OBJECTIVE_PATH = REPO / "EVRPTW_Benchmark/Reinforcement_Learning/configs/rivian_energy_vehicle_cost_v2.json"
OBJECTIVE = load_objective(OBJECTIVE_PATH)
TIMING_SCOPE = "adapter_solver_constructor_search_and_blocking_canonical_replay_v1"
PYTHON = Path("/home/exx/anaconda3/envs/maojie/bin/python")


def digest_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def json_text(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json_text(value) + "\n")
    temp.replace(path)


def load_job(job):
    if job.get("objective_config") != OBJECTIVE.to_dict():
        raise ValueError("manifest objective disagrees with the frozen ICLR objective")
    identity = job.get("dataset_identity", {})
    if identity.get("current_input_content_status") != "complete":
        raise ValueError("input content hashes have not been frozen")
    current_files = identity.get("current_files", {})
    if not current_files or set(current_files) != set(identity.get("expected_relative_files", [])):
        raise ValueError("frozen input file list is incomplete")
    identity_digest = hashlib.sha256(json.dumps(current_files, ensure_ascii=True,
        sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    if identity_digest != identity.get("current_input_content_sha256"):
        raise ValueError("frozen input content digest is inconsistent")
    dataset_root = Path(job["stage2_task"]["family_dir"]).parents[2]
    for relative, expected in identity["current_files"].items():
        path = dataset_root / relative
        if digest_file(path) != expected:
            raise ValueError(f"input changed since cohort freeze: {path}")
    if digest_file(job["stage2_task"]["index_path"]) != identity["index_sha256"]:
        raise ValueError("cohort index changed since freeze")
    instance, info = load_input_task({"input_kind": "stage2",
        "stage2_task": job["stage2_task"], "objective_config": OBJECTIVE.to_dict()})
    validation = validate_instance_structure(instance)
    if not validation.success:
        raise ValueError(validation.errors)
    return instance, info


def export_instance(instance, directory):
    """Serialize without changing the original float32 directed matrix values.

    Caller starts its online clock BEFORE this adapter transport/preparation.
    Source file loading is the only excluded preparation, as in the old runner.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    power, factor, source = charging_profile(instance)
    arrays = dict(T_s=running_time_matrix_s(instance),
                  D_km=np.asarray(instance.distance_matrix_km),
                  E_kwh=running_time_energy_matrix_kwh(instance))
    shape = (instance.num_terminals, instance.num_terminals)
    for name, matrix in arrays.items():
        if matrix.shape != shape or not np.isfinite(matrix).all() or np.any(matrix < 0):
            raise ValueError(f"invalid canonical {name} matrix")
    if instance.metadata.get("objective_distance_source") != "running_time_path_distance_km":
        raise ValueError("cost instance must be mapped to D_time by the old loader")
    np.savez(directory / "matrices.npz", **arrays)
    payload = {
        "schema": "iclr_external_solver_instance_v1",
        "instance_id": instance.instance_id,
        "num_customers": instance.num_customers,
        "num_charging_stations": instance.num_charging_stations,
        "working_start_s": instance.working_start_s,
        "working_end_s": instance.working_end_s,
        "battery_capacity_kwh": float(instance.vehicle["battery_capacity_kwh"]),
        "cargo_capacity_cm3": float(instance.vehicle["cargo_capacity_cm3"]),
        "vehicle_fixed_cost_usd": OBJECTIVE.vehicle_unit_cost,
        "distance_unit_cost_usd_per_km": OBJECTIVE.distance_unit_cost,
        "coordinates": np.vstack((instance.depot.reshape(1, 2), instance.customers,
                                    instance.charging_stations)).tolist(),
        "demands_cm3": instance.demands_cm3.tolist(),
        "service_time_s": instance.service_time_s.tolist(),
        "tw_s": instance.tw_s.tolist(),
        "charging_power_kw": power.tolist(),
        "charging_power_derating_factor": float(factor),
        "effective_charging_power_kw": (power * factor).tolist(),
        "charging_power_source": source,
        "matrices_file": "matrices.npz",
        "objective_distance_source": "running_time_path_distance_km",
        "node_order": "depot, customers, stations",
        "charging_model": "mandatory_full_charge_no_station_window_or_service",
        "matrix_content_sha256": {name: hashlib.sha256(
            np.ascontiguousarray(arr).tobytes()).hexdigest() for name, arr in arrays.items()},
    }
    atomic_json(directory / "instance.json", payload)
    return directory / "instance.json"


class CandidateReplay:
    """One candidate stream, no score for invalid or late complete candidates."""
    def __init__(self, instance, checkpoints, budget):
        self.instance = instance
        self.budget = budget
        self.recorder = IncumbentEventRecorder(checkpoints, budget)
        self.previous_time = -1.0
        self.count = 0
        self.invalid_count = 0
        self.late_count = 0
        self.verification_s = 0.0
        self.audit_by_routes = {}
        self.native_feasible_disagreements = 0
        self.native_objective_disagreements = 0

    def observe(self, event, received_elapsed):
        self.count += 1
        report = {"candidate_number": self.count, "received_elapsed_s": received_elapsed,
                  "event": event, "eligible": False, "validation": None}
        try:
            stamp = float(event["elapsed_s"])
            if not np.isfinite(stamp) or stamp < 0 or stamp < self.previous_time:
                raise ValueError("nonmonotonic/invalid candidate generation timestamp")
            if stamp > received_elapsed + 0.01:
                raise ValueError("candidate timestamp is in the future")
            self.previous_time = stamp
            if stamp > self.budget:
                self.late_count += 1
                report["rejection"] = "generated_after_budget"
                return report
            routes = event["routes"]
            # Do not turn floats, strings or boolean node IDs into valid integers.
            if not isinstance(routes, list) or any(not isinstance(r, list) for r in routes):
                raise ValueError("routes must be nested lists")
            if any(type(v) is not int for r in routes for v in r):
                raise ValueError("node IDs must be integers")
            before = time.monotonic()
            audit = validate_routes(self.instance, routes, tolerance=1e-5)
            elapsed = time.monotonic() - before
            self.verification_s += elapsed
            report.update(validation=audit, verification_time_s=elapsed)
            if not audit["passed"]:
                self.invalid_count += 1
                if event.get("internal_feasible", True):
                    self.native_feasible_disagreements += 1
                report["rejection"] = "canonical_replay_failed"
                return report
            fields = OBJECTIVE.fields(float(audit["objective_distance_km"]), len(routes))
            report.update(eligible=True, canonical_objective=fields)
            internal = event.get("internal_objective")
            try:
                if internal is not None and np.isfinite(float(internal)):
                    report["internal_minus_canonical_cost_usd"] = float(internal) - fields["objective_value"]
                    if event.get("internal_feasible", True) and abs(report["internal_minus_canonical_cost_usd"]) > 1e-5:
                        self.native_objective_disagreements += 1
            except (TypeError, ValueError):
                report["internal_objective_diagnostic_error"] = "non-numeric internal score"
            self.recorder.observe(stamp, fields["objective_value"], routes,
                objective_distance_km=audit["objective_distance_km"], objective_fields=fields)
            if self.recorder.best_event and self.recorder.best_event["routes"] == routes:
                self.audit_by_routes = audit
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            self.invalid_count += 1
            report["rejection"] = f"invalid_event: {exc}"
        return report
