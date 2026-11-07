#!/usr/bin/env python3
"""Restore pinned author Git metadata without replacing adapted snapshot files."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile

AUTHORS = {
    "HMA": {
        "revision": "0ebbcaa4c2b8385c2c9bff06d60689cb2c64229d",
        "url": "https://github.com/0SliverBullet/EVRP-TW-SPD-HMA.git",
    },
    "RoutingBlocks": {
        "revision": "2d2ee06c79b5d458b1aeeafd95a2b42b5441079a",
        "url": "https://github.com/tumBAIS/RoutingBlocks.git",
    },
}


def git(directory: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(directory), *args], check=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return result.stdout.strip()


def present(path: Path) -> bool:
    return os.path.lexists(path)


def source_entries(directory: Path):
    """Walk files and symlinks without following links or entering Git metadata."""
    for parent, directories, files in os.walk(directory, followlinks=False):
        base = Path(parent)
        directories[:] = sorted(name for name in directories if name != ".git")
        for name in list(directories):
            path = base / name
            if path.is_symlink():
                directories.remove(name)
                yield path
        for name in sorted(files):
            if name != ".git":
                yield base / name


def snapshot(directory: Path) -> dict:
    result = {}
    for path in source_entries(directory):
        info = path.lstat()
        if path.is_symlink():
            result[str(path.relative_to(directory))] = {"type": "symlink", "target": os.readlink(path)}
        elif stat.S_ISREG(info.st_mode):
            result[str(path.relative_to(directory))] = {
                "type": "file", "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "mode": stat.S_IMODE(info.st_mode),
            }
        else:
            raise ValueError(f"Unsupported special file in author snapshot: {path}")
    return result


def require_head(directory: Path, expected: str) -> str:
    if not present(directory / ".git"):
        raise ValueError(f"No author Git metadata: {directory}")
    actual = git(directory, "rev-parse", "HEAD")
    top = Path(git(directory, "rev-parse", "--show-toplevel")).resolve()
    if actual != expected or top != directory.resolve():
        raise ValueError(f"Refusing mismatching repository {directory}: HEAD={actual}, expected={expected}, top={top}")
    return actual


def missing_files(staging: Path, destination: Path) -> list[tuple[Path, Path]]:
    additions = []
    for source in source_entries(staging):
        relative = source.relative_to(staging)
        target = destination / relative
        # Existing adapted files, symlinks and directories always win.
        if present(target):
            continue
        for parent in target.parents:
            if parent == destination:
                break
            if present(parent) and (parent.is_symlink() or not parent.is_dir()):
                raise ValueError(f"Cannot restore {relative} through existing non-directory {parent}")
        additions.append((source, target))
    return additions


def restore(release_root: Path, local_vendor_root: Path | None) -> dict:
    vendor = release_root / "Other_benchmark/vendor"
    before = {}
    # Reject a mismatch in either repository before modifying either destination.
    for name, author in AUTHORS.items():
        destination = vendor / name
        if not destination.is_dir() or destination.is_symlink():
            raise ValueError(f"Expected a real source snapshot directory: {destination}")
        if present(destination / ".git"):
            require_head(destination, author["revision"])
        if local_vendor_root is not None:
            require_head(local_vendor_root / name, author["revision"])
        before[name] = snapshot(destination)

    records = []
    with tempfile.TemporaryDirectory(prefix="restore_author_git_") as temporary:
        prepared = []
        for name, author in AUTHORS.items():
            staging = Path(temporary) / name
            source = str(local_vendor_root / name) if local_vendor_root is not None else author["url"]
            # --no-local avoids hardlinks and alternates back to the supplied repo.
            command = ["git", "clone", "--no-local", "--no-checkout", source, str(staging)]
            subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            git(staging, "checkout", "--detach", author["revision"])
            require_head(staging, author["revision"])
            git(staging, "remote", "set-url", "origin", author["url"])
            if (staging / ".git/objects/info/alternates").exists():
                raise ValueError(f"Cloned metadata unexpectedly depends on another object directory: {name}")
            destination = vendor / name
            prepared.append((name, author, staging, destination, missing_files(staging, destination)))

        for name, author, staging, destination, additions in prepared:
            for source, target in additions:
                target.parent.mkdir(parents=True, exist_ok=True)
                if source.is_symlink():
                    os.symlink(os.readlink(source), target)
                else:
                    with source.open("rb") as incoming, target.open("xb") as outgoing:
                        shutil.copyfileobj(incoming, outgoing)
                    shutil.copystat(source, target)
            restored_metadata = not present(destination / ".git")
            if restored_metadata:
                # Stage metadata alongside the target, then publish by rename.
                pending = Path(tempfile.mkdtemp(prefix=".restore-git-", dir=destination))
                try:
                    shutil.copytree(staging / ".git", pending / "metadata")
                    if present(destination / ".git"):
                        raise FileExistsError(f"Git metadata appeared during restoration: {destination}")
                    (pending / "metadata").rename(destination / ".git")
                finally:
                    shutil.rmtree(pending)
            require_head(destination, author["revision"])
            after = snapshot(destination)
            changed = [path for path, value in before[name].items() if after.get(path) != value]
            if changed:
                raise RuntimeError(f"Original snapshot changed in {name}: {changed}")
            records.append({
                "name": name, "author_url": author["url"], "head": author["revision"],
                "destination": str(destination), "metadata_restored": restored_metadata,
                "original_entries_verified": len(before[name]), "original_entries_unchanged": True,
                "original_entry_hashes": before[name],
                "upstream_files_added": [str(target.relative_to(destination)) for _, target in additions],
            })
    return {"schema": "restore_pinned_author_git_v1", "release_root": str(release_root),
            "source": str(local_vendor_root) if local_vendor_root else "pinned author GitHub repositories",
            "repositories": records}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--local-vendor-root", type=Path,
                        help="Read-only local parent containing HMA/ and RoutingBlocks/ Git checkouts at the pinned HEADs")
    parser.add_argument("--report", type=Path, help="Optional JSON report with every preserved file hash")
    args = parser.parse_args()
    try:
        if args.report:
            report_path = args.report.resolve()
            for name in AUTHORS:
                destination = (args.release_root / "Other_benchmark/vendor" / name).resolve()
                if report_path == destination or destination in report_path.parents:
                    raise ValueError("Write --report outside the vendor snapshots to preserve all source files")
        report = restore(args.release_root.resolve(), args.local_vendor_root.resolve() if args.local_vendor_root else None)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        detail = error.stderr.strip() if isinstance(error, subprocess.CalledProcessError) and error.stderr else str(error)
        parser.exit(1, f"Author restoration failed: {detail}\n")
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    for record in report["repositories"]:
        print(f"{record['name']}: HEAD {record['head']}; "
              f"{record['original_entries_verified']} original entries unchanged; "
              f"{len(record['upstream_files_added'])} missing upstream files restored")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
