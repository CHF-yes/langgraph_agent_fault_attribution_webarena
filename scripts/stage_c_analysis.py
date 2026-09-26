#!/usr/bin/env python3
"""Stage C 主分析 CLI：读 pipeline 的行，产出配对退化、交互检验与多重比较结果。

只读已评分的产物，不发起任何 trial，也不修改任何 agent 输出::

    python scripts/stage_c_analysis.py \
        --rows experiments/stage_c/pipeline/rows.json \
        --manifest docs/task_manifest_public16.json \
        --output-dir experiments/stage_c/analysis
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from standard_agent.stage_c_analysis import format_report, main_analysis  # noqa: E402
from standard_agent.stage_c_pipeline import DEFAULT_MANIFEST, load_design  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rows", required=True, help="pipeline 输出的 rows.json")
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--bootstrap", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=20260921)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = json.loads(Path(args.rows).read_text(encoding="utf-8"))
    rows = payload.get("rows") if isinstance(payload, dict) else payload
    design = load_design(args.manifest)
    report = main_analysis(rows, design, n_boot=args.bootstrap, seed=args.seed)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "analysis.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    (out_dir / "analysis.md").write_text(format_report(report), encoding="utf-8")
    print(json.dumps({
        "main_models": report["main_models"],
        "n_pairs_main": report["n_pairs_main"],
        "cells": len(report["degradation_by_cell"]),
        "interaction": (report["interaction"] or {}).get(
            "coefficient_condition_x_model_x_architecture"),
        "scope_ok": (report.get("scope") or {}).get("ok"),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
