"""Per-trial traceability records for the formal Stage C matrix.

A formal trial is identified by the six-tuple
``(model, architecture, fault, task, seed, condition)``. Everything else --
the trace file, the official response, the HAR, the code revision and the run
configuration -- must be recoverable from that tuple, so a control arm and its
fault arm can be paired by key rather than by directory guessing.

This module is deliberately dependency-free: the runner writes records, the
pipeline and the analysis read them, and the offline tests exercise both
without a browser, an LLM or the official evaluator.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

TRIAL_RECORD_FILENAME = "trial_record.json"
TRIAL_RECORD_SCHEMA_VERSION = 1

# 配对方案标识。新方案：控制臂与故障臂共用同一次运行的 job seed，两臂因此能用
# 同一个 pair_key 配对。**历史产物不是这个方案**：旧产物的控制臂目录是
# ``control_seed_0``（见 experiments/ 下 83 处），因为它用的是 FaultConfig.off()
# 的固定 seed=0。旧产物也没有 trial_record.json，所以审计会把它们判为"不完整"，
# 而不是当成同一 trial —— 恢复运行时另由 design_fingerprint 拦截（见
# scripts/run_fault_matrix.py）。
PAIRING_SCHEME = "seed-paired-v2"
LEGACY_CONTROL_DIR_PREFIX = "control_seed_0"


CONDITION_CONTROL = "control"
CONDITION_FAULT = "fault"

_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def _safe(value: object) -> str:
    """Make a value safe for use inside a filename or key."""
    return _SAFE.sub("-", str(value).strip()) or "unknown"


def make_trial_id(*, model_profile: str, architecture: str, fault_type: str,
                  task_id: object, seed: object, condition: str,
                  replicate: object | None = None) -> str:
    """Return the deterministic key of a formal trial.

    The id is stable across machines and runs so that a control record and its
    fault record differ only in ``condition``.
    """
    parts = [
        f"model={_safe(model_profile)}",
        f"arch={_safe(architecture)}",
        f"fault={_safe(fault_type)}",
        f"task={_safe(task_id)}",
        f"seed={_safe(seed)}",
        f"cond={_safe(condition)}",
    ]
    if replicate is not None:
        parts.append(f"rep={_safe(replicate)}")
    return "|".join(parts)


def pair_key(*, model_profile: str, architecture: str, fault_type: str,
             task_id: object, seed: object, replicate: object | None = None) -> str:
    """Return the key shared by a control trial and its fault counterpart."""
    return make_trial_id(
        model_profile=model_profile, architecture=architecture, fault_type=fault_type,
        task_id=task_id, seed=seed, condition="*", replicate=replicate,
    )


def arm_dir_name(*, fault_label: str, seed: object) -> str:
    """控制/故障臂的输出目录名（新方案：两臂都用 job seed）。

    ``control_seed_0`` 属于历史方案，不要用本函数的产物去比对旧目录。
    """
    return f"{fault_label}_seed_{seed}"


def trial_file_stem(*, model_profile: str, architecture: str, fault_type: str,
                    task_id: object, seed: object, condition: str,
                    replicate: object | None = None) -> str:
    """Return a filesystem-safe stem for one trial's artifacts.

    Used for the trace file name, so a trace can be traced back to its trial
    without opening the record.
    """
    parts = [model_profile, architecture, fault_type, f"task{task_id}",
             f"seed{seed}", condition]
    if replicate is not None:
        parts.append(f"rep{replicate}")
    return "__".join(_safe(part) for part in parts)


def code_version(repo_root: str | Path | None = None) -> dict:
    """Return the code revision that produced a trial.

    ``git_dirty`` is recorded rather than forbidden: a trial produced from a
    dirty tree is not automatically invalid, but it must be visible in the
    manifest so the analysis can exclude or annotate it.
    """
    root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[1]
    info = {"repo_root": str(root), "git_sha": "", "git_branch": "", "git_dirty": None,
            "recorded_at": time.time()}
    for key, args in (
        ("git_sha", ["rev-parse", "HEAD"]),
        ("git_branch", ["rev-parse", "--abbrev-ref", "HEAD"]),
    ):
        try:
            out = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                                 text=True, timeout=10)
            if out.returncode == 0:
                info[key] = out.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        out = subprocess.run(["git", "-C", str(root), "status", "--porcelain"],
                             capture_output=True, text=True, timeout=20)
        if out.returncode == 0:
            info["git_dirty"] = bool(out.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    return info


def build_trial_record(*, model_profile: str, architecture: str, fault_type: str,
                       task_id: object, seed: object, condition: str,
                       run_config: dict, paths: dict | None = None,
                       replicate: object | None = None,
                       completed: bool | None = None,
                       success: bool | None = None,
                       injection_count: int | None = None,
                       error: str | None = None,
                       repo_root: str | Path | None = None) -> dict:
    """Build one trial record.

    ``paths`` carries the artifact locations (trace, agent_response, har, log);
    they are stored relative to ``artifact_root`` when the caller can provide
    one, otherwise verbatim, so a record stays readable after a directory move.
    """
    record = {
        "schema_version": TRIAL_RECORD_SCHEMA_VERSION,
        "pairing_scheme": PAIRING_SCHEME,
        "trial_id": make_trial_id(
            model_profile=model_profile, architecture=architecture, fault_type=fault_type,
            task_id=task_id, seed=seed, condition=condition, replicate=replicate),
        "pair_key": pair_key(
            model_profile=model_profile, architecture=architecture, fault_type=fault_type,
            task_id=task_id, seed=seed, replicate=replicate),
        "condition": condition,
        "model_profile": model_profile,
        "architecture": architecture,
        "fault_type": fault_type,
        "task_id": task_id,
        "fault_seed": seed,
        "replicate": replicate,
        "run_config": dict(run_config),
        "paths": dict(paths or {}),
        "completed": completed,
        "success": success,
        "injection_count": injection_count,
        "error": error,
        "code": code_version(repo_root),
        "created_at": time.time(),
    }
    return record


def write_trial_record(path: str | Path, record: dict) -> Path:
    """Write a trial record atomically (readers never see a partial file)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2, default=str) + "\n",
                   encoding="utf-8")
    os.replace(tmp, target)
    return target


def read_trial_record(path: str | Path) -> dict:
    """Read a trial record, rejecting an unexpected schema version."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    version = data.get("schema_version")
    if version != TRIAL_RECORD_SCHEMA_VERSION:
        raise ValueError(
            f"{path}: unsupported trial record schema_version={version!r} "
            f"(expected {TRIAL_RECORD_SCHEMA_VERSION})")
    # 注意：pairing_scheme 故意不在这里强制。旧产物缺少该字段时，读取层仍然
    # 允许读出 trial_id/条件，好让审计能**指名**该格子"记录属于旧方案"，
    # 而不是因为读不出来被误报成"根本没有产物"。方案是否可接受由
    # stage_c_pipeline.check_artifacts 判定。
    for field in ("trial_id", "pair_key", "condition", "model_profile",
                  "architecture", "fault_type", "task_id", "fault_seed"):
        if field not in data:
            raise ValueError(f"{path}: trial record missing required field {field!r}")
    return data


def discover_trial_records(root: str | Path) -> list[dict]:
    """Read every trial record under ``root`` (recursive), sorted by trial id."""
    records = []
    for path in sorted(Path(root).rglob(TRIAL_RECORD_FILENAME)):
        try:
            records.append(read_trial_record(path))
        except (OSError, ValueError):
            continue
    records.sort(key=lambda rec: rec["trial_id"])
    return records


def pair_records(records: list[dict]) -> dict[str, dict]:
    """Group records by ``pair_key`` into ``{"control": record, "fault": record}``.

    Raises on a duplicate arm: silently overwriting one of two control records
    would hide a rerun that must be reconciled before analysis.
    """
    grouped: dict[str, dict] = {}
    for record in records:
        bucket = grouped.setdefault(record["pair_key"], {})
        condition = record["condition"]
        if condition in bucket:
            raise ValueError(
                f"duplicate {condition} arm for pair {record['pair_key']}: "
                f"{bucket[condition]['trial_id']} vs {record['trial_id']}")
        bucket[condition] = record
    return grouped
