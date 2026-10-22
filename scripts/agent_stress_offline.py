#!/usr/bin/env python3
"""Offline audit and workload forecast for the stress proposal. No trial executor."""
from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC_DIR = ROOT / "docs" / "agent_stress_v1"
REQUIRED_BINDINGS = (
    "decision_impact",
    "access_evidence", "main_resource", "entity_identity", "field_contract",
    "detector_profile", "protected_sources", "evidence_mapping",
    "use_judgment", "recovery_references", "screening_evidence",
)
REFERENCE_CONDITIONS = ("H_episode", "C_episode", "HC_episode")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def number(value, label, *, positive=False):
    if isinstance(value, bool):
        raise ValueError(f"{label}: boolean is not a number")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"{label}: invalid number") from exc
    if not result.is_finite() or result < 0 or (positive and result == 0):
        raise ValueError(f"{label}: expected finite {'positive' if positive else 'nonnegative'} value")
    return result


def estimate(matrix, assumptions):
    baseline = assumptions["baseline"]
    n = number(baseline["observed_trials"], "observed_trials", positive=True)
    prompt = number(baseline["prompt_tokens_total"], "prompt_tokens_total") / n
    completion = number(baseline["completion_tokens_total"], "completion_tokens_total") / n
    rows = []
    for stage in matrix["stages"]:
        for key in ("tasks", "architectures", "repetitions", "conditions", "planned_trials"):
            if type(stage[key]) is not int or stage[key] < 1:
                raise ValueError(f"{stage['id']}.{key}: expected positive integer")
        product = stage["tasks"] * stage["architectures"] * stage["repetitions"] * stage["conditions"]
        if product != stage["planned_trials"]:
            raise ValueError(f"{stage['id']}: stage count mismatch")
        factors = assumptions["stage_multipliers"][stage["id"]]
        trials = Decimal(stage["planned_trials"])
        rows.append({
            "stage": stage["id"], "trials": int(trials),
            "prompt_tokens": float(trials * prompt * number(factors["prompt"], "prompt factor", positive=True)),
            "completion_tokens": float(trials * completion * number(factors["completion"], "completion factor", positive=True)),
        })
    if sum(r["trials"] for r in rows) != matrix["planned_total_trials"]:
        raise ValueError("total trial count mismatch")
    total_prompt = sum(Decimal(str(r["prompt_tokens"])) for r in rows)
    total_completion = sum(Decimal(str(r["completion_tokens"])) for r in rows)

    def priced(p, c, in_price, out_price):
        return float((p * number(in_price, "input price") + c * number(out_price, "output price")) / Decimal(1000000))

    scenarios = {}
    reserve = number(assumptions["planning_reserve_fraction"], "reserve fraction")
    for name, factor in assumptions["workload_scenarios"].items():
        factor = number(factor, f"scenario {name}", positive=True)
        p, c = total_prompt * factor, total_completion * factor
        scenarios[name] = {
            "prompt_tokens": float(p), "completion_tokens": float(c),
            "prompt_tokens_with_reserve": float(p * (1 + reserve)),
            "completion_tokens_with_reserve": float(c * (1 + reserve)),
            "illustrative_costs_only": [
                {"tariff": tariff["name"],
                 "currency": assumptions["billing"]["currency"],
                 "amount": round(priced(p, c, tariff["prompt_per_million"], tariff["completion_per_million"]), 4),
                 "amount_with_reserve": round(priced(p * (1 + reserve), c * (1 + reserve),
                    tariff["prompt_per_million"], tariff["completion_per_million"]), 4)}
                for tariff in assumptions["illustrative_tariffs"]
            ],
        }
    billing = assumptions["billing"]
    inp, outp = billing.get("actual_prompt_per_million"), billing.get("actual_completion_per_million")
    actual = None
    if (inp is None) != (outp is None):
        raise ValueError("both actual input/output prices must be supplied together")
    if inp is not None:
        actual = priced(total_prompt, total_completion, inp, outp)
    return {
        "schema": "agent_stress_cost_estimate/1",
        "planned_trials": matrix["planned_total_trials"],
        "baseline_prompt_per_trial": float(prompt),
        "baseline_completion_per_trial": float(completion),
        "uniform_baseline_prompt_tokens": float(Decimal(matrix["planned_total_trials"]) * prompt),
        "stage_adjusted_prompt_tokens": float(total_prompt),
        "stage_adjusted_completion_tokens": float(total_completion),
        "stages": rows, "workload_scenarios": scenarios,
        "actual_tariff_supplied": inp is not None,
        "actual_tariff_forecast_before_cache_or_reserve": actual,
        "forecast_is_actual_billing": False,
        "paid_trials_executed_by_this_tool": 0,
        "limitations": assumptions["notes"],
    }


def _verify_module():
    import sys
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from scripts.agent_stress_decision import verify
    return verify


def _decision_examples(root=ROOT):
    """Run the recomputation verifier on the committed positive/negative examples."""
    verify = _verify_module()
    examples = {}
    for name, expected in (("fixture_reviews", True), ("task118_title", False)):
        path = root / "experiments/agent_stress_v1/decisions" / name / "evidence.json"
        if not path.exists():
            examples[name] = "missing"
            continue
        result = verify(read_json(path), root=root)
        examples[name] = "passed" if result["passed"] else "rejected"
        if result["passed"] is not expected:
            examples[name] += ":unexpected"
    return examples


def check_decision_impact(item, *, root=ROOT):
    """已验证契约必须提供**可复算证据**；仅声明布尔字段不足以通过。

    这里调用 agent_stress_decision.verify()：它会重跑 computation、核对真值/污染/
    保护源输出、官方规则与工件哈希。声明层只用于早期提示，不能作为通过依据。
    """
    if item.get("status") != "verified":
        return []
    di = item.get("decision_impact")
    if not isinstance(di, dict) or not di.get("evidence"):
        return ["decision_impact: declaration without recomputable evidence"]
    verify = _verify_module()
    evidence = di["evidence"]
    result = verify(evidence, root=root,
                    require_hc="HC_episode" in (item.get("recovery_references") or {}))
    messages = [f"decision_impact: {e}" for e in result["errors"]]
    if not result["real_contract_eligible"]:
        messages.append("decision_impact: native grading required; examples do not verify real tasks")
    if (evidence.get("official") or {}).get("task_id") != item.get("task_id"):
        messages.append("decision_impact: grading task_id differs from contract")
    return messages


def audit(candidates, contracts, protocol, matrix, *, artifact_root=ROOT):
    errors = []
    source = {t["task_id"]: t for t in candidates["tasks"]}
    profiles = {t["task_id"]: t for t in contracts["tasks"]}
    if len(source) != len(candidates["tasks"]):
        errors.append("duplicate candidate IDs")
    if len(profiles) != len(contracts["tasks"]):
        errors.append("duplicate contract IDs")
    if set(source) != set(profiles):
        errors.append("candidate/contract identity sets differ")
    split_templates = {}
    login_by_split = {}
    for item in candidates["tasks"]:
        split_templates.setdefault(item["split_role"], set()).add(item["intent_template_id"])
        bucket = login_by_split.setdefault(item["split_role"],
                                           {"true": 0, "false": 0, "unknown": 0})
        value = item.get("requires_login")
        bucket["true" if value is True else "false" if value is False else "unknown"] += 1
        if item.get("requires_login") is not None and type(item["requires_login"]) is not bool:
            errors.append(f"task {item['task_id']}: invalid requires_login")
    if split_templates.get("development", set()) & split_templates.get("holdout_candidate", set()):
        errors.append("development and heldout template overlap")
    unresolved = []
    decision_results = {}
    verified_ids = []
    for task_id, item in profiles.items():
        src = source.get(task_id, {})
        for key in ("intent_template_id", "split_role", "site", "requires_login"):
            if item.get(key) != src.get(key):
                errors.append(f"task {task_id}: {key} differs from candidate metadata")
        missing = [key for key in REQUIRED_BINDINGS if not item.get(key)]
        if item.get("requires_login") is None:
            missing.append("requires_login")
        if missing or item.get("status") != "verified":
            unresolved.append({"task_id": task_id, "missing": missing, "status": item.get("status")})
        if item.get("status") != "verified":
            continue
        errors_before = len(errors)
        if missing:
            errors.append(f"task {task_id}: claims verified with missing bindings")
        decision_results[task_id] = check_decision_impact(item, root=artifact_root)
        for message in decision_results[task_id]:
            errors.append(f"task {task_id}: {message}")
        for protected in item.get("protected_sources", []):
            if protected.get("excluded_from_H") is not True or protected.get("excluded_from_C") is not True:
                errors.append(f"task {task_id}: protected source not excluded from both faults")
        for condition in REFERENCE_CONDITIONS:
            ref = item.get("recovery_references", {}).get(condition) or {}
            steps = ref.get("total_steps")
            if (ref.get("condition") != condition or type(steps) is not int
                    or not 1 <= steps <= matrix["scope"]["max_steps"]
                    or ref.get("native_success") is not True
                    or ref.get("protected_fact_read") is not True):
                errors.append(f"task {task_id}: unverified {condition} recovery reference")
            artifacts = ref.get("artifacts") or []
            if len(artifacts) < 7:
                errors.append(f"task {task_id}: incomplete {condition} reference artifacts")
            for artifact in artifacts:
                path = Path(artifact.get("path", ""))
                if path.is_absolute() or ".." in path.parts or not path.parts:
                    errors.append(f"task {task_id}: reference path must be project-relative")
                    continue
                try:
                    digest = hashlib.sha256((artifact_root / path).read_bytes()).hexdigest()
                    if digest != artifact.get("sha256"):
                        errors.append(f"task {task_id}: reference artifact hash mismatch")
                except OSError:
                    errors.append(f"task {task_id}: reference artifact missing")
        if len(errors) == errors_before:
            verified_ids.append(task_id)
    blockers = []
    if protocol.get("implemented") is not True:
        blockers.append("H/C runtime not implemented")
    if protocol.get("approved_for_paid_runs") is not True or matrix.get("approved_for_paid_runs") is not True:
        blockers.append("no paid-run authorization")
    if unresolved:
        blockers.append("task-specific bindings/replays still unresolved")
    if not matrix.get("enrolled_holdout_task_ids"):
        blockers.append("heldout task enrollment not frozen")
    if not any(not messages for messages in decision_results.values()):
        blockers.append("decision-impact evidence not established")
    return {
        "schema": "agent_stress_offline_audit/1",
        "candidate_count": len(source), "contract_count": len(profiles),
        "verified_contracts": len(verified_ids),
        "unresolved_contract_count": len(unresolved),
        "errors": errors, "structural_checks_passed": not errors,
        "paid_run_blockers": blockers,
        "ready_for_paid_runs": not errors and not blockers,
        "unresolved_tasks": unresolved,
        "login_requirements_by_split": login_by_split,
        "decision_impact_examples": _decision_examples(root=artifact_root),
        "decision_impact_verified_contracts": sum(not result for result in decision_results.values()),
        "validation_scope": "identity/readiness/linked-artifact checks; does not execute H/C or verify real browser solvability",
        "paid_trials_executed_by_this_tool": 0,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec-dir", type=Path, default=SPEC_DIR)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--require-ready", action="store_true",
                        help="Return 2 while runtime, bindings, enrollment or authorization is incomplete.")
    args = parser.parse_args(argv)
    spec_dir = args.spec_dir
    matrix = read_json(spec_dir.parent / "故障压力测试矩阵草案.json")
    candidates = read_json(spec_dir.parent / "故障压力测试候选任务.json")
    assumptions = read_json(spec_dir / "cost_assumptions.json")
    protocol = read_json(spec_dir / "protocol_spec.json")
    contracts = read_json(spec_dir / "task_contracts.json")
    result = audit(candidates, contracts, protocol, matrix)
    forecast = estimate(matrix, assumptions)
    if args.output_dir:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        for name, data in (("offline_audit.json", result), ("cost_estimate.json", forecast)):
            (args.output_dir / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "structural_checks_passed": result["structural_checks_passed"],
        "ready_for_paid_runs": result["ready_for_paid_runs"],
        "unresolved_contracts": result["unresolved_contract_count"],
        "planned_trials": forecast["planned_trials"],
        "forecast_prompt_tokens": forecast["stage_adjusted_prompt_tokens"],
        "actual_tariff_supplied": forecast["actual_tariff_supplied"],
        "paid_trials_executed": 0,
    }, ensure_ascii=False, indent=2))
    return 2 if result["errors"] or (args.require_ready and not result["ready_for_paid_runs"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
