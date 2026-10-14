"""Stage C 执行链的离线测试：任务定义、注入步口径、留痕、闭环、主分析。

这些测试不联网、不起浏览器、不调用任何模型，也不跑官方 evaluator：需要评测的地方
注入假 evaluator，需要数据的地方构造最小 fixture。覆盖的风险点是：

* 用占位 `eval: []` 生成响应会把导航任务写成检索任务，导致官方评分必然失败；
* 注入步口径散落各处会让 `agent_param_error` 落在错误的动作上；
* 控制臂与故障臂若不能配对，退化量就不可估计；
* 完成率被当成官方成功率；
* V4 Pro 混进 16 任务主估计。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 同目录的 test_provenance.py 会在导入时向 sys.modules 注入 standard_agent 桩模块，
# 使后续模块无法导入真实包（仓库既有问题，这里只做隔离，不改动其他测试）。
# 先清掉桩，再导入真实模块；本模块单独运行与整体运行结果一致。
for _stubbed in [name for name in list(sys.modules)
                 if name == "standard_agent" or name.startswith("standard_agent.")]:
    del sys.modules[_stubbed]

from fault_injection.config import (  # noqa: E402
    FaultConfig,
    injection_step_mode,
    injection_step_units,
    resolve_injection_step,
    stage_c_injection_step,
)
from standard_agent.stage_c_analysis import (  # noqa: E402
    HOLM_FAMILY_ID,
    cap_exhaustion_summary,
    assess_matrix,
    build_observations,
    degradation_by_fault,
    format_report,
    resample_tasks_by_stratum,
    strata_for_tasks,
    check_validation_scope,
    cluster_bootstrap_ci,
    degradation_by_cell,
    holm_adjust,
    interaction_on_degradation,
    main_analysis,
    pair_observations,
    task_floor_ceiling,
)
from standard_agent.stage_c_pipeline import (  # noqa: E402
    EVALUATION_STATUS_COMPATIBILITY,
    smoke_preflight,
    EVALUATION_STATUS_ERROR,
    EVALUATION_STATUS_NATIVE,
    audit,
    check_artifacts,
    classify_evaluation,
    collect_rows,
    expected_cells,
    load_design,
)
from standard_agent.trial_metadata import (  # noqa: E402
    PAIRING_SCHEME,
    build_trial_record,
    discover_trial_records,
    make_trial_id,
    pair_key,
    pair_records,
    read_trial_record,
    trial_file_stem,
    write_trial_record,
)
from standard_agent.evaluation import is_cap_exhausted, is_completed  # noqa: E402
from standard_agent.webarena_verified import (  # noqa: E402
    TaskDefinitionNotFound,
    schema_clamps_retrieved_data,
    load_task_definition,
    make_agent_response,
    write_agent_response,
)

NAVIGATE_TASK = {
    "task_id": 118,
    "intent": "go to the product page for a night guard",
    "sites": ["shopping"],
    "eval": [{
        "evaluator": "AgentResponseEvaluator",
        "results_schema": {"type": "null"},
        "expected": {"task_type": "navigate", "status": "SUCCESS", "retrieved_data": None},
    }],
}
RETRIEVE_TASK = {
    "task_id": 21,
    "intent": "find the reviewers",
    "sites": ["shopping"],
    "eval": [{
        "evaluator": "AgentResponseEvaluator",
        "results_schema": {"type": "array"},
        "expected": {"task_type": "retrieve", "status": "SUCCESS",
                     "retrieved_data": [{"name": "Dibbins"}]},
    }],
}


@pytest.fixture()
def dataset(tmp_path: Path) -> Path:
    path = tmp_path / "webarena-verified.json"
    path.write_text(json.dumps([NAVIGATE_TASK, RETRIEVE_TASK]), encoding="utf-8")
    return path


# ==========================================================================
# 1. 任务定义：导航任务必须带 NAVIGATE 语义
# ==========================================================================

def test_load_task_definition_returns_the_dataset_entry(dataset: Path):
    task = load_task_definition(118, path=dataset)
    assert task["task_id"] == 118
    assert task["eval"][0]["expected"]["task_type"] == "navigate"


def test_missing_task_definition_raises_instead_of_falling_back(dataset: Path):
    with pytest.raises(TaskDefinitionNotFound):
        load_task_definition(999, path=dataset)


def test_missing_dataset_raises(tmp_path: Path):
    with pytest.raises(TaskDefinitionNotFound):
        load_task_definition(118, path=tmp_path / "absent.json")


def test_navigate_task_produces_navigate_response(dataset: Path):
    task = load_task_definition(118, path=dataset)
    response = make_agent_response(task, completed=True, answer="arrived")
    assert response["task_type"] == "NAVIGATE"
    assert response["status"] == "SUCCESS"
    # 导航任务不带 retrieved_data；提交列表会破坏评分
    assert response["retrieved_data"] is None


def test_placeholder_eval_block_would_flip_task_type():
    """记录旧缺陷的失败模式：占位 eval 会把导航任务当成检索任务。"""
    placeholder = make_agent_response({"task_id": 118, "eval": []},
                                      completed=True, answer="arrived")
    real = make_agent_response(NAVIGATE_TASK, completed=True, answer="arrived")
    assert placeholder["task_type"] == "RETRIEVE"
    assert real["task_type"] == "NAVIGATE"
    assert placeholder["task_type"] != real["task_type"]


def test_retrieve_task_keeps_structured_answer(dataset: Path):
    task = load_task_definition(21, path=dataset)
    response = make_agent_response(task, completed=True,
                                   answer=json.dumps([{"name": "Dibbins"}]))
    assert response["task_type"] == "RETRIEVE"
    assert response["retrieved_data"] == [{"name": "Dibbins"}]


def test_write_agent_response_writes_valid_json(tmp_path: Path):
    path = write_agent_response(tmp_path / "t" / "agent_response.json", NAVIGATE_TASK,
                               completed=True, answer="arrived")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["task_type"] == "NAVIGATE"


# ==========================================================================
# 2. 注入步口径
# ==========================================================================

def test_stage_c_step_mapping_and_units():
    assert stage_c_injection_step("agent_param_error") == 1
    assert stage_c_injection_step("web_dom_missing") == 2
    assert stage_c_injection_step("web_http_error") == 2
    assert injection_step_units("agent_param_error") == "parameter_action"
    assert injection_step_units("web_dom_missing") == "execution_step"


def test_explicit_step_is_the_stage_e_sweep():
    assert resolve_injection_step("web_dom_missing", 3) == 3
    assert injection_step_mode(3) == "stage_e_explicit"
    assert injection_step_mode(None) == "stage_c_fixed"
    # 显式值覆盖 Stage C 默认（Stage E 扫描参数动作）
    assert resolve_injection_step("agent_param_error", 2) == 2


def test_unset_step_never_means_probability_mode():
    for fault in ("agent_param_error", "web_dom_missing", "web_http_error"):
        assert resolve_injection_step(fault, None) == stage_c_injection_step(fault)


def test_agent_param_error_fires_on_the_first_parameter_action():
    from fault_injection.agent_faults import inject_param_error
    step = resolve_injection_step("agent_param_error")
    assert step == 1
    config = FaultConfig.single_fault("agent_param_error", intensity="high", seed=7,
                                      injection_step=step)
    first, _ = inject_param_error(config, "click", element_id="1")
    second, _ = inject_param_error(config, "click", element_id="2")
    # 第 1 个参数动作就是被破坏的那个；之后恢复正常，且只注入一次
    assert first.startswith("invalid_")
    assert second == "2"
    assert len(config.log) == 1
    assert config.log[0]["parameter_action_index"] == 1


def test_other_fault_fires_on_the_second_execution_step():
    config = FaultConfig.single_fault("web_dom_missing", intensity="high", seed=7,
                                      injection_step=resolve_injection_step("web_dom_missing"))
    config.set_execution_step(1)
    assert not config.should_inject("web_dom_missing")
    config.set_execution_step(2)
    assert config.should_inject("web_dom_missing")
    config.set_execution_step(3)
    assert not config.should_inject("web_dom_missing")


def test_matrix_command_uses_resolved_steps(monkeypatch):
    from scripts.run_fault_matrix import command_for

    class Args:
        fault_injection_step = None
        model_profile = "deepseek_v41_flash"
        architecture = "react"
        max_steps = 20
        fault_intensity = "high"
        official_output_root = None

    job = {"task_id": 118, "seed": 1, "site": "shopping", "start_url": "http://x",
           "intent": "go", "fault_type": "agent_param_error"}
    command = command_for(Args(), job)
    assert command[command.index("--fault-injection-step") + 1] == "1"

    job["fault_type"] = "web_http_error"
    command = command_for(Args(), job)
    assert command[command.index("--fault-injection-step") + 1] == "2"

    Args.fault_injection_step = 3   # Stage E sweep
    command = command_for(Args(), job)
    assert command[command.index("--fault-injection-step") + 1] == "3"


# ==========================================================================
# 3. 逐 trial 留痕与配对
# ==========================================================================

def _record(**overrides) -> dict:
    payload = dict(model_profile="deepseek_v41_flash", architecture="react",
                   fault_type="web_dom_missing", task_id=118, seed=1, condition="fault",
                   replicate=0, run_config={"max_steps": 20}, paths={"trace": "traces/x.jsonl"})
    payload.update(overrides)
    return build_trial_record(**payload)


def test_control_and_fault_share_pair_key_but_not_trial_id():
    control = _record(condition="control")
    fault = _record(condition="fault")
    assert control["pair_key"] == fault["pair_key"]
    assert control["trial_id"] != fault["trial_id"]


def test_pair_key_changes_when_a_design_factor_changes():
    base = pair_key(model_profile="m", architecture="react", fault_type="f",
                    task_id=1, seed=1, replicate=0)
    for changed in (
        pair_key(model_profile="m2", architecture="react", fault_type="f", task_id=1, seed=1, replicate=0),
        pair_key(model_profile="m", architecture="plan_execute", fault_type="f", task_id=1, seed=1, replicate=0),
        pair_key(model_profile="m", architecture="react", fault_type="f2", task_id=1, seed=1, replicate=0),
        pair_key(model_profile="m", architecture="react", fault_type="f", task_id=2, seed=1, replicate=0),
        pair_key(model_profile="m", architecture="react", fault_type="f", task_id=1, seed=2, replicate=0),
    ):
        assert changed != base


def test_trial_file_stem_is_filesystem_safe_and_deterministic():
    kwargs = dict(model_profile="deepseek_v41_flash", architecture="plan_execute",
                  fault_type="web_dom_missing", task_id=118, seed=1, condition="fault",
                  replicate=0)
    stem = trial_file_stem(**kwargs)
    assert stem == trial_file_stem(**kwargs)
    assert "/" not in stem and "|" not in stem and "=" not in stem
    assert "task118" in stem and "seed1" in stem


def test_trial_record_roundtrip_carries_code_version_and_paths(tmp_path: Path):
    path = write_trial_record(tmp_path / "trial_record.json", _record())
    record = read_trial_record(path)
    assert record["paths"]["trace"] == "traces/x.jsonl"
    assert record["run_config"]["max_steps"] == 20
    assert "git_sha" in record["code"] and "git_dirty" in record["code"]


def test_read_trial_record_rejects_unknown_schema(tmp_path: Path):
    path = tmp_path / "trial_record.json"
    path.write_text(json.dumps({"schema_version": 99}), encoding="utf-8")
    with pytest.raises(ValueError):
        read_trial_record(path)


def test_pair_records_groups_arms_and_rejects_duplicates():
    records = [_record(condition="control"), _record(condition="fault")]
    grouped = pair_records(records)
    assert len(grouped) == 1
    assert set(next(iter(grouped.values()))) == {"control", "fault"}
    with pytest.raises(ValueError):
        pair_records(records + [_record(condition="control")])


def test_discover_trial_records_reads_nested_tree(tmp_path: Path):
    write_trial_record(tmp_path / "a" / "trial_record.json", _record())
    write_trial_record(tmp_path / "b" / "trial_record.json", _record(seed=2, condition="control"))
    found = discover_trial_records(tmp_path)
    assert len(found) == 2


# ==========================================================================
# 4. 闭环：完整性 → evaluator → 审计
# ==========================================================================

def _trial_dir(root: Path, *, model="deepseek_v41_flash", architecture="react",
               fault="web_dom_missing", task=118, seed=1, condition="fault",
               with_har=True, with_record=True, response=None) -> Path:
    trial_dir = root / model / architecture / f"{fault}_task{task}_seed{seed}" / \
        f"{'control' if condition == 'control' else fault}_seed_{seed}" / str(task)
    trial_dir.mkdir(parents=True, exist_ok=True)
    payload = response or {"task_type": "NAVIGATE", "status": "SUCCESS",
                           "retrieved_data": None, "error_details": None}
    (trial_dir / "agent_response.json").write_text(json.dumps(payload), encoding="utf-8")
    if with_har:
        (trial_dir / "network.har").write_text('{"log": {"entries": []}}', encoding="utf-8")
    if with_record:
        record = build_trial_record(
            model_profile=model, architecture=architecture, fault_type=fault,
            task_id=task, seed=seed, condition=condition, replicate=0,
            run_config={"max_steps": 20}, paths={})
        write_trial_record(trial_dir / "trial_record.json", record)
    return trial_dir


def test_missing_har_makes_a_cell_incomplete(tmp_path: Path):
    trial_dir = _trial_dir(tmp_path, with_har=False)
    integrity = check_artifacts(trial_dir)
    assert integrity["complete"] is False
    assert any("network.har" in reason for reason in integrity["reasons"])


def test_invalid_agent_response_is_rejected(tmp_path: Path):
    trial_dir = _trial_dir(tmp_path, response={"status": "SUCCESS"})
    integrity = check_artifacts(trial_dir)
    assert integrity["complete"] is False
    assert integrity["artifacts"]["agent_response"]["valid"] is False


def test_classify_evaluation_separates_native_compat_and_error():
    native = {"status": "success", "official_success": True, "error_msg": None,
              "evaluators_results": [{"evaluator_name": "AgentResponseEvaluator"}]}
    compat = {"status": "success", "official_success": True, "error_msg": None,
              "evaluators_results": [{"evaluator_name": "AgentResponseEvaluatorCompat"}]}
    error = {"status": "ERROR", "official_success": False, "error_msg": "boom",
             "evaluators_results": []}
    assert classify_evaluation(native)["evaluation_status"] == EVALUATION_STATUS_NATIVE
    assert classify_evaluation(compat)["evaluation_status"] == EVALUATION_STATUS_COMPATIBILITY
    assert classify_evaluation(error)["evaluation_status"] == EVALUATION_STATUS_ERROR
    # evaluator error 不算 agent 失败
    assert classify_evaluation(error)["official_success"] is None


def test_audit_keeps_official_and_completion_rates_apart(tmp_path: Path):
    design = {
        "main_models": ["m1"], "validation_model": "pro",
        "architectures": ["react"], "faults": ["web_dom_missing"],
        "repetitions": 1, "conditions": ["control", "fault"],
        "main_tasks": [118], "validation_tasks": [118], "category_of": {118: "public_navigation"},
    }
    _trial_dir(tmp_path, model="m1", condition="control", seed=1)
    _trial_dir(tmp_path, model="m1", condition="fault", seed=1)

    def fake_evaluate(task_id, *, agent_response_path, network_trace_path, config_path):
        # 控制臂判成功、故障臂判失败
        success = "control" in str(agent_response_path)
        return {"status": "success" if success else "failure", "official_success": success,
                "evaluators_results": [{"evaluator_name": "AgentResponseEvaluator"}]}

    rows = collect_rows(tmp_path, evaluate_fn=fake_evaluate, evaluate=True)
    report = audit(rows, expected_cells(design, model_profile="m1", architecture="react"))
    assert report["evaluated_cells"] == 2
    assert report["official_success_rate"] == pytest.approx(0.5)
    # 两个臂都自报 SUCCESS（完成后答），所以完成率是 1.0，与官方成功率不同
    assert report["submitted_completion_rate"] == pytest.approx(1.0)
    assert report["missing_cells"] == []
    assert report["evaluation_status_counts"][EVALUATION_STATUS_NATIVE] == 2


def test_audit_reports_missing_and_error_cells(tmp_path: Path):
    design = {
        "main_models": ["m1"], "validation_model": "pro",
        "architectures": ["react"], "faults": ["web_dom_missing"],
        "repetitions": 1, "conditions": ["control", "fault"],
        "main_tasks": [118], "validation_tasks": [118], "category_of": {118: "public_navigation"},
    }
    _trial_dir(tmp_path, model="m1", condition="control", seed=1)

    def fake_error(task_id, *, agent_response_path, network_trace_path, config_path):
        return {"status": "ERROR", "official_success": False, "error_msg": "evaluator crashed",
                "evaluators_results": []}

    rows = collect_rows(tmp_path, evaluate_fn=fake_error, evaluate=True)
    report = audit(rows, expected_cells(design, model_profile="m1", architecture="react"))
    assert len(report["missing_cells"]) == 1          # 缺 fault 臂
    assert len(report["error_cells"]) == 1            # 控制臂评分失败
    assert report["official_success_rate"] is None    # 没有可计入的格子
    assert report["evaluation_status_counts"][EVALUATION_STATUS_ERROR] == 1


def test_audit_detects_unpaired_arms(tmp_path: Path):
    design = {
        "main_models": ["m1"], "validation_model": "pro",
        "architectures": ["react"], "faults": ["web_dom_missing"],
        "repetitions": 1, "conditions": ["control", "fault"],
        "main_tasks": [118], "validation_tasks": [118], "category_of": {118: "public_navigation"},
    }
    _trial_dir(tmp_path, model="m1", condition="control", seed=1)

    def fake_evaluate(task_id, *, agent_response_path, network_trace_path, config_path):
        return {"status": "success", "official_success": True,
                "evaluators_results": [{"evaluator_name": "AgentResponseEvaluator"}]}

    rows = collect_rows(tmp_path, evaluate_fn=fake_evaluate, evaluate=True)
    report = audit(rows, expected_cells(design, model_profile="m1", architecture="react"))
    assert len(report["unpaired_keys"]) == 1


# ==========================================================================
# 5. 主分析
# ==========================================================================

def _analysis_rows(*, models=("m1", "m2"), architectures=("react", "plan_execute"),
                   faults=("web_dom_missing",), tasks=range(1, 17), seeds=(1, 2),
                   effect=None) -> list[dict]:
    """构造分析用行；``effect(model, architecture, fault)`` 给出故障臂的失败概率。"""
    import random
    rng = random.Random(20260926)
    effect = effect or (lambda model, architecture, fault: 0.0)
    rows = []
    for task in tasks:
        for fault in faults:
            for seed in seeds:
                for model in models:
                    for architecture in architectures:
                        base = 0.95 - 0.02 * (int(task) % 3)
                        drop = effect(model, architecture, fault)
                        for condition in ("control", "fault"):
                            probability = base - (drop if condition == "fault" else 0.0)
                            success = 1 if rng.random() < probability else 0
                            rows.append({
                                "official_success": bool(success),
                                "evaluation_status": "native",
                                "cell": {"model_profile": model, "architecture": architecture,
                                         "fault_type": fault, "task_id": int(task),
                                         "fault_seed": seed, "condition": condition},
                                "trial_dir": f"/x/{model}/{architecture}/{fault}/task{task}/{condition}_{seed}",
                            })
    return rows


def test_pairs_are_built_per_task_and_seed():
    rows = _analysis_rows(tasks=[1, 2])
    observations = build_observations(rows)
    pairs, unpaired = pair_observations(observations)
    assert unpaired == []
    assert len(pairs) == 2 * 2 * 2 * 2  # tasks × seeds × models × architectures


def test_bootstrap_interval_is_deterministic_for_a_fixed_seed():
    by_task = {1: [1.0, 0.0], 2: [1.0, 1.0], 3: [0.0, 1.0]}
    first = cluster_bootstrap_ci(by_task, n_boot=500, seed=7)
    second = cluster_bootstrap_ci(by_task, n_boot=500, seed=7)
    assert first == second
    assert first[0] <= first[1]


def test_degradation_recovers_a_planted_effect():
    rows = _analysis_rows(effect=lambda model, architecture, fault: 0.4)
    pairs, _ = pair_observations(build_observations(rows))
    cells = degradation_by_cell(pairs, n_boot=500, seed=1)
    assert len(cells) == 2 * 2  # models × architectures
    for cell in cells:
        assert cell["degradation"] > 0.1
        assert cell["ci_low"] > 0.0


def test_zero_effect_yields_no_positive_lower_bound():
    rows = _analysis_rows(effect=lambda model, architecture, fault: 0.0)
    pairs, _ = pair_observations(build_observations(rows))
    cells = degradation_by_cell(pairs, n_boot=500, seed=1)
    assert all(cell["ci_low"] <= 0.05 for cell in cells)


def test_interaction_detects_a_known_three_way_pattern():
    def effect(model, architecture, fault):
        return 0.7 if (model == "m2" and architecture == "plan_execute") else 0.0

    rows = _analysis_rows(effect=effect, seeds=(1, 2, 3, 4))
    result = interaction_on_degradation(build_observations(rows), models=["m1", "m2"],
                                        n_boot=400, seed=3)
    assert "error" not in result
    assert result["coefficient_condition_x_model_x_architecture"] > 0
    assert result["p_value"] is not None and result["p_value"] < 0.05
    assert result["bootstrap_ci_low"] > 0


def test_interaction_refuses_a_single_model_design():
    rows = _analysis_rows(models=("m1",))
    result = interaction_on_degradation(build_observations(rows))
    assert "error" in result


def test_holm_adjust_is_monotonic_and_bounded():
    adjusted = holm_adjust([0.01, 0.04, 0.03])
    assert adjusted == sorted(adjusted)
    assert all(0.0 <= value <= 1.0 for value in adjusted)
    assert adjusted[0] == pytest.approx(0.03)


def test_validation_scope_flags_v4_pro_outside_common_tasks():
    design = {"validation_model": "deepseek_v4_pro", "validation_tasks": [22, 25]}
    observations = [
        {"model_profile": "deepseek_v4_pro", "task_id": 22, "condition": "control",
         "architecture": "react", "fault_type": "web_dom_missing", "seed": 1,
         "official_success": 1, "evaluation_status": "native"},
        {"model_profile": "deepseek_v4_pro", "task_id": 118, "condition": "fault",
         "architecture": "react", "fault_type": "web_dom_missing", "seed": 1,
         "official_success": 0, "evaluation_status": "native"},
    ]
    scope = check_validation_scope(observations, design)
    assert scope["ok"] is False
    assert scope["violations"] == [118]


def test_main_analysis_excludes_validation_model_from_main_estimate():
    design = {
        "main_models": ["m1", "m2"], "validation_model": "pro",
        "validation_tasks": [1, 2], "main_tasks": list(range(1, 17)),
        "faults": ["web_dom_missing"], "architectures": ["react", "plan_execute"],
        "conditions": ["control", "fault"], "repetitions": 2, "category_of": {},
    }
    rows = _analysis_rows(effect=lambda model, architecture, fault: 0.3, seeds=(1, 2))
    pro_rows = _analysis_rows(models=("pro",), tasks=[1, 2], seeds=(1, 2),
                              effect=lambda model, architecture, fault: 0.2)
    report = main_analysis(rows + pro_rows, design, n_boot=300, seed=5)
    main_models = {item["model_profile"] for item in report["degradation_by_cell"]}
    assert main_models == {"m1", "m2"}
    assert report["secondary_validation_model"]["model_profile"] == "pro"
    assert all(item["model_profile"] == "pro"
               for item in report["secondary_validation_model"]["degradation_by_cell"])
    assert report["scope"]["ok"] is True


def test_design_manifest_has_the_frozen_sizes():
    design = load_design(ROOT / "docs" / "task_manifest_public16.json")
    assert len(design["main_tasks"]) == 16
    assert len(design["validation_tasks"]) == 8
    assert design["repetitions"] == 2
    assert set(design["faults"]) == {"web_http_error", "agent_param_error", "web_dom_missing"}
    cells = expected_cells(design, model_profile=design["main_models"][0],
                           architecture="react")
    # 16 任务 × 3 故障 × 2 重复 × 2 条件
    assert len(cells) == 192
    validation_cells = expected_cells(design, model_profile=design["validation_model"],
                                      architecture="react")
    assert len(validation_cells) == 8 * 3 * 2 * 2


# ==========================================================================
# 6. 评审驱动的加固：命名方案、恢复守卫、四类分母、分层聚类
# ==========================================================================

def test_trial_record_marks_the_new_pairing_scheme():
    record = _record()
    assert record["pairing_scheme"] == PAIRING_SCHEME
    assert PAIRING_SCHEME == "seed-paired-v2"


def test_legacy_record_is_identifiable_but_rejected_by_the_pipeline(tmp_path: Path):
    """旧方案记录必须能被读出并指名到格子，然后被判 incomplete，而不是被静默当成

    "没有产物"或"可以配对"。读取层保持宽容，判定层严格。
    """
    trial_dir = _trial_dir(tmp_path, model="m1", condition="control", seed=1)
    record_path = trial_dir / "trial_record.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["pairing_scheme"] = "legacy-control-seed0"
    record_path.write_text(json.dumps(record), encoding="utf-8")

    read_back = read_trial_record(record_path)          # 读得出（可识别）
    assert read_back["task_id"] == 118

    integrity = check_artifacts(trial_dir)               # 但不可用于正式分析
    assert integrity["complete"] is False
    assert any("legacy pairing scheme" in reason for reason in integrity["reasons"])

    design = {
        "main_models": ["m1"], "validation_model": "pro",
        "architectures": ["react"], "faults": ["web_dom_missing"],
        "repetitions": 1, "conditions": ["control", "fault"],
        "main_tasks": [118], "validation_tasks": [118], "category_of": {118: "public_navigation"},
    }
    report = audit(collect_rows(tmp_path), expected_cells(design, model_profile="m1",
                                                          architecture="react"))
    assert len(report["incomplete_cells"]) == 1          # 控制臂：指名到格子，理由明确
    assert len(report["missing_cells"]) == 1             # 故障臂：确实没有产物
    assert report["unidentified_artifact_dirs"] == []    # 没有"读不出来"的目录
    assert report["cell_counts"]["consistent"] is True


def test_arm_dir_name_documents_the_new_scheme():
    from standard_agent.trial_metadata import arm_dir_name
    # 新方案：控制臂与故障臂都用 job seed
    assert arm_dir_name(fault_label="control", seed=3) == "control_seed_3"
    # 历史方案是 control_seed_0（FaultConfig.off() 的固定 seed=0），二者不可混用
    assert arm_dir_name(fault_label="control", seed=3) != "control_seed_0"


def _matrix_args(**overrides):
    class Args:
        output_dir = ""
        workers = 1
        max_steps = 20
        trials = 2
        fault_type = "web_dom_missing"
        fault_types = None
        fault_intensity = "high"
        fault_seed = 1
        fault_injection_step = None
        model_profile = "deepseek_v41_flash"
        architecture = "react"
        task_ids = None
        resume = False
        job_timeout_minutes = 45
        official_output_root = None
    for key, value in overrides.items():
        setattr(Args, key, value)
    return Args()


def test_design_fingerprint_tracks_design_changes():
    from scripts.run_fault_matrix import design_fingerprint

    base = design_fingerprint(_matrix_args(), ["web_dom_missing"], {"web_dom_missing": 2}, [22, 118])
    same = design_fingerprint(_matrix_args(), ["web_dom_missing"], {"web_dom_missing": 2}, [118, 22])
    changed_step = design_fingerprint(_matrix_args(), ["web_dom_missing"], {"web_dom_missing": 3}, [22, 118])
    changed_model = design_fingerprint(_matrix_args(model_profile="qwen38_flash"),
                                       ["web_dom_missing"], {"web_dom_missing": 2}, [22, 118])
    changed_arch = design_fingerprint(_matrix_args(architecture="plan_execute"),
                                      ["web_dom_missing"], {"web_dom_missing": 2}, [22, 118])
    assert base == same                      # 任务顺序无关
    assert len({base, changed_step, changed_model, changed_arch}) == 4


def test_resume_ignores_status_files_from_a_different_design():
    """旧产物 control_seed_0 时代的 status 没有指纹，不能当成已完成而跳过。"""
    from scripts.run_fault_matrix import design_fingerprint

    args = _matrix_args()
    fingerprint = design_fingerprint(args, ["web_dom_missing"], {"web_dom_missing": 2}, [118])
    legacy_status = {"job": {"task_id": 118, "seed": 1, "fault_type": "web_dom_missing"},
                     "returncode": 0, "infrastructure_error": False, "fault_invalid": False}
    new_status = dict(legacy_status, design_fingerprint=fingerprint)
    assert legacy_status.get("design_fingerprint") != fingerprint   # 旧文件被拒
    assert new_status.get("design_fingerprint") == fingerprint      # 本次文件被接受


def test_audit_reports_four_distinct_denominators(tmp_path: Path):
    """missing / incomplete / unevaluated / error 必须分别计数且总和等于期望格子数。"""
    design = {
        "main_models": ["m1"], "validation_model": "pro",
        "architectures": ["react"], "faults": ["web_dom_missing"],
        "repetitions": 1, "conditions": ["control", "fault"],
        "main_tasks": [118], "validation_tasks": [118], "category_of": {118: "public_navigation"},
    }
    expected = expected_cells(design, model_profile="m1", architecture="react")
    assert len(expected) == 2

    # 控制臂完整、不评分（check-only）；故障臂缺 HAR → incomplete；另造一个既不完整也行
    _trial_dir(tmp_path, model="m1", condition="control", seed=1)
    _trial_dir(tmp_path, model="m1", condition="fault", seed=1, with_har=False)
    rows = collect_rows(tmp_path)          # evaluate=False → unevaluated
    report = audit(rows, expected)
    assert len(report["unevaluated_cells"]) == 1
    assert len(report["incomplete_cells"]) == 1
    assert report["cell_counts"]["expected"] == 2
    assert report["cell_counts"]["consistent"] is True
    assert report["official_success_rate"] is None


def test_stratified_resampling_keeps_category_sizes():
    import random as _random
    strata = {"cat_a": [1, 2, 3], "cat_b": [10, 11]}
    sampled = resample_tasks_by_stratum(strata, _random.Random(0))
    assert len(sampled) == 5
    assert sum(task in (1, 2, 3) for task in sampled) == 3
    assert sum(task in (10, 11) for task in sampled) == 2


def test_strata_for_tasks_buckets_by_category():
    strata = strata_for_tasks({1: "cat_a", 2: "cat_a", 3: "cat_b"}, [1, 2, 3, 4])
    assert strata["cat_a"] == [1, 2]
    assert strata["cat_b"] == [3]
    assert strata["uncategorized"] == [4]


def test_single_task_yields_no_false_precision():
    """只有一个任务时，重复 trial 不能把区间收窄——trial 不是独立样本。"""
    wide = {1: [1.0, -1.0, 1.0, -1.0]}
    low, high = cluster_bootstrap_ci(wide, n_boot=200, seed=0)
    assert low == high           # 任务间无变异 → 区间退化，不因 4 个 trial 而变窄


def test_stratified_bootstrap_differs_from_pooled_when_categories_differ():
    values = {1: [1.0], 2: [1.0], 3: [0.0], 4: [0.0]}
    strata = {"large": [1, 2], "small": [3]}          # 有意不平衡的分层
    pooled = cluster_bootstrap_ci(values, n_boot=300, seed=11)
    stratified = cluster_bootstrap_ci(values, strata=strata, n_boot=300, seed=11)
    assert pooled[0] <= stratified[1]
    assert stratified[0] <= pooled[1]


def test_main_analysis_uses_category_strata():
    design = {
        "main_models": ["m1", "m2"], "validation_model": "pro",
        "validation_tasks": [1, 2], "main_tasks": list(range(1, 17)),
        "faults": ["web_dom_missing"], "architectures": ["react", "plan_execute"],
        "conditions": ["control", "fault"], "repetitions": 2,
        "category_of": {**{task: "cat_a" for task in range(1, 9)},
                        **{task: "cat_b" for task in range(9, 17)}},
    }
    rows = _analysis_rows(effect=lambda model, architecture, fault: 0.3, seeds=(1, 2))
    report = main_analysis(rows, design, n_boot=200, seed=9)
    assert report["degradation_by_cell"]
    assert report["interaction"]["bootstrap_resamples"] == 200


# ==========================================================================
# 7. 评审第二轮：resume 两臂校验、trace 独立、正式推断前置检查
# ==========================================================================

def _write_job(tmp_path: Path, *, fault="web_dom_missing", task=118, seed=1,
               model="m1", architecture="react", max_steps=20,
               injection_step=2, fault_intensity="high", task_type="NAVIGATE",
               scheme=PAIRING_SCHEME, omit_har_in_fault=False,
               record_max_steps=None, record_injection_step=None,
               record_fault_intensity=None):
    """构造一个 job 目录：<job>/<task>/<label>_seed_<seed>/ 下两臂齐备。"""
    from standard_agent.trial_metadata import arm_dir_name

    job_dir = tmp_path / f"{fault}_task{task}_seed{seed}"
    for condition, label in (("control", "control"), ("fault", fault)):
        arm = job_dir / str(task) / arm_dir_name(fault_label=label, seed=seed)
        arm.mkdir(parents=True, exist_ok=True)
        (arm / "agent_response.json").write_text(json.dumps(
            {"task_type": task_type, "status": "SUCCESS", "retrieved_data": None,
             "error_details": None}), encoding="utf-8")
        if not (omit_har_in_fault and condition == "fault"):
            (arm / "network.har").write_text('{"log": {"entries": []}}', encoding="utf-8")
        record = build_trial_record(
            model_profile=model, architecture=architecture, fault_type=fault,
            task_id=task, seed=seed, condition=condition, replicate=0,
            # 与 main.py 一致：控制臂是 off / injection_step=None，故障臂才用
            # 预注册的强度与注入步。
            run_config={
                "max_steps": max_steps if record_max_steps is None else record_max_steps,
                "injection_step": (
                    record_injection_step if record_injection_step is not None
                    else (None if condition == "control" else injection_step)),
                "fault_intensity": (
                    record_fault_intensity if record_fault_intensity is not None
                    else ("off" if condition == "control" else fault_intensity))},
            paths={})
        if scheme != PAIRING_SCHEME:
            record["pairing_scheme"] = scheme
        write_trial_record(arm / "trial_record.json", record)
    return job_dir


def _job_ok(tmp_path, dataset, **kwargs):
    from standard_agent.stage_c_pipeline import job_arms_ok
    job_dir = _write_job(tmp_path, **kwargs)
    return job_arms_ok(job_dir, fault_type=kwargs.get("fault", "web_dom_missing"),
                       task_id=kwargs.get("task", 118), seed=kwargs.get("seed", 1),
                       model_profile=kwargs.get("model", "m1"),
                       architecture=kwargs.get("architecture", "react"),
                       max_steps=kwargs.get("max_steps", 20),
                       injection_step=kwargs.get("injection_step", 2),
                       fault_intensity=kwargs.get("fault_intensity", "high"),
                       dataset_path=dataset)


def test_resume_accepts_a_fully_complete_pair(tmp_path: Path, dataset: Path):
    ok, why = _job_ok(tmp_path, dataset)
    assert ok is True and why == []


def test_resume_rejects_when_one_arm_lacks_a_har(tmp_path: Path, dataset: Path):
    ok, why = _job_ok(tmp_path, dataset, omit_har_in_fault=True)
    assert ok is False
    assert any("network.har" in reason for reason in why)


def test_resume_rejects_design_mismatch_in_run_config(tmp_path: Path, dataset: Path):
    # 记录里写 99，当前设计是 20 → 不一致，必须重跑
    ok, why = _job_ok(tmp_path, dataset, record_max_steps=99)
    assert ok is False
    assert any("run_config.max_steps" in reason for reason in why)


def test_resume_rejects_an_injection_step_mismatch(tmp_path: Path, dataset: Path):
    # 记录里写 3（Stage E 的点），当前 Stage C 口径是 2 → 必须重跑
    ok, why = _job_ok(tmp_path, dataset, record_injection_step=3)
    assert ok is False
    assert any("run_config.injection_step" in reason for reason in why)


def test_resume_rejects_legacy_pairing_scheme(tmp_path: Path, dataset: Path):
    ok, why = _job_ok(tmp_path, dataset, scheme="legacy-control-seed0")
    assert ok is False
    assert any("legacy pairing scheme" in reason for reason in why)


def test_resume_rejects_when_task_type_differs_from_dataset(tmp_path: Path, dataset: Path):
    # 数据集里 task 118 是 navigate，响应却写成 RETRIEVE → 设计不一致，必须重跑
    ok, why = _job_ok(tmp_path, dataset, task_type="RETRIEVE")
    assert ok is False
    assert any("task_type" in reason for reason in why)


def test_resume_is_conservative_without_the_dataset(tmp_path: Path):
    ok, why = _job_ok(tmp_path, Path("/nonexistent/dataset.json"))
    assert ok is False
    assert any("dataset" in reason for reason in why)


def test_rerun_uses_an_independent_trace_directory(tmp_path: Path, monkeypatch):
    from standard_agent.core.trace import append_trace, get_trace_path, prepare_trace

    monkeypatch.setenv("TRACE_DIR", str(tmp_path))
    monkeypatch.setenv("TRACE_RUN_ID", "run-A")
    prepare_trace("cell")
    append_trace("cell", {"step": 1, "run": "A"})
    first = get_trace_path("cell")
    monkeypatch.setenv("TRACE_RUN_ID", "run-B")
    prepare_trace("cell")
    append_trace("cell", {"step": 1, "run": "B"})
    second = get_trace_path("cell")

    assert first != second
    assert first.exists() and second.exists()
    assert [json.loads(line)["run"] for line in first.read_text().splitlines()] == ["A"]
    assert [json.loads(line)["run"] for line in second.read_text().splitlines()] == ["B"]


def test_prepare_trace_rotates_instead_of_appending(tmp_path: Path, monkeypatch):
    from standard_agent.core.trace import append_trace, get_trace_path, prepare_trace

    monkeypatch.setenv("TRACE_DIR", str(tmp_path))
    monkeypatch.setenv("TRACE_RUN_ID", "same-run")
    prepare_trace("cell")
    append_trace("cell", {"run": "old"})
    path = get_trace_path("cell")
    prepare_trace("cell")                      # 同路径重跑
    append_trace("cell", {"run": "new"})

    assert [json.loads(line)["run"] for line in path.read_text().splitlines()] == ["new"]
    rotated = list(tmp_path.rglob("*.prev"))
    assert len(rotated) == 1
    assert [json.loads(line)["run"] for line in rotated[0].read_text().splitlines()] == ["old"]


def _row(model, architecture, fault, task, seed, condition, success,
         *, complete=True, status="native"):
    return {
        "official_success": bool(success) if success is not None else None,
        "evaluation_status": status,
        "complete": complete,
        "cell": {"model_profile": model, "architecture": architecture,
                 "fault_type": fault, "task_id": task, "fault_seed": seed,
                 "condition": condition},
        "trial_dir": f"/x/{model}/{architecture}/{fault}/{task}/{condition}_{seed}",
    }


def _complete_rows(tasks=(1, 2, 3, 4), faults=("f1", "f2", "f3"),
                   models=("m1", "m2"), architectures=("react", "plan_execute"),
                   seeds=(1,)):
    import random
    rng = random.Random(20260926)
    rows = []
    for fault in faults:
        for task in tasks:
            for seed in seeds:
                for model in models:
                    for architecture in architectures:
                        control = 1 if rng.random() < 0.9 else 0
                        fault_arm = 1 if rng.random() < 0.5 else 0
                        rows.append(_row(model, architecture, fault, task, seed, "control", control))
                        rows.append(_row(model, architecture, fault, task, seed, "fault", fault_arm))
    return rows


def _design(tasks=(1, 2, 3, 4), faults=("f1", "f2", "f3")):
    return {
        "main_models": ["m1", "m2"], "validation_model": "pro",
        "validation_tasks": [1, 2], "main_tasks": list(tasks),
        "faults": list(faults), "architectures": ["react", "plan_execute"],
        "conditions": ["control", "fault"], "repetitions": 1,
        "category_of": {task: ("cat_a" if task <= 2 else "cat_b") for task in tasks},
    }


def test_holm_family_is_the_three_fault_level_contrasts():
    report = main_analysis(_complete_rows(), _design(), n_boot=200, seed=3)
    family = report["holm_family"]
    assert family["family_id"] == HOLM_FAMILY_ID
    assert family["members"] == ["f1", "f2", "f3"]
    assert len(family["adjusted_p_values"]) == 3
    assert report["formal_inference_allowed"] is True
    # 逐格结果只作描述性，不进入家族
    assert all(item["in_holm_family"] is False for item in report["degradation_by_cell"])
    assert all(item["inference_permitted"] for item in report["degradation_by_fault"])


def test_missing_cell_blocks_formal_inference():
    rows = [row for row in _complete_rows() if not (row["cell"]["task_id"] == 4 and
                                                    row["cell"]["condition"] == "fault")]
    report = main_analysis(rows, _design(), n_boot=200, seed=3)
    assert report["formal_inference_allowed"] is False
    assert any("没有产物" in reason for reason in report["matrix_health"]["blocked_reasons"])
    assert all(item["p_value"] is None for item in report["degradation_by_fault"])
    assert all(item["p_value"] is None for item in report["degradation_by_cell"])
    assert "正式显著性推断已被拒绝" in format_report(report)


def test_unpaired_arms_block_formal_inference():
    rows = [row for row in _complete_rows() if row["cell"]["condition"] == "control"]
    report = main_analysis(rows, _design(), n_boot=100, seed=3)
    assert report["formal_inference_allowed"] is False
    assert any("未配平" in reason for reason in report["matrix_health"]["blocked_reasons"])


def test_evaluator_error_blocks_formal_inference():
    rows = _complete_rows()
    rows[0]["evaluation_status"] = "error"
    rows[0]["official_success"] = None
    report = main_analysis(rows, _design(), n_boot=100, seed=3)
    assert report["formal_inference_allowed"] is False
    assert any("评分报错" in reason for reason in report["matrix_health"]["blocked_reasons"])


def test_incomplete_artifacts_block_formal_inference():
    rows = _complete_rows()
    rows[1]["complete"] = False
    report = main_analysis(rows, _design(), n_boot=100, seed=3)
    assert report["formal_inference_allowed"] is False
    assert any("产物不完整" in reason for reason in report["matrix_health"]["blocked_reasons"])


def test_assess_matrix_accounts_for_every_expected_cell():
    health = assess_matrix(_complete_rows(), _design(), models=["m1", "m2"],
                           architectures=["react", "plan_execute"])
    assert health["expected_cells"] == len(_complete_rows())
    assert health["evaluated_cells"] == health["expected_cells"]
    assert health["formal_inference_allowed"] is True


def test_secondary_validation_results_never_report_significance():
    rows = _complete_rows() + [
        _row("pro", "react", fault, task, 1, condition, 1)
        for fault in ("f1", "f2", "f3") for task in (1, 2)
        for condition in ("control", "fault")
    ]
    report = main_analysis(rows, _design(), n_boot=100, seed=3)
    secondary = report["secondary_validation_model"]["degradation_by_cell"]
    assert secondary
    assert all(item["p_value"] is None and item["inference_permitted"] is False
               for item in secondary)


# ==========================================================================
# 8. 第三轮：控制臂期望分离、blocked 家族清空、表格列数
# ==========================================================================

def test_resume_accepts_a_complete_pair_with_off_control_arm(tmp_path: Path, dataset: Path):
    """回归：控制臂记录是 off / injection_step=None，必须能被判为合格并跳过。"""
    ok, why = _job_ok(tmp_path, dataset)
    assert ok is True, why


def test_resume_rejects_a_control_arm_that_claims_injection(tmp_path: Path, dataset: Path):
    """控制臂若写着 intensity=high / injection_step=2，说明它不是控制臂，必须重跑。"""
    ok, why = _job_ok(tmp_path, dataset, record_fault_intensity="high",
                      record_injection_step=2)
    assert ok is False
    assert any("fault_intensity" in reason for reason in why)


def test_resume_rejects_a_fault_arm_recorded_with_control_intensity(tmp_path: Path, dataset: Path):
    """反向：故障臂必须用预注册强度，不能记成 off。"""
    ok, why = _job_ok(tmp_path, dataset, record_fault_intensity="off")
    assert ok is False
    assert any("fault_intensity" in reason for reason in why)


def _collect_key_values(node, key_fragment):
    found = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key_fragment in str(key):
                found.append((key, value))
            found.extend(_collect_key_values(value, key_fragment))
    elif isinstance(node, list):
        for item in node:
            found.extend(_collect_key_values(item, key_fragment))
    return found


def test_blocked_report_keeps_no_p_values_anywhere():
    """被阻断时，产物 JSON 里不允许残留任何 p 值（含 holm_family 的 raw/adjusted）。"""
    rows = [row for row in _complete_rows() if not (row["cell"]["task_id"] == 4 and
                                                    row["cell"]["condition"] == "fault")]
    report = main_analysis(rows, _design(), n_boot=100, seed=3)
    assert report["formal_inference_allowed"] is False

    family = report["holm_family"]
    assert family["raw_p_values"] == []
    assert family["adjusted_p_values"] == []

    leftovers = [(key, value) for key, value in _collect_key_values(report, "p_value")
                 if value not in (None, [])]
    assert leftovers == [], f"blocked report still carries p values: {leftovers}"
    assert all(item["inference_permitted"] is False
               for item in report["degradation_by_cell"])


def test_allowed_report_has_the_family_p_values():
    report = main_analysis(_complete_rows(), _design(), n_boot=100, seed=3)
    assert report["formal_inference_allowed"] is True
    assert len(report["holm_family"]["raw_p_values"]) == 3
    assert len(report["holm_family"]["adjusted_p_values"]) == 3


def _markdown_tables(text: str):
    tables, current = [], []
    for line in text.splitlines():
        if line.startswith("|"):
            current.append(line)
        elif current:
            tables.append(current)
            current = []
    if current:
        tables.append(current)
    return tables


def test_markdown_tables_have_consistent_column_counts():
    report = main_analysis(_complete_rows(), _design(), n_boot=100, seed=3)
    tables = _markdown_tables(format_report(report))
    assert tables, "report should contain at least one table"
    for table in tables:
        widths = {len(line.strip().strip("|").split("|")) for line in table}
        assert len(widths) == 1, f"ragged table: {widths} in\n" + "\n".join(table)


def test_blocked_report_tables_are_still_well_formed():
    rows = [row for row in _complete_rows() if row["cell"]["condition"] == "control"]
    report = main_analysis(rows, _design(), n_boot=50, seed=3)
    tables = _markdown_tables(format_report(report))
    assert tables
    for table in tables:
        widths = {len(line.strip().strip("|").split("|")) for line in table}
        assert len(widths) == 1, f"ragged table: {widths}"


# ==========================================================================
# 9. 付费 smoke 开跑前门槛
# ==========================================================================

def _probe(head="6b241be" + "0" * 33, branch="stagec-exec-chain", dirty=False):
    def probe(repo_root):
        return {"head": head, "branch": branch, "dirty": dirty, "error": ""}
    return probe


def test_preflight_allows_an_empty_target_with_the_expected_commit(tmp_path: Path):
    report = smoke_preflight(repo_root=tmp_path, output_root=tmp_path / "out",
                             run_dir=tmp_path / "run", expect_commit="6b241be",
                             git_probe=_probe())
    assert report["allowed_to_run_paid_trials"] is True
    assert report["blocked_reasons"] == []
    assert report["existing_artifacts"] == 0


def test_preflight_blocks_when_artifacts_already_exist(tmp_path: Path):
    _trial_dir(tmp_path / "out", model="m1", condition="control", seed=1)
    report = smoke_preflight(repo_root=tmp_path, output_root=tmp_path / "out",
                             expect_commit="6b241be", git_probe=_probe())
    assert report["allowed_to_run_paid_trials"] is False
    assert report["existing_artifacts"] >= 1
    assert any("已有" in reason for reason in report["blocked_reasons"])
    assert "check" in report["next_step"]


def test_preflight_blocks_on_a_resume_status_file(tmp_path: Path):
    """即使没有 trial_record，只要 run-dir 里已有 status.json 也不能直接开跑。"""
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)
    (run_dir / "web_dom_missing_task22_seed1.status.json").write_text("{}", encoding="utf-8")
    report = smoke_preflight(repo_root=tmp_path, output_root=tmp_path / "out",
                             run_dir=run_dir, expect_commit="6b241be",
                             git_probe=_probe())
    assert report["allowed_to_run_paid_trials"] is False
    assert report["inventory"]["run_dir"]["status_json"] == 1


def test_preflight_blocks_on_a_commit_mismatch(tmp_path: Path):
    report = smoke_preflight(repo_root=tmp_path, output_root=tmp_path / "out",
                             expect_commit="2a7a9bb", git_probe=_probe())
    assert report["allowed_to_run_paid_trials"] is False
    assert any("!= 期望" in reason for reason in report["blocked_reasons"])


def test_preflight_blocks_when_the_version_cannot_be_read(tmp_path: Path):
    report = smoke_preflight(repo_root=tmp_path, output_root=tmp_path / "out",
                             expect_commit="6b241be",
                             git_probe=lambda repo: {"head": "", "branch": "", "dirty": None})
    assert report["allowed_to_run_paid_trials"] is False
    assert any("无法确认" in reason for reason in report["blocked_reasons"])


def test_preflight_blocks_a_dirty_tree_by_default(tmp_path: Path):
    report = smoke_preflight(repo_root=tmp_path, output_root=tmp_path / "out",
                             expect_commit="6b241be", git_probe=_probe(dirty=True))
    assert report["allowed_to_run_paid_trials"] is False
    assert any("未提交" in reason for reason in report["blocked_reasons"])


def test_preflight_can_explicitly_allow_existing_artifacts(tmp_path: Path):
    _trial_dir(tmp_path / "out", model="m1", condition="control", seed=1)
    report = smoke_preflight(repo_root=tmp_path, output_root=tmp_path / "out",
                             expect_commit="6b241be", allow_existing=True,
                             git_probe=_probe())
    assert report["allowed_to_run_paid_trials"] is True
    assert report["existing_artifacts"] >= 1


# ==========================================================================
# 10. 离线评分复核：诊断不进检索答案、cap 判定单一来源、耗尽率仅描述
# ==========================================================================

RETRIEVE_ARRAY_TASK = {
    "task_id": 21, "intent": "find the reviewers", "sites": ["shopping"],
    "eval": [{"evaluator": "AgentResponseEvaluator",
              "results_schema": {"type": "array"},
              "expected": {"task_type": "retrieve", "status": "SUCCESS",
                           "retrieved_data": ["Dibbins"]}}],
}


def test_incomplete_run_never_reports_a_retrieval_answer():
    """跑满步数时，harness 的诊断不能冒充检索答案。"""
    response = make_agent_response(RETRIEVE_ARRAY_TASK, completed=False,
                                   answer="Reached max steps (20)")
    assert response["status"] == "UNKNOWN_ERROR"
    assert response["retrieved_data"] is None
    assert "reached max steps" in response["error_details"].casefold()


def test_explicit_diagnostic_wins_over_the_answer_text():
    response = make_agent_response(RETRIEVE_ARRAY_TASK, completed=True,
                                   answer="[]", diagnostic="max_steps exhausted after 20 steps")
    assert response["status"] == "UNKNOWN_ERROR"
    assert response["retrieved_data"] is None
    assert response["error_details"] == "max_steps exhausted after 20 steps"


def test_completed_not_found_answer_is_unchanged():
    response = make_agent_response(RETRIEVE_ARRAY_TASK, completed=True,
                                   answer="No reviewer mentions that; nothing found.")
    assert response["status"] == "NOT_FOUND_ERROR"
    assert response["retrieved_data"] is None


def test_completed_retrieval_answer_is_still_parsed():
    response = make_agent_response(RETRIEVE_ARRAY_TASK, completed=True,
                                   answer=json.dumps(["Dibbins"]))
    assert response["status"] == "SUCCESS"
    assert response["retrieved_data"] == ["Dibbins"]


def test_cap_exhaustion_detection_has_one_source():
    assert is_cap_exhausted(True, "Reached max steps (20)") is True
    assert is_completed(True, "Reached max steps (20)") is False
    assert is_completed(True, "Dibbins") is True
    assert is_cap_exhausted(False, "") is False


def test_trial_record_carries_steps_cap_and_llm_calls(tmp_path: Path):
    record = build_trial_record(
        model_profile="m1", architecture="react", fault_type="web_dom_missing",
        task_id=22, seed=1, condition="control", replicate=0, run_config={},
        paths={}, steps=20, cap_exhausted=True, llm_calls=13)
    path = write_trial_record(tmp_path / "trial_record.json", record)
    back = read_trial_record(path)
    assert (back["steps"], back["cap_exhausted"], back["llm_calls"]) == (20, True, 13)


def test_cap_exhaustion_is_descriptive_not_a_success_metric():
    rows = _complete_rows(faults=("f1",), tasks=(1, 2), seeds=(1,))
    rows[0]["cap_exhausted"] = True
    rows[0]["steps"] = 20
    rows[1]["cap_exhausted"] = False
    rows[1]["steps"] = 6
    summary = cap_exhaustion_summary(rows)
    overall = summary["overall"]
    assert overall["present"] == len(rows)
    assert overall["known"] == 2
    assert overall["exhausted"] == 1
    assert overall["unknown"] == len(rows) - 2
    # 分母只含已知状态，未知不稀释比率
    assert overall["rate"] == pytest.approx(0.5)
    assert summary["by_fault"]["f1"]["exhausted"] == 1
    assert summary["mean_steps"] == pytest.approx((20 + 6) / 2)
    # 官方成功率仍只看 evaluator 判定：cap 标记不改变任何行的 official_success
    assert all("cap" not in json.dumps({"s": row["official_success"]}).casefold()
               for row in rows)


def test_unknown_cap_state_is_excluded_from_the_denominator():
    """未知状态既不进分子也不进分母；若全部未知则比率为 None 而不是 0。"""
    rows = _complete_rows(faults=("f1",), tasks=(1,), seeds=(1,))
    for row in rows:
        row["cap_exhausted"] = None
    summary = cap_exhaustion_summary(rows)
    assert summary["overall"]["known"] == 0
    assert summary["overall"]["unknown"] == len(rows)
    assert summary["overall"]["rate"] is None
    assert summary["overall"]["unknown_rate"] == pytest.approx(1.0)

    rows[0]["cap_exhausted"] = True          # 1 已知耗尽 + 其余未知
    summary = cap_exhaustion_summary(rows)
    assert summary["overall"]["known"] == 1
    assert summary["overall"]["rate"] == pytest.approx(1.0)
    assert summary["overall"]["unknown"] == len(rows) - 1


def test_collect_rows_derives_cap_state_with_three_states(tmp_path: Path):
    """采集侧：有标记→True；状态表明自行终止→False；其余保持未知。"""
    from standard_agent.stage_c_pipeline import collect_rows as _collect

    # 三个用例用不同 task id，避免落在同名目录（<task>/）里互相覆盖
    # (a) 响应里带 harness 标记 → True
    _trial_dir(tmp_path, model="m1", condition="fault", seed=1, task=118,
               response={"task_type": "RETRIEVE", "status": "UNKNOWN_ERROR",
                         "retrieved_data": None,
                         "error_details": "Reached max steps (20)"})
    # (b) 状态表明 agent 自行终止 → False
    _trial_dir(tmp_path, model="m1", condition="fault", seed=1, task=21,
               response={"task_type": "RETRIEVE", "status": "SUCCESS",
                         "retrieved_data": [], "error_details": None})
    # (c) UNKNOWN_ERROR 但没有标记（可能来自旧格式/其它中断）→ 未知
    _trial_dir(tmp_path, model="m1", condition="fault", seed=1, task=24,
               response={"task_type": "RETRIEVE", "status": "UNKNOWN_ERROR",
                         "retrieved_data": None, "error_details": None})
    rows = _collect(tmp_path)
    by_task = {str((row["cell"] or {}).get("task_id")): row for row in rows}
    assert by_task["118"]["cap_exhausted"] is True
    assert by_task["118"]["cap_exhausted_source"] == "derived_marker"
    assert by_task["21"]["cap_exhausted"] is False
    assert by_task["21"]["cap_exhausted_source"] == "derived_completed_status"
    assert by_task["24"]["cap_exhausted"] is None
    assert by_task["24"]["cap_exhausted_source"] == "unknown"


# ==========================================================================
# 11. dirty 口径：未跟踪的运行产物不算脏，两处必须同一规则
# ==========================================================================

def _make_git_repo(path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / "code.py").write_text("x = 1\n", encoding="utf-8")
    for args in (["add", "code.py"],
                 ["commit", "-qm", "init"]):
        subprocess.run(["git", "-C", str(path), "-c", "user.name=t",
                        "-c", "user.email=t@example.invalid", *args], check=True)
    return path


def test_untracked_run_output_does_not_mark_the_tree_dirty(tmp_path: Path):
    """回归：运行写在仓库内的未跟踪产物不是"代码脏"。"""
    from standard_agent.trial_metadata import code_version, git_status

    repo = _make_git_repo(tmp_path / "repo")
    (repo / "experiments").mkdir()
    (repo / "experiments" / "trial_record.json").write_text("{}", encoding="utf-8")
    (repo / "traces").mkdir()
    (repo / "traces" / "run.jsonl").write_text("{}\n", encoding="utf-8")

    status = git_status(repo)
    assert status["dirty"] is False
    assert status["head"]
    assert code_version(repo)["git_dirty"] is False


def test_modified_tracked_file_still_marks_the_tree_dirty(tmp_path: Path):
    from standard_agent.trial_metadata import code_version, git_status

    repo = _make_git_repo(tmp_path / "repo")
    (repo / "code.py").write_text("x = 2\n", encoding="utf-8")
    assert git_status(repo)["dirty"] is True
    assert code_version(repo)["git_dirty"] is True


def test_preflight_and_trial_records_share_the_same_dirty_rule(tmp_path: Path):
    """开跑前门槛与 trial 记录必须用同一口径，否则正式矩阵会被误标。"""
    from standard_agent.stage_c_pipeline import _default_git_probe, smoke_preflight
    from standard_agent.trial_metadata import git_status

    repo = _make_git_repo(tmp_path / "repo")
    (repo / "experiments").mkdir()
    (repo / "experiments" / "out.json").write_text("{}", encoding="utf-8")

    probe = _default_git_probe(repo)
    assert probe["dirty"] == git_status(repo)["dirty"] is False
    # 门槛据此放行（输出目录为空、HEAD 自洽）
    report = smoke_preflight(repo_root=repo, output_root=tmp_path / "out",
                             expect_commit=git_status(repo)["head"][:9])
    assert report["allowed_to_run_paid_trials"] is True
    assert report["blocked_reasons"] == []


# ==========================================================================
# 12. 门槛保守拒绝 + provenance 摘要（无敏感内容）
# ==========================================================================

def test_preflight_blocks_when_dirty_cannot_be_determined(tmp_path: Path):
    """回归：无法确认是否干净时必须和"确实脏"一样挡住。"""
    head = "32fd4147" + "0" * 32

    def probe(dirty):
        return lambda repo: {"head": head, "branch": "b", "dirty": dirty}

    unknown = smoke_preflight(repo_root=tmp_path, output_root=tmp_path / "out",
                              expect_commit="32fd4147", git_probe=probe(None))
    assert unknown["allowed_to_run_paid_trials"] is False
    assert any("无法确认" in reason for reason in unknown["blocked_reasons"])

    dirty = smoke_preflight(repo_root=tmp_path, output_root=tmp_path / "out",
                            expect_commit="32fd4147", git_probe=probe(True))
    assert dirty["allowed_to_run_paid_trials"] is False

    clean = smoke_preflight(repo_root=tmp_path, output_root=tmp_path / "out",
                            expect_commit="32fd4147", git_probe=probe(False))
    assert clean["allowed_to_run_paid_trials"] is True


def _synthetic_trace(dir_path: Path, name: str, events: list[dict]) -> Path:
    dir_path.mkdir(parents=True, exist_ok=True)
    path = dir_path / name
    path.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events),
                    encoding="utf-8")
    return path


def test_provenance_summary_aggregates_without_leaking_content(tmp_path: Path):
    from standard_agent.provenance_summary import SCHEMA, summarize_traces

    secret = "SECRET-AX-TREE-AND-PROMPT"
    name = "m1__react__web_dom_missing__task118__seed1__control__rep0.jsonl"
    _synthetic_trace(tmp_path, name, [
        {"event": "llm_provenance", "step": 1, "served_model": "m1-served",
         "served_model_differs": False, "prompt_tokens": 100, "completion_tokens": 10,
         "total_tokens": 110, "prompt_cache_hit_tokens": 40,
         "prompt_cache_miss_tokens": 60, "cache_usage_source": "openai", "latency_s": 1.5},
        {"event": "llm_provenance", "step": 2, "served_model": "m1-served",
         "served_model_differs": True, "prompt_tokens": 200, "completion_tokens": 20,
         "total_tokens": 220, "prompt_cache_hit_tokens": None,
         "prompt_cache_miss_tokens": None, "cache_usage_source": "",
         "latency_s": 280.0},
        # 含敏感内容的非 provenance 事件：不得进入摘要
        {"event": "action", "action": "goto", "observation": secret,
         "url": "http://localhost:7770/secret"},
    ])

    summary = summarize_traces(tmp_path, label="unit")
    assert summary["schema"] == SCHEMA
    assert summary["trials_total"] == 1
    trial = summary["trials"][0]
    assert trial["calls"] == 2
    assert trial["served_models"] == {"m1-served": 2}
    assert trial["served_model_differs"] == 1
    assert trial["tokens"] == {"prompt_tokens": 300, "completion_tokens": 30,
                               "total_tokens": 330, "prompt_cache_hit_tokens": 40,
                               "prompt_cache_miss_tokens": 60}
    assert trial["slow_calls"] == 1
    assert summary["slow_calls"]["count"] == 1
    assert summary["served_model_totals"] == {"m1-served": 2}
    assert summary["by_architecture"]["react"]["calls"] == 2

    blob = json.dumps(summary, ensure_ascii=False)
    assert secret not in blob
    assert "localhost" not in blob and "http://" not in blob
    assert "observation" not in blob


def test_provenance_summary_keeps_unparsable_trial_names(tmp_path: Path):
    from standard_agent.provenance_summary import summarize_traces

    _synthetic_trace(tmp_path, "weird-name.jsonl", [
        {"event": "llm_provenance", "served_model": "x", "prompt_tokens": 5},
    ])
    summary = summarize_traces(tmp_path)
    trial = summary["trials"][0]
    assert trial["trial"] == "weird-name"
    assert trial["model_profile"] is None
    assert trial["calls"] == 1
    assert summary["by_architecture"].get("(unknown)", {}).get("calls") == 1


def test_trial_record_carries_wall_clock_time(tmp_path: Path):
    """T0 排程与成本估算直接读记录，不必回头解析 trace 时间戳。"""
    record = build_trial_record(
        model_profile="m1", architecture="react", fault_type="web_dom_missing",
        task_id=118, seed=1, condition="control", replicate=0, run_config={}, paths={},
        steps=5, cap_exhausted=False, llm_calls=8, time_sec=132.4567)
    back = read_trial_record(write_trial_record(tmp_path / "trial_record.json", record))
    assert back["time_sec"] == pytest.approx(132.457)
    assert back["steps"] == 5 and back["llm_calls"] == 8


def test_matrix_condition_scope_is_passed_through():
    """T0 控制批：condition=control 只跑控制臂，both 保持既有行为。"""
    from scripts.run_fault_matrix import command_for

    class Args:
        fault_injection_step = None
        model_profile = "qwen38_flash"
        architecture = "react"
        max_steps = 20
        fault_intensity = "high"
        official_output_root = None
        condition = "control"

    job = {"task_id": 118, "seed": 1, "site": "shopping", "start_url": "http://x",
           "intent": "go", "fault_type": "web_dom_missing"}
    control = command_for(Args(), job)
    assert control[control.index("--condition") + 1] == "control"

    Args.condition = "both"
    assert "--condition" not in command_for(Args(), job)

    Args.condition = "fault"
    fault = command_for(Args(), job)
    assert fault[fault.index("--condition") + 1] == "fault"


def test_expected_cells_can_be_scoped_to_a_control_batch():
    """T0 控制批：只期望 control 条件、单一故障槽，且任务数×架构数可精确对齐。"""
    design = load_design(ROOT / "docs" / "task_manifest_public16.json")
    stage_c = expected_cells(design, model_profile="deepseek_v41_flash", architecture="react")
    t0 = expected_cells(design, model_profile="deepseek_v41_flash", architecture="react",
                        faults=["web_dom_missing"], conditions=["control"], seeds=[1])
    assert len(stage_c) == 192
    assert len(t0) == 16                     # 16 任务 × 1 槽 × 1 重复 × 1 条件
    assert {cell["condition"] for cell in t0} == {"control"}
    assert {cell["fault_type"] for cell in t0} == {"web_dom_missing"}
    # 验证模型仍然只在共同 8 任务上
    pro = expected_cells(design, model_profile=design["validation_model"],
                         architecture="react", faults=["web_dom_missing"],
                         conditions=["control"], seeds=[1])
    assert len(pro) == 8


def test_matrix_parser_defines_every_forwarded_flag():
    """回归：condition 曾只在 command_for 里透传而解析器没定义，导致整批秒退。"""
    from scripts.run_fault_matrix import build_parser

    parser = build_parser()
    base = ["--output-dir", "x"]
    assert parser.parse_args(base).condition == "both"          # 默认保持既有行为
    for value in ("control", "fault", "both"):
        assert parser.parse_args(base + ["--condition", value]).condition == value
    # 解析出的对象必须能驱动 command_for 把 --condition 真的传给 main.py
    from scripts.run_fault_matrix import command_for
    args = parser.parse_args(base + ["--condition", "control"])
    args.official_output_root = None
    job = {"task_id": 118, "seed": 1, "site": "shopping", "start_url": "http://x",
           "intent": "go", "fault_type": "web_dom_missing"}
    command = command_for(args, job)
    assert command[command.index("--condition") + 1] == "control"


def test_classify_log_does_not_blame_a_control_only_batch():
    """回归：控制批没有故障臂，不能被判 fault_invalid 并触发停止策略。"""
    from scripts.run_fault_matrix import classify_log

    control_log = ("        injection_count=0 fault_seed=1 faults=[] steps=[]\n"
                   "[ReAct][step=1] ACTION goto args={'url': 'http://localhost:7770/'}\n")
    status = classify_log(control_log, expect_fault=False)
    assert status["fault_expected"] is False
    assert status["fault_missing"] is False and status["fault_invalid"] is False
    assert status["fault_triggered"] is False
    assert status["infrastructure_error"] is False

    # 同样的日志在"期望故障臂"的批次里仍然是异常
    fault_status = classify_log(control_log, expect_fault=True)
    assert fault_status["fault_expected"] is True
    assert fault_status["fault_invalid"] is True

    # 故障臂正常注入的日志在两种口径下都算正常
    armed_log = ("        injection_count=0 fault_seed=1 faults=[] steps=[]\n"
                 "        injection_count=1 fault_seed=1 faults=['web_dom_missing'] steps=[2]\n")
    assert classify_log(armed_log, expect_fault=True)["fault_triggered"] is True
    assert classify_log(armed_log, expect_fault=True)["fault_invalid"] is False


def test_expected_cells_can_be_scoped_to_a_task_subset():
    """T0 首个小批只跑 2 个任务时，期望格子必须精确到该子集。"""
    design = load_design(ROOT / "docs" / "task_manifest_public16.json")
    scoped = expected_cells(design, model_profile="deepseek_v41_flash", architecture="react",
                            faults=["web_dom_missing"], conditions=["control"],
                            seeds=[1], tasks=[21, 118])
    assert len(scoped) == 2
    assert {int(cell["task_id"]) for cell in scoped} == {21, 118}
    # 不在主任务集里的任务会被过滤掉，而不是静默扩大期望
    outside = expected_cells(design, model_profile="deepseek_v41_flash", architecture="react",
                             faults=["web_dom_missing"], conditions=["control"],
                             seeds=[1], tasks=[21, 999])
    assert {int(cell["task_id"]) for cell in outside} == {21}


def test_provenance_summary_reports_idle_gaps(tmp_path: Path):
    """调用间隔必须扣掉调用自身耗时，否则浏览器空档会被低估。"""
    from standard_agent.provenance_summary import summarize_traces

    name = "m1__react__web_dom_missing__task118__seed1__control__rep0.jsonl"
    _synthetic_trace(tmp_path, name, [
        {"event": "llm_provenance", "timestamp": 100.0, "latency_s": 1.0,
         "served_model": "m", "prompt_tokens": 10},
        # 上次结束在 100.0，本次开始于 130.0 → 空闲 30s，调用自身 2s
        {"event": "llm_provenance", "timestamp": 132.0, "latency_s": 2.0,
         "served_model": "m", "prompt_tokens": 10},
        # 紧接：空闲 0s
        {"event": "llm_provenance", "timestamp": 140.0, "latency_s": 8.0,
         "served_model": "m", "prompt_tokens": 10},
    ])
    summary = summarize_traces(tmp_path)
    trial = summary["trials"][0]
    gaps = trial["idle_gap_s"]
    assert gaps["count"] == 2
    assert gaps["median"] == pytest.approx(15.0)     # (30 + 0) / 2
    assert gaps["max"] == pytest.approx(30.0)
    assert trial["completion_spacing_s"]["max"] == pytest.approx(32.0)
    assert summary["idle_gap_s_by_trial"][name.replace(".jsonl", "")]["max"] == pytest.approx(30.0)


def test_trial_record_file_stem_matches_the_artifact_name(tmp_path: Path):
    """记录里的 file_stem 必须等于 trace/产物文件名用的 stem，审计才能直接 join。"""
    from standard_agent.trial_metadata import read_trial_record, trial_file_stem

    kwargs = dict(model_profile="m1", architecture="react", fault_type="web_dom_missing",
                  task_id=118, seed=1, condition="control", replicate=0)
    record = build_trial_record(run_config={}, paths={}, **kwargs)
    assert record["file_stem"] == trial_file_stem(**kwargs)
    assert "|" not in record["file_stem"] and "__" in record["file_stem"]
    assert read_trial_record(write_trial_record(tmp_path / "t.json", record))["file_stem"] \
        == record["file_stem"]


# ==========================================================================
# 13. Step B 实战暴露的两处：mailto 误判、控制批的恢复检查
# ==========================================================================

def test_mailto_abort_is_not_an_infrastructure_error():
    """回归：点击 mailto:/tel: 链接导致的 net::ERR_ABORTED 是正常动作失败。

    它曾把整批 deepseek react 判成基础设施错误并触发停止策略，导致 3 个格子没跑。
    """
    from scripts.run_fault_matrix import classify_log

    mailto = ("[ReAct][step=6] OBSERVATION [Error] 点击失败: Page.goto: "
              "net::ERR_ABORTED at mailto:kilian@kilianvalkhof.com\n")
    tel = "[ReAct][step=3] OBSERVATION [Error] Page.goto: net::ERR_ABORTED at tel:+123\n"
    real = "[ReAct][step=4] OBSERVATION [Error] Page.goto: net::ERR_CONNECTION_RESET\n"

    assert classify_log(mailto, expect_fault=False)["infrastructure_error"] is False
    assert classify_log(tel, expect_fault=False)["infrastructure_error"] is False
    assert classify_log(real, expect_fault=False)["infrastructure_error"] is True


def test_resume_artifact_check_respects_the_condition_scope(tmp_path: Path, dataset: Path):
    """控制批只有控制臂：按 control 作用域应判合格，按 both 则不合格。"""
    from standard_agent.stage_c_pipeline import job_arms_ok
    from standard_agent.trial_metadata import arm_dir_name, build_trial_record, write_trial_record

    task, seed, fault = 118, 1, "web_dom_missing"
    job_dir = tmp_path / f"{fault}_task{task}_seed{seed}"
    arm = job_dir / str(task) / arm_dir_name(fault_label="control", seed=seed)
    arm.mkdir(parents=True)
    (arm / "agent_response.json").write_text(json.dumps(
        {"task_type": "NAVIGATE", "status": "SUCCESS", "retrieved_data": None,
         "error_details": None}), encoding="utf-8")
    (arm / "network.har").write_text('{"log": {"entries": []}}', encoding="utf-8")
    write_trial_record(arm / "trial_record.json", build_trial_record(
        model_profile="m1", architecture="react", fault_type=fault, task_id=task,
        seed=seed, condition="control", replicate=0,
        run_config={"max_steps": 20, "injection_step": None, "fault_intensity": "off"},
        paths={}))

    kwargs = dict(fault_type=fault, task_id=task, seed=seed, model_profile="m1",
                  architecture="react", max_steps=20, injection_step=None,
                  fault_intensity="off", dataset_path=dataset)
    assert job_arms_ok(job_dir, condition_scope="control", **kwargs)[0] is True
    both_ok, both_why = job_arms_ok(job_dir, condition_scope="both", **kwargs)
    assert both_ok is False and any("fault:" in reason for reason in both_why)


# ==========================================================================
# 14. schema 感知钳制：null-schema 任务的散文回答不再当检索结果
# ==========================================================================

NULL_SCHEMA_TASK = {
    "task_id": 22, "intent": "reviewers who mention X", "sites": ["shopping"],
    "eval": [{"evaluator": "AgentResponseEvaluator", "results_schema": {"type": "null"},
              "expected": {"task_type": "retrieve", "status": "NOT_FOUND_ERROR",
                           "retrieved_data": None}}],
}


def test_schema_clamp_helper_reads_the_contract():
    assert schema_clamps_retrieved_data(NULL_SCHEMA_TASK) is True
    assert schema_clamps_retrieved_data(RETRIEVE_ARRAY_TASK) is False
    assert schema_clamps_retrieved_data({"eval": []}) is False


def test_prose_answer_on_null_schema_goes_to_error_details():
    """回归：散文曾作为 list 进 retrieved_data，导致上游 schema 报错、只能兼容评分。"""
    prose = "Navigated to Page 1 of the customer reviews (URL: http://localhost:7770/x)"
    response = make_agent_response(NULL_SCHEMA_TASK, completed=True, answer=prose)
    assert response["status"] == "SUCCESS"          # 模型自报完成，判定不变
    assert response["retrieved_data"] is None       # 契约要求 null
    assert response["error_details"] == prose       # 内容仍可查


def test_array_schema_prose_is_unchanged():
    """array 契约下行为不变：散文仍序列化成列表（native 评分路径不变）。"""
    prose = "the reviewers are Dibbins"
    response = make_agent_response(RETRIEVE_ARRAY_TASK, completed=True, answer=prose)
    assert response["retrieved_data"] == [prose]
    assert response["error_details"] is None


def test_array_schema_json_answer_is_unchanged():
    response = make_agent_response(RETRIEVE_ARRAY_TASK, completed=True,
                                   answer=json.dumps(["Dibbins"]))
    assert response["retrieved_data"] == ["Dibbins"]


def test_null_schema_not_found_and_incomplete_paths_are_unchanged():
    not_found = make_agent_response(NULL_SCHEMA_TASK, completed=True,
                                    answer="No reviewer mentions that; nothing found.")
    assert not_found["status"] == "NOT_FOUND_ERROR"
    assert not_found["retrieved_data"] is None
    assert "no reviewer" in not_found["error_details"].casefold()

    incomplete = make_agent_response(NULL_SCHEMA_TASK, completed=False,
                                     answer="Reached max steps (20)")
    assert incomplete["status"] == "UNKNOWN_ERROR"
    assert incomplete["retrieved_data"] is None

    clamped_diagnostic = make_agent_response(
        NULL_SCHEMA_TASK, completed=False, answer="x",
        diagnostic="max_steps exhausted after 20 steps; no answer produced")
    assert clamped_diagnostic["error_details"].startswith("max_steps exhausted")


def test_trial_record_can_flag_the_schema_clamp():
    record = build_trial_record(
        model_profile="m1", architecture="react", fault_type="web_dom_missing",
        task_id=22, seed=1, condition="control", replicate=0,
        run_config={"response_schema_clamped": True}, paths={})
    assert record["run_config"]["response_schema_clamped"] is True


# ==========================================================================
# 15. thinking 开关：默认关闭并透传给端点（避免 reasoning_content 400）
# ==========================================================================

def test_profile_thinking_is_parsed_and_validated(monkeypatch):
    from standard_agent.config import ModelProfile, Settings

    # MODEL_PROFILES 是 import 时求值的类属性，需直接改类属性
    monkeypatch.setattr(Settings, "MODEL_PROFILES", ("probe",), raising=False)
    monkeypatch.setenv("MODEL_PROBE_API_KEY", "k")
    monkeypatch.setenv("MODEL_PROBE_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("MODEL_PROBE_NAME", "deepseek-flash")
    monkeypatch.setenv("MODEL_PROBE_THINKING", "disabled")
    profile = Settings.get_model_profiles()["probe"]
    assert profile.thinking == "disabled" and profile.validate() is True

    monkeypatch.setenv("MODEL_PROBE_THINKING", "sometimes")
    assert Settings.get_model_profiles()["probe"].validate() is False     # 只接受三种取值

    monkeypatch.delenv("MODEL_PROBE_THINKING")
    assert Settings.get_model_profiles()["probe"].thinking == Settings.LLM_THINKING  # 回落到全局


def test_create_llm_forwards_the_thinking_switch(monkeypatch):
    from standard_agent.config import ModelProfile
    from standard_agent.llm import provider

    captured = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            captured.clear()
            captured.update(kwargs)

    monkeypatch.setattr(provider, "ChatOpenAI", FakeChatOpenAI)

    def profile(thinking):
        return ModelProfile(name="p", api_key="k", base_url="https://api.deepseek.com",
                            model="m", temperature=0.2, request_timeout=120,
                            max_retries=1, thinking=thinking)

    monkeypatch.setattr(provider.settings, "get_model_profile", lambda name=None: profile("disabled"))
    provider.create_llm(profile_name="p")
    assert captured["extra_body"] == {"thinking": {"type": "disabled"}}

    monkeypatch.setattr(provider.settings, "get_model_profile", lambda name=None: profile("enabled"))
    provider.create_llm(profile_name="p")
    assert captured["extra_body"] == {"thinking": {"type": "enabled"}}

    monkeypatch.setattr(provider.settings, "get_model_profile", lambda name=None: profile("default"))
    provider.create_llm(profile_name="p")
    assert "extra_body" not in captured


# ==========================================================================
# 16. 故障未注入（agent 无参数动作）：标记并排除出退化估计
# ==========================================================================

def test_pairs_without_injection_are_excluded_from_degradation():
    design = {
        "main_models": ["m1"], "validation_model": "pro",
        "validation_tasks": [1], "main_tasks": [1, 2],
        "faults": ["f1"], "architectures": ["react"],
        "conditions": ["control", "fault"], "repetitions": 1, "category_of": {},
    }
    rows = []
    for task in (1, 2):
        rows.append(_row("m1", "react", "f1", task, 1, "control", 1))
        rows.append(_row("m1", "react", "f1", task, 1, "fault", 0))
    # task 1 的故障臂没有注入（agent 只做了 goto→stop），task 2 正常注入
    rows[1]["injection_count"] = 0
    rows[3]["injection_count"] = 1

    report = main_analysis(rows, design, n_boot=50, seed=1)
    assert report["pairs_fault_not_applied"] == 1
    cell = report["degradation_by_fault"][0]
    assert cell["n_pairs"] == 1          # 只统计真正注入了故障的那一对
    assert cell["n_tasks"] == 1


def test_resumable_status_ignores_missing_injection():
    """回归：故障臂未注入（逐格事实）不应导致每次 resume 都重跑。"""
    from scripts.run_fault_matrix import resumable_status

    assert resumable_status({"returncode": 0, "infrastructure_error": False}) is True
    assert resumable_status({"returncode": 0, "infrastructure_error": False,
                             "fault_invalid": True, "fault_missing": True}) is True
    assert resumable_status({"returncode": 0, "infrastructure_error": True}) is False
    assert resumable_status({"returncode": -15, "infrastructure_error": False}) is False
    assert resumable_status({}) is False


# ==========================================================================
# 17. 架构对等：两个臂必须拿到同一段作答契约
# ==========================================================================

def test_both_architectures_receive_the_same_answer_contract():
    """回归：JSON 作答要求曾只注入 react，PE 执行器没有 → 架构对比被污染。"""
    from standard_agent.core.nodes import (
        _answer_contract_context, _plan_executor_prompt, _react_system_prompt,
    )

    structured = ("How many commits did kilian make? "
                  "Return a list of objects with keys username and post_title.")
    plain = "Go to the product page for a night guard and add it to the cart."

    for task, expect_json in ((structured, True), (plain, False)):
        react = _react_system_prompt(task, "http://x", "AX")
        executor = _plan_executor_prompt(task, {"goal": "g"}, "http://x", "AX")
        assert ("ONLY valid JSON" in react) is expect_json
        assert ("ONLY valid JSON" in executor) is expect_json
        contract = _answer_contract_context(task)
        assert contract in react and contract in executor


# ==========================================================================
# 18. 口径一致性：分母同源、ITT/触发后分列、地板天花板、交互同口径
# ==========================================================================

def _mixed_injection_rows():
    """一个任务注入、一个任务未注入的极简网格。"""
    rows = _analysis_rows(tasks=[1, 2], seeds=(1,), faults=("f1",))
    for row in rows:
        if row["cell"]["task_id"] == 2 and row["cell"]["condition"] == "fault":
            row["injection_count"] = 0     # 未注入：只能进 ITT，不进触发后估计
        else:
            row["injection_count"] = 1
    return rows


def test_cell_rates_and_delta_use_the_same_pairs():
    pairs, _ = pair_observations(build_observations(_mixed_injection_rows()))
    cells = degradation_by_cell(pairs, n_boot=200, seed=1)
    for cell in cells:
        assert cell["n_pairs"] == 1                      # 只有注入的那一对进入触发后估计
        assert cell["n_pairs_itt"] == 2                  # ITT 保留两对
        assert cell["control_success_rate_itt"] is not None
        # 触发后行内 control/fault 的成功数之和必须等于 n_pairs
        combined = (cell["control_success_rate"] + cell["fault_success_rate"]) * cell["n_pairs"]
        assert abs(combined - round(combined)) < 1e-9


def test_fault_level_reports_itt_and_triggered_separately():
    pairs, _ = pair_observations(build_observations(_mixed_injection_rows()))
    cells = degradation_by_fault(pairs, n_boot=200, seed=1)
    assert cells
    item = cells[0]
    # 故障级会把 model×architecture 汇总：任务1 触发 4 对，任务2 未触发 4 对
    assert item["n_pairs"] == 4 and item["n_pairs_itt"] == 8
    assert item["degradation_itt"] is not None


def test_task_floor_ceiling_flags_extremes():
    observations = []
    for task in (1, 2, 3):
        for rep in range(4):
            observations.append({"task_id": task, "condition": "control",
                                 "official_success": task == 2 or (task == 3 and rep < 2)})
    result = task_floor_ceiling(observations, control_per_task=4)
    assert result["floor_tasks"] == [1]
    assert result["ceiling_tasks"] == [2]
    assert result["n_within_range"] == 1


def test_interaction_reports_one_estimand_for_interval_and_p():
    def effect(model, architecture, fault):
        return 0.7 if (model == "m2" and architecture == "plan_execute") else 0.0
    rows = _analysis_rows(effect=effect, seeds=(1, 2, 3, 4))
    result = interaction_on_degradation(build_observations(rows), models=["m1", "m2"],
                                        n_boot=400, seed=3)
    assert result["estimand"] == "task_equal_weighted_interaction"
    assert result["p_value"] is not None and result["p_value"] < 0.05
    assert result["bootstrap_ci_low"] > 0
    assert "sensitivity_observation_weighted" in result


# ==========================================================================
# 19. 三明治协方差：乘法顺序回归
# ==========================================================================

def test_cluster_robust_cov_matches_an_independent_sandwich():
    """回归：曾把 (X'X)^-1 @ meat @ (X'X)^-1 写成 meat @ (X'X)^-2，
    使交互项 SE 被放大约 2.5 倍。这里用独立实现逐元素比对。"""
    from standard_agent.stage_c_analysis import (
        cluster_robust_cov, normal_equations, solve,
    )

    def matmul(a, b):
        k = len(b)
        return [[sum(a[i][m] * b[m][j] for m in range(k)) for j in range(len(b[0]))]
                for i in range(len(a))]

    def transpose(a):
        return [list(r) for r in zip(*a)]

    def invert(a):
        n = len(a)
        m = [row[:] + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(a)]
        for col in range(n):
            piv = max(range(col, n), key=lambda r: abs(m[r][col]))
            m[col], m[piv] = m[piv], m[col]
            pv = m[col][col]
            m[col] = [v / pv for v in m[col]]
            for r in range(n):
                if r != col and m[r][col]:
                    f = m[r][col]
                    m[r] = [x - f * y for x, y in zip(m[r], m[col])]
        return [row[n:] for row in m]

    # 两个聚类、每个聚类内若干行，X 含截距与一个二值列
    X = [[1.0, 0.0], [1.0, 0.0], [1.0, 1.0],
         [1.0, 1.0], [1.0, 0.0], [1.0, 1.0]]
    y = [0.0, 1.0, 1.0, 1.0, 0.0, 0.0]
    clusters = ["a", "a", "a", "b", "b", "b"]

    xtx, xty = normal_equations(X, y)
    beta = solve(xtx, xty)
    u = [y[i] - sum(X[i][j] * beta[j] for j in range(len(beta))) for i in range(len(X))]
    k = len(beta)
    meat = [[0.0] * k for _ in range(k)]
    for group in ("a", "b"):
        idx = [i for i, c in enumerate(clusters) if c == group]
        score = [sum(X[i][j] * u[i] for i in idx) for j in range(k)]
        for i in range(k):
            for j in range(k):
                meat[i][j] += score[i] * score[j]
    G = 2
    meat = [[v * G / (G - 1) for v in row] for row in meat]
    inv = invert(xtx)
    reference = matmul(matmul(inv, meat), inv)

    got = cluster_robust_cov(X, u, clusters)
    for i in range(k):
        for j in range(k):
            assert abs(got[i][j] - reference[i][j]) < 1e-12
    # 顺序写错时会与"meat @ inv^2"一致，这里显式排除
    wrong = matmul(meat, matmul(inv, inv))
    assert abs(got[1][1] - wrong[1][1]) > 1e-12


# ==========================================================================
# 20. 评分护栏：默认不覆盖既有 official_eval.json
# ==========================================================================

def _eval_result(success=True, status="success"):
    return {"status": status, "official_success": success,
            "evaluators_results": [{"evaluator_name": "AgentResponseEvaluator",
                                    "result": success}],
            "error_msg": ""}


def test_evaluate_trial_reuses_a_valid_existing_eval(tmp_path):
    from standard_agent.stage_c_pipeline import evaluate_trial, evaluation_binding
    trial = _trial_dir(tmp_path)
    bound = {**_eval_result(),
             "_evaluation_binding": evaluation_binding(trial, None, _eval_result())}
    (trial / "official_eval.json").write_text(json.dumps(bound), encoding="utf-8")
    before = (trial / "official_eval.json").read_text()

    def boom(*args, **kwargs):
        raise AssertionError("evaluator must not be called when a valid eval exists")

    out = evaluate_trial(trial, task_id=118, config_path=None, evaluate_fn=boom, evaluate=True)
    assert out["source"] == "reused"
    assert out["evaluation_status"] == "native"
    assert (trial / "official_eval.json").read_text() == before


def test_evaluate_trial_evaluates_only_when_missing(tmp_path):
    from standard_agent.stage_c_pipeline import evaluate_trial
    trial = _trial_dir(tmp_path)
    calls = []

    def fake(task_id, **kwargs):
        calls.append(task_id)
        return _eval_result()

    out = evaluate_trial(trial, task_id=118, config_path=None, evaluate_fn=fake, evaluate=True)
    assert out["source"] == "evaluated" and calls == [118]
    assert json.loads((trial / "official_eval.json").read_text())["status"] == "success"


def test_evaluate_trial_does_not_overwrite_a_broken_eval(tmp_path):
    from standard_agent.stage_c_pipeline import evaluate_trial
    trial = _trial_dir(tmp_path)
    broken = {"status": "error", "error_msg": "boom", "evaluators_results": []}
    (trial / "official_eval.json").write_text(json.dumps(broken), encoding="utf-8")
    before = (trial / "official_eval.json").read_text()
    warnings = []

    def boom(*args, **kwargs):
        raise AssertionError("broken eval must not trigger a silent re-evaluation")

    out = evaluate_trial(trial, task_id=118, config_path=None, evaluate_fn=boom,
                         evaluate=True, warn=warnings.append)
    assert out["source"] == "existing_invalid"
    assert (trial / "official_eval.json").read_text() == before
    assert warnings and "error" in warnings[0]


def test_audit_reads_existing_eval_without_evaluating(tmp_path):
    from standard_agent.stage_c_pipeline import collect_rows
    trial = _trial_dir(tmp_path)
    (trial / "official_eval.json").write_text(json.dumps(_eval_result()), encoding="utf-8")
    _trial_dir(tmp_path, task=21)          # 无评分文件
    rows = collect_rows(tmp_path, evaluate=False)
    by_task = {str((row["cell"] or {}).get("task_id")): row for row in rows}
    assert by_task["118"]["official_success"] is True
    # 旧评分没有绑定：只读复用但标记为来源未验证
    assert by_task["118"]["evaluation_source"] == "reused_unverified"
    assert by_task["21"]["evaluation_status"] == "skipped"


def test_force_re_evaluate_backs_up_and_journals(tmp_path):
    from standard_agent.stage_c_pipeline import evaluate_trial
    trial = _trial_dir(tmp_path)
    (trial / "official_eval.json").write_text(json.dumps(_eval_result(success=False)),
                                              encoding="utf-8")
    out = evaluate_trial(trial, task_id=118, config_path="/tmp/cfg.json",
                         evaluate_fn=lambda *a, **k: _eval_result(success=True),
                         evaluate=True, force_re_evaluate=True)
    assert out["source"] == "re_evaluated"
    backups = list(trial.glob("official_eval.json.pre-re-evaluate-*.json"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text())["official_success"] is False
    journal = json.loads((trial / "official_eval.re-evaluate.json").read_text())
    assert journal["old"]["official_success"] is False
    assert journal["new"]["official_success"] is True
    assert journal["config_path"] == "/tmp/cfg.json"


def test_cli_rejects_force_re_evaluate_on_audit(tmp_path, monkeypatch, capsys):
    import sys as _sys
    from scripts.stage_c_pipeline import main as pipeline_main
    monkeypatch.setattr(_sys, "argv", [
        "stage_c_pipeline.py", "audit", "--root", str(tmp_path), "--force-re-evaluate"])
    assert pipeline_main() == 2
    assert "--force-re-evaluate" in capsys.readouterr().err


# ==========================================================================
# 21. E-P episode 状态机（纯逻辑）
# ==========================================================================

def test_single_variant_injects_once_then_releases():
    from fault_injection.episode import FaultEpisode
    episode = FaultEpisode("single", window_steps=5)
    ok, reason = episode.decide(proxy_step=2, action_uid="a1")
    assert (ok, reason) == (True, "inject")
    episode.record_injection(proxy_step=2, action_uid="a1", target={"kind": "url"})
    assert episode.injections == 1 and episode.released == "budget_reached"
    # 之后任何步都不再注入
    assert episode.decide(proxy_step=3, action_uid="a2") == (False, "released")


def test_repeat3_window_and_budget_release():
    from fault_injection.episode import FaultEpisode
    episode = FaultEpisode("repeat3", window_steps=5)
    episode.record_injection(proxy_step=2, action_uid="a1", target={"id": "T"})
    assert episode.t0 == 2 and episode.window_end == 6
    # 同动作去重
    assert episode.decide(proxy_step=3, action_uid="a1") == (False, "action_already_injected")
    # 目标不符 → 不注入、不耗次数
    assert episode.decide(proxy_step=3, action_uid="a2", matches_locked=False) == (False, "off_target")
    # 窗口内同目标可再注入，累计到 3 次后释放
    assert episode.decide(proxy_step=3, action_uid="a2")[0] is True
    episode.record_injection(proxy_step=3, action_uid="a2")
    episode.record_injection(proxy_step=4, action_uid="a3")
    assert episode.injections == 3 and episode.released == "budget_reached"
    # 另一 episode：窗口过期释放
    other = FaultEpisode("repeat3", window_steps=5)
    other.record_injection(proxy_step=2, action_uid="b1", target={"id": "T"})
    other.note_step(7)                       # 2 + 5 = 7 > window_end(6)
    assert other.released == "window_expired"
    assert other.decide(proxy_step=7, action_uid="b2") == (False, "released")


def test_episode_records_ambiguous_and_absent_without_consuming_dose():
    from fault_injection.episode import FaultEpisode
    episode = FaultEpisode("repeat3")
    assert episode.decide(proxy_step=2, action_uid="a1", target_status="ambiguous") == (False, "target_ambiguous")
    assert episode.decide(proxy_step=2, action_uid="a1", target_status="absent") == (False, "target_absent")
    assert episode.injections == 0 and episode.t0 is None


def test_episode_locks_the_first_target():
    from fault_injection.episode import FaultEpisode
    episode = FaultEpisode("repeat3", target_policy_version="legacy-v1")
    episode.record_injection(proxy_step=2, action_uid="a1", target={"role": "link", "name": "Next"})
    assert episode.target == {"role": "link", "name": "Next"}
    episode.record_injection(proxy_step=3, action_uid="a2", target={"role": "link", "name": "Other"})
    assert episode.target == {"role": "link", "name": "Next"}    # 不被后续覆盖
    assert episode.snapshot()["target_policy_version"] == "legacy-v1"


def test_episodes_do_not_share_counters():
    from fault_injection.episode import FaultEpisode
    a, b = FaultEpisode("repeat3"), FaultEpisode("repeat3")
    a.record_injection(proxy_step=2, action_uid="x")
    assert b.injections == 0 and b.t0 is None


def test_classify_injection_log_is_variant_aware():
    from fault_injection.episode import classify_injection_log
    inj = lambda step, uid: {"event": "injection", "proxy_step": step, "action_uid": uid}
    assert classify_injection_log([], variant="repeat3")["dose_class"] == "not_triggered"
    assert classify_injection_log([inj(2, "a")], variant="repeat3")["dose_class"] == "partial_dose"
    full = classify_injection_log([inj(2, "a"), inj(3, "b"), inj(4, "c")], variant="repeat3")
    assert full["dose_class"] == "full_dose" and full["valid"]
    dup = classify_injection_log([inj(2, "a"), inj(3, "a")], variant="repeat3")
    assert not dup["valid"] and any("more than once" in e for e in dup["errors"])
    outside = classify_injection_log([inj(2, "a"), inj(9, "b")], variant="repeat3")
    assert not outside["valid"] and any("outside window" in e for e in outside["errors"])
    over = classify_injection_log([inj(2, "a"), inj(3, "b"), inj(4, "c"), inj(5, "d")],
                                  variant="repeat3")
    assert not over["valid"] and over["dose_class"] == "implementation_error"
    # single 仍要求恰好一次
    assert classify_injection_log([inj(2, "a")], variant="single")["valid"] is True
    assert classify_injection_log([inj(2, "a"), inj(3, "b")], variant="single")["valid"] is False
    assert classify_injection_log([], variant="single")["valid"] is False


# ==========================================================================
# 22. 评分绑定：响应/HAR/config 变更后旧评分不可复用
# ==========================================================================

def test_new_evaluation_is_bound_to_its_inputs(tmp_path):
    import hashlib
    from standard_agent.stage_c_pipeline import evaluate_trial
    trial = _trial_dir(tmp_path)
    evaluate_trial(trial, task_id=118, config_path=None,
                   evaluate_fn=lambda *a, **k: _eval_result(), evaluate=True)
    written = json.loads((trial / "official_eval.json").read_text())
    binding = written["_evaluation_binding"]
    actual = hashlib.sha256((trial / "agent_response.json").read_bytes()).hexdigest()
    assert binding["agent_response_sha256"] == actual
    assert binding["network_har_sha256"] == hashlib.sha256(
        (trial / "network.har").read_bytes()).hexdigest()
    assert binding["evaluator_adapter_version"]


def test_stale_binding_is_not_reused(tmp_path):
    from standard_agent.stage_c_pipeline import evaluate_trial
    trial = _trial_dir(tmp_path)
    evaluate_trial(trial, task_id=118, config_path=None,
                   evaluate_fn=lambda *a, **k: _eval_result(), evaluate=True)
    before = (trial / "official_eval.json").read_text()
    # 响应被换成另一条：绑定不再匹配
    (trial / "agent_response.json").write_text(json.dumps(
        {"task_type": "NAVIGATE", "status": "SUCCESS",
         "retrieved_data": ["different"], "error_details": None}), encoding="utf-8")
    warnings = []

    def boom(*args, **kwargs):
        raise AssertionError("stale binding must not silently re-evaluate")

    out = evaluate_trial(trial, task_id=118, config_path=None, evaluate_fn=boom,
                         evaluate=True, warn=warnings.append)
    assert out["source"] == "stale_binding"
    assert out["official_success"] is None
    assert (trial / "official_eval.json").read_text() == before
    assert warnings and "绑定" in warnings[0]


def test_legacy_eval_without_binding_is_unverified(tmp_path):
    from standard_agent.stage_c_pipeline import evaluate_trial
    trial = _trial_dir(tmp_path)
    (trial / "official_eval.json").write_text(json.dumps(_eval_result()), encoding="utf-8")

    def boom(*args, **kwargs):
        raise AssertionError("legacy eval must reuse without evaluating")

    out = evaluate_trial(trial, task_id=118, config_path=None, evaluate_fn=boom, evaluate=True)
    assert out["source"] == "reused_unverified"


# ==========================================================================
# 23. E-P 目标匹配策略（跨刷新语义身份）
# ==========================================================================

def _elem(role="link", name="Next", url=None, role_index=0):
    attrs = {"url": url} if url else {}
    return {"role": role, "name": name, "attrs": attrs, "role_index": role_index}


def test_target_identity_survives_ax_id_change():
    from fault_injection.targets import identify_element, match_identity
    page = "http://localhost:7770/review/product/list?p=2"
    identity = identify_element({"116": _elem(role="link", name="Next")}, "116", page)
    # 刷新后同一元素换了 id（60），但语义身份不变 → 仍能匹配
    found, status = match_identity(identity, {"60": _elem(role="link", name="Next")}, page)
    assert (found, status) == ("60", "ok")


def test_target_absent_when_page_or_element_changes():
    from fault_injection.targets import identify_element, match_identity
    page = "http://localhost:7770/a"
    identity = identify_element({"1": _elem(name="Buy now")}, "1", page)
    # 换了页面 → absent（不跨页继续注入）
    assert match_identity(identity, {"2": _elem(name="Buy now")}, "http://localhost:7770/b") == (None, "absent")
    # 元素消失 → absent
    assert match_identity(identity, {"2": _elem(name="Other")}, page) == (None, "absent")


def test_ambiguous_target_is_never_guessed():
    from fault_injection.targets import identify_element, match_identity
    page = "http://localhost:7770/a"
    identity = identify_element({"1": _elem(name="Read more")}, "1", page)
    # 两个同名同 role 的候选且无 role_index 线索 → ambiguous，绝不随便挑
    found, status = match_identity(identity, {"7": _elem(name="Read more"),
                                              "8": _elem(name="Read more")}, page)
    assert (found, status) == (None, "ambiguous")
    # 即便身份里有 role_index，也不得用它消歧（刷新后序号会变，可能屏蔽错元素）
    identity2 = identify_element({"1": _elem(name="Read more", role_index=1)}, "1", page)
    found2, status2 = match_identity(identity2, {"7": _elem(name="Read more", role_index=0),
                                                 "8": _elem(name="Read more", role_index=1)}, page)
    assert (found2, status2) == (None, "ambiguous")


def test_href_and_normalization_rules():
    from fault_injection.targets import (dom_identity, match_identity, normalize_text,
                                         normalize_url)
    assert normalize_url("http://h/p?a=1#frag") == "http://h/p?a=1"      # 只去 fragment
    assert normalize_url("http://h/p?a=1") != "http://h/p?a=2"          # 保留 query 差异
    assert normalize_text("  a \n b ") == "a b"
    page = "http://h/p#top"
    identity = dom_identity(_elem(name="Docs", url="http://h/docs"), "http://h/p")
    # fragment 归一化后仍同页；href 不同 → 不匹配
    found, status = match_identity(identity, {"5": _elem(name="Docs", url="http://h/other")}, page)
    assert (found, status) == (None, "absent")
    found2, status2 = match_identity(identity, {"5": _elem(name="Docs", url="http://h/docs")}, page)
    assert (found2, status2) == ("5", "ok")


def test_param_identity_requires_action_and_target():
    from fault_injection.targets import param_identity, match_identity
    page = "http://localhost:7770/checkout"
    identity = param_identity("click", _elem(role="button", name="Place order"), page)
    assert identity["kind"] == "param" and identity["action"] == "click"
    found, status = match_identity(identity, {"9": _elem(role="button", name="Place order")}, page)
    assert (found, status) == ("9", "ok")


# ==========================================================================
# 24. E-P 持久化引擎（同目标重复故障，离线）
# ==========================================================================

def _ep_config(**kw):
    from fault_injection.config import FaultConfig
    base = dict(intensity="high", seed=7, injection_step=2,
                experiment_id="ep-persistence-v1", fault_variant="repeat3")
    base.update(kw)
    return FaultConfig(**base)


def test_persistence_http_replays_same_sample_on_the_same_page():
    from fault_injection.persistence import PersistenceEngine
    engine = PersistenceEngine(_ep_config(web_http_error=True))
    page = "http://localhost:7770/a"
    first = engine.apply_http({"url": page, "elements": {}, "page_content": ""},
                              proxy_step=2, action_uid="goto-1")
    assert engine._http_sample is not None                     # 抽到 500 或 503 之一
    assert engine.episode.target["page_url"] == page
    # 刷新同一页（新观察）仍故障，且重放**同一个**样本
    again = engine.apply_http({"url": page, "elements": {}, "page_content": "real"},
                              proxy_step=3, action_uid="goto-2")
    assert again["page_content"] == first["page_content"]
    # 别的 URL 不受影响
    other = engine.apply_http({"url": "http://localhost:7770/b", "elements": {},
                               "page_content": "real-b"}, proxy_step=3, action_uid="goto-3")
    assert other["page_content"] == "real-b"
    # 第 3 次后预算用尽释放，后续不再注入
    engine.apply_http({"url": page, "elements": {}, "page_content": "real"},
                      proxy_step=4, action_uid="goto-4")
    released = engine.apply_http({"url": page, "elements": {}, "page_content": "real"},
                                 proxy_step=5, action_uid="goto-5")
    assert released["page_content"] == "real"
    assert engine.episode.released == "budget_reached"


def test_persistence_dom_hides_the_same_elements_after_rebuild():
    from fault_injection.persistence import PersistenceEngine
    page = "http://localhost:7770/list"
    e = lambda r, n, i: {"role": r, "name": n, "attrs": {}, "role_index": i}
    obs = {"url": page,
           "page_content": "[1] link 'Next'\n[2] text 'body'\n[3] link 'Other'",
           "elements": {"1": e("link", "Next", 0), "2": e("text", "body", 0),
                        "3": e("link", "Other", 0)}}
    engine = PersistenceEngine(_ep_config(web_dom_missing=True))
    first = engine.apply_dom(obs, proxy_step=2, action_uid="a1")
    assert engine._dom_targets and first["elements"] != obs["elements"]
    hidden_names = {v["name"] for v in first["elements"].values()}
    # 重建后换了 id，但被屏蔽的是同一批语义元素
    rebuilt = {"url": page,
               "page_content": "[10] link 'Next'\n[20] text 'body'\n[30] link 'Other'",
               "elements": {"10": e("link", "Next", 0), "20": e("text", "body", 0),
                            "30": e("link", "Other", 0)}}
    second = engine.apply_dom(rebuilt, proxy_step=3, action_uid="a2")
    assert {v["name"] for v in second["elements"].values()} == hidden_names
    # 锁定元素之一缺失 → 跳过且不消耗剂量、不改动观察
    before_injections = engine.episode.injections
    missing = {"url": page, "page_content": "[40] link 'Next'",
               "elements": {"40": e("link", "Next", 0)}}
    third = engine.apply_dom(missing, proxy_step=4, action_uid="a3")
    assert third == missing
    assert engine.episode.injections == before_injections


def test_persistence_param_replays_the_same_corruption():
    from fault_injection.persistence import PersistenceEngine
    page = "http://localhost:7770/checkout"
    button = {"role": "button", "name": "Place order", "attrs": {}, "role_index": 0}
    engine = PersistenceEngine(_ep_config(agent_param_error=True, injection_step=1))
    eid, text = engine.apply_param(action="click", element_id="9", text=None,
                                   proxy_step=2, action_uid="p1",
                                   elements={"9": button}, page_url=page)
    assert eid.startswith("invalid_") and engine._param_target is not None
    # 同目标再次出现（新 AX id）→ 重放同一损坏规则
    eid2, _ = engine.apply_param(action="click", element_id="77", text=None,
                                 proxy_step=3, action_uid="p2",
                                 elements={"77": button}, page_url=page)
    assert eid2 == eid
    # 换成别的目标 → 跳过，不改写参数
    other = {"role": "button", "name": "Apply coupon", "attrs": {}, "role_index": 0}
    eid3, text3 = engine.apply_param(action="click", element_id="88", text=None,
                                     proxy_step=4, action_uid="p3",
                                     elements={"88": other}, page_url=page)
    assert (eid3, text3) == ("88", None)


# ==========================================================================
# 25. FaultInjector ↔ PersistenceEngine 接线（mock 环境，离线）
# ==========================================================================

class _FakeEnv:
    """最小 mock 环境：只提供 goto/click/observe，返回 PageObservation。"""

    def __init__(self, page_elements=None):
        from standard_agent.environment.browser import PageObservation
        self._PageObservation = PageObservation
        self.url = "about:blank"
        self.elements = page_elements or {}
        self.clicked = []

    def start(self): pass
    def stop(self): pass

    def _page(self, url):
        return self._PageObservation(url=url, ax_tree_text="[1] text 'real'",
                                     element_map=self.elements)

    def observe(self):
        return self._page(self.url)

    def goto(self, url):
        self.url = url
        return self._page(url)

    def click(self, element_id):
        self.clicked.append(str(element_id))
        return self._page(self.url)


def test_stage_c_injector_keeps_the_legacy_path():
    from fault_injection.config import FaultConfig
    from fault_injection.injector import FaultInjector
    config = FaultConfig(intensity="high", seed=7, web_http_error=True, injection_step=2)
    injector = FaultInjector(_FakeEnv(), config)
    assert injector._engine is None                       # Stage C 不启用引擎
    injector.goto("http://h/a")                           # step 1
    page = injector.goto("http://h/b")                    # step 2 → 注入
    assert "500" in page.ax_tree_text or "503" in page.ax_tree_text


def test_injector_repeat3_persists_http_and_replays_param():
    from fault_injection.config import FaultConfig
    from fault_injection.injector import FaultInjector
    # HTTP：同页刷新重复故障，别的 URL 不受影响
    cfg = FaultConfig(intensity="high", seed=7, web_http_error=True, injection_step=2,
                      experiment_id="ep-persistence-v1", fault_variant="repeat3")
    injector = FaultInjector(_FakeEnv(), cfg)
    assert injector._engine is not None
    injector.goto("http://h/a")                           # step 1：正常
    first = injector.goto("http://h/b")                   # step 2：注入
    assert "500" in first.ax_tree_text or "503" in first.ax_tree_text
    again = injector.goto("http://h/b")                   # step 3：同页刷新仍故障
    assert "500" in again.ax_tree_text or "503" in again.ax_tree_text
    other = injector.goto("http://h/c")                   # step 4：别的 URL 正常
    assert "500" not in other.ax_tree_text and "503" not in other.ax_tree_text
    assert injector._engine.episode.target["page_url"] == "http://h/b"

    # 参数：同目标重放同一损坏规则
    button = {"role": "button", "name": "Place order", "attrs": {}, "role_index": 0}
    env = _FakeEnv(page_elements={"9": button})
    cfg2 = FaultConfig(intensity="high", seed=7, agent_param_error=True, injection_step=1,
                       experiment_id="ep-persistence-v1", fault_variant="repeat3")
    injector2 = FaultInjector(env, cfg2)
    injector2.observe()                                   # 建立 _last_obs
    injector2.click("9")                                  # 首次 → invalid_
    first_eid = env.clicked[-1]
    assert first_eid.startswith("invalid_")
    env.elements = {"77": button}                         # 重建后换 id，语义目标不变
    injector2.observe()                                   # 真实流程：动作前必有观察
    injector2.click("77")                                 # 重放同一损坏规则
    assert env.clicked[-1] == first_eid


# ==========================================================================
# 26. run_fault_matrix：variant 校验与 CLI 透传
# ==========================================================================

def _count_line(count, seed=1, faults="'web_http_error'"):
    return (f"        injection_count={count} fault_seed={seed} faults=[{faults}] "
            f"steps={list(range(2, 2 + count))}\n")


def test_classify_log_single_rule_unchanged():
    from scripts.run_fault_matrix import classify_log
    one = classify_log(_count_line(1), expect_fault=True)
    assert one["fault_triggered"] and not one["fault_invalid"] and not one["fault_missing"]
    two = classify_log(_count_line(2), expect_fault=True)
    assert two["fault_invalid"] is True
    none = classify_log("no fault lines here\n", expect_fault=True)
    assert none["fault_missing"] is True and none["fault_triggered"] is False


def test_classify_log_repeat3_uses_structured_episode():
    import json as _json
    from scripts.run_fault_matrix import classify_log
    injections = [{"event": "injection", "proxy_step": s, "action_uid": f"goto:{s}"}
                  for s in (2, 3, 4)]
    payload = {"experiment_id": "ep-persistence-v1", "variant": "repeat3",
               "window_steps": 5, "injections": injections}
    text = _count_line(3) + "        fault_episode=" + _json.dumps(payload) + "\n"
    out = classify_log(text, expect_fault=True, fault_variant="repeat3", window_steps=5)
    assert out["fault_count"] == 3 and out["fault_triggered"] and not out["fault_invalid"]

    # 0 剂量：合法未触发，不算 missing、不算 invalid
    zero = {"experiment_id": "ep-persistence-v1", "variant": "repeat3",
            "window_steps": 5, "injections": []}
    text0 = _count_line(0) + "        fault_episode=" + _json.dumps(zero) + "\n"
    out0 = classify_log(text0, expect_fault=True, fault_variant="repeat3", window_steps=5)
    assert out0["fault_triggered"] is False and out0["fault_invalid"] is False
    assert out0["fault_missing"] is False

    # 同一 action_uid 注入两次 → 实现错误
    dup = {"experiment_id": "ep-persistence-v1", "variant": "repeat3", "window_steps": 5,
           "injections": [{"event": "injection", "proxy_step": 2, "action_uid": "a"},
                          {"event": "injection", "proxy_step": 3, "action_uid": "a"}]}
    textd = _count_line(2) + "        fault_episode=" + _json.dumps(dup) + "\n"
    outd = classify_log(textd, expect_fault=True, fault_variant="repeat3")
    assert outd["fault_invalid"] is True


def test_command_for_passes_experiment_identity():
    from scripts.run_fault_matrix import build_parser, command_for
    args = build_parser().parse_args([
        "--output-dir", "/tmp/x", "--model-profile", "m1", "--architecture", "react",
        "--experiment-id", "ep-persistence-v1", "--fault-variant", "repeat3",
        "--fault-window-steps", "5"])
    job = {"site": "shopping", "start_url": "http://h/a", "intent": "do it",
           "task_id": 24, "seed": 1, "fault_type": "web_http_error"}
    command = command_for(args, job)
    assert "--experiment-id" in command and "ep-persistence-v1" in command
    assert "--fault-variant" in command and "repeat3" in command
    assert "--fault-window-steps" in command and "5" in command


# ==========================================================================
# 27. 暴露日志：故障"被交付"而非声称模型识别
# ==========================================================================

def test_exposure_log_records_delivery_not_awareness():
    from fault_injection.config import FaultConfig
    from fault_injection.injector import FaultInjector
    cfg = FaultConfig(intensity="high", seed=7, web_http_error=True, injection_step=2,
                      experiment_id="ep-persistence-v1", fault_variant="repeat3")
    injector = FaultInjector(_FakeEnv(), cfg)
    injector.goto("http://h/a")                    # 未注入
    injector.goto("http://h/b")                    # 注入
    injector.goto("http://h/c")                    # 别的 URL，未注入
    exposures = injector.get_exposure_log()
    assert len(exposures) == 3
    assert [e["delivered"] for e in exposures] == [False, True, False]
    assert exposures[1]["faults_delivered"] == ["web_http_error"]
    # uid 唯一且绑定来源动作
    uids = {e["observation_uid"] for e in exposures}
    assert len(uids) == 3
    assert exposures[1]["source_action_uid"] == "goto:2"
    assert all(e["variant"] == "repeat3" for e in exposures)


def test_exposure_log_also_covers_stage_c():
    from fault_injection.config import FaultConfig
    from fault_injection.injector import FaultInjector
    cfg = FaultConfig(intensity="high", seed=7, web_http_error=True, injection_step=2)
    injector = FaultInjector(_FakeEnv(), cfg)
    injector.goto("http://h/a")
    injector.goto("http://h/b")
    exposures = injector.get_exposure_log()
    assert [e["delivered"] for e in exposures] == [False, True]
    assert exposures[1]["faults_delivered"] == ["web_http_error"]


# ==========================================================================
# 28. E-P 身份：id/stem/pair_key/指纹都不得与 Stage C 碰撞
# ==========================================================================

def test_stage_c_identity_is_unchanged():
    from standard_agent.trial_metadata import make_trial_id, pair_key, trial_file_stem
    common = dict(model_profile="m", architecture="react", fault_type="f",
                  task_id=24, seed=1)
    assert make_trial_id(**common, condition="fault") == \
        "model=m|arch=react|fault=f|task=24|seed=1|cond=fault"
    assert trial_file_stem(**common, condition="fault") == "m__react__f__task24__seed1__fault"
    # Stage C 的 pair_key 不带 exp/var
    assert "exp=" not in pair_key(**common)


def test_ep_identity_is_distinct_and_carries_variant():
    from standard_agent.trial_metadata import make_trial_id, pair_key, trial_file_stem
    common = dict(model_profile="m", architecture="react", fault_type="f",
                  task_id=24, seed=1, condition="fault",
                  experiment_id="ep-persistence-v1")
    single = make_trial_id(**common, fault_variant="single")
    repeat = make_trial_id(**common, fault_variant="repeat3")
    assert single != repeat
    assert "exp=ep-persistence-v1" in single and "var=single" in single
    assert "var=repeat3" in repeat
    assert trial_file_stem(**common, fault_variant="repeat3").startswith(
        "ep-persistence-v1__repeat3__")
    assert "var=repeat3" in pair_key(model_profile="m", architecture="react", fault_type="f",
                                     task_id=24, seed=1,
                                     experiment_id="ep-persistence-v1", fault_variant="repeat3")


def test_build_trial_record_emits_experiment_identity():
    from standard_agent.trial_metadata import build_trial_record
    record = build_trial_record(
        model_profile="m", architecture="react", fault_type="f", task_id=24, seed=1,
        condition="fault", run_config={}, replicate=0,
        experiment_id="ep-persistence-v1", fault_variant="repeat3")
    assert record["experiment_id"] == "ep-persistence-v1"
    assert record["fault_variant"] == "repeat3"
    assert "var=repeat3" in record["pair_key"]
    assert record["file_stem"].startswith("ep-persistence-v1__repeat3__")


def test_design_fingerprint_includes_variant_and_policy():
    from scripts.run_fault_matrix import build_parser, design_fingerprint
    base = ["--output-dir", "/tmp/x", "--model-profile", "m1", "--architecture", "react",
            "--experiment-id", "ep-persistence-v1", "--fault-window-steps", "5"]
    args_single = build_parser().parse_args(base + ["--fault-variant", "single"])
    args_repeat = build_parser().parse_args(base + ["--fault-variant", "repeat3"])
    fp = lambda a: design_fingerprint(a, ["web_http_error"], {"web_http_error": 2}, [24])
    assert fp(args_single) != fp(args_repeat)          # variant 进指纹
    a5 = build_parser().parse_args(base + ["--fault-variant", "repeat3"])
    a9 = build_parser().parse_args(["--output-dir", "/tmp/x", "--model-profile", "m1",
                                    "--architecture", "react",
                                    "--experiment-id", "ep-persistence-v1",
                                    "--fault-variant", "repeat3", "--fault-window-steps", "9"])
    assert fp(a5) != fp(a9)                            # 窗口进指纹


# ==========================================================================
# 29. E-P 交错调度与 D_f 分析
# ==========================================================================

def test_ep_schedule_is_deterministic_and_interleaved():
    from standard_agent.ep_schedule import build_schedule, first_variant_per_block
    kwargs = dict(models=["m1"], architectures=["react"], faults=["web_http_error"],
                  tasks=[24, 118], seeds=[1], schedule_seed=11)
    a = build_schedule(**kwargs)
    b = build_schedule(**kwargs)
    assert a == b                                        # 同 seed 同顺序
    assert a["blocks"] == 2 and a["cells"] == 8
    # 每个 block 四个 cell 各不相同
    for block in {e["block"] for e in a["entries"]}:
        cells = {(e["variant"], e["condition"]) for e in a["entries"] if e["block"] == block}
        assert cells == {("single", "control"), ("single", "fault"),
                         ("repeat3", "control"), ("repeat3", "fault")}
    assert len(first_variant_per_block(a)) == 2
    assert len(set(first_variant_per_block(a))) == 2     # 首 variant 轮换
    # pair_key 不同 variant 不同，file_stem 带 exp__variant
    stems = {e["file_stem"] for e in a["entries"]}
    assert len(stems) == len(a["entries"])
    assert all(s.startswith("ep-persistence-v1__") for s in stems)


def test_ep_schedule_seed_changes_order():
    from standard_agent.ep_schedule import build_schedule
    base = dict(models=["m1"], architectures=["react"], faults=["f"],
                tasks=[1, 2], seeds=[1])
    assert build_schedule(**base, schedule_seed=1) != build_schedule(**base, schedule_seed=2)


def _ep_row(variant, condition, success, task=24, fault="web_http_error",
            injections=1, steps=8, llm_calls=10):
    return {"cell": {"model_profile": "m1", "architecture": "react", "fault_type": fault,
                     "task_id": task, "fault_seed": 1, "condition": condition},
            "experiment_id": "ep-persistence-v1", "fault_variant": variant,
            "official_success": success, "injection_count": injections,
            "steps": steps, "llm_calls": llm_calls}


def test_ep_analysis_computes_difference_in_differences():
    from standard_agent.ep_analysis import ep_analysis
    rows = []
    for task in (24, 118):
        # single：control 成功、fault 成功 → Δ_single = 0
        rows += [_ep_row("single", "control", True, task),
                 _ep_row("single", "fault", True, task)]
        # repeat3：control 成功、fault 失败 → Δ_repeat3 = 1
        rows += [_ep_row("repeat3", "control", True, task),
                 _ep_row("repeat3", "fault", False, task)]
    analysis = ep_analysis(rows)
    entry = analysis["by_fault"]["web_http_error"]
    assert entry["delta_single"]["task_equal_weighted"] == 0
    assert entry["delta_repeat3"]["task_equal_weighted"] == 1
    assert entry["D_f"]["task_equal_weighted"] == 1        # 同 block 上的差中差
    # 4 任务版本不做确认性推断：不得出现任何 p 值键
    assert analysis["confirmatory_inference"] is False
    assert "p_value" not in json.dumps(analysis)


def test_ep_analysis_reports_dose_distribution_and_paired_cost():
    from standard_agent.ep_analysis import ep_analysis
    rows = [
        _ep_row("single", "control", True, injections=0),
        _ep_row("single", "fault", False, injections=1, steps=9),
        _ep_row("repeat3", "control", True, injections=0),
        _ep_row("repeat3", "fault", False, injections=3, steps=12, llm_calls=15),
    ]
    analysis = ep_analysis(rows)
    doses = analysis["dose_distribution"]["repeat3"]["web_http_error"]
    assert doses.get("full_dose") == 1
    single_doses = analysis["dose_distribution"]["single"]["web_http_error"]
    assert single_doses.get("full_dose") == 1            # single 的 1 次即 full_dose
    cost = analysis["paired_step_delta"]["repeat3:web_http_error"]
    assert cost["step_delta_mean"] == 12 - 8             # fault − control


# ==========================================================================
# 30. 执行链修复：控制臂身份 / 参数离目标 / 计数 / 缺日志 / 合成错误 / D_f 配对
# ==========================================================================

def test_control_arm_carries_experiment_identity():
    from main import _build_fault_config
    import argparse as _ap
    args = _ap.Namespace(fault_type="web_http_error", fault_intensity="off",
                         fault_seed=7, fault_injection_step=2,
                         experiment_id="ep-persistence-v1", fault_variant="repeat3",
                         fault_window_steps=5)
    # 控制臂也用同一身份（intensity=off 不注入，但身份必须一致）
    from fault_injection.config import FaultConfig
    control = FaultConfig(intensity="off", seed=7, experiment_id="ep-persistence-v1",
                          fault_variant="repeat3", fault_window_steps=5)
    assert control.fault_variant == "repeat3"
    assert control.experiment_id == "ep-persistence-v1"
    assert control.variant_label == "ep-persistence-v1:repeat3"


def test_param_engine_does_not_touch_other_targets_or_actions():
    from fault_injection.persistence import PersistenceEngine
    cfg = _ep_config(agent_param_error=True, injection_step=1)
    engine = PersistenceEngine(cfg)
    page = "http://h/checkout"
    a = {"role": "button", "name": "Place order", "attrs": {}, "role_index": 0}
    b = {"role": "button", "name": "Apply coupon", "attrs": {}, "role_index": 0}
    # 首次锁定 A
    eid_a, _ = engine.apply_param(action="click", element_id="9", text=None, proxy_step=2,
                                  action_uid="p1", elements={"9": a}, page_url=page)
    assert eid_a.startswith("invalid_")
    # 点击 B → 不改写
    eid_b, _ = engine.apply_param(action="click", element_id="8", text=None, proxy_step=3,
                                  action_uid="p2", elements={"8": b}, page_url=page)
    assert eid_b == "8"
    # 对 A 做 hover（动作类型不同）→ 不改写
    eid_h, _ = engine.apply_param(action="hover", element_id="9", text=None, proxy_step=4,
                                  action_uid="p3", elements={"9": a}, page_url=page)
    assert eid_h == "9"
    # 再次点击 A → 重放同一损坏
    eid_a2, _ = engine.apply_param(action="click", element_id="77", text=None, proxy_step=5,
                                   action_uid="p4",
                                   elements={"77": a}, page_url=page)
    assert eid_a2 == eid_a


def test_engine_injections_reach_config_log():
    from fault_injection.persistence import PersistenceEngine
    cfg = _ep_config(web_http_error=True)
    engine = PersistenceEngine(cfg)
    page = {"url": "http://h/a", "elements": {}, "page_content": "real"}
    engine.apply_http(page, proxy_step=2, action_uid="a1")
    engine.apply_http(page, proxy_step=3, action_uid="a2")
    assert len(cfg.log) == 2 == cfg.episode.injections      # 导出计数 == episode 计数
    assert [e["fault"] for e in cfg.log] == ["web_http_error"] * 2


def test_repeat3_without_mechanism_log_is_an_error():
    from scripts.run_fault_matrix import classify_log
    # 有 count 行但没有 fault_episode 结构化证据 → 无法区分未触发与日志丢失 → 实现错误
    text = _count_line(0) 
    out = classify_log(text, expect_fault=True, fault_variant="repeat3")
    assert out["fault_invalid"] is True and out["fault_missing"] is True


def test_synthetic_error_page_does_not_mask_a_real_api_error():
    from scripts.run_fault_matrix import classify_log
    synthetic = ("        injection_count=1 fault_seed=1 faults=['web_http_error'] steps=[2]\n"
                 "[1] heading '503 Service Unavailable'\n")
    assert classify_log(synthetic, expect_fault=True)["infrastructure_error"] is False
    # 同一次运行又出现真实 API 429 → 必须仍判为基础设施错误
    mixed = synthetic + "openai.APIStatusError: Error code: 429 - Rate limit reached\n"
    assert classify_log(mixed, expect_fault=True)["infrastructure_error"] is True


def test_d_f_requires_the_same_block_in_both_variants():
    from standard_agent.ep_analysis import ep_analysis
    rows = [
        _ep_row("single", "control", True, task=24),
        _ep_row("single", "fault", False, task=24),      # single 只有 task24
        _ep_row("repeat3", "control", True, task=118),
        _ep_row("repeat3", "fault", False, task=118),    # repeat3 只有 task118
    ]
    analysis = ep_analysis(rows)
    assert analysis["n_blocks"] == 0
    assert analysis["excluded_incomplete_blocks"]               # 两个不完整 block 被单列
    assert analysis["by_fault"]["web_http_error"]["D_f"] is None  # 绝不跨任务相减


def test_evaluator_binding_uses_current_environment_version():
    from standard_agent.stage_c_pipeline import current_evaluator_version, evaluation_binding
    version = current_evaluator_version()
    assert "adapter" in version
    assert "package_sha256" in version or "distribution" in version


def test_run_schedule_plan_is_interleaved_and_variant_scoped(tmp_path):
    from standard_agent.ep_schedule import build_schedule, write_schedule
    from scripts.ep_run_schedule import build_parser, plan_jobs, _commands
    schedule = build_schedule(models=["m1"], architectures=["react"],
                              faults=["web_http_error"], tasks=[24, 118], seeds=[1],
                              schedule_seed=5)
    path = write_schedule(schedule, tmp_path / "schedule.json")
    args = build_parser().parse_args(["--schedule", str(path),
                                      "--experiment-root", str(tmp_path / "ep")])
    jobs = plan_jobs(schedule)
    assert len(jobs) == 8                                   # 2 block × 2 variant × 2 condition
    # 逐 entry：条件顺序来自 schedule，而不是永远 control 在前
    assert {j["condition"] for j in jobs} == {"control", "fault"}
    assert len({(j["variant"], j["condition"]) for j in jobs}) == 4
    planned = _commands(args, schedule, jobs)
    assert all("/repeat3/outputs/" in p["output_root"] or "/single/outputs/" in p["output_root"]
               for p in planned)
    assert all("--fault-variant" in p["command"] for p in planned)
    # 每条 entry 一个日志文件，且路径含模型与架构
    logs = [p["log_path"] for p in planned]
    assert len(set(logs)) == len(logs)
    assert all("/m1/react/" in lp for lp in logs)


# ==========================================================================
# 31. 调度运行器与评测哈希：review 发现的剩余问题
# ==========================================================================

def test_runner_log_paths_differ_across_architectures(tmp_path):
    from standard_agent.ep_schedule import build_schedule, write_schedule
    from scripts.ep_run_schedule import build_parser, log_dir, plan_jobs
    schedule = build_schedule(models=["m1"], architectures=["react", "plan_execute"],
                              faults=["web_http_error"], tasks=[24], seeds=[1],
                              schedule_seed=3)
    path = write_schedule(schedule, tmp_path / "schedule.json")
    args = build_parser().parse_args(["--schedule", str(path),
                                      "--experiment-root", str(tmp_path / "ep")])
    jobs = plan_jobs(schedule)
    paths = {log_dir(args.experiment_root, j) for j in jobs}
    # 两个架构的日志目录必须不同（否则 ReAct 与 PE 会互相覆盖）
    # 日志目录同时区分 variant 与架构：2 variant × 2 arch = 4
    assert len({str(p) for p in paths}) == 4
    assert any("/react" in str(p) for p in paths)
    assert any("/plan_execute" in str(p) for p in paths)


def test_runner_plan_only_by_default_and_freeze_gate(tmp_path):
    from standard_agent.ep_schedule import build_schedule, write_schedule
    from scripts.ep_run_schedule import build_parser, check_freeze
    schedule = build_schedule(models=["m1"], architectures=["react"],
                              faults=["web_http_error"], tasks=[24], seeds=[1])
    path = write_schedule(schedule, tmp_path / "schedule.json")
    args = build_parser().parse_args(["--schedule", str(path),
                                      "--experiment-root", str(tmp_path / "ep"),
                                      "--freeze", str(tmp_path / "missing.json")])
    assert args.execute is False                     # 默认只出计划
    ok, note = check_freeze(args)
    assert ok is False and "冻结清单" in note         # 缺清单即拒绝
    # commit 不符也拒绝
    frozen = tmp_path / "frozen.json"
    frozen.write_text(json.dumps({"git_commit": "deadbeefdeadbeefdead"}))
    args2 = build_parser().parse_args(["--schedule", str(path),
                                       "--experiment-root", str(tmp_path / "ep"),
                                       "--freeze", str(frozen)])
    ok2, note2 = check_freeze(args2)
    # 工作区可能为脏（本地开发），两种原因都属"拒绝"；此处只要求拒绝且给出原因
    assert ok2 is False and note2
    # --allow-unfrozen 才放行调试
    args3 = build_parser().parse_args(["--schedule", str(path),
                                       "--experiment-root", str(tmp_path / "ep"),
                                       "--allow-unfrozen"])
    assert check_freeze(args3)[0] is True
    # 冻结提交之后的"只有清单变化"应被接受：用当前 HEAD 作为冻结提交（工作区脏，
    # 故先跳过；纯逻辑由上面两例保证）


def test_param_same_name_elements_are_ambiguous_and_skipped():
    from fault_injection.persistence import PersistenceEngine
    cfg = _ep_config(agent_param_error=True, injection_step=1)
    engine = PersistenceEngine(cfg)
    page = "http://h/checkout"
    save = {"role": "button", "name": "Save", "attrs": {}, "role_index": 0}
    save2 = {"role": "button", "name": "Save", "attrs": {}, "role_index": 1}
    eid, _ = engine.apply_param(action="click", element_id="1", text=None, proxy_step=2,
                                action_uid="p1", elements={"1": save}, page_url=page)
    assert eid.startswith("invalid_")
    # 页面出现两个同名 Save → 无法确定重放哪一个 → 跳过，不改写第二个
    eid2, _ = engine.apply_param(action="click", element_id="5", text=None, proxy_step=3,
                                 action_uid="p2", elements={"1": save, "5": save2},
                                 page_url=page)
    assert eid2 == "5"
    assert any(e.get("reason") == "target_ambiguous" for e in cfg.episode.events)


def test_evaluator_hash_covers_submodules():
    from standard_agent.stage_c_pipeline import current_evaluator_version
    version = current_evaluator_version()
    assert version.get("package_sources", 0) >= 1
    assert len(version.get("package_sha256", "")) == 64


def test_runner_execution_loop_writes_status_and_stops(tmp_path, monkeypatch):
    """运行器执行循环：写 status、按 variant 校验、基础设施错误触发停止策略。

    用假 Popen 注入一份含真实基础设施错误的日志，不启动任何真实进程/模型。
    """
    import scripts.ep_run_schedule as runner
    from standard_agent.ep_schedule import build_schedule, write_schedule
    schedule = build_schedule(models=["m1"], architectures=["react"],
                              faults=["web_http_error"], tasks=[24], seeds=[1],
                              schedule_seed=9)
    path = write_schedule(schedule, tmp_path / "schedule.json")

    class FakeProc:
        def __init__(self, command, cwd=None, stdout=None, stderr=None,
                     text=None, start_new_session=None):
            self.pid = 4242
            self.returncode = 0
            stdout.write("Connection refused\nTraceback (most recent call last):\n")

        def wait(self, timeout=None): return 0
        def poll(self): return 0

    monkeypatch.setattr(runner.subprocess, "Popen", FakeProc)
    import sys as _sys
    monkeypatch.setattr(_sys, "argv", [
        "ep_run_schedule.py", "--schedule", str(path),
        "--experiment-root", str(tmp_path / "ep"),
        "--execute", "--allow-unfrozen", "--job-timeout-minutes", "1"])
    rc = runner.main()
    assert rc == 1                                        # 基础设施错误 → 停止本批
    statuses = list((tmp_path / "ep").rglob("*.status.json"))
    assert statuses
    first = json.loads(statuses[0].read_text())
    assert first["infrastructure_error"] is True
    assert first["variant"] in ("single", "repeat3")
    assert first["condition"] in ("control", "fault")
    assert first["architecture"] == "react"


# ==========================================================================
# 32. 机制错误停止 / 恢复 / 冻结严格化 / DOM 歧义
# ==========================================================================

def test_classify_log_exposes_mechanism_error_separately():
    from scripts.run_fault_matrix import classify_log
    # repeat3 缺结构化证据 → mechanism_error
    out = classify_log(_count_line(0), expect_fault=True, fault_variant="repeat3")
    assert out["mechanism_error"] is True
    # repeat3 有结构化证据且 0 剂量 → 合法未触发，不是机制错误
    import json as _json
    payload = {"variant": "repeat3", "window_steps": 5, "injections": []}
    ok = classify_log(_count_line(0) + "        fault_episode=" + _json.dumps(payload) + "\n",
                      expect_fault=True, fault_variant="repeat3")
    assert ok["mechanism_error"] is False and ok["fault_missing"] is False
    # 次数越界 → 机制错误
    over = {"variant": "repeat3", "window_steps": 5,
            "injections": [{"event": "injection", "proxy_step": s, "action_uid": f"a{s}"}
                           for s in (2, 3, 4, 5)]}
    bad = classify_log(_count_line(4) + "        fault_episode=" + _json.dumps(over) + "\n",
                       expect_fault=True, fault_variant="repeat3")
    assert bad["mechanism_error"] is True
    # single 的 0 次是"合法未触发"，不是机制错误
    single = classify_log(_count_line(0), expect_fault=True, fault_variant="single")
    assert single["mechanism_error"] is False


def _runner_with(monkeypatch, tmp_path, log_text, extra_argv=()):
    import scripts.ep_run_schedule as runner
    from standard_agent.ep_schedule import build_schedule, write_schedule
    schedule = build_schedule(models=["m1"], architectures=["react"],
                              faults=["web_http_error"], tasks=[24], seeds=[1],
                              schedule_seed=2)
    schedule["window_steps"] = 5
    path = write_schedule(schedule, tmp_path / "schedule.json")

    class FakeProc:
        def __init__(self, command, cwd=None, stdout=None, stderr=None,
                     text=None, start_new_session=None):
            self.pid = 4242
            self.returncode = 0
            stdout.write(log_text)

        def wait(self, timeout=None): return 0
        def poll(self): return 0

    monkeypatch.setattr(runner.subprocess, "Popen", FakeProc)
    import sys as _sys
    argv = ["ep_run_schedule.py", "--schedule", str(path),
            "--experiment-root", str(tmp_path / "ep"),
            "--execute", "--allow-unfrozen", *extra_argv]
    monkeypatch.setattr(_sys, "argv", argv)
    return runner


def test_runner_stops_on_mechanism_error(tmp_path, monkeypatch):
    # repeat3 缺机制日志（没有任何 fault_episode 行）→ 应停止且返回非零
    runner = _runner_with(monkeypatch, tmp_path,
                          "        injection_count=0 fault_seed=1 faults=[] steps=[]\n")
    assert runner.main() != 0
    statuses = list((tmp_path / "ep").rglob("*.status.json"))
    assert statuses
    assert json.loads(statuses[0].read_text())["mechanism_error"] is True


def test_runner_requires_empty_dir_and_supports_resume(tmp_path, monkeypatch):
    # 先跑一次（合法日志：single/control 无故障也合法）
    log = ("        injection_count=0 fault_seed=1 faults=[] steps=[]\n"
           "        fault_episode={\"variant\": \"repeat3\", \"window_steps\": 5, \"injections\": []}\n")
    runner = _runner_with(monkeypatch, tmp_path, log)
    assert runner.main() == 0
    statuses = list((tmp_path / "ep").rglob("*.status.json"))
    assert statuses
    # 再次执行、未加 --resume → 拒绝（保留旧产物）
    runner2 = _runner_with(monkeypatch, tmp_path, log)
    assert runner2.main() == 3
    # 加 --resume → 跳过已完成的 job
    runner3 = _runner_with(monkeypatch, tmp_path, log, extra_argv=("--resume",))
    assert runner3.main() == 0


def test_freeze_requires_readable_clean_git(tmp_path, monkeypatch):
    from scripts.ep_run_schedule import build_parser, check_freeze
    frozen = tmp_path / "frozen.json"
    frozen.write_text(json.dumps({"git_commit": "a" * 40, "git_dirty": False,
                                 "experiment_id": "ep-persistence-v1",
                                 "window_steps": 5, "variants": {"single": 1, "repeat3": 3},
                                 "target_policy_version": "semantic-target-lock-v1"}))
    args = build_parser().parse_args(["--schedule", str(tmp_path / "s.json"),
                                      "--experiment-root", str(tmp_path / "ep"),
                                      "--freeze", str(frozen)])
    # Git 状态不可读（dirty=None）→ 必须拒绝，而不是放行
    monkeypatch.setattr("scripts.ep_run_schedule.git_status",
                        lambda root: {"head": "a" * 40, "dirty": None, "branch": "b"})
    ok, note = check_freeze(args, {"experiment_id": "ep-persistence-v1",
                                   "window_steps": 5, "variants": ["single", "repeat3"]})
    assert ok is False and "不可确认" in note
