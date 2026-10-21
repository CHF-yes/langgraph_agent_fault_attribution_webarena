#!/usr/bin/env python3
"""HTTP service v1 离线验收：跑相关测试并写 acceptance.json。

明确区分：
* 离线测试覆盖的代码行为（endpoint 匹配、query/p 参数、窗口、释放、两架构失败处理）；
* 真实运行尚未覆盖的验收项（例如"换 query 后仍被阻断"在真实 agent 上是否发生）。
后者必须在四臂块里逐项标记 covered / not covered，不能由离线测试代签。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "experiments/ep_http_service_v1/acceptance.json"
TESTS = ["tests/test_http_service_v1.py", "tests/test_agent_protocol.py",
         "tests/test_ep_acceptance.py"]


def main() -> int:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT), str(ROOT / ".venv311/lib/python3.11/site-packages"),
         "/data/webarena/_testenv/py311", env.get("PYTHONPATH", "")]).strip(os.pathsep)
    proc = subprocess.run([sys.executable, "-m", "pytest", *TESTS, "-q"],
                          cwd=ROOT, env=env, capture_output=True, text=True)
    summary = (proc.stdout or "").strip().splitlines()[-1] if proc.stdout else ""
    report = {
        "experiment_id": "ep-http-service-v1",
        "kind": "offline_acceptance",
        "tests": TESTS,
        "returncode": proc.returncode,
        "summary": summary,
        "passed": proc.returncode == 0,
        "code_behaviour_covered": {
            "scheme_host_port_exact": True,
            "path_segment_boundary_and_searching_excluded": True,
            "query_values_p_sort_ignored": True,
            "search_endpoint_pagination_still_faulted": True,
            "non_search_pagination_clean": True,
            "single_release_and_window_release": True,
            "control_arm_zero_injections": True,
            "evidence_hash_and_release_timeline": True,
            "both_architectures_same_retry_failure_path": True,
        },
        "real_run_coverage": "not_started",
        "real_run_items_to_mark_in_the_four_arm_block": [
            "first qualified navigation actually matches the endpoint",
            "a query change on the same endpoint is still replaced (covered/not covered)",
            "non-endpoint pages stay clean in a real agent trajectory",
            "window expiry restores the endpoint in a real trajectory",
            "control arm remains uncontaminated",
        ],
        "paid_trials": 0,
        "note": "Offline tests prove code behaviour only; they do not certify real-run coverage.",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "summary": summary}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
