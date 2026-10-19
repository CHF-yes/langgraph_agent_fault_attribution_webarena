"""HTTP v2 offline tests: real CLI/artifact path, fake browser and no model/network."""

import copy
import json
import sys
from pathlib import Path

import pytest

from fault_injection.config import FaultConfig
from fault_injection.http_persistence import EXPERIMENT_ID, classify_episode
from fault_injection.injector import FaultInjector

PAGE = "http://example.test/resource?a=1"
OTHER = "http://example.test/alternative"


class Browser:
    def __init__(self):
        self.url = "about:blank"

    def observe(self):
        from standard_agent.environment.browser import PageObservation
        return PageObservation(url=self.url, ax_tree_text="[1] link 'Alternative'",
                               element_map={"1": {"role": "link", "name": "Alternative"}})

    def goto(self, url):
        self.url = url
        return self.observe()

    def click(self, element_id):
        if element_id == "navigate":
            self.url = PAGE
        return self.observe()

    def scroll(self, direction):
        return self.observe()


def proxy(variant="persistent", **kwargs):
    config = FaultConfig.single_fault(
        "web_http_error", intensity="high", seed=1, experiment_id=EXPERIMENT_ID,
        fault_variant=variant, fault_window_steps=5, **kwargs)
    return FaultInjector(Browser(), config)


def evidence(injector):
    ep = injector.config.episode
    return {"experiment_id": EXPERIMENT_ID, "variant": ep.variant,
            "window_steps": ep.window_steps, "snapshot": ep.snapshot(),
            "injections": [e for e in ep.events if e["event"] == "injection"]}


def test_bootstrap_and_passive_reads_cannot_activate_the_fault():
    p = proxy()
    assert "unavailable" not in p.goto(PAGE).ax_tree_text
    p.arm_http_fault()
    p.observe()
    p.get_obs()
    p.scroll("down")
    p.click("same_page")
    assert p.config.episode.injections == 0
    p.click("navigate")  # Same URL still is not a navigation event.
    assert p.config.episode.injections == 0
    p.goto(PAGE)         # An explicit reload does qualify.
    assert p.config.episode.t0 == 5
    assert p.config.episode.injections == 1


def test_first_navigation_can_be_a_click_and_late_in_the_trajectory():
    p = proxy()
    p.goto(OTHER)
    p.arm_http_fault()
    for _ in range(7):
        p.scroll("down")
    result = p.click("navigate")
    assert "unavailable" in result.ax_tree_text
    assert p.config.episode.t0 == 9
    assert p.config.episode.target["page_url"] == PAGE


def test_fragment_only_goto_does_not_activate_first_fault():
    p = proxy()
    p.goto(PAGE)
    p.arm_http_fault()
    p.goto(PAGE + "#section")
    assert p.config.episode.injections == 0
    p.goto(PAGE + "#section")  # Explicit same-URL reload now qualifies.
    assert p.config.episode.injections == 1


@pytest.mark.parametrize("variant,count", [("single", 1), ("persistent", 5)])
def test_refreshes_and_new_reads_preserve_fault_but_do_not_double_count(variant, count):
    p = proxy(variant)
    p.goto(PAGE)
    p.arm_http_fault()
    first = p.goto(PAGE)
    for _ in range(3):
        assert p.observe()["page_content"] == first.ax_tree_text
        assert p.get_obs().ax_tree_text == first.ax_tree_text
    assert len(p.config.log) == 1
    assert p.config.log[0]["fault_layer"] == p.config.fault_layer == "observation"
    assert first.url == PAGE and not first.element_map
    for _ in range(4):
        p.goto(PAGE)
    assert len(p.config.log) == p.config.episode.injections == count
    assert p.goto(PAGE).ax_tree_text == "[1] link 'Alternative'"  # t0+5 releases
    assert classify_episode(evidence(p), expected_window=5)["valid"]


def test_target_is_not_replaced_when_agent_uses_an_alternative():
    p = proxy()
    p.goto(OTHER)
    p.arm_http_fault()
    p.goto(PAGE + "#section")
    assert "unavailable" not in p.goto(OTHER).ax_tree_text
    assert "unavailable" not in p.goto(PAGE.replace("a=1", "a=2")).ax_tree_text
    assert "unavailable" in p.goto(PAGE).ax_tree_text
    assert p.config.episode.injections == 2
    assert p.config.episode.target["page_url"] == PAGE
    assert any(e.get("reason") == "off_target" for e in p.get_exposure_log())


def test_url_filter_waits_without_faulting_other_resources():
    p = proxy(fault_target_url=PAGE + "#ignored")
    p.goto(OTHER)
    p.arm_http_fault()
    p.goto(OTHER)
    p.goto("http://example.test/unrelated")
    assert p.config.episode.injections == 0
    p.goto(PAGE)
    assert p.config.episode.injections == 1


@pytest.mark.parametrize("disabled", ["off", "flag"])
def test_controls_and_disabled_configs_never_inject(disabled):
    p = proxy()
    if disabled == "off":
        p.config.intensity = "off"
    else:
        p.config.enabled = False
    p.goto(PAGE)
    p.arm_http_fault()
    for _ in range(8):
        assert "unavailable" not in p.goto(PAGE).ax_tree_text
    assert p.config.log == []
    assert p.config.episode.target is None


def test_reset_clears_target_window_arming_and_exposures():
    p = proxy()
    p.goto(PAGE)
    p.arm_http_fault()
    p.goto(PAGE)
    first = p.config.log[0]["detail"]["error_type"]
    p.reset()
    assert p.get_exposure_log() == []
    assert p.config.episode.target is None and not p.config.episode.armed
    p.goto(OTHER)
    p.arm_http_fault()
    p.goto(OTHER)
    assert p.config.log[0]["detail"]["error_type"] == first
    assert p.config.episode.target["page_url"] == OTHER


@pytest.mark.parametrize("kwargs", [
    {"fault_variant": "repeat3"}, {"injection_step": 2}, {"fault_window_steps": 0},
    {"web_dom_missing": True}, {"fault_target_url": "about:blank"},
])
def test_v2_rejects_incompatible_protocol_settings(kwargs):
    values = dict(intensity="high", experiment_id=EXPERIMENT_ID,
                  fault_variant="persistent", web_http_error=True)
    values.update(kwargs)
    with pytest.raises(ValueError):
        FaultConfig(**values)


@pytest.mark.parametrize("corruption", ["target", "window", "duplicate", "snapshot", "variant"])
def test_mechanism_validation_rejects_corrupt_evidence(corruption):
    p = proxy()
    p.goto(PAGE)
    p.arm_http_fault()
    p.goto(PAGE)
    p.goto(PAGE)
    data = copy.deepcopy(evidence(p))
    if corruption == "target":
        data["injections"][1]["target"]["page_url"] = OTHER
    elif corruption == "window":
        data["injections"][1]["proxy_step"] = 99
    elif corruption == "duplicate":
        data["injections"][1]["action_uid"] = data["injections"][0]["action_uid"]
    elif corruption == "snapshot":
        data["snapshot"]["injections"] = 0
    else:
        data["variant"] = "repeat3"
    assert not classify_episode(data)["valid"]


def test_classifier_keeps_untriggered_trials_and_detects_control_contamination():
    from scripts.run_fault_matrix import classify_log
    p = proxy("single")
    p.goto(PAGE)
    p.arm_http_fault()
    payload = evidence(p)
    result = classify_log("fault_episode=" + json.dumps(payload),
                          experiment_id=EXPERIMENT_ID, fault_variant="single")
    assert not result["mechanism_error"] and not result["fault_invalid"]
    p.goto(PAGE)
    result = classify_log("fault_episode=" + json.dumps(evidence(p)), expect_fault=False,
                          experiment_id=EXPERIMENT_ID, fault_variant="single")
    assert result["mechanism_error"]
    assert classify_log("", experiment_id=EXPERIMENT_ID)["mechanism_error"]


@pytest.fixture
def dataset(tmp_path, monkeypatch):
    data = [{"task_id": 24, "sites": ["shopping"], "start_url": PAGE,
             "intent": "Visit the resource", "eval": [{"evaluator": "AgentResponseEvaluator",
                 "results_schema": {"type": "null"},
                 "expected": {"task_type": "navigate", "status": "SUCCESS", "retrieved_data": None}}]}]
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps(data))
    monkeypatch.setenv("WEBARENA_DATASET", str(path))
    return path


@pytest.mark.parametrize("architecture", ["react", "plan_execute"])
def test_real_cli_four_arms_and_evaluation_binding(tmp_path, monkeypatch, dataset, architecture):
    import main
    from test_ep_end_to_end import _install_fakes
    from standard_agent.config import settings
    from standard_agent.stage_c_pipeline import evaluate_trial, collect_rows
    _install_fakes(monkeypatch, navigations=6)
    monkeypatch.setattr(settings, "validate", lambda *a, **k: True)
    monkeypatch.setenv("MODEL_DEEPSEEK_V41_FLASH_API_KEY", "offline-test-not-a-real-key")
    monkeypatch.setenv("MODEL_DEEPSEEK_V41_FLASH_BASE_URL", "http://offline.test")
    monkeypatch.setenv("MODEL_DEEPSEEK_V41_FLASH_NAME", "offline-fake-model")
    for variant, count in [("single", 1), ("persistent", 5)]:
        root = tmp_path / variant / "outputs" / "m" / architecture / "web_http_error_task24_seed1"
        monkeypatch.setattr(sys, "argv", [
            "main.py", "--benchmark", "--site", "shopping", "--url", PAGE,
            "--task", "Visit resource", "--task-id", "24", "--model-profile", "deepseek_v41_flash",
            "--architecture", architecture, "--max-steps", "20", "--trials", "1",
            "--fault-type", "web_http_error", "--fault-intensity", "high", "--fault-seed", "1",
            "--experiment-id", EXPERIMENT_ID, "--fault-variant", variant,
            "--fault-window-steps", "5", "--webarena-output-dir", str(root)])
        main.main()
        for condition in ("control", "fault"):
            arm = root / "24" / ("control_seed_1" if condition == "control" else "web_http_error_seed_1")
            record = json.loads((arm / "trial_record.json").read_text())
            data = json.loads((arm / "fault_evidence.json").read_text())
            assert record["experiment_id"] == EXPERIMENT_ID
            assert record["fault_variant"] == variant
            assert record["run_config"]["injection_step"] is None
            assert record["run_config"]["mechanism"] == "synthetic_http_observation_replacement"
            assert Path(record["paths"]["fault_evidence"]).exists()
            assert record["injection_count"] == (count if condition == "fault" else 0)
            assert classify_episode(data, expected_variant=variant, expected_window=5)["valid"]
            bootstrap_reason = "control_or_disabled" if condition == "control" else "not_armed"
            assert data["exposures"][0]["reason"] == bootstrap_reason
            evaluate_trial(arm, task_id=24, config_path=None, evaluate=True,
                           evaluate_fn=lambda *a, **k: {"status": "success", "official_success": True,
                               "evaluators_results": [{"evaluator_name": "OfflineStub", "result": True}],
                               "error_msg": ""})
            assert "_evaluation_binding" in json.loads((arm / "official_eval.json").read_text())
            from argparse import Namespace
            from scripts.http_persistence_probe import validate_trial_artifacts
            item = {"trial_dir": str(arm), "job": {
                "variant": variant, "condition": condition, "model_profile": "deepseek_v41_flash",
                "architecture": architecture, "task_id": 24, "seed": 1}}
            check_args = Namespace(max_steps=20, fault_window_steps=5, fault_target_url=None)
            assert validate_trial_artifacts(item, check_args) == []
    rows = collect_rows(tmp_path, evaluate=False)
    assert len(rows) == 4
    assert all(r["experiment_id"] == EXPERIMENT_ID for r in rows)


def test_probe_plans_four_separate_arms_without_executing(tmp_path, monkeypatch, dataset):
    from scripts import http_persistence_probe as probe
    args = probe.build_parser().parse_args(["--experiment-root", str(tmp_path / "fresh")])
    schedule, planned = probe.build_plan(args)
    assert len(planned) == 4 and schedule["experiment_id"] == EXPERIMENT_ID
    assert len({item["log_path"] for item in planned}) == 4
    assert {(p["job"]["variant"], p["job"]["condition"]) for p in planned} == {
        (v, c) for v in ("single", "persistent") for c in ("control", "fault")}
    assert all("--fault-injection-step" not in p["command"] for p in planned)
    assert all(p["job"]["window_steps"] == 10 for p in planned)
    monkeypatch.setattr(sys, "argv", ["probe", "--experiment-root", str(tmp_path / "fresh")])
    monkeypatch.setattr(probe, "run_planned", lambda *a, **k: pytest.fail("plan must not run trials"))
    assert probe.main() == 0
    assert not (tmp_path / "fresh").exists()


def test_probe_stops_after_first_arm_with_missing_artifacts(tmp_path, monkeypatch, dataset):
    from scripts import http_persistence_probe as probe
    from scripts import ep_run_schedule as runner
    args = probe.build_parser().parse_args(["--experiment-root", str(tmp_path / "fresh")])
    args.resume = False
    _, planned = probe.build_plan(args)
    calls = []
    p = proxy("single")
    p.goto(PAGE)
    p.arm_http_fault()

    class Process:
        pid = 12345
        returncode = 0

        def __init__(self, command, **kwargs):
            calls.append(command)
            kwargs["stdout"].write("fault_episode=" + json.dumps(evidence(p)) + "\n")

        def wait(self, **kwargs):
            return 0

    monkeypatch.setattr(runner.subprocess, "Popen", Process)
    assert runner.run_planned(args, planned, "debug", EXPERIMENT_ID,
                              verify_trial=probe.validate_trial_artifacts) == 2
    assert len(calls) == 1
    status = json.loads(Path(planned[0]["status_path"]).read_text())
    assert status["mechanism_error"] and status["artifact_errors"]


def test_probe_refuses_to_overwrite_existing_runs(tmp_path, monkeypatch, dataset):
    from scripts import http_persistence_probe as probe
    root = tmp_path / "used"
    (root / "single" / "run").mkdir(parents=True)
    (root / "single" / "run" / "old.log").write_text("preserve me")
    monkeypatch.setattr(sys, "argv", ["probe", "--experiment-root", str(root), "--execute"])
    monkeypatch.setattr(probe, "run_planned", lambda *a, **k: pytest.fail("must not execute"))
    assert probe.main() == 3
    assert (root / "single" / "run" / "old.log").read_text() == "preserve me"
