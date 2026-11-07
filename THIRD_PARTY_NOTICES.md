# Third-party sources and notices

This repository contains adaptations of author code. Existing copyright and license notices remain in their source files. No repository-wide license is intended to replace third-party terms or assert ownership of upstream code.

## HMA

- Source: https://github.com/0SliverBullet/EVRP-TW-SPD-HMA
- Pinned revision: `0ebbcaa4c2b8385c2c9bff06d60689cb2c64229d`
- Snapshot: `Other_benchmark/vendor/HMA`
- The pinned upstream snapshot contains no LICENSE, COPYING, NOTICE, or explicit license grant in its README. Its license is therefore recorded as **not supplied by the upstream snapshot**, not MIT or an original work of this repository.
- The source and licensing provenance of the upstream `src/argparse.h` is not independently established by the snapshot.

## RoutingBlocks

- Source: https://github.com/tumBAIS/RoutingBlocks
- Pinned revision: `2d2ee06c79b5d458b1aeeafd95a2b42b5441079a`
- Snapshot: `Other_benchmark/vendor/RoutingBlocks`
- Upstream README and source headers provide MIT terms, including Patrick S. Klein's copyright notices.
- `native/include/routingblocks/LocalSearch.h` additionally preserves MIT notices for Thibaut Vidal (2020) and ORTEC (2022).

The following bundled components retain their own notices:

| Component | Notice location | Terms recorded in the file |
| --- | --- | --- |
| xoshiro | `native/lib/xoshiro/LICENSE.txt` | MIT, Ryo Suzuki |
| dynamic_bitset | `native/lib/dynamic_bitset/LICENSE.txt` | MIT, Maxime Pinard |
| small_vector | `native/lib/small_vector/LICENSE.txt` | MIT, Gene Harvey |
| libpopcnt | `native/lib/dynamic_bitset/dynamic_bitset/libpopcnt.h` | BSD 2-Clause, Kim Walisch and Wojciech Muła |
| CPM.cmake | `native/cmake/CPM.cmake`, `bindings/cmake/CPM.cmake` | MIT, Lars Melchior and contributors |

These paths are relative to the RoutingBlocks snapshot. The build downloads pybind11 at `d2ac3f5c70c76c367a8af6592fc4ac2eb325b253`, whose license is BSD 3-Clause. Build caches and compiled distributions are excluded from this Git repository. Optional upstream tests reference Catch2 `v3.1.0`; its source is not bundled here.

## Adaptation provenance

Adaptation patches, exact upstream revisions, source hashes, and necessary task changes are recorded under `provenance/` and each solver's adapter directory. Original author compatible tests are compatibility checks, not claims of reproducing published performance.
