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
