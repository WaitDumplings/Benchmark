"""Run pinned author adapters with the ICLR verifier and a process-group deadline."""
from __future__ import annotations

import argparse
import fcntl
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import json
import math
import multiprocessing
from multiprocessing.reduction import DupFd
import os
from pathlib import Path
import platform
import signal
import socket
import subprocess
import threading
import time
import traceback
import uuid

from common import (BASE, META, OBJECTIVE, OBJECTIVE_PATH, PYTHON, TIMING_SCOPE,
    CandidateReplay, atomic_json, digest_file, export_instance, json_text, load_job,
    stable_view_seed)
from benchmark_output import (IncrementalCsvStore, TIME_TRACE_FIELDNAMES,
    error_snapshot_rows, save_result_artifacts, snapshot_rows)
from evrptw_core.schema import EVRPTWSolution, merge_route_sequences
from resources import JobSampler, hardware_snapshot, physical_cpu_representatives

NAMES = {"hma": "HMA adapted to the benchmark reference task",
         "routingblocks": "RoutingBlocks EVRPTW ALNS adapted variant"}
PINS = {"hma": "0ebbcaa4c2b8385c2c9bff06d60689cb2c64229d",
        "routingblocks": "2d2ee06c79b5d458b1aeeafd95a2b42b5441079a"}
QUEUE_LOCK_FD_ENV = "ICLR_BENCHMARK_LOCK_FD"
_queue_lock_fd = None
SUMMARY_FIELDS = """instance_id file family_id city_slug split_id track_id scale_id day_type
solver_name status benchmark_status benchmark_completed has_incumbent feasible
objective_distance_km objective_mode objective_profile_id objective_unit
objective_distance_source objective_value objective_cost_usd electricity_cost_usd
vehicle_cost_usd vehicles_started vehicle_count runtime_s first_feasible_time_s
time_limit_s terminated_by_time_limit timing_scope seed seed_scheme
run_contract_fingerprint run_contract_json route_validation_passed charging_visit_count
total_charging_time_s routes_json route_sequence_json algorithm_profile_id
solution_path time_trace_path errors traceback source_read_time_s adapter_export_time_s
verification_time_s overtime_s early_stop_reason candidate_count invalid_candidate_count
late_candidate_count author_revision adaptation_sha256 dataset_identity_json
cpu_threads hardware_json internal_objective_usd initial_cost_usd initial_vehicle_count
raw_events_path candidate_audits_path exit_code orphaned_child_terminated stage_times_json
peak_job_rss_bytes max_solver_process_threads max_job_cpu_percent_one_core worker_pid worker_affinity_json""".split()
SUMMARY_FIELDS += ["native_feasible_disagreement_count", "native_objective_disagreement_count", "active_job_limit"]


def revision(solver):
    vendor = BASE / "vendor" / ("HMA" if solver == "hma" else "RoutingBlocks")
    head = subprocess.check_output(["git", "-C", str(vendor), "rev-parse", "HEAD"], text=True).strip()
    if head != PINS[solver]:
        raise RuntimeError(f"unapproved author source revision {head}")
    diff = subprocess.check_output(["git", "-C", str(vendor), "diff", "--binary", "HEAD"])
    files = {}
    # Include adapter scripts/native source, excluding build products and dependencies.
    for folder in (BASE / (solver + "_adapter"), BASE / "benchmark_bridge"):
        for pattern in ("*.py", "*.hpp", "*.h", "*.cpp", "*.json", "*.sh"):
            for p in folder.glob(pattern):
                files[str(p.relative_to(BASE))] = digest_file(p)
    canonical_sources = [META / "benchmark_common.py", META / "benchmark_output.py", OBJECTIVE_PATH,
        META / "AuthorSolvers/run_author_solvers.py",
        BASE.parent / "ICLR/EVRPTW-DB/EVRPTW_Benchmark/Exact/Gurobi_Solver/route_validator.py",
        *sorted((BASE.parent / "ICLR/EVRPTW-DB/EVRPTW_Core/evrptw_core").glob("*.py")),
        *sorted((BASE.parent / "ICLR/EVRPTW-DB/EVRPTW_Dataset_Generator/src/evrptw_stage2").glob("*.py"))]
    for p in canonical_sources:
        files[str(p)] = digest_file(p)
    data = {"author_revision": head, "tracked_patch_sha256": hashlib.sha256(diff).hexdigest(),
            "files": files}
    untracked = subprocess.check_output(["git", "-C", str(vendor), "ls-files", "--others",
                                         "--exclude-standard"], text=True).splitlines()
    data["untracked_source_files"] = {name: digest_file(vendor / name) for name in untracked
        if Path(name).suffix in (".h", ".hpp", ".cpp", ".py", ".json", ".sh")}
    binaries = ([BASE / "hma_adapter/bin/hma_reference"] if solver == "hma" else
        sorted((BASE / "routingblocks_adapter/.venv/lib").glob("python*/site-packages/routingblocks/*.so")))
    if not binaries or any(not p.is_file() for p in binaries):
        raise RuntimeError(f"compiled solver artifacts missing for {solver}")
    data["compiled_artifacts"] = {str(p): digest_file(p) for p in binaries}
    data["adaptation_sha256"] = hashlib.sha256(json_text(data).encode()).hexdigest()
    return data, diff


def command(solver, instance_dir, output, seed, budget, start):
    ack = output / "candidate.ack"
    if solver == "hma":
        return [str(PYTHON), str(BASE / "hma_adapter/run.py"), "--input",
                str(instance_dir / "instance.json"), "--output-dir", str(output),
                "--seed", str(seed), "--budget-s", str(budget),
                "--start-monotonic", str(start), "--ack-file", str(ack)]
    return [str(BASE / "routingblocks_adapter/.venv/bin/python"),
            str(BASE / "routingblocks_adapter/run.py"), "--instance-dir", str(instance_dir),
            "--output-events", str(output / "events.jsonl"), "--seed", str(seed),
            "--time-limit", str(budget), "--monotonic-start-s", str(start),
            "--ack-file", str(ack)]


def supervise(argv, output, start, budget, replay):
    """Watchdog works even while the parent is replaying or native LS blocks.

    The adapter waits for integer acknowledgements, so online verification consumes
    the same search budget as in the old runner. Generation timestamps are retained.
    """
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    stop_watchdog = threading.Event()
    killed = threading.Event()
    event_path = output / "events.jsonl"
    ack_path = output / "candidate.ack"
    atomic_json(ack_path, 0)
    event_path.touch(exist_ok=True)
    with (output / "solver.log").open("w") as log:
        if time.monotonic() - start >= budget:
            return {"runtime_s": time.monotonic() - start, "exit_code": None,
                    "terminated_by_time_limit": True, "early_stop_reason": "adapter_budget_exhausted"}
        environment = os.environ.copy()
        inherited_lock = environment.get(QUEUE_LOCK_FD_ENV)
        pass_fds = ()
        if inherited_lock is not None:
            descriptor = int(inherited_lock)
            os.fstat(descriptor)  # Fail closed if a lease was declared but lost.
            pass_fds = (descriptor,)
        process = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True, env=environment, pass_fds=pass_fds)
        atomic_json(output / "process.json", {"pid": process.pid, "command": argv,
                    "start_monotonic_s": start, "budget_s": budget})

        def watchdog():
            if not stop_watchdog.wait(max(0.0, start + budget - time.monotonic())):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                    killed.set()
                except ProcessLookupError:
                    pass

        guard = threading.Thread(target=watchdog, daemon=True)
        guard.start()
        sampler = JobSampler(process.pid, os.getpid())
        next_sample = 0.
        nlines = 0
        try:
            with event_path.open("rb") as events, (output / "candidate_audits.jsonl").open("w") as audits, \
                    (output / "resources.jsonl").open("w") as samples:
                while True:
                    if time.monotonic() >= next_sample:
                        samples.write(json_text(sampler.sample()) + "\n")
                        samples.flush()
                        next_sample = time.monotonic() + 1.
                    offset = events.tell()
                    line = events.readline()
                    if line and line.endswith(b"\n"):
                        nlines += 1
                        try:
                            def reject_constant(value):
                                raise ValueError(f"nonfinite JSON constant: {value}")
                            report = replay.observe(json.loads(line, parse_constant=reject_constant),
                                                    time.monotonic() - start)
                        except (ValueError, UnicodeError) as exc:
                            replay.invalid_count += 1
                            report = {"eligible": False, "rejection": str(exc),
                                      "raw_line": line.decode(errors="replace")}
                        audits.write(json_text(report) + "\n")
                        audits.flush()
                        atomic_json(ack_path, nlines)
                        continue
                    if line:
                        events.seek(offset)
                    if process.poll() is not None:
                        # A child can flush between readline(EOF) and poll(exit).
                        events.seek(offset)
                        final_line = events.readline()
                        if final_line.endswith(b"\n"):
                            events.seek(offset)
                            continue
                        if final_line:
                            replay.invalid_count += 1
                            audits.write(json_text({"eligible": False,
                                "rejection": "incomplete_event_at_exit", "bytes": len(final_line)}) + "\n")
                        break
                    stop_watchdog.wait(0.01)
            code = process.wait()
        finally:
            stop_watchdog.set()
            guard.join(timeout=1)
            # Always clean the owned process group, including children whose
            # wrapper has already exited. Never let an orphan bypass the cap.
            orphaned = False
            for proc_stat in Path("/proc").glob("[0-9]*/stat"):
                try:
                    fields = proc_stat.read_text().rsplit(")", 1)[1].split()
                    if int(fields[2]) == process.pid and fields[0] != "Z" and int(proc_stat.parent.name) != process.pid:
                        orphaned = True
                except (OSError, ValueError, IndexError):
                    continue
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            if process.poll() is None:
                process.wait()
        runtime = time.monotonic() - start
    resource_summary = sampler.summary()
    atomic_json(output / "resources_summary.json", resource_summary)
    deadline_kill = killed.is_set() and code == -signal.SIGKILL
    expired = deadline_kill or (code == 0 and runtime >= budget)
    return {"runtime_s": runtime, "exit_code": code,
            "terminated_by_time_limit": expired,
            "orphaned_child_terminated": orphaned,
            "early_stop_reason": "orphaned_child_terminated" if orphaned and not deadline_kill
                else "wall_clock_deadline" if expired
                else ("native_completion" if code == 0 else "adapter_error"),
            **{k: resource_summary[k] for k in ("peak_job_rss_bytes", "max_solver_process_threads",
                                               "max_job_cpu_percent_one_core")}}


def solve(job, solver, root, budget, checkpoints, provenance, manifest_hash):
    view = job["stage2_task"]["view_id"]
    seed = int(job.get("solver_seed", stable_view_seed(1234, view)))
    profile = solver + "_iclr_fullcharge_dtime_v1"
    contract = {"schema": "iclr_author_adapted_run_v1", "stage2_task": job["stage2_task"],
        "dataset_identity": job.get("dataset_identity", {}), "manifest_sha256": manifest_hash,
        "solver": solver, "profile": profile, "revision": provenance,
        "seed": seed, "seed_scheme": "blake2b_view_id_v1", "base_seed": 1234,
        "budget_s": budget, "checkpoints_s": checkpoints, "cpu_threads": 1,
        "global_active_job_limit": job.get("_execution_workers", 1),
        "objective": OBJECTIVE.to_dict(), "objective_distance_source": "running_time_path_distance_km",
        "timing_scope": TIMING_SCOPE, "online_verifier": "blocking_ack",
        "snapshot_rule": "complete_candidate_generation_time_at_or_before_cutoff"}
    contract_str = json_text(contract)
    fingerprint = hashlib.sha256(contract_str.encode()).hexdigest()
    folder = Path(root) / solver / "instances" / view / fingerprint[:16]
    result_path = folder / "result.json"
    if result_path.exists():
        return json.loads(result_path.read_text())
    folder.mkdir(parents=True, exist_ok=True)
    attempt = folder / ("attempt_" + uuid.uuid4().hex[:10])
    attempt.mkdir()
    atomic_json(attempt / "contract.json", contract)
    info = {k: job["stage2_task"].get(k, "") for k in
            ("family_id", "city_slug", "split_id", "track_id", "scale_id")}
    summary = dict.fromkeys(SUMMARY_FIELDS, "")
    summary.update(instance_id=view, **info, solver_name=NAMES[solver],
        seed=seed, seed_scheme="blake2b_view_id_v1", run_contract_fingerprint=fingerprint,
        run_contract_json=contract_str, algorithm_profile_id=profile,
        author_revision=PINS[solver], adaptation_sha256=provenance["adaptation_sha256"],
        dataset_identity_json=json_text(job.get("dataset_identity", {})), cpu_threads=1,
        worker_pid=os.getpid(), worker_affinity_json=json_text(sorted(os.sched_getaffinity(0))),
        active_job_limit=job.get("_execution_workers", 1),
        hardware_json=json_text({"host": socket.gethostname(), "platform": platform.platform(),
                                "cpu_count": os.cpu_count()}),
        time_limit_s=budget, timing_scope=TIMING_SCOPE,
        raw_events_path=str(attempt / "events.jsonl"),
        candidate_audits_path=str(attempt / "candidate_audits.jsonl"))
    trace_provenance = {k: summary[k] for k in ("solver_name", "algorithm_profile_id", "seed",
                                               "seed_scheme", "run_contract_fingerprint")}
    try:
        before_load = time.monotonic()
        for source_path, expected_hash in provenance.get("files", {}).items():
            path = Path(source_path)
            if not path.is_absolute():
                path = BASE / path
            if digest_file(path) != expected_hash:
                raise RuntimeError(f"source changed during the queued invocation: {path}")
        for compiled_path, expected_hash in provenance.get("compiled_artifacts", {}).items():
            if digest_file(compiled_path) != expected_hash:
                raise RuntimeError("compiled solver changed during the queued invocation")
        instance, info = load_job(job)
        summary.update(info, day_type=instance.day_type,
                       source_read_time_s=time.monotonic() - before_load)
        replay = CandidateReplay(instance, checkpoints, budget)
        start = time.monotonic()
        export_instance(instance, attempt / "input")
        summary["adapter_export_time_s"] = time.monotonic() - start
        execution = supervise(command(solver, attempt / "input", attempt, seed, budget, start),
                              attempt, start, budget, replay)
        best = replay.recorder.best_event
        status = "COMPLETED_WITH_INCUMBENT" if best else "UNFINISHED_NO_INCUMBENT"
        native_error = execution["exit_code"] not in (0, None) and not (
            execution["exit_code"] == -signal.SIGKILL and execution["terminated_by_time_limit"])
        orphan_error = execution.get("early_stop_reason") == "orphaned_child_terminated"
        if native_error or orphan_error:
            status = "ERROR_WITH_INCUMBENT" if best else "ERROR"
        natural = execution["exit_code"] == 0 and not execution["terminated_by_time_limit"] and not execution.get("orphaned_child_terminated")
        snapshots = replay.recorder.snapshots(runtime_s=execution["runtime_s"],
                                              natural_completion=natural, final_status=status)
        routes = best["routes"] if best else []
        fields = OBJECTIVE.fields(best["objective_distance_km"], len(routes)) if best else {
            k: None for k in ("objective_value", "objective_cost_usd", "electricity_cost_usd",
                              "vehicle_cost_usd", "vehicles_started")}
        audit = replay.audit_by_routes
        summary.update(fields)
        summary.update(execution, status=status, benchmark_status=status,
            benchmark_completed=status == "COMPLETED_WITH_INCUMBENT", has_incumbent=bool(best),
            feasible=bool(best), objective_distance_km=best["objective_distance_km"] if best else None,
            objective_distance_source="running_time_path_distance_km",
            objective_mode=OBJECTIVE.mode, objective_profile_id=OBJECTIVE.profile_id,
            objective_unit=OBJECTIVE.unit,
            vehicle_count=len(routes) if best else None,
            first_feasible_time_s=replay.recorder.first_feasible_time_s,
            route_validation_passed=bool(best), charging_visit_count=audit.get("charging_visit_count"),
            total_charging_time_s=audit.get("total_charging_time_s"),
            routes_json=json.dumps(routes), route_sequence_json=json.dumps(merge_route_sequences(routes)),
            verification_time_s=replay.verification_s, overtime_s=max(0, execution["runtime_s"] - budget),
            candidate_count=replay.count, invalid_candidate_count=replay.invalid_count,
            late_candidate_count=replay.late_count,
            native_feasible_disagreement_count=replay.native_feasible_disagreements,
            native_objective_disagreement_count=replay.native_objective_disagreements)
        native_status = {}
        for status_file in (attempt / "native_stop.json", attempt / "events.status.json",
                            attempt / "adapter_manifest.json"):
            if status_file.exists():
                native_status[status_file.name] = json.loads(status_file.read_text())
        summary["stage_times_json"] = json_text(native_status)
        if status.startswith("ERROR"):
            summary["errors"] = json_text({"exit_code": execution["exit_code"],
                "reason": execution["early_stop_reason"], "native_status": native_status})
        # Preserve the first feasible and selected raw internal cost in the summary.
        audit_path = attempt / "candidate_audits.jsonl"
        for line in (audit_path.read_text().splitlines() if audit_path.exists() else []):
            entry = json.loads(line)
            if entry.get("eligible"):
                if summary["initial_cost_usd"] == "":
                    summary["initial_cost_usd"] = entry["canonical_objective"]["objective_value"]
                    summary["initial_vehicle_count"] = len(entry["event"]["routes"])
                if best and entry["event"]["elapsed_s"] == best["event_time_s"] and entry["event"]["routes"] == best["routes"]:
                    summary["internal_objective_usd"] = entry["event"].get("internal_objective")
        solution = EVRPTWSolution(instance_id=view, solver_name=NAMES[solver], routes=routes,
            objective_distance_km=best["objective_distance_km"], vehicle_count=len(routes),
            runtime_s=execution["runtime_s"], feasible=True,
            metadata={"contract": contract, "independent_replay": audit, **fields}).to_dict() if best else None
        result = {"summary_row": summary, "snapshots": snapshots, "solution": solution,
            "time_rows": snapshot_rows(view, info, snapshots, replay.recorder.first_feasible_time_s,
                                       provenance=trace_provenance)}
    except Exception as exc:
        summary.update(status="ERROR", benchmark_status="ERROR", benchmark_completed=False,
            has_incumbent=False, feasible=False, objective_cost_usd=None, objective_value=None,
            errors=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc())
        result = {"summary_row": summary, "snapshots": [], "solution": None,
                  "time_rows": error_snapshot_rows(view, info, tuple(checkpoints), "ERROR",
                                                   summary["errors"], provenance=trace_provenance)}
    save_result_artifacts(result, solver_name=NAMES[solver],
                          solutions_dir=folder / "solutions", checkpoints_dir=folder / "checkpoints")
    atomic_json(result_path, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--solvers", nargs="+", choices=list(NAMES), default=list(NAMES))
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--budget", type=float, default=1800)
    parser.add_argument("--checkpoints", default="60,300,900,1800")
    parser.add_argument("--limit", type=int, help="Diagnostic subset only; never a full cohort")
    args = parser.parse_args()
    checkpoints = tuple(float(v) for v in args.checkpoints.split(","))
    if not math.isfinite(args.budget) or args.budget <= 0 or args.budget > 1800 or not 1 <= args.workers <= 30 or any(
            not math.isfinite(v) or v < 0 or v > args.budget for v in checkpoints):
        parser.error("budget must be (0,1800], checkpoints within budget, workers in [1,30]")
    hardware = hardware_snapshot()
    cpus = hardware["physical_cpu_representatives"]
    if args.workers > len(cpus):
        parser.error(f"only {len(cpus)} physical cores available under current affinity")
    for path, value in hardware["cgroup_controls"].items():
        if path.endswith("/cpu.max") and not value.startswith("max"):
            quota, period = map(int, value.split())
            if args.workers > quota / period:
                parser.error(f"requested workers exceed CPU quota {value}")
    # The coordinator, verifier helpers and all descendants remain on this set,
    # so even accounting activity cannot exceed the authorized physical cores.
    os.sched_setaffinity(0, set(cpus[:args.workers]))
    jobs = [json.loads(line) for line in args.manifest.read_text().splitlines() if line.strip()]
    if args.limit:
        jobs = jobs[:args.limit]
    jobs = [{**job, "_execution_workers": args.workers} for job in jobs]
    if len({j["stage2_task"]["view_id"] for j in jobs}) != len(jobs):
        raise ValueError("duplicate manifest instance IDs")
    args.output.mkdir(parents=True, exist_ok=True)
    # One global scheduler for both algorithms, even across output directories.
    # Every spawned worker receives a duplicate of this same open-file description.
    # Its adapter/native subprocess also retains the lease: a coordinator crash
    # cannot release the global cap while any owned computation is still alive.
    global_lock = (BASE / ".active_benchmark_queue.lock").open("a+")
    try:
        fcntl.flock(global_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        parser.error("another global solver queue is active; both solvers must share one queue")
    global_lock.seek(0)
    global_lock.truncate()
    global_lock.write(json_text({"pid": os.getpid(), "output": str(args.output.resolve()),
                                "workers": args.workers}))
    global_lock.flush()
    provenance = {}
    for solver in args.solvers:
        provenance[solver], patch = revision(solver)
        (args.output / solver).mkdir(exist_ok=True)
        (args.output / solver / "author_source.patch").write_bytes(patch)
    manifest_hash = digest_file(args.manifest)
    invocation = {"schema": "iclr_author_solver_invocation_v1", "manifest": str(args.manifest.resolve()),
        "manifest_sha256": manifest_hash, "solvers": args.solvers, "workers": args.workers,
        "budget_s": args.budget, "checkpoints_s": checkpoints, "instance_count": len(jobs),
        "job_count": len(jobs) * len(args.solvers), "subset_limit": args.limit,
        "execution": "one_global_spawn_process_pool", "threads_per_job": 1,
        "execution_cpu_affinity": sorted(os.sched_getaffinity(0)),
        "hardware": hardware,
        "revision": provenance, "started_utc": datetime.now(timezone.utc).isoformat()}
    atomic_json(args.output / "invocation.json", invocation)
    counts = {solver: {"finished": 0, "feasible": 0, "errors": 0} for solver in args.solvers}
    with ExitStack() as stack:
        stores = {solver: stack.enter_context(IncrementalCsvStore(
            summary_path=args.output / solver / "summary.csv",
            trace_path=args.output / solver / "time_trace.csv", summary_fieldnames=SUMMARY_FIELDS,
            trace_fieldnames=TIME_TRACE_FIELDNAMES, solver_key=solver, resume=True)) for solver in args.solvers}
        atomic_json(args.output / "status.json", {"state": "running", "counts": counts, **invocation})
        context = multiprocessing.get_context("spawn")
        cpu_queue = context.Queue()
        for cpu in cpus[:args.workers]:
            cpu_queue.put((cpu, DupFd(global_lock.fileno())))
        with ProcessPoolExecutor(max_workers=args.workers, mp_context=context,
                                 initializer=initialize_worker, initargs=(cpu_queue,)) as pool:
            futures = {pool.submit(solve, job, solver, args.output, args.budget, checkpoints,
                                  provenance[solver], manifest_hash): solver
                       for job in jobs for solver in args.solvers}
            for future in as_completed(futures):
                solver = futures[future]
                result = future.result()
                stores[solver].record_result(result)
                stores[solver].flush_canonical()
                row = result["summary_row"]
                counts[solver]["finished"] += 1
                counts[solver]["feasible"] += int(bool(row["feasible"]))
                counts[solver]["errors"] += int(row["status"].startswith("ERROR"))
                atomic_json(args.output / "status.json", {"state": "running", "counts": counts,
                    "last_instance": row["instance_id"], "updated_utc": datetime.now(timezone.utc).isoformat(),
                    **invocation})
                print(json_text({"solver": solver, "instance_id": row["instance_id"],
                    "status": row["status"], "cost": row["objective_cost_usd"],
                    "runtime_s": row["runtime_s"]}), flush=True)
    atomic_json(args.output / "status.json", {"state": "complete", "counts": counts,
        "finished_utc": datetime.now(timezone.utc).isoformat(), **invocation})


def initialize_worker(cpu_queue):
    # Every native subprocess inherits one physical CPU and one-thread libraries.
    global _queue_lock_fd
    assignment = cpu_queue.get()
    if isinstance(assignment, tuple):
        cpu, descriptor = assignment
        _queue_lock_fd = descriptor.detach()
        os.fstat(_queue_lock_fd)
        os.environ[QUEUE_LOCK_FD_ENV] = str(_queue_lock_fd)
    else:
        cpu = assignment  # Standalone affinity tests have no benchmark queue.
    os.sched_setaffinity(0, {cpu})


if __name__ == "__main__":
    main()
