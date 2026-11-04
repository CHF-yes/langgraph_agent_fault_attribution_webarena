#!/usr/bin/env python3
"""Offline diagnosis of the 108 development screening runs.

Reads existing run artifacts, extracts tool observations and assistant
content from traces, and writes evidence-linked cause labels. This entry
never constructs an LLM and never starts a browser.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CANDIDATES = ROOT / "docs" / "故障压力测试候选任务.json"
DEFAULT_EXPERIMENTS = ROOT / "experiments" / "agent_stress_v1"
DEFAULT_OUTPUT = DEFAULT_EXPERIMENTS / "development_diagnosis"
SCREENING_GLOB = "screening_qwen*_run1"
MAX_STEPS = 20


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def norm(value):
    text = str(value).lower()
    text = (text.replace("\u2019", "'").replace("\u2018", "'")
            .replace("\u201c", '"').replace("\u201d", '"'))
    text = re.sub(r"[*_`#>\[\]\-/]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def numeric_is_ambiguous(value):
    return is_number(value) and abs(float(value)) < 10


def leaves(value, path=""):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from leaves(item, f"{path}/{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from leaves(item, f"{path}/{index}")
    else:
        yield path, value


def expected_regex(value):
    if not isinstance(value, str) or not value:
        return None
    if value.startswith("^") or value.endswith("$") or "\\" in value:
        return value
    return None


def loose_regex(value):
    text = value
    if text.startswith("^"):
        text = text[1:]
    if text.endswith("$"):
        text = text[:-1]
    return text


def number_pattern(value):
    if isinstance(value, float) and value.is_integer():
        text = str(int(value))
    else:
        text = str(value)
    if "." in text:
        return re.compile(r"(?<![\d.])" + re.escape(text) + r"(?![\d.])")
    return re.compile(r"(?<!\d)0*" + re.escape(text) + r"(?!\d)")


def match_in_text(value, text):
    if text is None:
        return False
    if is_number(value):
        return bool(number_pattern(value).search(text))
    if isinstance(value, str):
        pattern = expected_regex(value)
        if pattern:
            found = re.search(loose_regex(pattern), text)
            if found:
                return True
            for part in re.split(r"[^0-9A-Za-z.]+", re.sub(r"\\[a-zA-Z]", " ", value)):
                if len(part) >= 2 and part.lower() not in {"s", "d", "w"}:
                    if re.search(r"(?<!\d)" + re.escape(part) + r"(?!\d)", text):
                        return True
            return False
        needle = norm(value)
        if not needle:
            return False
        if needle in text:
            return True
        if len(needle) > 40 and norm(needle[:40]) in text:
            return True
    return False


def actual_leaves(value):
    return [leaf for _, leaf in leaves(value)]


def match_in_actual(value, actual):
    if is_number(value):
        for leaf in actual_leaves(actual):
            if is_number(leaf) and abs(float(leaf) - float(value)) < 1e-9:
                return True
            if isinstance(leaf, str):
                text = leaf.strip().lower()
                number = str(int(value)) if isinstance(value, float) and value.is_integer() else str(value)
                if re.fullmatch(r"[#\s]*0*" + re.escape(number) + r"(?:\.0+)?", text):
                    return True
        return False
    if isinstance(value, str):
        pattern = expected_regex(value)
        for leaf in actual_leaves(actual):
            if not isinstance(leaf, str):
                continue
            text = norm(leaf)
            if pattern:
                if re.search(loose_regex(pattern), text):
                    return True
                continue
            needle = norm(value)
            if needle and (needle in text or (len(needle) > 40 and norm(needle[:40]) in text)):
                return True
        return False
    return False


def read_trace(path):
    events = []
    with Path(path).open(encoding="utf-8") as stream:
        for line in stream:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def trace_text(events, *, events_only=False):
    parts = []
    for event in events:
        if events_only and event.get("event") is None:
            continue
        if event.get("event") is None:
            parts.append(event.get("observation") or "")
        else:
            parts.append(event.get("content") or "")
    return norm("\n".join(parts))


def observation_steps(events):
    steps = []
    for event in events:
        if event.get("event") is None:
            observation = norm(event.get("observation") or "")
            if observation:
                steps.append((int(event.get("step") or 0), observation))
    return steps


def first_seen(value, steps):
    for step, text in steps:
        if match_in_text(value, text):
            return step
    return None


def first_pattern_step(events, patterns):
    for event in events:
        text = norm((event.get("observation") or "") if event.get("event") is None else (event.get("content") or ""))
        for pattern in patterns:
            if pattern.search(text):
                return int(event.get("step") or 0), pattern.pattern, text[:240]
    return None, None, None


def pattern_hits(events, patterns):
    hits = []
    for event in events:
        text = norm((event.get("observation") or "") if event.get("event") is None else (event.get("content") or ""))
        for pattern in patterns:
            match = pattern.search(text)
            if match:
                hits.append({
                    "step": int(event.get("step") or 0),
                    "pattern": pattern.pattern,
                    "matched": match.group(0),
                })
                break
    return hits


def tool_errors(events):
    found = []
    for event in events:
        if event.get("event") is None and event.get("error"):
            found.append({
                "step": int(event.get("step") or 0),
                "action": event.get("action"),
                "args": event.get("args"),
                "observation": (event.get("observation") or "")[:240],
            })
    return found


def shape(value):
    if value is None:
        return "null"
    if isinstance(value, list):
        kinds = sorted({type(item).__name__ for item in value})
        return "list[" + ",".join(kinds) + "]"
    return type(value).__name__


def shape_mismatch(expected, actual):
    if type(expected) is not type(actual):
        return True
    if isinstance(expected, list) and expected and actual:
        expected_kinds = {type(item).__name__ for item in expected}
        actual_kinds = {type(item).__name__ for item in actual}
        return expected_kinds != actual_kinds
    return False


def prose_in_value_array(expected, actual):
    if isinstance(actual, str):
        return True
    if not isinstance(actual, list):
        return False
    expected_items = [item for _, item in leaves(expected)]
    short_expected = all(not isinstance(item, str) or len(item) <= 80 for item in expected_items)
    for item in actual:
        if isinstance(item, str):
            matched = sum(1 for expected_item in expected_items if match_in_actual(expected_item, [item]))
            if len(item) > 60 and (short_expected or matched >= 2
                                   or re.search(r"\b(the|is|are|was|were|review|order|answer)\b", item.lower())):
                return True
            if short_expected and matched >= 2:
                return True
    return False


def actual_contains_any_expected(expected, actual):
    expected_items = [item for _, item in leaves(expected)]
    return sum(1 for item in expected_items if match_in_actual(item, actual)), len(expected_items)


def build_record(record_path, eval_path, trace_root, candidates):
    record = load_json(record_path)
    evaluation = load_json(eval_path)
    arm = Path(record_path).parent
    trace_path = Path(record["paths"]["trace"])
    if not trace_path.is_absolute():
        trace_path = Path(trace_root) / trace_path
    trace_available = trace_path.exists()
    events = read_trace(trace_path) if trace_available else []

    result = evaluation["evaluators_results"][0]
    expected = (result.get("expected") or {}).get("retrieved_data")
    actual_object = result.get("actual_normalized") or {}
    actual = actual_object.get("retrieved_data")
    expected_items = [item for _, item in leaves(expected)]
    evaluable_items = [item for item in expected_items if not numeric_is_ambiguous(item)]
    ambiguous_items = [item for item in expected_items if numeric_is_ambiguous(item)]
    obs_steps = observation_steps(events)

    seen = []
    unseen = []
    first_seen_steps = {}
    for index, item in enumerate(expected_items):
        step = first_seen(item, obs_steps) if trace_available and not numeric_is_ambiguous(item) else None
        key = f"{index}:{str(item)[:60]}"
        first_seen_steps[key] = step
        if numeric_is_ambiguous(item):
            continue
        if step is None:
            unseen.append(item)
        else:
            seen.append(item)

    matched_actual = sum(1 for item in evaluable_items if match_in_actual(item, actual))
    actual_text = norm(json.dumps(actual, ensure_ascii=False))
    answer_text_matches = sum(1 for item in evaluable_items if match_in_text(item, actual_text))
    cap_exhausted = bool(record.get("cap_exhausted"))
    actual_status = str(actual_object.get("status", "")).lower()
    contract = {
        "actual_null": actual is None and actual_status == "success" and not cap_exhausted,
        "agent_reported_failure": actual_status in ("not_found_error", "unknown_error"),
        "shape_mismatch": actual is not None and shape_mismatch(expected, actual),
        "prose_in_value_array": actual is not None and prose_in_value_array(expected, actual),
        "expected_shape": shape(expected),
        "actual_shape": shape(actual),
    }
    contract_any = any(contract[key] for key in ("actual_null", "shape_mismatch", "prose_in_value_array"))
    observed_ratio = (len(seen) / len(evaluable_items)) if evaluable_items and trace_available else None
    actual_ratio = (matched_actual / len(evaluable_items)) if evaluable_items else None
    answer_text_ratio = (answer_text_matches / len(evaluable_items)) if evaluable_items else None

    auth_patterns = [
        re.compile(r"invalid login or password"),
        re.compile(r"you need to sign in or sign up before continuing"),
        re.compile(r"unable to sign in"),
        re.compile(r"could not log in"),
        re.compile(r"authentication required"),
        re.compile(r"401 unauthorized"),
        re.compile(r"403 forbidden"),
        re.compile(r"access denied"),
        re.compile(r"not authorized"),
        re.compile(r"blocked: could not log in"),
    ]
    auth_hits = pattern_hits(events, auth_patterns)
    first_auth = auth_hits[0] if auth_hits else None
    last_auth = auth_hits[-1] if auth_hits else None
    auth_final_block = any(
        phrase in actual_text
        for phrase in ("could not log in", "unable to sign in", "login failed",
                       "blocked: could not log in", "authentication failed")
    )
    max_seen_step = max((step for step in first_seen_steps.values() if step is not None), default=None)
    auth_terminal = bool(
        last_auth is not None
        and (auth_final_block or (answer_text_matches == 0
                                  and (max_seen_step is None or max_seen_step <= last_auth["step"])))
    )
    type_text_actions = sum(1 for event in events
                            if event.get("event") is None and event.get("action") == "type_text")
    auth_kind = None
    if last_auth is not None:
        auth_kind = ("login_interaction"
                     if len(auth_hits) >= 2 or type_text_actions >= 2
                     else "missing_authenticated_state")
    errors = tool_errors(events)

    causes = []
    if not evaluation.get("official_success"):
        if auth_terminal:
            if auth_kind == "login_interaction":
                causes.append("observation_or_tool")
            else:
                causes.append("environment_or_truth")
        if contract_any:
            causes.append("output_contract")
        if (errors and (observed_ratio is None or observed_ratio < 1 or len(errors) >= 2)
                and "observation_or_tool" not in causes):
            causes.append("observation_or_tool")
        if trace_available and observed_ratio is not None and observed_ratio < 1:
            causes.append("retrieval_incomplete")
        if (trace_available and observed_ratio == 1 and not contract_any
                and actual_ratio is not None and actual_ratio < 1):
            causes.append("reasoning_or_calculation")
        if cap_exhausted:
            causes.append("termination")
        if not causes:
            causes.append("unknown")

    priority = [
        "environment_or_truth",
        "observation_or_tool",
        "output_contract",
        "retrieval_incomplete",
        "reasoning_or_calculation",
        "termination",
        "unknown",
    ]
    primary = next((cause for cause in priority if cause in causes), None)

    answer_step = max((int(event.get("step") or 0) for event in events), default=int(record.get("steps") or 0))
    if auth_terminal and last_auth is not None:
        first_problem_step = last_auth["step"]
        first_problem_kind = "authentication_or_environment"
    elif errors and "observation_or_tool" in causes:
        first_problem_step = errors[0]["step"]
        first_problem_kind = "tool_error"
    elif cap_exhausted:
        first_problem_step = answer_step
        first_problem_kind = "step_cap"
    elif contract_any:
        first_problem_step = answer_step
        first_problem_kind = "answer_contract"
    elif causes:
        first_problem_step = answer_step
        first_problem_kind = "answer_content"
    else:
        first_problem_step = None
        first_problem_kind = None

    evidence_steps = sorted({step for step in (first_problem_step,) if step is not None})
    limitations = []
    if not trace_available:
        limitations.append("trace_missing")
    else:
        limitations.append("trace_has_no_full_model_input")
    if observed_ratio is None:
        limitations.append("observation_coverage_unavailable")
    if ambiguous_items:
        limitations.append("ambiguous_small_numeric_visibility")
    if (contract_any and answer_text_ratio == 1
            and actual_ratio is not None and actual_ratio < 1):
        limitations.append("answer_content_unverified_for_prose")
    if causes == ["unknown"]:
        limitations.append("insufficient_evidence")

    trial_path = Path(record_path)
    return {
        "schema": "agent_stress_development_diagnosis/1",
        "diagnosis_date": "2026-11-04",
        "trial_id": record.get("trial_id"),
        "task_id": record.get("task_id"),
        "site": candidates.get(record.get("task_id"), {}).get("site"),
        "split_role": candidates.get(record.get("task_id"), {}).get("split_role"),
        "architecture": record.get("architecture"),
        "seed": record.get("fault_seed"),
        "model_profile": record.get("model_profile"),
        "official_success": bool(evaluation.get("official_success")),
        "end_mode": "step_cap" if record.get("cap_exhausted") else "answer",
        "steps": record.get("steps"),
        "max_steps": (record.get("run_config") or {}).get("max_steps"),
        "llm_calls": record.get("llm_calls"),
        "time_sec": record.get("time_sec"),
        "first_problem_step": first_problem_step,
        "first_problem_kind": first_problem_kind,
        "primary_cause": primary,
        "causes": causes,
        "expected_items": expected_items,
        "expected_evaluable_total": len(evaluable_items),
        "expected_ambiguous_items": ambiguous_items,
        "expected_seen_in_observations": len(seen),
        "expected_total": len(expected_items),
        "expected_seen_ratio": round(observed_ratio, 4) if observed_ratio is not None else None,
        "expected_unseen": unseen,
        "expected_first_seen_steps": first_seen_steps,
        "actual_expected_matches": matched_actual,
        "actual_expected_ratio": round(actual_ratio, 4) if actual_ratio is not None else None,
        "answer_text_matches": answer_text_matches,
        "answer_text_ratio": round(answer_text_ratio, 4) if answer_text_ratio is not None else None,
        "contract": contract,
        "authentication_evidence": {
            "first_step": first_auth["step"] if first_auth else None,
            "last_step": last_auth["step"] if last_auth else None,
            "pattern": last_auth["pattern"] if last_auth else None,
            "matched": last_auth["matched"] if last_auth else None,
            "terminal": auth_terminal,
            "kind": auth_kind,
        } if last_auth is not None else None,
        "tool_errors": errors,
        "limitations": limitations,
        "reply_evidence": {
            "answer_step": answer_step,
            "evidence_steps": evidence_steps,
            "trace_event_count": len(events),
        },
        "artifacts": {
            "trial_record": str(trial_path),
            "official_eval": str(eval_path),
            "agent_response": record.get("paths", {}).get("agent_response"),
            "trace": record.get("paths", {}).get("trace"),
            "network_har": record.get("paths", {}).get("network_har"),
            "trace_resolved": str(trace_path),
        },
        "review_status": "automated_evidence",
    }


def expected_grid(tasks):
    keys = set()
    for task in tasks:
        for architecture in ("react", "plan_execute"):
            for seed in (1, 2, 3):
                keys.add(f"qwen38_flash_task{task['task_id']}_{architecture}_seed{seed}")
    return keys


def audit_runs(experiment_root, candidates, trace_root, hash_har=False):
    tasks = candidates["tasks"]
    expected_keys = expected_grid(tasks)
    by_key = {}
    run_totals = {}
    problems = []
    for run_root in sorted(Path(experiment_root).glob(SCREENING_GLOB)):
        progress_path = run_root / "progress.json"
        progress = load_json(progress_path) if progress_path.exists() else {}
        run_totals[run_root.name] = {
            "completed": len(progress.get("completed") or []),
            "prompt_tokens": progress.get("prompt_tokens"),
            "stop_reason": progress.get("stop_reason"),
            "skipped_completed": progress.get("skipped_completed"),
        }
        for trial_path in sorted(run_root.glob("*/outputs/*/*/trial_record.json")):
            key = trial_path.parts[-5]
            if key in by_key:
                problems.append({"kind": "duplicate_key", "key": key})
                continue
            by_key[key] = trial_path
            record = load_json(trial_path)
            eval_path = trial_path.parent / "official_eval.json"
            response_path = trial_path.parent / "agent_response.json"
            har_path = trial_path.parent / "network.har"
            evaluation = load_json(eval_path) if eval_path.exists() else {}
            expected = {
                "completed_matches_end_mode": bool(record.get("completed")) == (not bool(record.get("cap_exhausted"))),
                "injection_count": record.get("injection_count") == 0,
                "condition_control": record.get("condition") == "control" or record.get("fault_type") == "control",
                "model_profile": record.get("model_profile") == "qwen38_flash",
                "max_steps": (record.get("run_config") or {}).get("max_steps") == MAX_STEPS,
                "evaluation_present": bool(evaluation),
                "official_success_bool": type(evaluation.get("official_success")) is bool,
                "evaluation_task": evaluation.get("task_id") == record.get("task_id"),
                "score_status_consistent": (
                    evaluation.get("official_success") is True and evaluation.get("status") == "success"
                ) or (
                    evaluation.get("official_success") is False and evaluation.get("status") == "failure"
                ),
            }
            binding = evaluation.get("_evaluation_binding") or {}
            if response_path.exists() and binding.get("agent_response_sha256"):
                expected["response_hash"] = sha256_file(response_path) == binding["agent_response_sha256"]
            else:
                expected["response_hash"] = False
            if hash_har and har_path.exists() and binding.get("network_har_sha256"):
                expected["har_hash"] = sha256_file(har_path) == binding["network_har_sha256"]
            for name, passed in expected.items():
                if not passed:
                    problems.append({"kind": "trial_check", "key": key, "check": name})
    missing = sorted(expected_keys - set(by_key))
    extra = sorted(set(by_key) - expected_keys)
    if missing:
        problems.append({"kind": "missing_trials", "keys": missing[:20], "count": len(missing)})
    if extra:
        problems.append({"kind": "unexpected_trials", "keys": extra[:20], "count": len(extra)})
    return {
        "schema": "agent_stress_screening_integrity/1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_roots": run_totals,
        "trial_count": len(by_key),
        "expected_trial_count": len(expected_keys),
        "unique_trial_count": len(set(by_key)),
        "missing_count": len(missing),
        "unexpected_count": len(extra),
        "problem_count": len(problems),
        "problems": problems,
        "hashed_har": hash_har,
    }


def summarise(records):
    total = len(records)
    success = sum(1 for item in records if item["official_success"])
    failure = total - success
    causes = Counter()
    primary = Counter()
    by_arch = defaultdict(lambda: {"trials": 0, "success": 0, "causes": Counter()})
    by_task = defaultdict(lambda: {"trials": 0, "success": 0, "causes": Counter(), "cap": 0, "auth": 0})
    contract_trials = []
    unknown_trials = []
    environment_tasks = set()
    for item in records:
        by_arch[item["architecture"]]["trials"] += 1
        by_arch[item["architecture"]]["success"] += int(item["official_success"])
        by_task[item["task_id"]]["trials"] += 1
        by_task[item["task_id"]]["success"] += int(item["official_success"])
        by_task[item["task_id"]]["cap"] += int(item["end_mode"] == "step_cap")
        by_task[item["task_id"]]["auth"] += int(bool(item.get("authentication_evidence")))
        for cause in item["causes"]:
            causes[cause] += 1
            by_arch[item["architecture"]]["causes"][cause] += 1
            by_task[item["task_id"]]["causes"][cause] += 1
        if item["primary_cause"]:
            primary[item["primary_cause"]] += 1
        if "output_contract" in item["causes"]:
            contract_trials.append(item["trial_id"])
        if item["causes"] == ["unknown"]:
            unknown_trials.append(item["trial_id"])
        if item.get("authentication_evidence") and not item["official_success"]:
            environment_tasks.add(item["task_id"])
    for task_id, values in by_task.items():
        values["causes"] = dict(values["causes"])
    for architecture, values in by_arch.items():
        values["causes"] = dict(values["causes"])
    contract_by_task = Counter(
        item["task_id"] for item in records if "output_contract" in item["causes"]
    )
    return {
        "schema": "agent_stress_development_diagnosis_summary/1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "108 development trials; holdout not used for cause attribution",
        "trial_count": total,
        "success": success,
        "failure": failure,
        "end_modes": dict(Counter(item["end_mode"] for item in records)),
        "primary_cause_counts": dict(primary),
        "cause_counts_multilabel": dict(causes),
        "architecture": by_arch,
        "task": {str(key): value for key, value in sorted(by_task.items())},
        "tasks_with_authentication_evidence": sorted(environment_tasks),
        "output_contract_trials": len(contract_trials),
        "output_contract_by_task": {str(k): v for k, v in sorted(contract_by_task.items())},
        "unknown_trials": unknown_trials,
        "limitations": [
            "Traces record tool observations and assistant content, not the full assembled model prompt.",
            "Observation coverage is an upper bound on what the model could have seen.",
            "No reference-path verification was performed in this offline pass.",
            "Automated cause labels need manual review for trials with only wrong-answer evidence.",
        ],
    }


def markdown(summary, integrity):
    lines = []
    lines.append("# Development screening diagnosis")
    lines.append("")
    lines.append(f"Generated: {summary['generated_at']}")
    lines.append("")
    lines.append("Scope: 108 development trials only. Holdout trials were not used for cause attribution.")
    lines.append("")
    lines.append("## Outcome")
    lines.append("")
    lines.append(f"- Trials: {summary['trial_count']}")
    lines.append(f"- Official success: {summary['success']}")
    lines.append(f"- Official failure: {summary['failure']}")
    lines.append(f"- End modes: {summary['end_modes']}")
    lines.append("")
    lines.append("## Primary causes")
    lines.append("")
    lines.append("| Cause | Trials |")
    lines.append("| --- | ---: |")
    for cause, count in sorted(summary["primary_cause_counts"].items(), key=lambda item: -item[1]):
        lines.append(f"| {cause} | {count} |")
    lines.append("")
    lines.append("## Multi-label causes")
    lines.append("")
    lines.append("| Cause | Trials |")
    lines.append("| --- | ---: |")
    for cause, count in sorted(summary["cause_counts_multilabel"].items(), key=lambda item: -item[1]):
        lines.append(f"| {cause} | {count} |")
    lines.append("")
    lines.append("## By architecture")
    lines.append("")
    lines.append("| Architecture | Trials | Success |")
    lines.append("| --- | ---: | ---: |")
    for architecture, values in sorted(summary["architecture"].items()):
        lines.append(f"| {architecture} | {values['trials']} | {values['success']} |")
    lines.append("")
    lines.append("## By task")
    lines.append("")
    lines.append("| Task | Split | Site | Trials | Success | Step cap | Auth evidence | Primary causes |")
    lines.append("| ---: | --- | --- | ---: | ---: | ---: | ---: | --- |")
    for task_id, values in sorted(summary["task"].items(), key=lambda item: int(item[0])):
        causes = values["causes"]
        top = ", ".join(f"{cause}={count}" for cause, count in sorted(causes.items(), key=lambda item: -item[1]))
        lines.append(
            f"| {task_id} | {values.get('split_role', '')} | {values.get('site', '')} | "
            f"{values['trials']} | {values['success']} | {values['cap']} | {values['auth']} | {top} |"
        )
    lines.append("")
    lines.append("## Integrity audit")
    lines.append("")
    lines.append(f"- Trials found: {integrity['trial_count']} / expected {integrity['expected_trial_count']}")
    lines.append(f"- Missing: {integrity['missing_count']}; unexpected: {integrity['unexpected_count']}")
    lines.append(f"- Failed checks: {integrity['problem_count']}")
    lines.append("")
    lines.append("## Limitations")
    lines.append("")
    for limitation in summary["limitations"]:
        lines.append(f"- {limitation}")
    lines.append("")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--experiments-root", type=Path, default=DEFAULT_EXPERIMENTS)
    parser.add_argument("--trace-root", type=Path, default=ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--hash-har", action="store_true")
    args = parser.parse_args(argv)

    candidates_doc = load_json(args.candidates)
    candidates = {task["task_id"]: task for task in candidates_doc["tasks"]}
    records = []
    for trial_path in sorted(Path(args.experiments_root).glob(f"{SCREENING_GLOB}/*/outputs/*/*/trial_record.json")):
        record = load_json(trial_path)
        task = candidates.get(record.get("task_id")) or {}
        if task.get("split_role") != "development":
            continue
        eval_path = trial_path.parent / "official_eval.json"
        if not eval_path.exists():
            continue
        records.append(build_record(trial_path, eval_path, args.trace_root, candidates))
    if not records:
        raise SystemExit("no development trial records found")
    records.sort(key=lambda item: (item["task_id"], item["architecture"], item["seed"]))

    summary = summarise(records)
    for task_id in summary["task"]:
        task = candidates.get(int(task_id), {})
        summary["task"][task_id]["split_role"] = task.get("split_role")
        summary["task"][task_id]["site"] = task.get("site")

    integrity = audit_runs(args.experiments_root, candidates_doc, args.trace_root, hash_har=args.hash_har)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    with (args.output_dir / "development_diagnosis.jsonl").open("w", encoding="utf-8") as stream:
        for item in records:
            stream.write(json.dumps(item, ensure_ascii=False) + "\n")
    (args.output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.output_dir / "integrity_audit.json").write_text(json.dumps(integrity, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.output_dir / "summary.md").write_text(markdown(summary, integrity), encoding="utf-8")
    print(json.dumps({
        "trials": summary["trial_count"],
        "success": summary["success"],
        "failure": summary["failure"],
        "primary_causes": summary["primary_cause_counts"],
        "integrity_problems": integrity["problem_count"],
        "output_dir": str(args.output_dir),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
