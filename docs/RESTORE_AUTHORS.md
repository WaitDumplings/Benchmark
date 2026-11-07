# Restore pinned author repositories

This release tracks adapted vendor files as plain source files. The benchmark revision checks also require the authors' Git metadata. Restore it once after cloning the release:

```bash
python scripts/restore_author_git.py --report /tmp/author_restore.json
```

This downloads the author repositories, checks out these fixed revisions in temporary directories, restores missing upstream files, and installs each vendor's `.git` metadata:

| Directory | Author revision |
| --- | --- |
| `Other_benchmark/vendor/HMA` | `0ebbcaa4c2b8385c2c9bff06d60689cb2c64229d` |
| `Other_benchmark/vendor/RoutingBlocks` | `2d2ee06c79b5d458b1aeeafd95a2b42b5441079a` |

Existing adapted files are never replaced. Every original file hash, file mode and symlink target is checked afterward. An existing `.git` with a different HEAD is rejected before either vendor is modified. A matching existing repository is retained, so rerunning is supported. No solver or build is started.

For an offline restore, provide a directory containing existing `HMA/` and `RoutingBlocks/` Git checkouts at the revisions above:

```bash
python scripts/restore_author_git.py \
  --local-vendor-root /path/to/existing/vendor \
  --report /tmp/author_restore.json
```

The local repositories are read only. Their uncommitted changes are not copied; the release's adapted source files remain authoritative. Git objects are copied without references or hardlinks to the local repositories. The restored repositories use the public author URLs as their `origin` remotes.

Use `--release-root /path/to/release` to restore another release checkout. The JSON report records all original file hashes and added upstream paths. The script requires Python 3.10+ and Git; no Python packages are needed. It only restores repository metadata and missing files; follow the main README for dependencies and builds.

Release maintainers should run this after publication or in a disposable test copy. Keep vendor sources flat in the published repository; do not add the restored nested repositories as Git submodules/gitlinks.
