#!/usr/bin/env python3
"""E-P 成本估算：用 Stage C 现有 trace 的 token 用量估首批与完整矩阵。

只读 trace 的 llm_provenance 事件（含 prompt/completion tokens），不发 trial。
价格需显式给（--price-in/--price-out，单位 元/百万 token）；不给则只报 token。
"""

from __future__ import annotations

import argparse
import glob
import json
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PILOT_TASKS = (24, 118)
ALL_TASKS = (24, 27, 132, 118)


def tokens_for_tasks(tasks, roots):
    prompt, completion, trials = [], [], 0
    for task in tasks:
        for root in roots:
            for path in glob.glob(f"{root}/**/*task{task}*.jsonl", recursive=True):
                total_p = total_c = 0
                calls = 0
                for line in open(path, encoding="utf-8", errors="ignore"):
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if event.get("event") != "llm_provenance":
                        continue
                    total_p += event.get("prompt_tokens") or 0
                    total_c += event.get("completion_tokens") or 0
                    calls += 1
                if calls:
                    prompt.append(total_p)
                    completion.append(total_c)
                    trials += 1
    return prompt, completion, trials


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--traces-glob", default="traces/stage_c_slice*")
    parser.add_argument("--price-in", type=float, default=None, help="元/百万 prompt token")
    parser.add_argument("--price-out", type=float, default=None, help="元/百万 completion token")
    parser.add_argument("--output", default="experiments/ep_persistence_v1/cost_estimate.json")
    args = parser.parse_args()

    roots = sorted(glob.glob(str(ROOT / args.traces_glob)))
    out = {"unit": "tokens_per_trial", "source": "Stage C traces (llm_provenance)",
           "matched_trace_roots": [Path(r).name for r in roots], "estimates": {}}
    for label, tasks, n_trials in (("pilot_48", PILOT_TASKS, 48),
                                   ("full_384", ALL_TASKS, 384)):
        prompt, completion, n = tokens_for_tasks(tasks, roots)
        mean_p = statistics.mean(prompt) if prompt else 0
        mean_c = statistics.mean(completion) if completion else 0
        entry = {"observed_trials": n, "mean_prompt_tokens": round(mean_p),
                 "mean_completion_tokens": round(mean_c),
                 "planned_trials": n_trials,
                 "est_prompt_tokens": round(mean_p * n_trials),
                 "est_completion_tokens": round(mean_c * n_trials)}
        if args.price_in is not None and args.price_out is not None:
            entry["est_cost"] = round((mean_p * n_trials / 1e6) * args.price_in
                                      + (mean_c * n_trials / 1e6) * args.price_out, 2)
            entry["currency"] = "元"
        out["estimates"][label] = entry
    path = ROOT / args.output
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
