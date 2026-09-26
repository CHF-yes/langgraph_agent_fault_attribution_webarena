#!/usr/bin/env python3
"""离线复核评分链：预算耗尽格的重新提交 + 真 NOT_FOUND 回答的原生评分。

**不调用任何 LLM，也不启动 agent trial**：只把已保存的产物重新组装后交给本地
官方 evaluator。三件事：

1. 对已跑满步数的格子，用修好的适配器重新生成 ``agent_response.json``
   （诊断进 ``error_details``、``retrieved_data`` 为 null），并给出改前/改后的
   评分分类，确认改后由**原生** evaluator 判失败。
2. 对同一批任务构造**真正的 NOT_FOUND_ERROR 回答**，确认它们在原生路径上能被
   正确评分（预期 success）。
3. 全部结论写入 ``--out`` 目录，原始 smoke 产物一个字节都不改。

用法::

    python scripts/stage_c_recheck_responses.py \
        --smoke-root experiments/stage_c_smoke/outputs \
        --task-ids 22 24 \
        --out experiments/stage_c_smoke/recheck \
        --config experiments/webarena_local_config.json
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from standard_agent.stage_c_pipeline import (  # noqa: E402
    DEFAULT_MANIFEST,
    classify_evaluation,
    default_evaluate,
    load_design,
    resolve_evaluator_config,
)
from standard_agent.webarena_verified import (  # noqa: E402
    dataset_path,
    load_task_definition,
    make_agent_response,
)

CAP_MARKER = "max_steps exhausted"


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _is_cap_response(response: dict) -> bool:
    """旧产物里耗尽格的特征：诊断被写进了 retrieved_data。"""
    data = response.get("retrieved_data")
    if isinstance(data, list):
        return any("reached max steps" in str(item).casefold() for item in data)
    return "reached max steps" in str(data).casefold()


def _evaluate(dir_path: Path, task_id: int, config_path) -> dict:
    result = default_evaluate(task_id, agent_response_path=dir_path / "agent_response.json",
                              network_trace_path=dir_path / "network.har",
                              config_path=config_path)
    return {"result": result, **classify_evaluation(result)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--smoke-root", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--config", default="experiments/webarena_local_config.json")
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    parser.add_argument("--task-ids", type=int, nargs="+", default=[22, 24],
                        help="要验证真 NOT_FOUND 原生评分的任务")
    options = parser.parse_args()

    smoke_root = ROOT / options.smoke_root
    out_root = ROOT / options.out
    out_root.mkdir(parents=True, exist_ok=True)
    dataset = dataset_path()
    resolved = resolve_evaluator_config(options.config, out_root, dataset_path=dataset)
    config_path = resolved["config_path"]

    report: dict = {"config_substitutions": resolved["substitutions"],
                    "cap_exhausted_recheck": [], "not_found_native_probe": []}

    # ---- 1) 预算耗尽格：改前 vs 改后 ----
    for response_path in sorted(smoke_root.rglob("agent_response.json")):
        original = _read(response_path)
        if not _is_cap_response(original):
            continue
        cell_dir = response_path.parent
        trial_record_path = cell_dir / "trial_record.json"
        record = _read(trial_record_path) if trial_record_path.exists() else {}
        task_id = int(record.get("task_id") or cell_dir.parent.name)
        steps = record.get("steps") or 20
        task = load_task_definition(task_id, path=dataset)
        har = cell_dir / "network.har"

        before = _evaluate(cell_dir, task_id, config_path)

        fixed_dir = out_root / "cap_exhausted_fixed" / str(cell_dir.relative_to(smoke_root))
        fixed_dir.mkdir(parents=True, exist_ok=True)
        fixed = make_agent_response(
            task, completed=False, answer=original.get("error_details") or "",
            diagnostic=f"{CAP_MARKER} after {steps} steps; no answer produced")
        (fixed_dir / "agent_response.json").write_text(
            json.dumps(fixed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if har.exists():
            shutil.copy(har, fixed_dir / "network.har")
        after = _evaluate(fixed_dir, task_id, config_path)

        report["cap_exhausted_recheck"].append({
            "cell": str(cell_dir.relative_to(smoke_root)),
            "task_id": task_id, "steps": steps,
            "before": {"status": original.get("status"),
                       "retrieved_data": original.get("retrieved_data"),
                       "evaluation_status": before["evaluation_status"],
                       "official_success": before["official_success"]},
            "after": {"status": fixed["status"], "retrieved_data": fixed["retrieved_data"],
                      "error_details": fixed["error_details"],
                      "evaluation_status": after["evaluation_status"],
                      "official_success": after["official_success"]},
        })

    # ---- 2) 真 NOT_FOUND 回答的原生评分探针 ----
    design = load_design(options.manifest)
    available_har = {int(p.parent.parent.name): p
                     for p in smoke_root.rglob("network.har")
                     if p.parent.parent.name.isdigit()}
    for task_id in options.task_ids:
        task = load_task_definition(task_id, path=dataset)
        probe_dir = out_root / "not_found_probe" / str(task_id)
        probe_dir.mkdir(parents=True, exist_ok=True)
        response = make_agent_response(
            task, completed=True,
            answer="No reviewer on this product page mentions that; nothing found.")
        (probe_dir / "agent_response.json").write_text(
            json.dumps(response, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        har = available_har.get(task_id)
        if har is None:
            legacy = sorted((ROOT / "experiments").rglob(f"task_{task_id}/*/network.har"))
            har = legacy[0] if legacy else None
        if har is None:
            report["not_found_native_probe"].append(
                {"task_id": task_id, "skipped": "no network.har available for this task"})
            continue
        shutil.copy(har, probe_dir / "network.har")
        outcome = _evaluate(probe_dir, task_id, config_path)
        report["not_found_native_probe"].append({
            "task_id": task_id,
            "har": str(har.relative_to(ROOT)),
            "response": response,
            "evaluation_status": outcome["evaluation_status"],
            "official_success": outcome["official_success"],
            "evaluator_names": [item.get("evaluator_name")
                                for item in (outcome["result"].get("evaluators_results") or [])],
        })

    (out_root / "recheck_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")

    print(json.dumps({
        "cap_exhausted_cells": len(report["cap_exhausted_recheck"]),
        "after_all_native": all(item["after"]["evaluation_status"] == "native"
                                for item in report["cap_exhausted_recheck"]),
        "after_all_failed": all(item["after"]["official_success"] is False
                                for item in report["cap_exhausted_recheck"]),
        "not_found_native": {item["task_id"]: {
            "evaluation_status": item.get("evaluation_status"),
            "official_success": item.get("official_success")}
            for item in report["not_found_native_probe"]},
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
