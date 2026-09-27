#!/usr/bin/env python3
"""把 trace 里的 LLM provenance 汇总成不含敏感内容的 JSON。

trace 含提示词与页面观察，不适合入库；本脚本只落盘聚合计数与 token/延迟统计::

    python scripts/summarize_llm_provenance.py \
        --trace-dir traces/stage_c_smoke_qwen \
        --out experiments/stage_c_smoke_qwen/provenance_summary.json \
        --label "Qwen 两架构小批 (qwen38_flash, 8 trials)"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from standard_agent.provenance_summary import summarize_traces  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--trace-dir", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--label", default=None)
    options = parser.parse_args()

    summary = summarize_traces(ROOT / options.trace_dir, label=options.label)
    out = ROOT / options.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n",
                   encoding="utf-8")
    print(json.dumps({
        "out": options.out,
        "trials": summary["trials_total"],
        "calls": summary["totals"]["calls"],
        "served_model_totals": summary["served_model_totals"],
        "served_model_differs": summary["totals"]["served_model_differs"],
        "slow_calls": summary["slow_calls"]["count"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
