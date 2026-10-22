"""Offline proposal checks; these are not H/C runtime or paid-trial tests."""
from __future__ import annotations
import copy
from pathlib import Path
import unittest

from scripts.agent_stress_offline import audit, estimate, read_json

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "docs" / "agent_stress_v1"


class TestAgentStressOffline(unittest.TestCase):
    def setUp(self):
        self.matrix = read_json(ROOT / "docs/故障压力测试矩阵草案.json")
        self.candidates = read_json(ROOT / "docs/故障压力测试候选任务.json")
        self.contracts = read_json(SPEC / "task_contracts.json")
        self.protocol = read_json(SPEC / "protocol_spec.json")
        self.assumptions = read_json(SPEC / "cost_assumptions.json")

    def test_unresolved_proposal_is_valid_but_not_ready(self):
        report = audit(self.candidates, self.contracts, self.protocol, self.matrix)
        self.assertTrue(report["structural_checks_passed"])
        self.assertEqual(report["unresolved_contract_count"], 58)
        self.assertFalse(report["ready_for_paid_runs"])
        self.assertIn("H/C runtime not implemented", report["paid_run_blockers"])

    def test_claiming_verified_does_not_hide_missing_reference(self):
        contracts = copy.deepcopy(self.contracts)
        contracts["tasks"][0]["status"] = "verified"
        report = audit(self.candidates, contracts, self.protocol, self.matrix)
        self.assertFalse(report["structural_checks_passed"])
        self.assertTrue(any("claims verified with missing bindings" in e for e in report["errors"]))

    def test_template_leakage_is_rejected(self):
        candidates = copy.deepcopy(self.candidates)
        dev_template = next(t["intent_template_id"] for t in candidates["tasks"] if t["split_role"] == "development")
        next(t for t in candidates["tasks"] if t["split_role"] == "holdout_candidate")["intent_template_id"] = dev_template
        report = audit(candidates, self.contracts, self.protocol, self.matrix)
        self.assertTrue(any("template overlap" in e for e in report["errors"]))

    def test_metadata_mismatch_is_rejected(self):
        contracts = copy.deepcopy(self.contracts)
        contracts["tasks"][0]["requires_login"] = not bool(contracts["tasks"][0]["requires_login"])
        report = audit(self.candidates, contracts, self.protocol, self.matrix)
        self.assertTrue(any("requires_login differs" in e for e in report["errors"]))

    def test_cost_includes_long_budget_and_keeps_unknown_price_unknown(self):
        result = estimate(self.matrix, self.assumptions)
        self.assertEqual(result["planned_trials"], 2124)
        self.assertAlmostEqual(result["baseline_prompt_per_trial"], 365305 / 4)
        self.assertAlmostEqual(result["uniform_baseline_prompt_tokens"], 2124 * 365305 / 4)
        self.assertAlmostEqual(result["stage_adjusted_prompt_tokens"], (348 + 432 + 1152 + 192 * 2.5) * 365305 / 4)
        self.assertFalse(result["actual_tariff_supplied"])
        self.assertIsNone(result["actual_tariff_forecast_before_cache_or_reserve"])
        self.assertFalse(result["forecast_is_actual_billing"])

    def test_pricing_units_and_invalid_tariffs(self):
        assumptions = copy.deepcopy(self.assumptions)
        assumptions["billing"]["actual_prompt_per_million"] = 1
        assumptions["billing"]["actual_completion_per_million"] = 2
        result = estimate(self.matrix, assumptions)
        expected = (result["stage_adjusted_prompt_tokens"] + result["stage_adjusted_completion_tokens"] * 2) / 1_000_000
        self.assertAlmostEqual(result["actual_tariff_forecast_before_cache_or_reserve"], expected)
        assumptions["billing"]["actual_prompt_per_million"] = -1
        with self.assertRaises(ValueError):
            estimate(self.matrix, assumptions)

    def test_stage_count_mismatch_and_partial_price_are_rejected(self):
        matrix = copy.deepcopy(self.matrix)
        matrix["stages"][0]["planned_trials"] += 1
        with self.assertRaises(ValueError):
            estimate(matrix, self.assumptions)
        assumptions = copy.deepcopy(self.assumptions)
        assumptions["billing"]["actual_prompt_per_million"] = 1
        with self.assertRaises(ValueError):
            estimate(self.matrix, assumptions)


class TestDecisionImpactEvidence(unittest.TestCase):
    """声明布尔不再充分；已验证契约必须能重算并被官方规则拒绝污染输出。"""

    def setUp(self):
        self.matrix = read_json(ROOT / "docs/故障压力测试矩阵草案.json")
        self.candidates = read_json(ROOT / "docs/故障压力测试候选任务.json")
        self.contracts = read_json(SPEC / "task_contracts.json")
        self.protocol = read_json(SPEC / "protocol_spec.json")
        self.decisions = ROOT / "experiments/agent_stress_v1/decisions"

    def test_declaration_without_evidence_is_rejected(self):
        from scripts.agent_stress_offline import check_decision_impact
        item = {"status": "verified", "decision_impact": {
            "changes_required_output": True,
            "official_tolerance_accepts_polluted": False,
            "protected_source_output_matches_truth": True,
            "decision_check": "pass"}}
        errors = check_decision_impact(item)
        self.assertEqual(errors, ["decision_impact: declaration without recomputable evidence"])

    def test_fixture_evidence_recomputes_and_passes(self):
        from scripts.agent_stress_decision import verify
        evidence = read_json(self.decisions / "fixture_reviews/evidence.json")
        result = verify(evidence)
        self.assertTrue(result["passed"], result["errors"])
        # 污染确实改变任务输出：[] -> ["Josef Bürger"]
        self.assertEqual(result["outputs"]["truth"], {"authors": []})
        self.assertEqual(result["outputs"]["polluted"], {"authors": ["Josef Bürger"]})

    def test_real_task118_title_change_has_no_decision_impact(self):
        from scripts.agent_stress_decision import verify
        evidence = read_json(self.decisions / "task118_title/evidence.json")
        result = verify(evidence)
        self.assertFalse(result["passed"])
        self.assertTrue(any("official:polluted" in e for e in result["errors"]))
        self.assertTrue(result["grading"]["protected"])
        self.assertFalse(any("protected source" in e for e in result["errors"]))

    def test_hash_tampering_is_rejected(self):
        import copy
        from scripts.agent_stress_decision import verify
        evidence = copy.deepcopy(read_json(self.decisions / "fixture_reviews/evidence.json"))
        evidence["inputs"]["truth"]["sha256"] = "0" * 64
        result = verify(evidence)
        self.assertFalse(result["passed"])
        self.assertTrue(any("sha256 mismatch" in e for e in result["errors"]))

    def test_hc_payload_with_hidden_c_content_is_rejected(self):
        import copy, hashlib, json
        from scripts.agent_stress_decision import verify
        evidence = copy.deepcopy(read_json(self.decisions / "fixture_reviews/evidence.json"))
        payload = ROOT / "experiments/agent_stress_v1/decisions/_hc_hidden_test.json"
        try:
            payload.write_text(json.dumps({"marker": "RESOURCE UNAVAILABLE: reviews list"}),
                               encoding="utf-8")
            evidence["hc"]["hc_payload"] = {
                "path": str(payload.relative_to(ROOT)),
                "sha256": hashlib.sha256(payload.read_bytes()).hexdigest()}
            result = verify(evidence)
        finally:
            payload.unlink(missing_ok=True)
        self.assertFalse(result["passed"])
        self.assertTrue(any("C content" in e for e in result["errors"]))

    def test_audit_reports_login_requirements_separately_by_split(self):
        report = audit(self.candidates, self.contracts, self.protocol, self.matrix)
        split = report["login_requirements_by_split"]
        self.assertEqual(set(split), {"development", "holdout_candidate"})
        self.assertEqual(sum(split["development"].values()), 18)
        self.assertEqual(sum(split["holdout_candidate"].values()), 40)
        self.assertIn("decision-impact evidence not established",
                      report["paid_run_blockers"])


if __name__ == "__main__":
    unittest.main()
