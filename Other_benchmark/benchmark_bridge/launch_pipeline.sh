#!/usr/bin/env bash
set -euo pipefail
cd /data/Maojie
exec /home/exx/anaconda3/envs/maojie/bin/python \
  /data/Maojie/Other_benchmark/benchmark_bridge/pipeline.py \
  --output /data/Maojie/Other_benchmark/results/iclr_t1_author_solvers_20261106 \
  >> /data/Maojie/Other_benchmark/results/iclr_t1_author_solvers_20261106/pipeline.log 2>&1
