"""聚合 trace 里的 LLM provenance，产出**不含敏感内容**的摘要。

动机：`trial_record.json` 只记录我们**请求**的 `model_profile`，而"端点实际返回了哪个
模型"目前只存在于 trace 里。trace 含提示词、URL 与页面观察，不适合入库；因此这里只把
计数与统计落盘：每次调用的 served_model、是否发生替换、token/缓存用量、延迟与冷启动次数。

摘要里**不含**：提示词文本、模型回答、页面观察、URL、任务 intent、错误正文。
文件名（trial stem）只含模型/架构/故障/任务/seed/条件/重复，本身不含敏感信息。
"""

from __future__ import annotations

import json
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

SCHEMA = "llm_provenance_summary/1"
SCHEMA_NOTE = ("仅聚合计数与 token/延迟统计，不含提示词、回答、页面观察、URL 或任务正文。")
SLOW_CALL_THRESHOLD_S = 60.0
SLOW_CALL_NOTE = ("统计单次调用延迟 > 阈值。Qwen 端点上实测为网关空闲驱逐导致的冷启动"
                  "（273–280s）；其它端点也可能是长生成或客户端重试，故用中性口径命名。")

_TOKEN_FIELDS = ("prompt_tokens", "completion_tokens", "total_tokens",
                 "prompt_cache_hit_tokens", "prompt_cache_miss_tokens")


def parse_trial_stem(stem: str) -> dict:
    """从 trace 文件名解析 trial 身份；解析不出来就留空而不是猜。"""
    parts = stem.split("__")
    if len(parts) < 7:
        return {"trial": stem, "model_profile": None, "architecture": None,
                "fault": None, "task": None, "seed": None, "condition": None,
                "replicate": None}
    model, architecture, fault, task, seed, condition, replicate = parts[:7]
    return {
        "trial": stem,
        "model_profile": model,
        "architecture": architecture,
        "fault": fault,
        "task": task[4:] if task.startswith("task") else task,
        "seed": seed[4:] if seed.startswith("seed") else seed,
        "condition": condition,
        "replicate": replicate[3:] if replicate.startswith("rep") else replicate,
    }


def _empty_tokens() -> dict:
    return {field: 0 for field in _TOKEN_FIELDS}


def summarize_traces(trace_dir: str | Path, label: str | None = None) -> dict:
    """读取目录下所有 ``*.jsonl``，产出聚合摘要。"""
    trace_dir = Path(trace_dir)
    trials: list[dict] = []
    totals = {"calls": 0, "served_model_differs": 0, **_empty_tokens()}
    by_architecture: dict[str, dict] = defaultdict(lambda: {"calls": 0, **_empty_tokens()})
    served_models: Counter = Counter()
    served_sources: Counter = Counter()
    latencies: list[float] = []
    slow_by_arch: Counter = Counter()

    for path in sorted(trace_dir.rglob("*.jsonl")):
        identity = parse_trial_stem(path.stem)
        calls = 0
        differs = 0
        models: Counter = Counter()
        sources: Counter = Counter()
        tokens = _empty_tokens()
        lat: list[float] = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("event") != "llm_provenance":
                continue
            calls += 1
            served = event.get("served_model") or "(unreported)"
            models[served] += 1
            served_models[served] += 1
            source = event.get("cache_usage_source") or "(none)"
            sources[source] += 1
            served_sources[source] += 1
            if event.get("served_model_differs"):
                differs += 1
            for field in _TOKEN_FIELDS:
                value = event.get(field)
                if isinstance(value, int):
                    tokens[field] += value
                    totals[field] += value
                    by_architecture[identity["architecture"] or "(unknown)"][field] += value
            if isinstance(event.get("latency_s"), (int, float)):
                lat.append(float(event["latency_s"]))
                latencies.append(float(event["latency_s"]))

        arch_key = identity["architecture"] or "(unknown)"
        by_architecture[arch_key]["calls"] += calls
        slow = sum(1 for value in lat if value > SLOW_CALL_THRESHOLD_S)
        if slow:
            slow_by_arch[arch_key] += slow
        totals["calls"] += calls
        totals["served_model_differs"] += differs
        trials.append({
            **identity,
            "calls": calls,
            "served_models": dict(models),
            "served_model_differs": differs,
            "cache_usage_sources": dict(sources),
            "tokens": tokens,
            "latency_s": {
                "min": min(lat) if lat else None,
                "median": statistics.median(lat) if lat else None,
                "max": max(lat) if lat else None,
            },
            "slow_calls": slow,
        })

    return {
        "schema": SCHEMA,
        "schema_note": SCHEMA_NOTE,
        "label": label or str(trace_dir),
        "source_dir": str(trace_dir),
        "generated_at": time.time(),
        "trials_total": len(trials),
        "trials": trials,
        "totals": totals,
        "by_architecture": dict(by_architecture),
        "served_model_totals": dict(served_models),
        "cache_usage_source_totals": dict(served_sources),
        "latency_s": {
            "calls_with_latency": len(latencies),
            "min": min(latencies) if latencies else None,
            "median": statistics.median(latencies) if latencies else None,
            "max": max(latencies) if latencies else None,
        },
        "slow_calls": {
            "threshold_s": SLOW_CALL_THRESHOLD_S,
            "note": SLOW_CALL_NOTE,
            "count": sum(slow_by_arch.values()),
            "by_architecture": dict(slow_by_arch),
        },
    }
