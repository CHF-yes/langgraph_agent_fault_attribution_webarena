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
            "steps": None,
            "cap_exhausted": None,
            "llm_calls": None,
            "cell": None,
        }
        record = integrity.get("trial_record")
        if record:
            row["trial_id"] = record["trial_id"]
            row["pair_key"] = record["pair_key"]
            row["condition"] = record["condition"]
            row["steps"] = record.get("steps")
            row["cap_exhausted"] = record.get("cap_exhausted")
            row["llm_calls"] = record.get("llm_calls")
            row["cell"] = {key: record[key] for key in (
                "model_profile", "architecture", "fault_type", "task_id",
                "fault_seed", "condition")}
        response = integrity.get("agent_response")
        if response:
            row["submitted_success"] = str(response.get("status") or "").casefold() == "success"
            if row["cap_exhausted"] is None:
                # 早于 cap_exhausted 字段的产物（例如首批 smoke）：从响应形态推断，
                # 只用于描述性耗尽率，绝不参与官方判定。来源单独标注以便区分。
                marker_zone = (json.dumps(response.get("retrieved_data"), ensure_ascii=False)
                               + " " + str(response.get("error_details") or ""))
                row["cap_exhausted"] = "reached max steps" in marker_zone.casefold()
                row["cap_exhausted_source"] = "derived_from_response"
            else:
                row["cap_exhausted_source"] = "trial_record"
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


# --------------------------------------------------------------------------
# 恢复运行前置检查：两臂都要完整且与当前设计一致
# --------------------------------------------------------------------------

ARM_CONDITIONS = (("control", "control"), ("fault", None))  # (condition, arm label)


def arm_artifacts_ok(arm_dir: str | Path, *, expect: dict,
                     task_type: str | None = None) -> tuple[bool, list[str]]:
    """检查单臂产物是否**完整且与当前设计一致**。

    ``expect`` 至少包含 model_profile / architecture / fault_type / task_id /
    fault_seed / condition；可选 max_steps / injection_step / fault_intensity 与
    数据集里的 task_type。任何一项不符都返回原因，绝不放行。
    """
    integrity = check_artifacts(arm_dir)
    reasons = list(integrity["reasons"])
    if not integrity["complete"]:
        return False, reasons

    record = integrity["trial_record"] or {}
    for field in ("model_profile", "architecture", "fault_type", "task_id",
                  "fault_seed", "condition"):
        actual = record.get(field)
        wanted = expect.get(field)
        if isinstance(wanted, int) and isinstance(actual, (int, str)):
            try:
                actual = int(actual)
            except (TypeError, ValueError):
                pass
        if actual != wanted:
            reasons.append(f"trial_record {field}={actual!r} != design {wanted!r}")

    run_config = record.get("run_config") or {}
    for field in ("max_steps", "injection_step", "fault_intensity"):
        if field in expect and run_config.get(field) != expect[field]:
            reasons.append(
                f"trial_record run_config.{field}={run_config.get(field)!r} "
                f"!= design {expect[field]!r}")

    if task_type is not None:
        response = integrity.get("agent_response") or {}
        actual_type = str(response.get("task_type") or "").upper()
        if actual_type != str(task_type).upper():
            reasons.append(f"agent_response task_type={actual_type!r} "
                           f"!= dataset {str(task_type).upper()!r}")

    return (not reasons), reasons


def job_arms_ok(job_dir: str | Path, *, fault_type: str, task_id: int, seed: int,
                model_profile: str, architecture: str, max_steps: int,
                injection_step: int | None, fault_intensity: str,
                dataset_path: str | Path | None = None) -> tuple[bool, list[str]]:
    """控制臂与故障臂**都**完整且一致，调用方才能跳过这个格子。

    ``--resume`` 的语义是"这个格子已经按当前设计跑完了"，单臂完整不足以支持这个
    判断：缺了控制臂就没有配对的基线，缺了故障臂就没有处理组。
    """
    from standard_agent.trial_metadata import arm_dir_name

    task_type = None
    if dataset_path is not None:
        try:
            from standard_agent.webarena_verified import (
                TaskDefinitionNotFound, load_task_definition, task_type_for,
            )
            task_type = task_type_for(load_task_definition(int(task_id), path=dataset_path))
        except TaskDefinitionNotFound as exc:
            return False, [f"dataset unavailable, cannot verify design: {exc}"]
        except Exception as exc:  # noqa: BLE001 - 保守处理，宁可重跑
            return False, [f"dataset check failed: {exc}"]

    reasons: list[str] = []
    for condition, label in ARM_CONDITIONS:
        arm_label = fault_type if label is None else label
        arm_dir = Path(job_dir) / str(task_id) / arm_dir_name(
            fault_label=arm_label, seed=seed)
        # 两臂的注入配置**不同**，不能共用一套期望：控制臂不注入任何故障，其记录
        # 必然是 intensity="off"、injection_step=None；只有故障臂才用预注册的强度
        # 与注入步。共用期望会让完整的控制臂永远判为"配置不符"，--resume 永远不跳过。
        if condition == "control":
            arm_intensity, arm_step = "off", None
        else:
            arm_intensity, arm_step = fault_intensity, injection_step
        ok, why = arm_artifacts_ok(
            arm_dir,
            expect={"model_profile": model_profile, "architecture": architecture,
                    "fault_type": fault_type, "task_id": int(task_id),
                    "fault_seed": int(seed), "condition": condition,
                    "max_steps": max_steps, "injection_step": arm_step,
                    "fault_intensity": arm_intensity},
            task_type=task_type,
        )
        if not ok:
            reasons.extend(f"{condition}: {reason}" for reason in why)
    return (not reasons), reasons


# --------------------------------------------------------------------------
# 付费 smoke 的开跑前门槛
# --------------------------------------------------------------------------

def _default_git_probe(repo_root: str | Path) -> dict:
    """读取仓库版本信息；任何失败都返回空值，由调用方判为"无法确认"。"""
    import subprocess

    info = {"head": "", "branch": "", "dirty": None, "error": ""}
    try:
        for key, args in (("head", ["rev-parse", "HEAD"]),
                          ("branch", ["rev-parse", "--abbrev-ref", "HEAD"])):
            result = subprocess.run(["git", "-C", str(repo_root), *args],
                                    capture_output=True, text=True, timeout=15)
            if result.returncode == 0:
                info[key] = result.stdout.strip()
        # 只看**已跟踪**文件的改动：仓库里通常还有本地实验产物等未跟踪内容，
        # 它们不改变"跑的是哪个版本的代码"这个判断，不能当成脏树。
        result = subprocess.run(
            ["git", "-C", str(repo_root), "status", "--porcelain", "--untracked-files=no"],
            capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            info["dirty"] = bool(result.stdout.strip())
    except (OSError, subprocess.SubprocessError) as exc:
        info["error"] = str(exc)
    return info


def smoke_preflight(*, repo_root: str | Path, output_root: str | Path,
                    expect_commit: str = "", run_dir: str | Path | None = None,
                    allow_existing: bool = False, require_clean: bool = True,
                    git_probe=None) -> dict:
    """付费 smoke 开跑前的门槛：代码版本正确 + 目标目录没有既有产物。

    这是"宁可挡住、不可重复付费"的检查：版本读不到、或目录里已有任何产物时一律
    不放行（除非显式 ``allow_existing``）。非空时给出 `next_step` 指向完整性审计。
    """
    probe = (git_probe or _default_git_probe)(repo_root)
    head = str(probe.get("head") or "")
    reasons: list[str] = []

    if not head:
        reasons.append("无法确认当前代码版本（读不到 git HEAD）")
    elif expect_commit and not head.startswith(expect_commit):
        reasons.append(f"当前 HEAD {head[:9]} != 期望 {expect_commit}")
    if require_clean and probe.get("dirty"):
        reasons.append("工作区有未提交改动，正式运行要求干净树")

    roots = {"official_output_root": Path(output_root)}
    if run_dir is not None:
        roots["run_dir"] = Path(run_dir)

    inventory = {}
    total_artifacts = 0
    for name, root in roots.items():
        counts = {"agent_response": 0, "network_har": 0, "trial_record": 0,
                  "status_json": 0, "manifest_json": 0}
        if root.exists():
            counts["agent_response"] = len(discover_trials(root))
            counts["network_har"] = len(list(root.rglob(NETWORK_HAR)))
            counts["trial_record"] = len(list(root.rglob(TRIAL_RECORD)))
            counts["status_json"] = len(list(root.rglob("*.status.json")))
            counts["manifest_json"] = len(list(root.rglob("manifest.json")))
        inventory[name] = {"path": str(root), "exists": root.exists(), **counts}
        total_artifacts += (counts["agent_response"] + counts["trial_record"]
                            + counts["status_json"])

    if total_artifacts and not allow_existing:
        reasons.append(f"目标目录已有 {total_artifacts} 个产物/状态文件，"
                       f"先跑完整性审计再决定，避免覆盖或重复付费")

    allowed = not reasons
    return {
        "repo_root": str(repo_root),
        "head": head, "head_short": head[:9],
        "branch": probe.get("branch") or "",
        "working_tree_dirty": probe.get("dirty"),
        "expect_commit": expect_commit,
        "inventory": inventory,
        "existing_artifacts": total_artifacts,
        "allowed_to_run_paid_trials": allowed,
        "blocked_reasons": reasons,
        "next_step": ("可以开跑 12 条 smoke" if allowed else
                      "先运行: python scripts/stage_c_pipeline.py check "
                      "--root <official_output_root>（非空时）并核对版本/工作区"),
    }
