#!/usr/bin/env python3
"""E-P 零付费验收运行器：跑 tests/test_ep_acceptance.py 并落盘结果。

不调用模型 API、不发 trial —— 这是设计第 9 节所说的"离线验收"。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "experiments/ep_persistence_v1/acceptance.json"


def main() -> int:
    # 测试依赖（pytest）在本机位于 _testenv；子进程需要显式带上。
    env = dict(os.environ)
    extra = [str(ROOT)]
    for candidate in ("/data/webarena/_testenv/py311",
                      str(ROOT / ".venv311/lib/python3.11/site-packages")):
        if Path(candidate).exists():
            extra.append(candidate)
    env["PYTHONPATH"] = os.pathsep.join(
        extra + [env.get("PYTHONPATH", "")]).strip(os.pathsep)
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_ep_acceptance.py", "-q"],
        cwd=ROOT, capture_output=True, text=True, env=env)
    summary = (proc.stdout or "").strip().splitlines()[-1] if proc.stdout else ""
    passed = proc.returncode == 0
    report = {
        "experiment_id": "ep-persistence-v1",
        "kind": "offline_acceptance",
        "criteria_module": "tests/test_ep_acceptance.py",
        "returncode": proc.returncode,
        "summary": summary,
        "passed": passed,
        "note": "标准：同目标重复故障确实生效、记录完整、成本可接受；不要求成功率下降。",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
