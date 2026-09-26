#!/usr/bin/env python3
"""付费 smoke 开跑前门槛：确认代码版本正确且目标目录没有既有产物。

退出码：0 = 可以开跑；2 = 被挡住（有既有产物 / 版本不符 / 工作区不干净 /
读不到版本）。这是防重复付费的保守检查。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from standard_agent.stage_c_pipeline import smoke_preflight  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--official-output-root", required=True)
    parser.add_argument("--run-dir", default=None,
                        help="矩阵的 --output-dir（含 *.status.json / manifest.json）")
    parser.add_argument("--expect-commit", default="",
                        help="期望的 HEAD（可给短 sha，如 6b241be）")
    parser.add_argument("--repo-root", default=str(ROOT))
    parser.add_argument("--allow-existing", action="store_true")
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--json-out", default=None)
    options = parser.parse_args()

    report = smoke_preflight(
        repo_root=options.repo_root,
        output_root=options.official_output_root,
        run_dir=options.run_dir,
        expect_commit=options.expect_commit,
        allow_existing=options.allow_existing,
        require_clean=not options.allow_dirty,
    )
    if options.json_out:
        Path(options.json_out).write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["allowed_to_run_paid_trials"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
