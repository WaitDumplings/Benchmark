"""Real-process deadline regressions for optional native status metadata.

No solver is launched. Each fake process emits an actual candidate event for
the existing canonical verifier, then exits or waits for the real watchdog.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import signal
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


class OptionalStatusMetadataTests(unittest.TestCase):
    def run_fake(self, files, *, exit_code=None, candidate=True, slow_replay=False):
        job = {"stage2_task": {"view_id": "synthetic_status_metadata", "family_id": "synthetic",
            "city_slug": "toy", "split_id": "test", "track_id": "test1_new_seed", "scale_id": "Cus100"},
            "solver_seed": 1234}
        instance = make_instance(n=1)
        budget = .15 if slow_replay else .6
        outputs = []

        def fake_command(solver, instance_dir, output, seed, supplied_budget, start):
            outputs.append(output)
            script = """
import json,os,sys,time
from pathlib import Path
p=Path(sys.argv[1]);start=float(sys.argv[2]);settings=json.loads(sys.argv[3])
for name,raw in settings['files'].items():
 (p/name).write_bytes(bytes.fromhex(raw))
if settings['candidate']:
 with (p/'events.jsonl').open('a') as stream:
  stream.write(json.dumps({'elapsed_s':time.monotonic()-start,'routes':[[0,1,0]]})+'\\n')
  stream.flush()
if settings['exit_code'] is None:
 time.sleep(30)
else:
 os._exit(settings['exit_code'])
"""
            settings = {"files": {name: raw.hex() for name, raw in files.items()},
                        "candidate": candidate, "exit_code": exit_code}
            return [sys.executable, "-c", script, str(output), str(start), json.dumps(settings)]

        actual_validate = common.validate_routes

        def validate(*args, **kwargs):
            if slow_replay:
                time.sleep(.3)
            return actual_validate(*args, **kwargs)

        with tempfile.TemporaryDirectory(prefix="iclr_status_metadata_") as tmp, \
             mock.patch.object(runner, "load_job", return_value=(instance, dict(job["stage2_task"], file="synthetic"))), \
             mock.patch.object(runner, "export_instance"), \
             mock.patch.object(runner, "command", side_effect=fake_command), \
             mock.patch.object(common, "validate_routes", side_effect=validate):
            result = runner.solve(job, "hma", tmp, budget, [budget],
                                  {"adaptation_sha256": "test-only"}, "test-only")
            self.assertEqual(len(outputs), 1)
            # Neither successful recovery nor an error may rewrite raw evidence.
            for name, raw in files.items():
                self.assertEqual((outputs[0] / name).read_bytes(), raw)
            audits = [json.loads(line) for line in (outputs[0] / "candidate_audits.jsonl").read_text().splitlines()]
            stored = json.loads(next(Path(tmp).rglob("result.json")).read_text())
            self.assertEqual(stored["summary_row"]["status"], result["summary_row"]["status"])
            self.assertEqual(stored["solution"], result["solution"])
        return result, audits, budget

    def test_watchdog_preserves_verified_incumbent_and_raw_partial_metadata(self):
        files = {"native_stop.json": b"", "events.status.json": b'{"stop_reason":',
                 "adapter_manifest.json": b'{"message":"\xff"}'}
        result, audits, _ = self.run_fake(files)
        row = result["summary_row"]
        self.assertEqual(row["exit_code"], -signal.SIGKILL)
        self.assertTrue(row["terminated_by_time_limit"])
        self.assertEqual(row["status"], "COMPLETED_WITH_INCUMBENT")
        self.assertTrue(row["has_incumbent"])
        self.assertTrue(row["feasible"])
        self.assertEqual(row["candidate_count"], 1)
        self.assertTrue(audits[0]["eligible"])
        self.assertEqual(row["objective_cost_usd"], common.OBJECTIVE.fields(2., 1)["objective_cost_usd"])
        self.assertEqual(result["solution"]["routes"], [[0, 1, 0]])
        warnings = json.loads(row["stage_times_json"])["status_read_warnings"]
        self.assertEqual(len(warnings), 3)
        for warning in warnings:
            raw = files[warning["file_name"]]
            self.assertEqual(warning["size_bytes"], len(raw))
            self.assertEqual(warning["sha256"], hashlib.sha256(raw).hexdigest())
            self.assertTrue(warning["raw_file_preserved"])
            self.assertIn(warning["error_type"], ("JSONDecodeError", "UnicodeDecodeError"))
            self.assertEqual(Path(warning["path"]).name, warning["file_name"])

    def test_watchdog_without_incumbent_does_not_invent_feasible_result(self):
        valid_manifest = {"schema": "synthetic", "seed": 1234}
        result, audits, _ = self.run_fake({"native_stop.json": b"",
            "adapter_manifest.json": json.dumps(valid_manifest).encode()}, candidate=False)
        row = result["summary_row"]
        self.assertEqual(row["status"], "UNFINISHED_NO_INCUMBENT")
        self.assertTrue(row["terminated_by_time_limit"])
        self.assertFalse(row["has_incumbent"])
        self.assertFalse(row["feasible"])
        self.assertEqual(row["candidate_count"], 0)
        self.assertIsNone(result["solution"])
        self.assertEqual(audits, [])
        metadata = json.loads(row["stage_times_json"])
        self.assertEqual(metadata["adapter_manifest.json"], valid_manifest)
        self.assertEqual(len(metadata["status_read_warnings"]), 1)

    def test_normal_exit_with_malformed_metadata_still_errors(self):
        result, audits, _ = self.run_fake({"native_stop.json": b""}, exit_code=0)
        row = result["summary_row"]
        self.assertEqual(row["exit_code"], 0)
        self.assertFalse(row["terminated_by_time_limit"])
        self.assertEqual(row["status"], "ERROR")
        self.assertIn("JSONDecodeError", row["errors"])
        self.assertTrue(audits[0]["eligible"])

    def test_normal_exit_with_slow_replay_is_not_a_metadata_write_interruption(self):
        result, audits, budget = self.run_fake({"events.status.json": b'{"stop":'},
                                              exit_code=0, slow_replay=True)
        row = result["summary_row"]
        self.assertEqual(row["exit_code"], 0)
        self.assertGreater(row["runtime_s"], budget)
        self.assertTrue(row["terminated_by_time_limit"])
        self.assertEqual(row["status"], "ERROR")
        self.assertIn("JSONDecodeError", row["errors"])
        self.assertTrue(audits[0]["eligible"])

    def test_native_error_with_valid_metadata_keeps_error_and_incumbent(self):
        result, audits, _ = self.run_fake({"native_stop.json": b'{"stop_reason":"error"}'},
                                         exit_code=23, slow_replay=True)
        row = result["summary_row"]
        self.assertEqual(row["exit_code"], 23)
        self.assertFalse(row["terminated_by_time_limit"])
        self.assertEqual(row["status"], "ERROR_WITH_INCUMBENT")
        self.assertTrue(row["has_incumbent"])
        self.assertEqual(row["early_stop_reason"], "adapter_error")
        self.assertEqual(json.loads(row["stage_times_json"])["native_stop.json"]["stop_reason"], "error")
        self.assertTrue(audits[0]["eligible"])

    def test_native_error_with_malformed_metadata_is_not_recovered(self):
        result, audits, _ = self.run_fake({"native_stop.json": b""},
                                         exit_code=23, slow_replay=True)
        row = result["summary_row"]
        self.assertEqual(row["exit_code"], 23)
        self.assertFalse(row["terminated_by_time_limit"])
        self.assertEqual(row["early_stop_reason"], "adapter_error")
        self.assertEqual(row["status"], "ERROR")
        self.assertIn("JSONDecodeError", row["errors"])
        self.assertTrue(audits[0]["eligible"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
