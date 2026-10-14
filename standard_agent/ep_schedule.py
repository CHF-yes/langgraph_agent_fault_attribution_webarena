"""E-P 交错调度：用确定性 seed 生成 block 内的交错顺序并落盘 ``schedule.json``。

设计第 4 节要求：同一 block（同任务、同 seed）内的 single/control、single/fault、
repeat3/control、repeat3/fault 四个 trial 在尽量相同的时间窗口内交错运行，并轮换
variant 顺序与 condition 顺序。block 之间也做确定性打散，避免模型/架构/故障/任务
总是以同一顺序出现。

调度只决定**运行顺序与身份**，不代表任何结果；``schedule.json`` 里记录 seed、
每个 entry 的 variant/condition 与 pair_key，便于事后审计"是否真的交错"。
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from standard_agent.trial_metadata import pair_key, trial_file_stem

EP_VARIANTS = ("single", "repeat3")
EP_CONDITIONS = ("control", "fault")


def _cells_for_block(index: int, variants, conditions):
    """一个 block 的 4 个 cell，带顺序平衡。

    * variant 顺序按 block 奇偶交替（保证交错且首 variant 轮换）；
    * condition 顺序按 (index // 2) 奇偶交替。
    """
    ordered_variants = list(variants) if index % 2 == 0 else list(reversed(variants))
    ordered_conditions = list(conditions) if (index // 2) % 2 == 0 else list(reversed(conditions))
    return [(variant, condition) for variant in ordered_variants
            for condition in ordered_conditions]


def build_schedule(*, models, architectures, faults, tasks, seeds,
                   experiment_id: str = "ep-persistence-v1",
                   variants=EP_VARIANTS, conditions=EP_CONDITIONS,
                   schedule_seed: int = 20261014) -> dict:
    """生成确定性交错调度。同 seed → 同顺序。"""
    blocks = [(model, architecture, fault, int(task), int(seed))
              for model in models for architecture in architectures
              for fault in faults for task in tasks for seed in seeds]
    rng = random.Random(schedule_seed)
    rng.shuffle(blocks)
    entries = []
    for index, (model, architecture, fault, task, seed) in enumerate(blocks):
        for order, (variant, condition) in enumerate(_cells_for_block(index, variants, conditions)):
            entries.append({
                "block": index,
                "block_order": order,
                "model_profile": model,
                "architecture": architecture,
                "fault_type": fault,
                "task_id": task,
                "fault_seed": seed,
                "experiment_id": experiment_id,
                "variant": variant,
                "condition": condition,
                "pair_key": pair_key(
                    model_profile=model, architecture=architecture, fault_type=fault,
                    task_id=task, seed=seed, experiment_id=experiment_id,
                    fault_variant=variant),
                "file_stem": trial_file_stem(
                    model_profile=model, architecture=architecture, fault_type=fault,
                    task_id=task, seed=seed, condition=condition,
                    experiment_id=experiment_id, fault_variant=variant),
            })
    return {
        "schema": "ep_schedule/1",
        "experiment_id": experiment_id,
        "schedule_seed": schedule_seed,
        "variants": list(variants),
        "conditions": list(conditions),
        "blocks": len(blocks),
        "cells": len(entries),
        "entries": entries,
    }


def first_variant_per_block(schedule: dict) -> list[str]:
    """每个 block 的第一个 variant，用于验证交错确实轮换。"""
    firsts = {}
    for entry in schedule["entries"]:
        firsts.setdefault(entry["block"], entry["variant"])
    return [firsts[key] for key in sorted(firsts)]


def write_schedule(schedule: dict, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(schedule, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return path
