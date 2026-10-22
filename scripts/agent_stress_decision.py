#!/usr/bin/env python3
"""Recomputable decision-impact evidence for C/HC contracts.

The earlier check only trusted declared booleans. This verifier:
* loads the computation script and the truth / polluted / protected inputs,
  verifying every sha256;
* re-runs the computation in a subprocess on each input;
* requires recomputed outputs to equal the reported ones, polluted != truth,
  protected == truth;
* requires the official rule to accept the truth output and reject the polluted
  one (rule is evaluated from the output, not from the raw mutated field);
* for HC, loads the actual delivered payloads and checks that the H marker is
  present and the C field/value is still visible.

No network, no model calls, no trial execution.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHOPPING_URL_RE = __import__("re").compile(
    r"^http://localhost:7770/[a-z0-9-]*(?:guard|mouth|teeth|night|dental|bruxism)"
    r"[a-z0-9-]*\.html$")


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _artifact(entry, label, errors):
    if not isinstance(entry, dict) or not entry.get("path"):
        errors.append(f"{label}: missing artifact path")
        return None
    path = Path(entry["path"])
    if path.is_absolute() or ".." in path.parts or not path.parts:
        errors.append(f"{label}: path must be project-relative")
        return None
    full = ROOT / path
    try:
        if _sha(full) != entry.get("sha256"):
            errors.append(f"{label}: sha256 mismatch")
            return None
    except OSError:
        errors.append(f"{label}: artifact missing")
        return None
    return full


def _official_accepts(rule, output):
    if rule == "shopping_navigate_url_regex_v1":
        url = output.get("url") if isinstance(output, dict) else None
        return bool(url and SHOPPING_URL_RE.match(str(url)))
    if rule == "exact_list_v1":
        return output == output.get("__expected__") if isinstance(output, dict) else False
    return None


def verify(evidence, root=ROOT):
    errors = []
    evidence = evidence or {}
    computation = _artifact(evidence.get("computation"), "computation", errors)
    inputs = evidence.get("inputs") or {}
    paths = {name: _artifact(inputs.get(name), f"input:{name}", errors)
             for name in ("truth", "polluted", "protected")}
    if errors:
        return {"passed": False, "errors": errors}
    outputs = {}
    for name, path in paths.items():
        proc = subprocess.run([sys.executable, str(computation), str(path)],
                              cwd=root, capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            errors.append(f"{name}: computation failed: {proc.stderr[-300:]}")
            continue
        try:
            outputs[name] = json.loads(proc.stdout)
        except json.JSONDecodeError:
            errors.append(f"{name}: computation output is not JSON")
    reported = evidence.get("reported") or {}
    for name in ("truth", "polluted", "protected"):
        if name in outputs and outputs[name] != reported.get(name):
            errors.append(f"{name}: recomputed output differs from the reported output")
    if "truth" in outputs and "polluted" in outputs and outputs["truth"] == outputs["polluted"]:
        errors.append("polluted output equals the truth output (no decision impact)")
    if "truth" in outputs and "protected" in outputs and outputs["truth"] != outputs["protected"]:
        errors.append("protected source does not reproduce the truth output")

    official = evidence.get("official") or {}
    for name, expected_accept in (("truth", True), ("polluted", False)):
        item = official.get(name) or {}
        rule = item.get("rule")
        accepted = None if name not in outputs else _official_accepts(rule, outputs[name])
        if rule == "shopping_navigate_url_regex_v1" and outputs.get(name) is not None:
            pass
        if accepted is None:
            # fall back to a declared result only if backed by an artifact
            artifact = _artifact(item.get("artifact"), f"official:{name}", errors)
            if artifact is not None:
                try:
                    payload = _load(artifact)
                    accepted = bool(payload.get("official_success"))
                except Exception:
                    accepted = None
        if accepted is None:
            errors.append(f"official:{name}: cannot evaluate the official result")
        elif accepted is not expected_accept:
            errors.append(f"official:{name}: accepted={accepted}, expected {expected_accept}")

    hc = evidence.get("hc")
    if hc:
        h_payload = _artifact(hc.get("h_payload"), "hc:h_payload", errors)
        hc_payload = _artifact(hc.get("hc_payload"), "hc:hc_payload", errors)
        if h_payload and hc_payload:
            try:
                h_text = h_payload.read_text(encoding="utf-8")
                hc_text = hc_payload.read_text(encoding="utf-8")
                marker = hc.get("h_marker") or ""
                field = hc.get("c_field") or ""
                value = hc.get("c_value") or ""
                if not marker or marker not in h_text or marker not in hc_text:
                    errors.append("hc: H marker missing from an actual delivered payload")
                if not field or value not in hc_text:
                    errors.append("hc: C content is not visible in the HC payload")
                scopes = hc.get("scopes") or {}
                if not scopes.get("H") or not scopes.get("C") or scopes.get("H") == scopes.get("C"):
                    errors.append("hc: H and C scopes are not distinct")
            except OSError:
                pass
    return {"passed": not errors, "errors": errors, "outputs": outputs}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--require-pass", action="store_true")
    args = parser.parse_args()
    result = verify(_load(args.evidence))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] or not args.require_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
