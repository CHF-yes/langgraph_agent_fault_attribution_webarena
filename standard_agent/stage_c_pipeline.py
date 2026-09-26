"""Stage C 正式矩阵闭环：产物完整性 → 官方 evaluator → 审计 → 缺失/失败清单。

闭环的四步是分开的、可单独重跑的，因为它们的失败原因不同：

1. **完整性检查**：trial 目录里是否有 agent_response.json / trial_record.json /
   network.har。缺 HAR 的格子不能进入评分。
2. **官方 evaluator**：跑 native 评分。这一层不修改 agent 输出，也不替模型作答。
3. **审计分类**：把每个格子标成 native / compatibility / error。compatibility 指
   命中上游 null-schema 兼容分支，error 指评分本身失败——两者都必须与
   "评测判定为失败"（合法的研究结果）区分开。
4. **缺失/失败清单**：给出需要补跑或需要排查的格子。

**完成率绝不是官方成功率。** Agent 自报 SUCCESS 只是它认为自己完成了；本模块
把这两个口径分开记录、分开输出，禁止用完成率替代官方成功率。
"""

from __future__ import annotations

import json
from pathlib import Path

AGENT_RESPONSE = "agent_response.json"
TRIAL_RECORD = "trial_record.json"
NETWORK_HAR = "network.har"

EVALUATION_STATUS_NATIVE = "native"
EVALUATION_STATUS_COMPATIBILITY = "compatibility"
EVALUATION_STATUS_ERROR = "error"
EVALUATION_STATUS_SKIPPED = "skipped"

_COMPAT_EVALUATOR_NAME = "AgentResponseEvaluatorCompat"

DEFAULT_MANIFEST = "docs/task_manifest_public16.json"


# --------------------------------------------------------------------------
# 设计清单：哪些格子是"应该存在"的
# --------------------------------------------------------------------------

def load_design(manifest_path: str | Path = DEFAULT_MANIFEST) -> dict:
    """读取冻结清单，产出模型/架构/故障/重复/条件与任务角色。"""
    raw = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    design = raw.get("design") or {}
    categories = raw.get("categories") or []
    main_tasks: list[int] = []
    validation_tasks: list[int] = []
    category_of: dict[int, str] = {}
    for category in categories:
        for task_id in category.get("main_task_ids", []):
            main_tasks.append(int(task_id))
            category_of[int(task_id)] = category["name"]
        for task_id in category.get("validation_task_ids", []):
            validation_tasks.append(int(task_id))
            category_of[int(task_id)] = category["name"]
    return {
        "main_models": list(raw.get("main_models") or []),
        "validation_model": raw.get("validation_model"),
        "architectures": list(design.get("architectures") or []),
        "faults": list(design.get("faults") or []),
        "repetitions": int(design.get("independent_repetitions") or 1),
        "conditions": list(design.get("conditions") or ["control", "fault"]),
        "max_steps": int(design.get("max_steps") or 20),
        "main_tasks": sorted(set(main_tasks)),
        "validation_tasks": sorted(set(validation_tasks)),
        "category_of": category_of,
        "raw": raw,
    }


def expected_cells(design: dict, *, model_profile: str, architecture: str,
                   faults: list[str] | None = None,
                   seeds: list[int] | None = None) -> list[dict]:
    """列出某个 (model, architecture) 下应当存在的 trial 格子。

    验证模型（V4 Pro）只在共同 8 任务上出现，这是冻结设计的一部分：它不能进入
    16 任务主估计。当 ``model_profile`` 是验证模型时，任务集合被限制为
    8 个共同任务。
    """
    if model_profile == design.get("validation_model"):
        tasks = list(design["validation_tasks"])
    else:
        tasks = list(design["main_tasks"])
    faults = list(faults if faults is not None else design["faults"])
    seeds = list(seeds) if seeds is not None else [1 + index for index in range(design["repetitions"])]
    cells = []
    for task_id in tasks:
        for fault_type in faults:
            for seed in seeds:
                for condition in design["conditions"]:
                    cells.append({
                        "model_profile": model_profile,
                        "architecture": architecture,
                        "fault_type": fault_type if condition == "fault" else fault_type,
                        "task_id": task_id,
                        "seed": seed,
                        "condition": condition,
                        "category": design["category_of"].get(task_id, ""),
                        "role": ("validation" if model_profile == design.get("validation_model")
                                 else "main"),
                    })
    return cells


# --------------------------------------------------------------------------
# 第 1 步：产物完整性
# --------------------------------------------------------------------------

def parse_agent_response(path: str | Path) -> dict:
    """读取并做最小 schema 校验；不合法则抛 ValueError。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("agent_response is not a JSON object")
    task_type = data.get("task_type")
    status = data.get("status")
    if not task_type or not status:
        raise ValueError("agent_response missing task_type/status")
    if "retrieved_data" not in data:
        raise ValueError("agent_response missing retrieved_data")
    return data


def check_artifacts(trial_dir: str | Path) -> dict:
    """检查一个 trial 目录的产物完整性。

    返回 ``{"complete": bool, "artifacts": {name: {"present","valid","reason"}},
    "trial_record": {...}|None, "agent_response": {...}|None}``。
    """
    trial_dir = Path(trial_dir)
    result: dict = {"trial_dir": str(trial_dir), "complete": True, "artifacts": {},
                    "trial_record": None, "agent_response": None, "reasons": []}

    for name, filename in (("agent_response", AGENT_RESPONSE),
                           ("trial_record", TRIAL_RECORD),
                           ("har", NETWORK_HAR)):
        path = trial_dir / filename
        entry = {"path": str(path), "present": path.exists(), "valid": False, "reason": ""}
        if not path.exists():
            entry["reason"] = "missing"
            result["complete"] = False
            result["reasons"].append(f"{filename}: missing")
        else:
            entry["valid"] = path.stat().st_size > 0
            if not entry["valid"]:
                entry["reason"] = "empty"
                result["complete"] = False
                result["reasons"].append(f"{filename}: empty")
        result["artifacts"][name] = entry

    if result["artifacts"]["agent_response"]["valid"]:
        try:
            result["agent_response"] = parse_agent_response(trial_dir / AGENT_RESPONSE)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            result["artifacts"]["agent_response"]["valid"] = False
            result["artifacts"]["agent_response"]["reason"] = f"invalid: {exc}"
            result["complete"] = False
            result["reasons"].append(f"{AGENT_RESPONSE}: invalid: {exc}")

    if result["artifacts"]["trial_record"]["valid"]:
        try:
            from standard_agent.trial_metadata import PAIRING_SCHEME, read_trial_record
            record = read_trial_record(trial_dir / TRIAL_RECORD)
            result["trial_record"] = record
            scheme = record.get("pairing_scheme")
            if scheme != PAIRING_SCHEME:
                # 旧产物（如 control_seed_0 时代）没有这个字段。它们可以读出来、
                # 可以指名到格子，但不能进入本次正式分析：报 incomplete 并带上
                # 具体理由，绝不静默配对或静默丢弃。
                result["artifacts"]["trial_record"]["valid"] = False
                result["artifacts"]["trial_record"]["reason"] = (
                    f"legacy pairing scheme {scheme!r} != {PAIRING_SCHEME!r}")
                result["complete"] = False
                result["reasons"].append(
                    f"{TRIAL_RECORD}: legacy pairing scheme {scheme!r} != {PAIRING_SCHEME!r}")
        except (OSError, ValueError) as exc:
            result["artifacts"]["trial_record"]["valid"] = False
            result["artifacts"]["trial_record"]["reason"] = f"invalid: {exc}"
            result["complete"] = False
            result["reasons"].append(f"{TRIAL_RECORD}: invalid: {exc}")

    return result


# --------------------------------------------------------------------------
# 第 2 步：官方 evaluator
# --------------------------------------------------------------------------

def classify_evaluation(result: dict) -> dict:
    """把 evaluator 返回分类为 native / compatibility / error。

    - ``error``：评分没有跑完（status==ERROR 或 error_msg 非空）。这不是 agent 的失败。
    - ``compatibility``：命中了上游 null-schema 兼容分支（本项目适配器的兜底）。
    - ``native``：上游 evaluator 正常给出了判定。
    """
    if not isinstance(result, dict):
        return {"evaluation_status": EVALUATION_STATUS_ERROR,
                "official_success": None, "reason": "evaluator returned no dict"}
    reason = str(result.get("error_msg") or "")
    status = str(result.get("status") or "").casefold()
    evaluators = [str(item.get("evaluator_name") or "")
                  for item in (result.get("evaluators_results") or [])]
    if status == EVALUATION_STATUS_ERROR or (reason and not evaluators):
        return {"evaluation_status": EVALUATION_STATUS_ERROR,
                "official_success": None, "reason": reason or "evaluator error"}
    if any(_COMPAT_EVALUATOR_NAME in name for name in evaluators):
        return {"evaluation_status": EVALUATION_STATUS_COMPATIBILITY,
                "official_success": bool(result.get("official_success")),
                "reason": reason}
    if not evaluators:
        return {"evaluation_status": EVALUATION_STATUS_ERROR,
                "official_success": None, "reason": "no evaluator results"}
    return {"evaluation_status": EVALUATION_STATUS_NATIVE,
            "official_success": bool(result.get("official_success")), "reason": reason}


def default_evaluate(task_id: int, *, agent_response_path, network_trace_path, config_path):
    from standard_agent.webarena_evaluator import evaluate_task_safe
    return evaluate_task_safe(
        task_id, agent_response_path=agent_response_path,
        network_trace_path=network_trace_path, config_path=config_path,
    )


def _readable_exists(path) -> bool:
    """``Path.exists()`` 会在无权进入的目录上抛 PermissionError，这里降级为 False。

    正式配置指向服务器布局 ``/root/...``；本机复算时该路径可能可 stat 失败而不是
    "不存在"，两种情况都应触发路径替换，而不是让整条审计崩掉。
    """
    try:
        return Path(path).exists()
    except OSError:
        return False


def resolve_evaluator_config(config_path: str | Path | None, out_dir: str | Path,
                             dataset_path: str | Path | None = None) -> dict:
    """返回可用于本机的 evaluator 配置路径，并记录做过的路径替换。

    正式产物是在服务器布局（``/root/...``）下生成的。本机复算时若 ``test_data_file``
    指向不存在的路径，就用 ``WEBARENA_DATASET`` 替换，并把替换写进 ``substitutions``，
    这样审计报告可以说清"评分用的到底是哪份数据"。
    """
    out_dir = Path(out_dir)
    substitutions: dict = {}
    if config_path is None:
        return {"config_path": None, "substitutions": substitutions}
    config_path = Path(config_path)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    candidate = config.get("test_data_file")
    replacement = Path(dataset_path) if dataset_path else None
    if candidate and not _readable_exists(candidate) and replacement and _readable_exists(replacement):
        substitutions["test_data_file"] = {"from": candidate, "to": str(replacement)}
        config["test_data_file"] = str(replacement)
    if substitutions:
        out_dir.mkdir(parents=True, exist_ok=True)
        resolved = out_dir / "evaluator_config.resolved.json"
        resolved.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
        return {"config_path": resolved, "substitutions": substitutions}
    return {"config_path": config_path, "substitutions": substitutions}


def evaluate_trial(trial_dir: str | Path, *, task_id: int, config_path,
                   evaluate_fn=None) -> dict:
    """对一个 trial 目录跑官方 evaluator 并写出 ``official_eval.json``。"""
    trial_dir = Path(trial_dir)
    evaluate_fn = evaluate_fn or default_evaluate
    result = evaluate_fn(
        task_id, agent_response_path=trial_dir / AGENT_RESPONSE,
        network_trace_path=trial_dir / NETWORK_HAR, config_path=config_path,
    )
    classification = classify_evaluation(result)
    (trial_dir / "official_eval.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8")
    return {"result": result, **classification}


# --------------------------------------------------------------------------
# 第 3/4 步：审计与清单
# --------------------------------------------------------------------------

def discover_trials(root: str | Path) -> list[Path]:
    """列出含 agent_response.json 的 trial 目录。"""
    return sorted({path.parent for path in Path(root).rglob(AGENT_RESPONSE)})


def cell_key(cell: dict) -> tuple:
    return (cell["model_profile"], cell["architecture"], cell["fault_type"],
            int(cell["task_id"]), int(cell["seed"]), cell["condition"])


def collect_rows(root: str | Path, *, config_path=None, evaluate_fn=None,
                 out_dir=None, dataset_path=None, limit: int | None = None,
                 evaluate: bool = False) -> list[dict]:
    """对 ``root`` 下所有 trial 执行完整性检查（并按需评分）。"""
    evaluated_config = {"config_path": config_path, "substitutions": {}}
    if out_dir is not None:
        evaluated_config = resolve_evaluator_config(
            config_path, out_dir, dataset_path=dataset_path)
    rows = []
    for trial_dir in discover_trials(root):
        if limit is not None and len(rows) >= limit:
            break
        integrity = check_artifacts(trial_dir)
        row = {
            "trial_dir": str(trial_dir),
            "complete": integrity["complete"],
            "reasons": integrity["reasons"],
            "evaluation_status": EVALUATION_STATUS_SKIPPED,
            "official_success": None,
            "submitted_success": None,
            "trial_id": "",
            "pair_key": "",
            "condition": "",
            "cell": None,
        }
        record = integrity.get("trial_record")
        if record:
            row["trial_id"] = record["trial_id"]
            row["pair_key"] = record["pair_key"]
            row["condition"] = record["condition"]
            row["cell"] = {key: record[key] for key in (
                "model_profile", "architecture", "fault_type", "task_id",
                "fault_seed", "condition")}
        response = integrity.get("agent_response")
        if response:
            row["submitted_success"] = str(response.get("status") or "").casefold() == "success"
        if integrity["complete"] and (evaluate or evaluate_fn is not None):
            task_id = int((record or {}).get("task_id") or trial_dir.parts[-3])
            outcome = evaluate_trial(trial_dir, task_id=task_id,
                                     config_path=evaluated_config["config_path"],
                                     evaluate_fn=evaluate_fn)
            row.update({key: outcome[key] for key in (
                "evaluation_status", "official_success", "reason")})
        rows.append(row)
    if out_dir is not None:
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        (Path(out_dir) / "rows.json").write_text(
            json.dumps({"config_substitutions": evaluated_config["substitutions"],
                        "rows": rows}, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8")
    return rows


def audit(rows: list[dict], expected: list[dict]) -> dict:
    """产出缺失/失败清单，并把官方成功率与完成率分开统计。"""
    by_key: dict[tuple, dict] = {}
    for row in rows:
        cell = row.get("cell")
        if not cell:
            continue
        key = (cell["model_profile"], cell["architecture"], cell["fault_type"],
               int(cell["task_id"]), int(cell["fault_seed"]), cell["condition"])
        by_key.setdefault(key, row)

    # 四类"没有进入官方成功率分母"的格子必须分别列出，不能合并成一个数字：
    #   missing      根本没有产物
    #   incomplete   产物存在但不完整/非法（如缺 HAR）
    #   unevaluated  产物完整但本次没有评分（例如只跑 check 模式）
    #   error        评分本身失败（evaluator error）
    missing, incomplete, unevaluated, errors = [], [], [], []
    evaluated, official_successes = [], []
    compatibility = 0
    submitted_successes = []
    for cell in expected:
        row = by_key.get(cell_key(cell))
        if row is None:
            missing.append({**cell, "why": "no artifacts found"})
            continue
        if not row["complete"]:
            incomplete.append({**cell, "why": "; ".join(row.get("reasons") or [])})
            continue
        status = row["evaluation_status"]
        if status == EVALUATION_STATUS_ERROR:
            errors.append({**cell, "why": row.get("reason") or "evaluator error",
                           "trial_dir": row["trial_dir"]})
            continue
        if status == EVALUATION_STATUS_SKIPPED:
            unevaluated.append({**cell, "why": "not evaluated in this run",
                                "trial_dir": row["trial_dir"]})
            continue
        if status == EVALUATION_STATUS_COMPATIBILITY:
            compatibility += 1
        evaluated.append(row)
        if row["official_success"]:
            official_successes.append(row)
        submitted_successes.append(bool(row.get("submitted_success")))

    # 配对检查：控制臂与故障臂必须成对出现，否则退化量不可估计。
    # 用 trial record 里的 condition 判定，而不是目录名。
    pairs: dict[str, set[str]] = {}
    for row in rows:
        if row.get("pair_key"):
            pairs.setdefault(row["pair_key"], set()).add(row.get("condition") or "unknown")
    unpaired = sorted(key for key, arms in pairs.items() if not {"control", "fault"} <= arms)

    # 连 trial_record 都读不出来、因而无法归属到任何期望格子的目录，单独列出。
    unidentified = [{"trial_dir": row["trial_dir"], "reasons": row.get("reasons") or []}
                    for row in rows if not row.get("cell")]
    total_expected = len(expected)
    counts = {
        "expected": total_expected,
        "evaluated": len(evaluated),
        "missing": len(missing),
        "incomplete": len(incomplete),
        "unevaluated": len(unevaluated),
        "error": len(errors),
    }
    counts["accounted"] = (counts["evaluated"] + counts["missing"] + counts["incomplete"]
                           + counts["unevaluated"] + counts["error"])
    counts["consistent"] = counts["accounted"] == counts["expected"]
    return {
        "expected_cells": total_expected,
        "cell_counts": counts,
        "found_cells": len(evaluated) + len(errors) + len(unevaluated),
        "missing_cells": missing,
        "incomplete_cells": incomplete,
        "unevaluated_cells": unevaluated,
        "error_cells": errors,
        "missing_or_failed": (missing + incomplete
                              + [dict(cell, why="evaluator error") for cell in errors]
                              + [dict(cell, why="not evaluated") for cell in unevaluated]),
        "evaluated_cells": len(evaluated),
        "evaluation_status_counts": {
            EVALUATION_STATUS_NATIVE: sum(
                1 for row in evaluated if row["evaluation_status"] == EVALUATION_STATUS_NATIVE),
            EVALUATION_STATUS_COMPATIBILITY: compatibility,
            EVALUATION_STATUS_ERROR: len(errors),
        },
        # 两个口径分开报，且官方成功率只用 evaluator 判定。
        "official_successes": len(official_successes),
        "official_success_rate": (len(official_successes) / len(evaluated)) if evaluated else None,
        "submitted_completion_rate": (
            sum(submitted_successes) / len(submitted_successes)) if submitted_successes else None,
        "rate_denominators": {
            "official_success_rate": "evaluated cells only (evaluator status success/failure)",
            "submitted_completion_rate": "evaluated cells only (agent_response status==SUCCESS)",
        },
        "unidentified_artifact_dirs": unidentified,
        "unpaired_keys": unpaired,
        "note": ("official_success_rate 只统计 evaluator 判定；submitted_completion_rate 是 "
                 "agent 自报完成率。二者不可互相替代，报告里必须分别标注。"),
    }
