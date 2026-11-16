"""Offline acceptance tests for the frozen-plan execution runner.

No model calls and no browser trials: these tests cover plan tampering,
authorization scope, command regeneration and per-record resume verification.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import agent_stress_plan_runner as runner

ROOT = Path(__file__).resolve().parents[1]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TestPlanGuard(unittest.TestCase):
    def test_tampered_plan_starts_no_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan_path = Path(tmp) / "plan.json"
            plan_path.write_text(json.dumps({"schema": "agent_stress_screening_plan/3",
                                             "plan_sha256": "0" * 64,
                                             "cells": [], "planned_trials": 0}))
            with patch.object(runner, "preflight", return_value={"blockers": []}), \
                    patch.object(runner.subprocess, "run") as spawn:
                rc = runner.main(["--plan", str(plan_path),
                                  "--run-root", str(Path(tmp) / "run"), "--check"])
            self.assertEqual(rc, 2)
            spawn.assert_not_called()

    def test_authorization_requires_approval_and_matching_scope(self):
        plan = {"plan_sha256": "a" * 64, "cells": [{"cell_key": "x"}],
                "code": {"fingerprint_sha256": "b" * 64}, "dataset_sha256": "c" * 64}
        with tempfile.TemporaryDirectory() as tmp:
            auth = Path(tmp) / "auth.json"
            auth.write_text(json.dumps({
                "status": "candidate", "approved_for_paid_runs": False,
                "plan_sha256": "d" * 64, "cell_keys": ["y"],
                "prompt_token_stop_before_next_trial": 0,
            }))
            _, errors = runner.check_authorization(plan, auth)
        joined = " | ".join(errors)
        self.assertIn("not frozen", joined)
        self.assertIn("not approved", joined)
        self.assertIn("plan hash differs", joined)
        self.assertIn("cell scope differs", joined)
        self.assertIn("positive integer", joined)

    def test_authorization_accepts_matching_frozen_scope(self):
        plan = {"plan_sha256": "a" * 64, "cells": [{"cell_key": "x"}],
                "code": {"fingerprint_sha256": "b" * 64}, "dataset_sha256": "c" * 64}
        with tempfile.TemporaryDirectory() as tmp:
            auth = Path(tmp) / "auth.json"
            auth.write_text(json.dumps({
                "status": "frozen", "approved_for_paid_runs": True,
                "plan_sha256": "a" * 64, "cell_keys": ["x"],
                "prompt_token_stop_before_next_trial": 1000,
                "code_fingerprint_sha256": "b" * 64,
                "dataset_sha256": "c" * 64,
            }))
            _, errors = runner.check_authorization(plan, auth)
        self.assertEqual(errors, [])


class TestCompletedRecordVerification(unittest.TestCase):
    def _fixture(self, tmp: Path):
        arm = tmp / "arm"
        arm.mkdir()
        storage_sha = "s" * 64
        cell = {
            "cell_key": "m_react_control", "task_id": 1, "task": {"task_id": 1},
            "model_profile": "m", "architecture": "react", "seed": 1,
            "condition": "control", "fault_variant": "single",
            "experiment_id": "stage-c", "max_steps": 20,
            "site": "shopping", "arm_dir": str(arm),
        }
        plan = {"task": {"site": "shopping"}, "storage_state_sha256": {"shopping": storage_sha}}
        trace = arm / "trace.jsonl"
        trace.write_text(json.dumps({"event": "llm_provenance", "prompt_tokens": 10}) + "\n")
        record = {
            "task_id": 1, "model_profile": "m", "architecture": "react",
            "fault_seed": 1, "condition": "control", "fault_variant": "single",
            "experiment_id": "stage-c", "injection_count": 0, "steps": 2,
            "completed": True, "paths": {"trace": str(trace)},
            "run_config": {"max_steps": 20, "submission_contract": True,
                           "retrieval_repair": True, "storage_state_sha256": storage_sha},
            "code": {"git_sha": "deadbeef"},
        }
        (arm / "trial_record.json").write_text(json.dumps(record))
        (arm / "agent_response.json").write_text(json.dumps({"retrieved_data": ["x"]}))
        (arm / "network.har").write_text("{}")
        binding = {
            "agent_response_sha256": _sha(arm / "agent_response.json"),
            "network_har_sha256": _sha(arm / "network.har"),
            "config_sha256": runner.file_sha(runner.CONFIG),
        }
        (arm / "official_eval.json").write_text(json.dumps({
            "official_success": False, "status": "failure", "score": 0.0,
            "_evaluation_binding": binding}))
        return arm, cell, plan

    def test_valid_official_failure_counts_as_completed(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, cell, plan = self._fixture(Path(tmp))
            row, errors = runner.verify_completed(cell, plan)
        self.assertEqual(errors, [])
        self.assertFalse(row["official_success"])
        self.assertEqual(row["prompt_tokens"], 10)

    def test_identity_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            arm, cell, plan = self._fixture(Path(tmp))
            record = json.loads((arm / "trial_record.json").read_text())
            record["model_profile"] = "other"
            (arm / "trial_record.json").write_text(json.dumps(record))
            row, errors = runner.verify_completed(cell, plan)
        self.assertIsNone(row)
        self.assertTrue(any("model_profile" in error for error in errors))

    def test_missing_usage_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            arm, cell, plan = self._fixture(Path(tmp))
            (arm / "trace.jsonl").write_text(json.dumps({"event": "step"}) + "\n")
            row, errors = runner.verify_completed(cell, plan)
        self.assertIsNone(row)
        self.assertTrue(any("llm_provenance" in error for error in errors))


class TestStoredCommands(unittest.TestCase):
    def test_plans_commands_match_validated_parameters(self):
        plans = [
            ROOT / "experiments/http_acceptance_v1/plan_20261116/plan.json",
            ROOT / "experiments/agent_stress_v1/screening_480_plan_20261116/plan.json",
        ]
        for path in plans:
            if not path.is_file():
                self.skipTest(f"plan not generated: {path}")
            plan = json.loads(path.read_text())
            for cell in plan["cells"][:3]:
                self.assertEqual(runner.expected_command(cell, plan), cell["command"])


if __name__ == "__main__":
    unittest.main()
