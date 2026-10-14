#!/usr/bin/env python3
"""E-P 持续性分析 CLI：读 E-P 的 rows.json，写 D_f（差中差）报告。

只读，不发 trial。用法::

    python scripts/ep_analysis.py --rows experiments/ep_persistence_v1/**/rows.json \
        --output-dir experiments/ep_persistence_v1/analysis
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from standard_agent.ep_analysis import ep_analysis, format_report  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", nargs="+", required=True, help="一个或多个 rows.json")
    parser.add_argument("--output-dir", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows = []
    for path in args.rows:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        rows.extend(payload.get("rows") or [])
    analysis = ep_analysis(rows)
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "ep_analysis.json").write_text(
        json.dumps(analysis, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    (out / "ep_analysis.md").write_text(format_report(analysis), encoding="utf-8")
    print(json.dumps({"rows": len(rows),
                      "faults": sorted(analysis["by_fault"]),
                      "confirmatory_inference": analysis["confirmatory_inference"]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
