"""Report per-(model, architecture) protocol health from JSONL traces.

T0 release standard (``docs/experiment_roadmap.md`` §7) requires the
``THOUGHT:`` contract violation rate and the ``thought`` missing rate to be
reported separately for every ``(model, architecture)`` cell.  This script
implements that report; it reads traces only and touches no network or
credentials.

Definitions used here:

``action step``
    A trace entry recording a tool execution (``action`` non-empty).  This is
    the population the ``THOUGHT:`` contract applies to: every tool call is
    supposed to carry reasoning in the reply text.

``thought missing``
    An action step whose ``thought`` is empty.  A model that answers with a
    tool call but no text content produces these; see roadmap §7 for why that
    is counted rather than treated as a task failure.

``violation``
    A ``protocol_violation`` event.  For ``react`` this comes from
    ``agent_node``; for ``plan_execute`` from ``plan_executor_node``.  In a
    healthy run ``violations`` should be close to ``thought_missing``, so a
    large gap between the two means the two counters disagree and the
    instrumentation itself needs a look.

Usage::

    python3 scripts/protocol_health.py
    python3 scripts/protocol_health.py --trace-dir traces --json report.json
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

UNKNOWN = "(unknown)"


def _cell_for_file(path: Path) -> tuple[str, str] | None:
    """Determine one trace's (model, architecture) cell.

    A trace file is a single trial, so ``model_profile`` and ``architecture``
    are constant within it.  ``model_profile`` only appears on provenance and
    decision events, so the file is scanned until both are known.
    """
    model = None
    architecture = None
    try:
        handle = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return None
    with handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            model = model or entry.get("model_profile") or entry.get("model")
            architecture = architecture or entry.get("architecture")
            if model and architecture:
                return str(model), str(architecture)
    if architecture:
        return (str(model) if model else UNKNOWN), str(architecture)
    return None


def _empty_cell() -> dict[str, Any]:
    return {"traces": 0, "action_steps": 0, "thought_missing": 0, "violations": 0}


def scan(trace_dir: Path) -> dict[str, Any]:
    cells: dict[tuple[str, str], dict[str, Any]] = defaultdict(_empty_cell)
    skipped = 0
    for path in sorted(trace_dir.glob("*.jsonl")):
        cell = _cell_for_file(path)
        if cell is None:
            skipped += 1
            continue
        bucket = cells[cell]
        bucket["traces"] += 1
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("event") == "protocol_violation":
                bucket["violations"] += 1
            if entry.get("action"):
                bucket["action_steps"] += 1
                if not str(entry.get("thought") or "").strip():
                    bucket["thought_missing"] += 1

    rows = []
    for (model, architecture), b in sorted(cells.items()):
        steps = b["action_steps"]
        rows.append({
            "model": model,
            "architecture": architecture,
            "traces": b["traces"],
            "action_steps": steps,
            "thought_missing": b["thought_missing"],
            "violations": b["violations"],
            "thought_missing_rate": round(b["thought_missing"] / steps, 4) if steps else None,
            "violation_rate": round(b["violations"] / steps, 4) if steps else None,
        })
    return {"trace_dir": str(trace_dir), "skipped_files": skipped, "cells": rows}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace-dir", default="traces",
                        help="Directory holding per-trial .jsonl traces")
    parser.add_argument("--json", dest="json_path", default=None,
                        help="Also write the report as JSON to this path")
    args = parser.parse_args()

    report = scan(Path(args.trace_dir))
    rows = report["cells"]
    if not rows:
        print(f"no traces found under {report['trace_dir']}")
        return 1

    width = max(len(r["model"]) for r in rows) + 2
    header = (f"{'model':<{width}}{'arch':<14}{'traces':>7}{'steps':>7}"
              f"{'no-thought':>11}{'missing%':>10}{'viol':>6}{'viol%':>8}")
    print(header)
    print("-" * len(header))
    for r in rows:
        missing = "n/a" if r["thought_missing_rate"] is None else f"{r['thought_missing_rate'] * 100:.1f}"
        viol = "n/a" if r["violation_rate"] is None else f"{r['violation_rate'] * 100:.1f}"
        print(f"{r['model']:<{width}}{r['architecture']:<14}{r['traces']:>7}"
              f"{r['action_steps']:>7}{r['thought_missing']:>11}{missing:>10}"
              f"{r['violations']:>6}{viol:>8}")

    if report["skipped_files"]:
        print(f"\nskipped {report['skipped_files']} trace file(s) with no architecture field")
    if args.json_path:
        Path(args.json_path).write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(f"\nreport written to {args.json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
