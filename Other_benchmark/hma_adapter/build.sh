#!/usr/bin/env bash
set -euo pipefail
adapter_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$adapter_dir/../vendor/HMA/src"
build_output="$adapter_dir/bin/hma_reference.build.$$"
g++ -std=c++11 -O3 -o "$build_output" evrp_tw_spd_solver.cpp eval.cpp operator.cpp search_framework.cpp solution.cpp util.cpp data.cpp evolution.cpp reference_runtime.cpp
mv "$build_output" "$adapter_dir/bin/hma_reference"
