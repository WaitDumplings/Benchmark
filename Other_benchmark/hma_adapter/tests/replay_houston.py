"""Replay the retained Houston crash with the original input, seed and search options."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

p = argparse.ArgumentParser()
p.add_argument('--binary', required=True)
p.add_argument('--output-dir', required=True)
p.add_argument('--budget-s', type=float, default=300)
args = p.parse_args()
base = Path(__file__).resolve().parents[2]
original = base / 'results/iclr_t1_author_solvers_20261106/smoke/hma/instances/iv_7cb53d6f98d45d414a59d2bc/d06ba2ad7afced12/attempt_3bcd44563e'
manifest = json.loads((original / 'adapter_manifest.json').read_text())
output = Path(args.output_dir).resolve()
output.mkdir(parents=True, exist_ok=False)
command = manifest['command'][:]
command[0] = str(Path(args.binary).resolve())
command[command.index('--output') + 1] = str(output / 'native_solution.txt')
command[command.index('--time') + 1] = str(args.budget_s)
env = os.environ.copy()
for name in ['OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'BLIS_NUM_THREADS']:
    env[name] = '1'
env.pop('HMA_ACK_PATH', None)
env['HMA_BUDGET_S'] = str(args.budget_s)
env['HMA_EVENTS_PATH'] = str(output / 'events.jsonl')
env['HMA_STOP_PATH'] = str(output / 'native_stop.json')
env.setdefault('ASAN_OPTIONS', 'detect_leaks=0:halt_on_error=1')
env.setdefault('UBSAN_OPTIONS', 'print_stacktrace=1:halt_on_error=1')
started = time.monotonic()
env['HMA_START_MONOTONIC'] = str(started)
record = dict(command=command, binary_sha256=hashlib.sha256(Path(command[0]).read_bytes()).hexdigest(), original_manifest=str(original / 'adapter_manifest.json'), budget_s=args.budget_s, ack=False, sanitizer_options={k:env[k] for k in ['ASAN_OPTIONS','UBSAN_OPTIONS']})
(output / 'replay_manifest.json').write_text(json.dumps(record, indent=2)+'\n')
with (output / 'native.log').open('w') as log:
    result = subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=args.budget_s+10)
record.update(returncode=result.returncode, elapsed_s=time.monotonic()-started)
(output / 'process_result.json').write_text(json.dumps(record, indent=2)+'\n')
print(json.dumps(record, indent=2))
raise SystemExit(result.returncode)
