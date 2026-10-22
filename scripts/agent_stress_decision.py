#!/usr/bin/env python3
"""Recompute decision evidence; synthetic examples never qualify real tasks.

Only the native branch calls the installed official evaluator. Saved success
booleans are never a grading fallback. No model calls or Agent trials.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAMES = ("truth", "polluted", "protected")
SHOPPING_URL_RE = re.compile(
    r"^http://localhost:7770/[a-z0-9-]*(?:guard|mouth|teeth|night|dental|bruxism)"
    r"[a-z0-9-]*\.html$")


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _artifact(entry, label, errors, root):
    if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
        errors.append(f"{label}: missing artifact path")
        return None
    path = Path(entry["path"])
    base = Path(root).resolve()
    full = (base / path).resolve()
    if path.is_absolute() or ".." in path.parts or not path.parts or not full.is_relative_to(base):
        errors.append(f"{label}: path must remain project-relative")
        return None
    try:
        if hashlib.sha256(full.read_bytes()).hexdigest() != entry.get("sha256"):
            errors.append(f"{label}: sha256 mismatch")
            return None
    except OSError:
        errors.append(f"{label}: artifact missing")
        return None
    return full


def _pointer(data, pointer):
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise ValueError("expected a nonempty JSON pointer")
    for token in pointer[1:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        data = data[int(token)] if isinstance(data, list) else data[token]
    return data


def _native_grades(official, outputs, root, errors):
    """Regrade complete agent_response objects with bound installed sources/HARs."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from standard_agent.stage_c_pipeline import current_evaluator_version, classify_evaluation
    from standard_agent.webarena_evaluator import evaluate_task_safe
    from standard_agent.webarena_verified import dataset_path

    config = _artifact(official.get("config"), "official:config", errors, root)
    dataset = _artifact(official.get("dataset"), "official:dataset", errors, root)
    fingerprint = current_evaluator_version()
    if (official.get("evaluator_package_sha256") != fingerprint.get("package_sha256")
            or not fingerprint.get("package_sources")):
        errors.append("official: evaluator source fingerprint missing or mismatched")
    if dataset is not None and dataset.resolve() != dataset_path().resolve():
        errors.append("official: bound dataset is not the active evaluator dataset")
    if dataset is not None and config is not None:
        configured_dataset = Path(_load(config).get("test_data_file", ""))
        if not configured_dataset.is_absolute():
            configured_dataset = ROOT / configured_dataset
        if (not configured_dataset.is_file()
                or hashlib.sha256(configured_dataset.read_bytes()).hexdigest()
                != hashlib.sha256(dataset.read_bytes()).hexdigest()):
            errors.append("official: evaluator configuration points to a different dataset")
    task_id = official.get("task_id")
    if type(task_id) is not int:
        errors.append("official: task_id missing")
    traces = {name: _artifact((official.get("network_traces") or {}).get(name),
                             f"official:HAR:{name}", errors, root) for name in NAMES}
    if errors:
        return {}, {}
    accepted, results = {}, {}
    with tempfile.TemporaryDirectory(prefix="stress-grade-") as directory:
        for name in NAMES:
            if not isinstance(outputs[name], dict) or "status" not in outputs[name]:
                errors.append(f"official:{name}: expected complete agent_response object")
                continue
            response = Path(directory) / f"{name}.json"
            response.write_text(json.dumps(outputs[name], ensure_ascii=False), encoding="utf-8")
            result = evaluate_task_safe(task_id, agent_response_path=response,
                                       network_trace_path=traces[name], config_path=config)
            verdict = classify_evaluation(result)
            results[name] = result
            if verdict["evaluation_status"] != "native":
                errors.append(f"official:{name}: native evaluation unavailable")
            elif type(result.get("official_success")) is not bool:
                errors.append(f"official:{name}: nonboolean official result")
            else:
                accepted[name] = result["official_success"]
    return accepted, results


def _grades(official, outputs, root, errors):
    kind = official.get("kind")
    if kind == "native":
        return _native_grades(official, outputs, root, errors)
    if kind == "synthetic_fixture":
        criterion = _artifact(official.get("criterion"), "fixture:criterion", errors, root)
        if criterion is None:
            return {}, {}
        rule = _load(criterion)
        if rule.get("rule") != "exact_output_v1" or "expected_output" not in rule:
            errors.append("fixture: unsupported criterion")
            return {}, {}
        return {name: value == rule["expected_output"] for name, value in outputs.items()}, {}
    if kind == "navigation_regex_mirror":
        # Historical negative example only; this mirror is NOT native grading.
        return {name: bool(isinstance(value, dict)
                           and SHOPPING_URL_RE.fullmatch(str(value.get("url"))))
                for name, value in outputs.items()}, {}
    errors.append("official: unsupported grading kind; saved booleans cannot establish grading")
    return {}, {}


def _hc_check(hc, root, errors):
    if not isinstance(hc, dict):
        errors.append("hc: actual delivery evidence required")
        return
    h_path = _artifact(hc.get("h_payload"), "hc:h_payload", errors, root)
    hc_path = _artifact(hc.get("hc_payload"), "hc:hc_payload", errors, root)
    scopes = hc.get("scopes") or {}
    if not scopes.get("H") or not scopes.get("C") or scopes.get("H") == scopes.get("C"):
        errors.append("hc: H and C scopes are not distinct")
    if not h_path or not hc_path:
        return
    h_data, hc_data = _load(h_path), _load(hc_path)
    try:
        marker = hc["h_marker"]
        if not isinstance(marker, str) or not marker:
            raise ValueError("empty H marker")
        if (_pointer(h_data, hc["h_marker_pointer"]) != marker
                or _pointer(hc_data, hc["h_marker_pointer"]) != marker):
            raise ValueError("H marker mismatch")
    except (KeyError, IndexError, TypeError, ValueError):
        errors.append("hc: H marker missing from an actual delivered payload")
    try:
        # Bind entity AND field to the delivered object; unrelated substrings do not count.
        entity_parent = hc["c_entity_pointer"].rsplit("/", 1)[0]
        field_parent, field_name = hc["c_field_pointer"].rsplit("/", 1)
        if (entity_parent != field_parent or field_name != hc["c_field"]
                or _pointer(hc_data, hc["c_entity_pointer"]) != hc["c_entity_key"]
                or _pointer(hc_data, hc["c_field_pointer"]) != hc["c_value"]):
            raise ValueError("entity/field mismatch")
    except (KeyError, IndexError, TypeError, ValueError):
        errors.append("hc: C content is not visible on the bound entity/field in the HC payload")


def verify(evidence, root=ROOT, *, require_hc=False):
    errors, outputs, grades, grade_results = [], {}, {}, {}
    evidence = evidence or {}
    try:
        computation = _artifact(evidence.get("computation"), "computation", errors, root)
        paths = {name: _artifact((evidence.get("inputs") or {}).get(name),
                                f"input:{name}", errors, root) for name in NAMES}
        if not errors:
            for name, path in paths.items():
                try:
                    proc = subprocess.run([sys.executable, str(computation), str(path)],
                                          cwd=root, capture_output=True, text=True, timeout=30)
                    if proc.returncode != 0:
                        errors.append(f"{name}: computation failed (rc={proc.returncode})")
                    else:
                        outputs[name] = json.loads(proc.stdout)
                except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError):
                    errors.append(f"{name}: computation failed or returned invalid JSON")
        if len(outputs) == 3:
            for name in NAMES:
                if outputs[name] != (evidence.get("reported") or {}).get(name):
                    errors.append(f"{name}: recomputed output differs from the reported output")
            if outputs["truth"] == outputs["polluted"]:
                errors.append("polluted output equals the truth output (no decision impact)")
            grades, grade_results = _grades(evidence.get("official") or {}, outputs, root, errors)
            # Different legal answers are allowed; protected output must also be accepted.
            for name, expected in (("truth", True), ("polluted", False), ("protected", True)):
                if name not in grades:
                    errors.append(f"official:{name}: no recomputed grading result")
                elif grades[name] is not expected:
                    errors.append(f"official:{name}: accepted={grades[name]}, expected {expected}")
        if require_hc or evidence.get("hc") is not None:
            _hc_check(evidence.get("hc"), root, errors)
    except (OSError, ValueError, KeyError, TypeError, ImportError) as exc:
        errors.append(f"invalid or unavailable evidence: {type(exc).__name__}: {exc}")
    native = (evidence.get("official") or {}).get("kind") == "native"
    return {"passed": not errors, "errors": errors, "outputs": outputs,
            "grading": grades, "native_results": grade_results,
            "real_contract_eligible": not errors and native,
            "validation_level": "native" if native else "example_only"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--require-pass", action="store_true")
    args = parser.parse_args()
    result = verify(_load(args.evidence))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] or not args.require_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
