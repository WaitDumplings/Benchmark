"""Short real-process regressions for the shared queue lease and error gate.

No solver is launched. The fake native process only holds an inherited descriptor.
"""
from __future__ import annotations

import fcntl
import json
import multiprocessing
from multiprocessing.reduction import DupFd
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / "benchmark_bridge"))
import common
import run as runner
from test_bridge import make_instance


def _native_script():
    return """
import json,os,sys,time
from pathlib import Path
p=Path(sys.argv[1]);fd=int(os.environ['ICLR_BENCHMARK_LOCK_FD'])
(p/'native.json').write_text(json.dumps({'pid':os.getpid(),'inode':os.fstat(fd).st_ino}))
until=time.monotonic()+10
while not (p/'release').exists() and time.monotonic()<until:time.sleep(.01)
"""


def _lease_worker(queue, directory):
    runner.initialize_worker(queue)
    root = Path(directory)
    (root / "worker.pid").write_text(str(os.getpid()))
    wrapper = """
import os,subprocess,sys
from pathlib import Path
p=Path(sys.argv[1]);(p/'wrapper.pid').write_text(str(os.getpid()))
fd=int(os.environ['ICLR_BENCHMARK_LOCK_FD']);os.fstat(fd)
subprocess.run([sys.executable,'-c',sys.argv[2],str(p)],pass_fds=(fd,))
"""
    replay = common.CandidateReplay(make_instance(n=1), [10.], 10.)
    runner.supervise([sys.executable, "-c", wrapper, str(root), _native_script()],
                     root / "attempt", time.monotonic(), 10., replay)


def _coordinator(directory):
    root = Path(directory)
    with (root / "queue.lock").open("a+") as lease:
        fcntl.flock(lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        context = multiprocessing.get_context("spawn")
        queue = context.Queue()
        queue.put((min(os.sched_getaffinity(0)), DupFd(lease.fileno())))
        worker = context.Process(target=_lease_worker, args=(queue, directory))
        worker.start()
        time.sleep(20)


def _kill_owned(pid):
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


class QueueLeaseTests(unittest.TestCase):
    def test_coordinator_and_worker_death_cannot_unlock_active_native(self):
        with tempfile.TemporaryDirectory(prefix="iclr_queue_lease_") as tmp:
            root = Path(tmp)
            log = (root / "coordinator.log").open("w")
            coordinator = subprocess.Popen([sys.executable, __file__, "--coordinator", tmp],
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            owned = [coordinator.pid]
            try:
                until = time.monotonic() + 6
                while not (root / "native.json").exists() and time.monotonic() < until:
                    if coordinator.poll() is not None:
                        break
                    time.sleep(.01)
                self.assertTrue((root / "native.json").exists(), (root / "coordinator.log").read_text())
                native = json.loads((root / "native.json").read_text())
                worker = int((root / "worker.pid").read_text())
                wrapper = int((root / "wrapper.pid").read_text())
                owned.extend([worker, wrapper, native["pid"]])
                self.assertEqual(native["inode"], (root / "queue.lock").stat().st_ino)
                with (root / "queue.lock").open("a+") as contender:
                    for pid in (coordinator.pid, worker, wrapper):
                        _kill_owned(pid)
                        if pid == coordinator.pid:
                            coordinator.wait(timeout=2)
                        time.sleep(.04)
                        with self.assertRaises(BlockingIOError, msg=f"lease released after PID {pid} died"):
                            fcntl.flock(contender.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    (root / "release").touch()
                    until = time.monotonic() + 2
                    while True:
                        try:
                            fcntl.flock(contender.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                            break
                        except BlockingIOError:
                            if time.monotonic() >= until:
                                self.fail("lease survived after the last owned native process exited")
                            time.sleep(.01)
            finally:
                for name in ("worker.pid", "wrapper.pid"):
                    if (root / name).exists():
                        owned.append(int((root / name).read_text()))
                if (root / "native.json").exists():
                    owned.append(json.loads((root / "native.json").read_text())["pid"])
                for pid in set(owned):
                    _kill_owned(pid)
                coordinator.wait(timeout=2)
                log.close()

    def test_native_error_is_not_hidden_by_slow_canonical_replay(self):
        job = {"stage2_task": {"view_id": "synthetic_native_error", "family_id": "synthetic",
            "city_slug": "toy", "split_id": "test", "track_id": "test1_new_seed", "scale_id": "Cus100"},
            "solver_seed": 1234}
        instance = make_instance(n=1)
        actual_validate = common.validate_routes
        def slow_validate(*args, **kwargs):
            time.sleep(.25)
            return actual_validate(*args, **kwargs)
        def fake_command(solver, instance_dir, output, seed, budget, start):
            script = """
import json,os,sys,time
from pathlib import Path
p=Path(sys.argv[1]);start=float(sys.argv[2])
with (p/'events.jsonl').open('a') as f:
 f.write(json.dumps({'elapsed_s':time.monotonic()-start,'routes':[[0,1,0]]})+'\\n');f.flush()
os._exit(23)
"""
            return [sys.executable, "-c", script, str(output), str(start)]
        with tempfile.TemporaryDirectory(prefix="iclr_error_gate_") as tmp, \
             mock.patch.object(runner, "load_job", return_value=(instance, dict(job["stage2_task"], file="synthetic"))), \
             mock.patch.object(runner, "export_instance"), \
             mock.patch.object(runner, "command", side_effect=fake_command), \
             mock.patch.object(common, "validate_routes", side_effect=slow_validate):
            result = runner.solve(job, "hma", tmp, .15, [.15],
                                  {"adaptation_sha256": "test-only"}, "test-only")
        row = result["summary_row"]
        self.assertGreater(row["runtime_s"], .15)
        self.assertEqual(row["exit_code"], 23)
        self.assertEqual(row["status"], "ERROR_WITH_INCUMBENT")
        self.assertFalse(row["terminated_by_time_limit"])
        self.assertEqual(row["early_stop_reason"], "adapter_error")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--coordinator":
        _coordinator(sys.argv[2])
    else:
        unittest.main(verbosity=2)
