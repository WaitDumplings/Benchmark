#!/usr/bin/env bash
set -euo pipefail
ADAPTER_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
SOLVER_ROOT="$ADAPTER_ROOT/../vendor/RoutingBlocks"
python3 -m venv "$ADAPTER_ROOT/.venv"
"$ADAPTER_ROOT/.venv/bin/python" -m pip install \
  'scikit-build-core==0.7.1' 'cmake==3.31.10' 'ninja==1.13.2' \
  'pyproject-metadata==0.8.1' 'pathspec==1.1.1' 'pydantic==2.14.3' \
  'click==8.5.1' 'numpy==2.4.6' 'pytest==9.2.0'
cd "$SOLVER_ROOT"
CMAKE_BUILD_PARALLEL_LEVEL=4 "$ADAPTER_ROOT/.venv/bin/python" -m pip wheel \
  --no-build-isolation --no-deps . -w "$ADAPTER_ROOT/adapted_wheels" \
  -Ccmake.define.routingblocks_BUILD_TESTS=OFF -Cbuild-dir="$ADAPTER_ROOT/build_upstream"
"$ADAPTER_ROOT/.venv/bin/python" -m pip install --no-deps --force-reinstall \
  "$ADAPTER_ROOT"/adapted_wheels/routingblocks-0.2.1-*.whl
cd "$ADAPTER_ROOT"
"$ADAPTER_ROOT/.venv/bin/python" -m pytest test_native.py -q
"$ADAPTER_ROOT/.venv/bin/python" check_fixtures.py
