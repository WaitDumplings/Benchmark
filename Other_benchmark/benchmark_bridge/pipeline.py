#!/usr/bin/env python3
"""Durable smoke -> resource ramp -> fixed panel -> formal T1 job queue.

Stages have separate result directories. Diagnostic budgets never satisfy formal
jobs. Only correctness/resource checks gate progression; relative solver quality
does not. User authorization limits the shared queue to 30 physical cores.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import subprocess
import sys

from common import BASE, PYTHON, REPO, atomic_json, digest_file, json_text
from resources import hardware_snapshot

ENTRY = REPO / "EVRPTW_Benchmark/MetaHeuristics/AuthorSolvers/run_author_solvers.py"
PHASES = ("smoke", "resource_8", "resource_16", "resource_30", "panel", "formal")


def now():
    return datetime.now(timezone.utc).isoformat()


def prepare_plan(manifests, output):
    diagnostic = output / "diagnostic_manifests"
    diagnostic.mkdir(parents=True, exist_ok=True)
    panel = [json.loads(line) for line in (manifests / "panel.jsonl").read_text().splitlines()]
    large = [j for j in panel if j["stage2_task"]["customer_count"] == 1000]
    phases = []
    for name in PHASES:
        if name.startswith("resource_"):
            workers = int(name.split("_")[-1])
            selected = large[:workers // 2]
            path = diagnostic / f"{name}.jsonl"
            text = "".join(json_text({**j, "phase": name,
                "resource_diagnostic_only": True}) + "\n" for j in selected)
            if path.exists() and path.read_text() != text:
                raise ValueError(f"existing diagnostic manifest differs: {path}")
            path.write_text(text)
            budget, checkpoints = 60, "30,60"
        else:
            path = manifests / (name + ".jsonl")
            workers = 8 if name == "smoke" else 30
            budget = 60 if name == "smoke" else 1800
            checkpoints = "10,30,60" if name == "smoke" else "60,300,900,1800"
        phases.append({"name": name, "manifest": str(path.resolve()),
            "manifest_sha256": digest_file(path), "target_workers": workers,
            "budget_s": budget, "checkpoints": checkpoints,
            "job_count": 2 * sum(bool(s.strip()) for s in path.read_text().splitlines()),
            "output": str((output / name).resolve())})
    return phases


def stage_assessment(directory, expected_jobs):
    directory = Path(directory)
    state = json.loads((directory / "status.json").read_text())
    import csv
    rows = []
    for solver in ("hma", "routingblocks"):
        with (directory / solver / "summary.csv").open() as f:
            rows.extend({**r, "solver": solver} for r in csv.DictReader(f))
    failures = []
    max_rss = 0
    by_solver = {s: {"jobs": 0, "feasible": 0, "errors": 0, "peak_job_rss_bytes": 0}
                 for s in ("hma", "routingblocks")}
    if state["state"] != "complete" or len(rows) != expected_jobs:
        failures.append("stage did not complete all declared jobs")
    for row in rows:
        key = row["instance_id"] + "/" + row["solver"]
        stats = by_solver[row["solver"]]
        stats["jobs"] += 1
        stats["feasible"] += int(str(row["feasible"]).lower() == "true")
        if row["status"].startswith("ERROR"):
            stats["errors"] += 1
            failures.append(f"{key}: adapter error requires review")
        for metric in ("native_feasible_disagreement_count", "native_objective_disagreement_count"):
            if int(row.get(metric) or 0):
                failures.append(f"{key}: {metric}={row[metric]}")
        if int(row.get("max_solver_process_threads") or 0) > 1:
            failures.append(f"{key}: solver process exceeded one thread")
        rss = int(row.get("peak_job_rss_bytes") or 0)
        max_rss = max(max_rss, rss)
        stats["peak_job_rss_bytes"] = max(stats["peak_job_rss_bytes"], rss)
    return {"passed": not failures, "failures": failures, "by_solver": by_solver,
            "peak_job_rss_bytes": max_rss, "assessed_utc": now(),
            "gate": "runtime_and_semantic_consistency_not_relative_solution_quality"}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifests", type=Path, default=BASE / "manifests/iclr_t1_v1")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--stop-after", choices=PHASES, default="formal")
    p.add_argument("--prepare-only", action="store_true")
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    phases = prepare_plan(args.manifests, args.output)
    hardware = hardware_snapshot()
    max_workers = min(30, hardware["available_physical_cores"])
    # Keep the waiting coordinator and every phase within the same physical CPU
    # set; the per-phase runner narrows this further for diagnostic waves.
    os.sched_setaffinity(0, set(hardware["physical_cpu_representatives"][:max_workers]))
    status = {"schema": "iclr_author_solver_pipeline_v1", "updated_utc": now(),
        "state": "prepared", "max_active_jobs_authorized": 30, "threads_per_job": 1,
        "coordinator_cpu_affinity": sorted(os.sched_getaffinity(0)),
        "started_hardware": hardware, "phases": [{**v, "state": "prepared"} for v in phases],
        "formal_state": "prepared_pending_diagnostics_and_panel", "source_manifest": str(args.manifests.resolve())}
    status_path = args.output / "pipeline_status.json"
    atomic_json(status_path, status)
    if args.prepare_only:
        print(json_text({"state": "prepared", "status": str(status_path)}))
        return
    for index, phase in enumerate(phases):
        requested = min(phase["target_workers"], max_workers)
        target = status["phases"][index]
        command = [str(PYTHON), str(ENTRY), "--manifest", phase["manifest"],
            "--output", phase["output"], "--workers", str(requested),
            "--budget", str(phase["budget_s"]), "--checkpoints", phase["checkpoints"]]
        target.update(state="running", actual_workers=requested, command=command, started_utc=now())
        status.update(state="running", active_phase=phase["name"], updated_utc=now())
        if phase["name"] == "formal":
            status["formal_state"] = "running"
        atomic_json(status_path, status)
        print(json_text({"phase": phase["name"], "state": "running", "workers": requested}), flush=True)
        with (args.output / (phase["name"] + ".log")).open("a") as log:
            code = subprocess.call(command, stdout=log, stderr=subprocess.STDOUT)
        if code:
            assessment = {"passed": False, "failures": [f"runner exited {code}"]}
        else:
            assessment = stage_assessment(phase["output"], phase["job_count"])
        target.update(state="complete" if assessment["passed"] else "needs_review",
                      finished_utc=now(), assessment=assessment)
        if not assessment["passed"]:
            status.update(state="needs_review", updated_utc=now())
            atomic_json(status_path, status)
            print(json_text({"phase": phase["name"], "assessment": assessment}), flush=True)
            return 2
        # Threefold observed job memory plus a system reserve. This is a resource
        # guard, not a mathematical bound on future FPO label growth.
        peak = assessment["peak_job_rss_bytes"]
        current = hardware_snapshot()
        reserve = max(24 * 1024**3, current["memory_total_bytes"] * .15)
        available = max(0., current["memory_available_bytes"] - reserve)
        memory_workers = math.floor(available / (3 * peak)) if peak else 0
        if memory_workers < 1:
            status.update(state="needs_review", resource_error="insufficient measured memory headroom")
            atomic_json(status_path, status)
            return 2
        max_workers = min(max_workers, memory_workers)
        status["selected_worker_limit"] = max_workers
        status["updated_utc"] = now()
        if phase["name"] == "formal":
            status["formal_state"] = "complete"
        atomic_json(status_path, status)
        if phase["name"] == args.stop_after:
            status.update(state="complete" if phase["name"] == "formal" else "paused_after_requested_phase")
            atomic_json(status_path, status)
            return 0
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
