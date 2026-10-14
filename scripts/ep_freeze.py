#!/usr/bin/env python3
"""E-P 冻结：把代码版本、目标策略版本、窗口、任务与数据集哈希写进冻结清单。

设计要求"全部离线验收通过后再冻结代码与配置"。本脚本拒绝在脏树上冻结，
并把冻结清单落盘，供首批运行引用。
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fault_injection.episode import DEFAULT_WINDOW_STEPS, FAULT_VARIANTS  # noqa: E402
from fault_injection.targets import TARGET_POLICY_NAME, TARGET_POLICY_VERSION  # noqa: E402
from standard_agent.stage_c_pipeline import EVALUATOR_ADAPTER_VERSION  # noqa: E402
from standard_agent.trial_metadata import PAIRING_SCHEME, git_status  # noqa: E402

OUT = ROOT / "docs/ep_persistence_frozen_v1.json"


def _sha256(path: Path):
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def main() -> int:
    import os
    status = git_status(ROOT)
    dataset = os.getenv("WEBARENA_DATASET")
    payload = {
        "schema": "experiment_freeze/1",
        "experiment_id": "ep-persistence-v1",
        "git_commit": status.get("head"),
        "git_branch": status.get("branch"),
        "git_dirty": status.get("dirty"),
        "target_policy_name": TARGET_POLICY_NAME,
        "target_policy_version": TARGET_POLICY_VERSION,
        "window_steps": DEFAULT_WINDOW_STEPS,
        "variants": FAULT_VARIANTS,
        "pairing_scheme": PAIRING_SCHEME,
        "evaluator_adapter_version": EVALUATOR_ADAPTER_VERSION,
        "dataset_path": dataset,
        "dataset_sha256": _sha256(Path(dataset)) if dataset else None,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))
    if status.get("dirty"):
        print("⚠️ 工作区为脏（tracked 文件与 HEAD 不同）；冻结清单已写，但不应据此启动首批。",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
