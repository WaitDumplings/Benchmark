#!/usr/bin/env python3
"""Freeze solver source, author patches, native artifacts and cohort manifests.

This is a source/artifact snapshot, not an experiment launcher or dataset archive.
Run --inspect while developing; create the archive after all source changes stop.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile

BASE = Path(__file__).resolve().parents[1]
REPO = BASE.parent / "ICLR/EVRPTW-DB"
PINS = {"HMA": "0ebbcaa4c2b8385c2c9bff06d60689cb2c64229d",
        "RoutingBlocks": "2d2ee06c79b5d458b1aeeafd95a2b42b5441079a"}
SOURCE_SUFFIXES = {".py", ".pyi", ".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".hxx",
    ".cu", ".cuh", ".inc", ".tpp", ".json", ".jsonl", ".toml", ".txt", ".cmake",
    ".in", ".sh", ".bash", ".bat", ".ps1", ".md", ".rst", ".yaml", ".yml", ".cfg", ".ini", ".lock", ".patch"}
SPECIAL_NAMES = {"Makefile", "LICENSE", "LICENSE.md", "COPYING", ".gitignore", ".gitattributes",
                 ".clang-format", ".cmake-format"}
CACHE_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache",
              ".ruff_cache", "_skbuild", "node_modules", "CMakeFiles"}
VENDOR_DATA_DIRS = {"data", "datasets", "instances", "image", "solution"}


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args])


def git_files(repo, *args):
    return [os.fsdecode(x) for x in git(repo, "ls-files", "-z", *args).split(b"\0") if x]


def excluded(relative, *, vendor=False, adapter=False):
    parts = Path(relative).parts
    for part in parts[:-1]:
        if part in CACHE_DIRS or part == "build" or part.startswith("build_"):
            return "build_or_environment_cache"
        if vendor and part.lower() in VENDOR_DATA_DIRS:
            return "author_dataset_or_example_output"
        if adapter and part == "evidence":
            return "runtime_evidence_kept_separately"
    return None


def source_like(path):
    return Path(path).suffix.lower() in SOURCE_SUFFIXES or Path(path).name in SPECIAL_NAMES


def walk_files(root, *, vendor=False, adapter=False):
    if not root.is_dir():
        return
    for current, dirs, files in os.walk(root):
        relative_dir = Path(current).relative_to(root)
        dirs[:] = [d for d in dirs if not excluded(relative_dir / d / "probe", vendor=vendor, adapter=adapter)]
        for name in files:
            path = Path(current) / name
            yield path, path.relative_to(root)


def collect(base=BASE, repo=REPO):
    sources = {}
    generated = {}
    authors = {}

    def add(path, namespace=None):
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(path)
        # Freeze a symlink's bytes only when the target remains inside this workspace.
        if not path.resolve().is_relative_to(base.parent.resolve()):
            raise ValueError(f"Source link escapes the workspace: {path}")
        member = namespace or "source/" + str(path.relative_to(base.parent))
        previous = sources.setdefault(member, path)
        if previous != path:
            raise ValueError(f"Archive member collision: {member}")

    # Only the synthetic tests/fixtures belong in the source release. The bridge
    # also has real-instance export probes, which remain dataset-derived evidence.
    for path in (base / "benchmark_bridge").iterdir():
        if path.is_file() and source_like(path):
            add(path)
    for path, relative in walk_files(base / "tests"):
        if source_like(path) or ("fixtures" in relative.parts and path.suffix in {".npz", ".npy"}):
            add(path)
    for name in ("README.md", "HMA_RoutingBlocks_code_agent_handoff.zh-CN.md",
                 "HMA_RoutingBlocks_code_agent_handoff.zh-CN_update.md"):
        if (base / name).is_file():
            add(base / name)
    for folder in (base / "hma_adapter", base / "routingblocks_adapter"):
        for path, relative in walk_files(folder, adapter=True):
            binary = "bin" in relative.parts or path.suffix in {".whl", ".so", ".pyd", ".dll", ".dylib"}
            if source_like(path) or binary:
                add(path)
    # Keep bounded correctness reports with the release, while large event logs,
    # exported instances and historical binaries remain in the evidence tree.
    for relative in (
        "hma_adapter/evidence/canonical_comparison.json",
        "hma_adapter/evidence/lock_inheritance.json",
        "hma_adapter/evidence/houston_failure/crash_fix_report.json",
        "hma_adapter/evidence/houston_failure/release_after_fix/canonical_summary.json",
        "routingblocks_adapter/evidence/canonical_fixture_comparison.json",
    ):
        add(base / relative)
    for path, relative in walk_files(base / "manifests"):
        if path.suffix in {".json", ".jsonl", ".sha256"}:
            add(path)
    required = [base / "tests/test_bridge.py", base / "tests/fixtures/index.json",
                base / "hma_adapter/bin/hma_reference", base / "routingblocks_adapter/requirements.lock"]
    for path in required:
        add(path)
    if not list((base / "routingblocks_adapter/adapted_wheels").glob("*.whl")):
        raise FileNotFoundError("An adapted RoutingBlocks wheel must exist before freezing")

    # Keep the exact external canonical code used by the adapters alongside its
    # project path. The large dataset is represented by the frozen hash manifests.
    reference_paths = [
        repo / "EVRPTW_Benchmark/MetaHeuristics/benchmark_common.py",
        repo / "EVRPTW_Benchmark/MetaHeuristics/benchmark_output.py",
        repo / "EVRPTW_Benchmark/MetaHeuristics/AuthorSolvers/run_author_solvers.py",
        repo / "EVRPTW_Benchmark/Exact/Gurobi_Solver/route_validator.py",
        repo / "EVRPTW_Benchmark/Reinforcement_Learning/configs/rivian_energy_vehicle_cost_v2.json",
        repo / "EVRPTW_Dataset_Generator/src/evrptw_stage2/artifacts.py",
        repo / "EVRPTW_Dataset_Generator/src/evrptw_stage2/contracts.py",
        *sorted((repo / "EVRPTW_Core/evrptw_core").glob("*.py")),
    ]
    for path in reference_paths:
        add(path)

    for name, expected in PINS.items():
        vendor = base / "vendor" / name
        head = git(vendor, "rev-parse", "HEAD").decode().strip()
        if head != expected:
            raise ValueError(f"{name} HEAD {head} differs from approved revision {expected}")
        patch = git(vendor, "diff", "--binary", "--full-index", "HEAD", "--")
        status = git(vendor, "status", "--porcelain=v1", "--untracked-files=all").decode()
        tracked = set(git_files(vendor))
        untracked = set(git_files(vendor, "--others", "--exclude-standard"))
        source_candidates = set()
        omitted_untracked = []
        for relative in sorted(tracked | untracked):
            reason = excluded(relative, vendor=True)
            path = vendor / relative
            if reason is None and source_like(relative) and path.is_file():
                add(path)
                source_candidates.add(relative)
            elif relative in untracked:
                omitted_untracked.append({"path": relative, "reason": reason or "not_source_or_native_release_artifact"})
        # A .gitignore rule can hide a newly introduced native source. Inspect the
        # remaining source tree too, with build/data directories pruned in advance.
        ignored_sources = []
        for path, relative_path in walk_files(vendor, vendor=True):
            relative = str(relative_path)
            if source_like(relative) and relative not in source_candidates:
                add(path)
                source_candidates.add(relative)
                if relative not in tracked and relative not in untracked:
                    ignored_sources.append(relative)
        untracked_sources = sorted(relative for relative in source_candidates if relative not in tracked)
        author = {"schema": "pinned_author_worktree_snapshot_v1", "repository": str(vendor),
            "head": head, "expected_head": expected, "status_porcelain": status,
            "git_diff_binary_sha256": hashlib.sha256(patch).hexdigest(),
            "patch_covers": "git diff --binary --full-index HEAD (staged and unstaged tracked changes)",
            "included_untracked_sources": untracked_sources, "included_ignored_sources": sorted(ignored_sources),
            "omitted_untracked_files": omitted_untracked,
            "untracked_source_handling": "Actual file content is packaged under source/Other_benchmark/vendor; it is not assumed to occur in git diff.",
            "tracked_source_files_in_snapshot": len(source_candidates & tracked)}
        generated[f"author_snapshots/{name}/author_revision.json"] = json_bytes(author)
        generated[f"author_snapshots/{name}/author_source.patch"] = patch
        authors[name] = author
    return sources, generated, authors


def stat_identity(path):
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def digest_file(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


class HashedReader:
    def __init__(self, stream):
        self.stream = stream
        self.digest = hashlib.sha256()

    def read(self, size=-1):
        value = self.stream.read(size)
        self.digest.update(value)
        return value


def create_archive(output, label):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", label):
        raise ValueError("Label must be a simple filename component")
    sources, generated, authors = collect()
    created = datetime.now(timezone.utc)
    epoch = int(created.timestamp())
    records = {}
    before = {}
    for member, path in sorted(sources.items()):
        before[member] = stat_identity(path)
        records[member] = {"sha256": digest_file(path), "size_bytes": path.stat().st_size,
                           "source_path": str(path), "mode": path.stat().st_mode & 0o777}
        if stat_identity(path) != before[member]:
            raise RuntimeError(f"File changed while preparing release: {path}")
    for member, data in generated.items():
        records[member] = {"sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data), "mode": 0o644}
    manifest = {"schema": "iclr_author_solver_release_snapshot_v1", "label": label,
        "created_at_utc": created.isoformat(), "archive_name": label + ".tar.gz",
        "archive_format": "gzip tar; source files retain workspace-relative paths below source/",
        "file_count_excluding_manifest": len(records), "files": records, "authors": authors,
        "snapshot_scope": ["bridge source", "tests and shared small fixtures", "adapter source/config/build scripts/locks",
                           "native binaries and upstream/adapted wheels", "author working source plus complete tracked binary diff and untracked sources",
                           "current cohort and file hash manifests", "canonical ICLR reference source",
                           "selected bounded correctness reports"],
        "excluded": [".venv and dependency caches", "native build directories", "large datasets and parent matrices",
                     "runtime results/evidence directories except explicitly selected correctness reports", "author input datasets and example output"],
        "claims": {"source_snapshot_only": True, "formal_cohort_completed": False,
                   "dataset_bytes_included": False, "submission_matrix_byte_identity_proven": False},
        "reproduction_note": "Restore the dataset separately against manifests, clone the pinned author revisions, and restore patched/untracked sources. Git metadata and an entire ICLR installation are not bundled. Native binaries/wheels are platform specific."}
    manifest_data = json_bytes(manifest)
    output.mkdir(parents=True, exist_ok=True)
    archive = output / (label + ".tar.gz")
    manifest_path = output / (label + ".manifest.json")
    checksum_path = output / (label + ".tar.gz.sha256")
    partial = archive.with_suffix(archive.suffix + ".partial")
    for path in (archive, manifest_path, checksum_path, partial):
        if path.exists():
            raise FileExistsError(f"Release is immutable; use a new label: {path}")
    try:
        with partial.open("xb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=epoch) as zipped, \
                tarfile.open(fileobj=zipped, mode="w", format=tarfile.PAX_FORMAT) as tar:
            for member, record in sorted(records.items()):
                info = tarfile.TarInfo(member)
                info.size = record["size_bytes"]
                info.mode = record["mode"]
                info.mtime = epoch
                if member in sources:
                    path = sources[member]
                    if stat_identity(path) != before[member]:
                        raise RuntimeError(f"Source changed before archive copy: {path}")
                    with path.open("rb") as stream:
                        reader = HashedReader(stream)
                        tar.addfile(info, reader)
                    if reader.digest.hexdigest() != record["sha256"] or stat_identity(path) != before[member]:
                        raise RuntimeError(f"Source changed during archive copy: {path}")
                else:
                    tar.addfile(info, io.BytesIO(generated[member]))
            info = tarfile.TarInfo("release_manifest.json")
            info.size, info.mode, info.mtime = len(manifest_data), 0o644, epoch
            tar.addfile(info, io.BytesIO(manifest_data))
        # Also catch newly added source files or a concurrently changed index/HEAD.
        after_sources, after_generated, after_authors = collect()
        if set(after_sources) != set(sources) or after_generated != generated or after_authors != authors:
            raise RuntimeError("Source inventory or author git state changed while freezing; retry after edits stop")
        for member, path in sources.items():
            if stat_identity(path) != before[member]:
                raise RuntimeError(f"Source changed after archive copy: {path}")
        with tarfile.open(partial, "r:gz") as tar:
            members = {entry.name: entry for entry in tar.getmembers()}
            if set(members) != set(records) | {"release_manifest.json"}:
                raise RuntimeError("Archive inventory verification failed")
            for name, record in records.items():
                digest = hashlib.sha256()
                with tar.extractfile(name) as stream:
                    for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
                        digest.update(block)
                if digest.hexdigest() != record["sha256"]:
                    raise RuntimeError(f"Archive member checksum failed: {name}")
            if tar.extractfile("release_manifest.json").read() != manifest_data:
                raise RuntimeError("Embedded manifest verification failed")
        archive_hash = digest_file(partial)
        partial.replace(archive)
        manifest_path.write_bytes(manifest_data)
        checksum_path.write_text(f"{archive_hash}  {archive.name}\n")
        return {"archive": str(archive), "sha256": archive_hash, "bytes": archive.stat().st_size,
                "manifest": str(manifest_path), "checksum_file": str(checksum_path),
                "files": len(records), "untracked_sources": {k: v["included_untracked_sources"] for k, v in authors.items()}}
    except Exception:
        if partial.exists():
            partial.unlink()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inspect", action="store_true", help="Inspect inventory only; do not create an archive")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--label", default="iclr_author_solvers_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    args = parser.parse_args()
    if args.inspect:
        sources, generated, authors = collect()
        print(json.dumps({"source_files": len(sources), "generated_files": len(generated),
            "source_bytes": sum(p.stat().st_size for p in sources.values()),
            "untracked_sources": {k: v["included_untracked_sources"] for k, v in authors.items()},
            "included_ignored_sources": {k: v["included_ignored_sources"] for k, v in authors.items()}}, indent=2))
    else:
        if args.output_dir is None:
            parser.error("--output-dir is required unless --inspect is used")
        print(json.dumps(create_archive(args.output_dir.resolve(), args.label), indent=2))


if __name__ == "__main__":
    main()
