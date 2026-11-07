"""Freeze the existing ICLR T1 cohorts and current input bytes, without solving.

Use the ICLR conda Python (pandas/numpy are required):
  python cohort.py --freeze --output-dir ../manifests/iclr_t1_v1

The smoke manifest becomes usable before the complete cohort is hashed. Current
file hashes are intentionally not described as historical submission hashes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from datetime import datetime, timezone
from typing import Any


DEFAULT_REPO = Path("/data/Maojie/ICLR/EVRPTW-DB")
DEFAULT_OUTPUT = Path("/data/Maojie/Other_benchmark/manifests/iclr_t1_v1")
SCHEMA = "iclr_t1_solver_job_v1"
SELECTION_SCHEME = "iclr_t1_city_panel_sha256_seed1234_v1"
MATRICES = (
    "distance_matrix_km", "distance_path_travel_time_s",
    "running_time_shortest_matrix_s", "running_time_path_distance_km",
)
LIMITATIONS = [
    "Hashes bind the current existing files, not unrecorded historical submission bytes.",
    "Archived reconstruction and matrix restore explicitly did not validate content hashes.",
    "Cus500 matches the archived executed Neural T1 500 IDs and index hash; that archive is not proof of the final submitted paper table.",
    "Cus1000 shares the same frozen T1 index release; its final submitted Neural test roster has not been established locally.",
    "The archived Neural Cus500 test priced D_dist; its costs must not be directly compared with this D_time task.",
    "This script freezes data and checks loader structure only; it does not run or validate either heuristic solver.",
]


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode()


def digest_json(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def atomic_json(path: Path, payload: Any) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                    indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def atomic_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w") as stream:
        for row in rows:
            stream.write(canonical_bytes(row).decode() + "\n")
    temporary.replace(path)


def hash_file(path: Path) -> dict[str, Any]:
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
    after = path.stat()
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
    if identity(before) != identity(after):
        raise RuntimeError(f"Input changed while hashing: {path}")
    return {"path": str(path), "sha256": digest.hexdigest(),
            "size_bytes": after.st_size, "mtime_ns": after.st_mtime_ns}


def source_files(task: Any, dataset_root: Path) -> list[str]:
    """Exact disk inputs read by the existing load_materialized_view API."""
    family = Path(task.family_dir)
    view = family / "views" / task.view_id
    family_file = family / "family_manifest.json"
    view_file = view / "view_manifest.json"
    family_manifest = json.loads(family_file.read_text())
    view_manifest = json.loads(view_file.read_text())
    if family_manifest["family_id"] != task.family_id:
        raise ValueError(f"Wrong family identity: {task.view_id}")
    if view_manifest["view_id"] != task.view_id or view_manifest["family_id"] != task.family_id:
        raise ValueError(f"Wrong view identity: {task.view_id}")
    if view_manifest.get("non_release_pilot"):
        raise ValueError(f"Pilot view in formal index: {task.view_id}")
    files = [family_file, view_file, family / family_manifest["terminal_index"]]
    files.extend(family / family_manifest["matrix_files"][name] for name in MATRICES)
    files.extend(view / view_manifest[name] for name in (
        "terminal_parent_indices", "customer_attributes", "charging_attributes"))
    return sorted({str(p.resolve().relative_to(dataset_root)) for p in files})


def panel_key(seed: int, view_id: str) -> str:
    return hashlib.sha256(
        f"{SELECTION_SCHEME}\0{seed}\0{view_id}".encode()).hexdigest()


def stratify(tasks: list[Any], per_city: int, seed: int) -> list[Any]:
    cities = sorted({task.city_slug for task in tasks})
    if len(cities) != 10:
        raise ValueError(f"Expected 10 T1 cities, found {cities}")
    selected = []
    for city in cities:
        candidates = [task for task in tasks if task.city_slug == city]
        if len(candidates) != 50:
            raise ValueError(f"Expected 50 T1 instances per city, {city} has {len(candidates)}")
        selected.extend(sorted(candidates,
                               key=lambda t: (panel_key(seed, t.view_id), t.view_id))[:per_city])
    return selected


def freeze(repo: Path, output: Path, hash_scope: str) -> None:
    sys.path[:0] = [str(repo / "EVRPTW_Core"),
                   str(repo / "EVRPTW_Dataset_Generator/src"),
                   str(repo / "EVRPTW_Benchmark/MetaHeuristics")]
    from benchmark_common import read_stage2_tasks, stable_view_seed, load_input_task
    from evrptw_core.objective import load_objective
    from evrptw_core.validation import validate_instance_structure
    import pandas as pd

    started = time.perf_counter()
    dataset = (repo / "EVRPTW_Dataset/Instances_v2/us_11city").resolve()
    index = dataset / "generation_plan/core/test/test1_new_seed/view_index.parquet"
    objective_path = repo / "EVRPTW_Benchmark/Reinforcement_Learning/configs/rivian_energy_vehicle_cost_v2.json"
    objective = load_objective(objective_path)
    tasks = read_stage2_tasks(index, family_root=dataset / "materialized/families")
    frame = pd.read_parquet(index)
    if len(frame) != 1500 or frame.view_id.duplicated().any():
        raise ValueError("Expected unique 1500-row T1 index")
    rows_by_id = {str(row["view_id"]): row for row in frame.to_dict(orient="records")}
    by_scale = {scale: [t for t in tasks if t.scale_id.lower() == scale]
                for scale in ("cus100", "cus500", "cus1000")}
    for scale, members in by_scale.items():
        if len(members) != 500 or any(t.split_id != "test" or t.track_id != "test1_new_seed" for t in members):
            raise ValueError(f"Unexpected full T1 cohort: {scale}")
        stratify(members, 1, 1234)  # Assert each city has 50 entries.
    groups = {
        "smoke": stratify(by_scale["cus100"], 1, 1234),
        "panel": stratify(by_scale["cus500"], 3, 1234) + stratify(by_scale["cus1000"], 3, 1234),
        "formal": by_scale["cus500"] + by_scale["cus1000"],
    }
    if not {t.view_id for t in groups["panel"]} <= {t.view_id for t in groups["formal"]}:
        raise AssertionError("Panel is not a subset of the formal cohort")
    output.mkdir(parents=True, exist_ok=True)
    old_metadata = output / "metadata.json"
    if old_metadata.exists():
        old = json.loads(old_metadata.read_text())
        if old.get("status") == "complete":
            raise FileExistsError(f"Completed manifest is immutable; choose a new output directory: {output}")
    index_hash = hash_file(index)
    archive = repo / "EVRPTW_Benchmark/results_legacy/TERRAN_stable_cost_v1/eval_after_val2100_20260912/tests/provenance.json"
    neural = json.loads(archive.read_text())
    historical = neural["dataset"]["test1_new_seed"]
    match_hash = historical["index_sha256"] == index_hash["sha256"]
    match_ids = historical["view_ids"] == [t.view_id for t in by_scale["cus500"]]
    if not match_hash or not match_ids:
        raise ValueError("Current T1 index does not match archived Neural Cus500 evidence")
    evidence_paths = {
        "test_index": index,
        "family_index": index.with_name("family_index.parquet"),
        "run_manifest": dataset / "run_manifest.json",
        "split_registry": dataset / "generation_plan/split_registry.json",
        "reconstruction_contract": dataset / "_reconstruction/reconstruction_contract.json",
        "reconstruction_instance_registry": dataset / "_reconstruction/instance_registry.parquet",
        "reference_profile": dataset / "_reconstruction/reference_profile.json",
        "charging_power_medians": dataset / "_reconstruction/us_national_charging_power_medians_v1.json",
        "matrix_restore_report": dataset / "matrix_restore_report.json",
        "historical_neural_provenance": archive,
        "historical_neural_summary": archive.parent / "test1_new_seed/summary.json",
        "historical_neural_routes": archive.parent / "test1_new_seed/perinstance.jsonl",
        "objective_profile": objective_path,
        "loader_source": repo / "EVRPTW_Dataset_Generator/src/evrptw_stage2/artifacts.py",
        "benchmark_loader_verifier_source": repo / "EVRPTW_Benchmark/MetaHeuristics/benchmark_common.py",
        "objective_adapter_source": repo / "EVRPTW_Core/evrptw_core/objective.py",
        "neural_verifier_source": repo / "EVRPTW_Benchmark/Exact/Gurobi_Solver/route_validator.py",
        "cohort_freezer_source": Path(__file__).resolve(),
    }
    evidence = {name: hash_file(path) for name, path in evidence_paths.items()}
    reconstruction = json.loads(evidence_paths["reconstruction_contract"].read_text())
    run = json.loads(evidence_paths["run_manifest"].read_text())
    restore = json.loads(evidence_paths["matrix_restore_report"].read_text())
    all_tasks = {t.view_id: t for group in groups.values() for t in group}
    inputs = {view_id: source_files(task, dataset) for view_id, task in all_tasks.items()}
    all_paths = sorted({path for files in inputs.values() for path in files})
    smoke_paths = sorted({path for task in groups["smoke"] for path in inputs[task.view_id]})
    expected_bytes = sum((dataset / path).stat().st_size for path in all_paths)
    hashes: dict[str, dict[str, Any]] = {}
    metadata = {
        "schema": "iclr_t1_cohort_freeze_v1", "status": "hashing_smoke",
        "created_at_utc": now(), "dataset_root": str(dataset),
        "base_seed": 1234, "seed_scheme": "blake2b_view_id_v1",
        "selection_scheme": SELECTION_SCHEME,
        "selection_rule": "Sort SHA256(domain + NUL + base_seed + NUL + view_id) within each city; no performance information is used.",
        "objective_config": objective.to_dict(), "objective_distance_source": "running_time_path_distance_km",
        "evidence": evidence,
        "source_release_name": reconstruction["source_dataset_root_name"],
        "generation_code_provenance": run["code_provenance"],
        "historical_reconstruction_file_hash_validation_performed": reconstruction.get("file_hash_validation_performed"),
        "historical_restore_file_hash_validation_performed": restore.get("file_hash_validation_performed"),
        "historical_restore_validation_contract": restore.get("validation_contract"),
        "historical_neural_cus500": {
            "index_sha256_matches": match_hash, "ordered_500_view_ids_match": match_ids,
            "archive": str(archive), "historical_cost_distance_source": "distance_matrix_km",
            "publication_table_identity_proven": False,
        },
        "historical_neural_cus1000": {
            "same_source_index_release": True, "final_submitted_neural_cohort_proven": False,
        },
        "hash_semantics": "SHA256 of current loader-consumed on-disk bytes; not a retrospective submission hash claim.",
        "groups": {}, "unique_input_files": len(all_paths), "unique_input_bytes": expected_bytes,
        "hashed_files": 0, "hashed_bytes": 0, "smoke_loader_checks": [],
        "limitations": LIMITATIONS,
    }
    for name, members in groups.items():
        ids = [task.view_id for task in members]
        counts: dict[str, int] = {}
        for task in members:
            key = f"{task.scale_label}/{task.city_slug}"
            counts[key] = counts.get(key, 0) + 1
        metadata["groups"][name] = {
            "count": len(ids), "manifest": f"{name}.jsonl",
            "ordered_view_ids_sha256": digest_json(ids), "counts_by_scale_city": counts,
            "input_hash_status": "pending",
        }

    def write_manifests() -> None:
        for name, members in groups.items():
            jobs = []
            for task in members:
                known = all(path in hashes for path in inputs[task.view_id])
                content = {path: hashes[path]["sha256"] for path in inputs[task.view_id] if path in hashes}
                row = rows_by_id[task.view_id]
                jobs.append({
                    "schema": SCHEMA, "phase": name, "job_id": f"{name}/{task.scale_label}/{task.view_id}",
                    "input_kind": "stage2", "stage2_task": task.to_dict(),
                    "instance_id": task.view_id, "family_id": task.family_id,
                    "scale": task.scale_label, "city_slug": task.city_slug, "day_type": str(row["day_type"]),
                    "base_seed": 1234, "seed": stable_view_seed(1234, task.view_id),
                    "solver_seed": stable_view_seed(1234, task.view_id), "seed_scheme": "blake2b_view_id_v1",
                    "objective_config": objective.to_dict(),
                    "objective_distance_source": "running_time_path_distance_km",
                    "time_limit_s": 1800.0, "checkpoints_s": [60.0, 300.0, 900.0, 1800.0],
                    "dataset_identity": {
                        "index_sha256": index_hash["sha256"], "source_release_name": metadata["source_release_name"],
                        "current_input_content_status": "complete" if known else "pending",
                        "current_input_content_sha256": digest_json(content) if known else None,
                        "current_files": content, "expected_relative_files": inputs[task.view_id],
                        "metadata_path": str(output / "metadata.json"),
                        "historical_submission_bytes_verified": False,
                    },
                })
            path = output / f"{name}.jsonl"
            atomic_jsonl(path, jobs)
            info = metadata["groups"][name]
            info["input_hash_status"] = "complete" if all(j["dataset_identity"]["current_input_content_status"] == "complete" for j in jobs) else "pending"
            info["manifest_sha256"] = hash_file(path)["sha256"]
        atomic_json(output / "metadata.json", metadata)

    def do_hash(relative_paths: list[str], stream: Any) -> None:
        progress_time = time.perf_counter()
        for relative in relative_paths:
            if relative in hashes:
                continue
            record = hash_file(dataset / relative)
            record["relative_path"] = relative
            hashes[relative] = record
            stream.write(canonical_bytes(record).decode() + "\n")
            metadata["hashed_files"] = len(hashes)
            metadata["hashed_bytes"] += record["size_bytes"]
            if len(hashes) % 100 == 0 or time.perf_counter() - progress_time > 20:
                stream.flush()
                metadata["updated_at_utc"] = now()
                atomic_json(output / "metadata.json", metadata)
                print(json.dumps({"status": metadata["status"], "hashed_files": len(hashes),
                                  "total_files": len(all_paths), "hashed_bytes": metadata["hashed_bytes"]}), flush=True)
                progress_time = time.perf_counter()

    write_manifests()
    try:
        with (output / "file_hashes.jsonl").open("w") as stream:
            do_hash(smoke_paths, stream)
            for task in groups["smoke"]:
                instance, _ = load_input_task({"input_kind": "stage2", "stage2_task": task.to_dict(),
                                               "objective_config": objective.to_dict()})
                result = validate_instance_structure(instance)
                if not result.success:
                    raise ValueError(f"Smoke loader structure failed: {task.view_id}: {result.errors}")
                metadata["smoke_loader_checks"].append({"view_id": task.view_id, "city_slug": task.city_slug,
                    "passed": True, "num_customers": instance.num_customers,
                    "num_stations": instance.num_charging_stations,
                    "objective_distance_source": instance.metadata["objective_distance_source"]})
            metadata["status"] = "hashing_full_cohort" if hash_scope == "all" else "smoke_complete_full_hash_pending"
            write_manifests()
            stream.flush()
            print(json.dumps({"status": "smoke_ready", "manifest": str(output / "smoke.jsonl"),
                              "count": len(groups["smoke"])}), flush=True)
            if hash_scope == "all":
                do_hash(all_paths, stream)
                stream.flush()
                os.fsync(stream.fileno())
                # Detect post-hash mutation before claiming one consistent snapshot.
                for relative, record in hashes.items():
                    stat = (dataset / relative).stat()
                    if (stat.st_size, stat.st_mtime_ns) != (record["size_bytes"], record["mtime_ns"]):
                        raise RuntimeError(f"Input changed during cohort freeze: {relative}")
                metadata["status"] = "complete"
                metadata["current_content_aggregate_sha256"] = digest_json(
                    {relative: hashes[relative]["sha256"] for relative in sorted(hashes)})
        metadata["file_hashes_sha256"] = hash_file(output / "file_hashes.jsonl")["sha256"]
        metadata["finished_at_utc"] = now()
        metadata["wall_seconds"] = time.perf_counter() - started
        write_manifests()
        print(json.dumps({"status": metadata["status"], "metadata": str(output / "metadata.json"),
                          "hashed_files": len(hashes), "wall_seconds": metadata["wall_seconds"]}), flush=True)
    except Exception as exc:
        metadata["status"] = "failed"
        metadata["error"] = f"{type(exc).__name__}: {exc}"
        metadata["updated_at_utc"] = now()
        atomic_json(output / "metadata.json", metadata)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freeze", action="store_true", required=True)
    parser.add_argument("--repo-root", type=Path, default=DEFAULT_REPO)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--hash-scope", choices=("smoke", "all"), default="all")
    args = parser.parse_args()
    freeze(args.repo_root.resolve(), args.output_dir.resolve(), args.hash_scope)


if __name__ == "__main__":
    main()
