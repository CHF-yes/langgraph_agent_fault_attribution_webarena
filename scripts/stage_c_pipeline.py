#!/usr/bin/env python3
"""Stage C 闭环 CLI：完整性检查 → 官方 evaluator → 审计 → 缺失/失败清单。

用法示例（不发起任何 agent trial，只处理已存在的产物）::

    python scripts/stage_c_pipeline.py run \
        --root experiments/stage_c/outputs \
        --output-dir experiments/stage_c/pipeline \
        --config experiments/webarena_local_config.json \
        --model-profile deepseek_v41_flash --architecture react

``check`` 只做完整性检查，``evaluate`` 只评分，``audit`` 只汇总（读已有的行）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from standard_agent.stage_c_pipeline import (  # noqa: E402
    DEFAULT_MANIFEST,
    audit,
    check_artifacts,
    collect_rows,
    discover_trials,
    expected_cells,
    load_design,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["check", "evaluate", "audit", "run"])
    parser.add_argument("--root", required=True, help="含 trial 产物的目录")
    parser.add_argument("--output-dir", default=None, help="审计输出目录")
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    parser.add_argument("--config", default=None, help="官方 evaluator 配置 JSON")
    parser.add_argument("--dataset", default=os.getenv("WEBARENA_DATASET"),
                        help="本机数据集路径，用于替换配置中的 /root 路径")
    parser.add_argument("--model-profile", action="append", default=None,
                        help="限定模型；缺省用清单里的主模型 + 验证模型")
    parser.add_argument("--architecture", action="append", default=None)
    parser.add_argument("--limit", type=int, default=None, help="最多处理多少 trial（调试用）")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    design = load_design(args.manifest)
    models = args.model_profile or (
        list(design["main_models"]) + ([design["validation_model"]]
                                       if design.get("validation_model") else []))
    architectures = args.architecture or list(design["architectures"])
    expected = []
    for model in models:
        for architecture in architectures:
            expected.extend(expected_cells(design, model_profile=model,
                                           architecture=architecture))
    if args.output_dir is None:
        args.output_dir = str(Path(args.root) / "_stage_c_pipeline")
    Path(args.output_dir).mkdir(parents=True, exist_ok=True)

    if args.command == "check":
        trials = discover_trials(args.root)
        results = [check_artifacts(trial) for trial in trials]
        summary = {
            "trials": len(results),
            "complete": sum(1 for item in results if item["complete"]),
            "incomplete": [
                {"trial_dir": item["trial_dir"], "reasons": item["reasons"]}
                for item in results if not item["complete"]],
        }
        (Path(args.output_dir) / "integrity.json").write_text(
            json.dumps({"summary": summary, "trials": results},
                       ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        print(json.dumps({"trials": summary["trials"], "complete": summary["complete"],
                          "incomplete": len(summary["incomplete"])}, ensure_ascii=False))
        return 0

    rows = collect_rows(
        args.root,
        config_path=args.config,
        out_dir=args.output_dir,
        dataset_path=args.dataset,
        limit=args.limit,
        evaluate=(args.command in ("evaluate", "run")),
    )
    report = audit(rows, expected)
    (Path(args.output_dir) / "audit.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({
        "expected_cells": report["expected_cells"],
        "evaluated_cells": report["evaluated_cells"],
        "missing_cells": len(report["missing_cells"]),
        "error_cells": len(report["error_cells"]),
        "evaluation_status_counts": report["evaluation_status_counts"],
        "official_success_rate": report["official_success_rate"],
        "submitted_completion_rate": report["submitted_completion_rate"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
