"""最小 smoke tests — 不依赖外部网络 / LLM API。

运行方式:
    python -m unittest discover -s tests -v
    # 或
    pytest tests/ -v
"""

import os
import sys
import unittest

# 确保从项目根目录导入
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


class TestGraphBuild(unittest.TestCase):
    def test_build_graph(self):
        from standard_agent.core.graph import build_graph
        app = build_graph()
        self.assertIsNotNone(app)

    def test_supported_architectures_build(self):
        from standard_agent.core.graph import build_graph

        self.assertIsNotNone(build_graph("react"))
        self.assertIsNotNone(build_graph("plan_execute"))
        with self.assertRaises(ValueError):
            build_graph("unknown")

    def test_plan_steps_are_structured(self):
        from standard_agent.core.nodes import _parse_plan_steps

        steps = _parse_plan_steps(
            '{"steps":[{"id":9,"goal":"Search product","success_condition":"Result visible"}]}'
        )
        self.assertEqual(steps, [{
            "id": 1,
            "goal": "Search product",
            "success_condition": "Result visible",
        }])

    def test_agent_state_reducer(self):
        from standard_agent.core.state import add_action_history
        self.assertEqual(
            add_action_history([{"step": 1}], [{"step": 2}]),
            [{"step": 1}, {"step": 2}],
        )
        self.assertEqual(add_action_history(None, [{"step": 1}]), [{"step": 1}])


class TestTools(unittest.TestCase):
    def test_all_tools_count_and_names(self):
        from standard_agent.tools.web_tools import ALL_TOOLS
        names = sorted(t.name for t in ALL_TOOLS)
        self.assertEqual(names, [
            "click", "go_back", "go_forward", "goto", "hover",
            "scroll", "select_option", "stop", "type_text",
        ])

    def test_stop_does_not_mutate_page_task_state(self):
        from standard_agent.tools.web_tools import get_page_state, reset_page_state, stop

        reset_page_state()
        stop.invoke({"answer": "answer"})
        self.assertFalse(hasattr(get_page_state(), "task_done"))


class TestAriaParser(unittest.TestCase):
    def test_parse_aria_snapshot(self):
        from standard_agent.environment.browser import parse_aria_snapshot
        snapshot = (
            '- heading "Page Title" [level=1]\n'
            '- link "Home"\n'
            '- button "Add to Cart"\n'
            '- button "Add to Cart"\n'
            '- textbox "Search"\n'
        )
        text, element_map = parse_aria_snapshot(snapshot)
        self.assertIn("[1] heading 'Page Title'", text)
        self.assertEqual(len(element_map), 5)
        # 同 role 元素应带递增 role_index，用于精确定位
        self.assertEqual(element_map["3"]["role_index"], 0)
        self.assertEqual(element_map["4"]["role_index"], 1)
        self.assertEqual(element_map["5"]["role"], "textbox")

    def test_url_metadata_is_attached_to_link(self):
        from standard_agent.environment.browser import parse_aria_snapshot

        text, element_map = parse_aria_snapshot(
            "- link 'Project'\n  - /url '/a11y/project'"
        )
        self.assertNotIn("/url", text)
        self.assertEqual(len(element_map), 1)
        self.assertEqual(element_map["1"]["attrs"]["url"], "/a11y/project")

        _, live_style_map = parse_aria_snapshot(
            '- link "space — space"\n  - /url: /f/space'
        )
        self.assertEqual(len(live_style_map), 1)
        self.assertEqual(live_style_map["1"]["attrs"]["url"], "/f/space")

    def test_sync_browser_exposes_fallback_events(self):
        from standard_agent.environment.browser import SyncBrowserEnv

        env = SyncBrowserEnv()
        self.assertEqual(env.fallback_events, [])

    def test_colon_role_hint_does_not_become_button_name(self):
        from standard_agent.environment.browser import parse_aria_snapshot

        _, element_map = parse_aria_snapshot("- button: Sort by 'button'")
        self.assertEqual(element_map["1"]["role"], "button")
        self.assertEqual(element_map["1"]["name"], "Sort by")

        _, live_style_map = parse_aria_snapshot(
            '- \'button "Sort by: Submissions"\':'
        )
        self.assertEqual(live_style_map["1"]["role"], "button")
        self.assertEqual(live_style_map["1"]["name"], "Sort by: Submissions")

    def test_parse_aria_line_variants(self):
        from standard_agent.environment.browser import _parse_aria_line
        # 标准双引号
        self.assertEqual(
            _parse_aria_line('- button "Add to Cart"'),
            ("button", "Add to Cart", {}),
        )
        # 单引号
        self.assertEqual(
            _parse_aria_line("- link 'Home'"),
            ("link", "Home", {}),
        )
        # Magento 风格: role: 名称 "quoted" [name=...]
        self.assertEqual(
            _parse_aria_line('- button: 搜索 \'Search\' [name="Search"i]'),
            ("button", "Search", {"name": "Search"}),
        )
        # paragraph 冒号内容
        self.assertEqual(
            _parse_aria_line("- paragraph: long text content here"),
            ("paragraph", "long text content here", {}),
        )
        # 属性解析
        self.assertEqual(
            _parse_aria_line('- heading "Page Title" [level=1]'),
            ("heading", "Page Title", {"level": "1"}),
        )


class TestFaults(unittest.TestCase):
    def test_faults_import(self):
        import fault_injection
        from fault_injection.injector import FaultInjector
        from fault_injection.proxy import FaultProxy
        self.assertTrue(callable(FaultProxy))
        self.assertTrue(callable(FaultInjector))

    def test_fault_config_factories(self):
        from fault_injection import FaultConfig
        self.assertEqual(FaultConfig.off().intensity, "off")
        self.assertEqual(FaultConfig.low_all_web().web_timeout, True)
        self.assertEqual(FaultConfig.high_all().gitlab_quota, True)

    def test_fault_config_exposes_single_fault_metadata(self):
        from fault_injection import FaultConfig

        config = FaultConfig.single_fault("web_dom_missing", seed=7)
        self.assertEqual(config.enabled_faults, ("web_dom_missing",))
        self.assertEqual(config.fault_label, "web_dom_missing")

    def test_fixed_injection_step_triggers_once(self):
        from fault_injection import FaultConfig

        config = FaultConfig.single_fault(
            "web_dom_missing", intensity="high", seed=7, injection_step=2
        )
        config.set_execution_step(1)
        self.assertFalse(config.should_inject("web_dom_missing"))
        config.set_execution_step(2)
        self.assertTrue(config.should_inject("web_dom_missing"))
        self.assertFalse(config.should_inject("web_dom_missing"))
        config.set_execution_step(3)
        self.assertFalse(config.should_inject("web_dom_missing"))
        self.assertEqual(config.fault_layer, "observation")
        self.assertEqual(FaultConfig.off().fault_label, "control")
        entry = config.record_injection("web_dom_missing", {"removed_ids": ["1"]})
        self.assertEqual(entry["fault_layer"], "observation")
        self.assertEqual(entry["fault_seed"], 7)
        config.set_execution_step(4)
        entry = config.record_injection("web_dom_missing")
        self.assertEqual(entry["step"], 4)
        self.assertEqual(entry["injection_index"], 2)

        with self.assertRaises(ValueError):
            FaultConfig.single_fault("unknown_fault")

    def test_agent_faults_module(self):
        from fault_injection import agent_faults
        self.assertTrue(callable(agent_faults.inject_state_misjudge))
        self.assertTrue(callable(agent_faults.inject_param_error))

    def test_fault_taxonomy_classifies_recovery(self):
        from fault_injection.taxonomy import classify_behavior, catalog_rows

        labels = classify_behavior(
            fault_type="web_dom_missing",
            injection_log=[{"fault": "web_dom_missing", "step": 2}],
            action_history=[
                {"step": 1, "action": "click", "error": True},
                {"step": 2, "action": "goto", "error": False},
                {"step": 3, "action": "stop", "error": False},
            ],
            success=True,
            completed=True,
        )
        self.assertEqual(labels["behavior_category"], "observation_refreshed")
        self.assertEqual(labels["tolerance_layer"], "mechanism_level")
        self.assertGreater(labels["recovery_steps"], 0)
        self.assertTrue(any(row["fault_type"] == "web_timeout" for row in catalog_rows()))

    def test_agent_fault_flags_are_copied(self):
        from fault_injection import FaultConfig

        config = FaultConfig(
            intensity="low",
            agent_state_misjudge=True,
            agent_param_error=True,
        )
        self.assertTrue(config.agent_state_misjudge)
        self.assertTrue(config.agent_param_error)

    def test_fault_config_rejects_unknown_intensity(self):
        from fault_injection import FaultConfig

        with self.assertRaises(ValueError):
            FaultConfig(intensity="typo")

    def test_seeded_random_reset_replays_sequence(self):
        from fault_injection import SeededRandom

        rng = SeededRandom(42)
        first = [rng.randint(0, 100) for _ in range(3)]
        rng.reset()
        self.assertEqual(first, [rng.randint(0, 100) for _ in range(3)])

    def test_site_url_mapping_includes_cms_and_map(self):
        from run_baseline import resolve_start_url, site_for_url

        task = {"sites": ["cms"], "start_urls": ["__CMS__/wp-admin"]}
        url = resolve_start_url(task)
        self.assertEqual(url, "http://localhost:8080/wp-admin")
        self.assertEqual(site_for_url(url, task), "cms")

    def test_fault_injector_can_read_element_before_observation(self):
        from fault_injection import FaultConfig
        from fault_injection.injector import FaultInjector

        class Env:
            def click(self, element_id):
                raise AssertionError("click should not be reached in this test")

        injector = FaultInjector(Env(), FaultConfig.off())
        self.assertEqual(injector._get_element_name("1"), "")

    def test_benchmark_evaluator_overrides_completion_status(self):
        from fault_injection import FaultConfig, BenchmarkRunner, TrialResult

        runner = BenchmarkRunner(
            lambda *args: TrialResult(
                "task", "wrong", True, 1, 0.1, completed=True,
                answer="wrong answer",
            ),
            evaluator=lambda trial: "expected" in trial.answer,
        )
        runner.add_config("control", FaultConfig.off())
        runner.add_task("task", "find answer", "http://example")
        report = runner.run(trials_per_config=1)
        self.assertEqual(report["configs"]["control"].success_rate, 0)

    def test_benchmark_attaches_experiment_metadata(self):
        from fault_injection import FaultConfig, BenchmarkRunner, TrialResult

        runner = BenchmarkRunner(
            lambda *args: TrialResult("task", "wrong", True, 1, 0.1),
            experiment_id="exp_test",
            model_profile="model_a",
        )
        runner.add_config("control", FaultConfig.off())
        runner.add_config("dom", FaultConfig.single_fault("web_dom_missing", seed=9))
        runner.add_task("task", "find answer", "http://example")
        report = runner.run(trials_per_config=1)
        control = report["tasks"]["task"]["control"][0]
        fault = report["tasks"]["task"]["dom"][0]
        self.assertEqual(control.experiment_id, "exp_test")
        self.assertEqual(control.model_profile, "model_a")
        self.assertEqual(control.fault_type, "control")
        self.assertEqual(fault.fault_type, "web_dom_missing")
        self.assertEqual(fault.fault_layer, "observation")
        self.assertEqual(fault.fault_seed, 9)
        self.assertEqual(fault.to_dict()["experiment_id"], "exp_test")


class TestConfigSecurity(unittest.TestCase):
    def test_model_profiles_are_independent(self):
        from standard_agent.config import settings

        profiles = settings.get_model_profiles()
        self.assertEqual(set(profiles), {"deepseek", "gpt54", "gemini", "grok", "glm52"})
        self.assertTrue(all(profile.validate() for profile in profiles.values()))

    def test_no_hardcoded_api_key(self):
        # 确保密钥只来自 .env / 环境变量，不硬编码在源码中
        with open(os.path.join(PROJECT_ROOT, "standard_agent", "config.py"), "r") as f:
            source = f.read()
        self.assertNotIn("sk-", source)
        self.assertIn('os.getenv("OPENAI_API_KEY"', source)

    def test_webarena_credentials_are_environment_sourced(self):
        with open(os.path.join(PROJECT_ROOT, "standard_agent", "environment", "webarena_config.py"), "r") as f:
            source = f.read()
        self.assertIn('os.getenv("SHOPPING_AUTO_LOGIN"', source)
        self.assertNotIn("Password.123", source)
        self.assertNotIn("test1234", source)

    def test_health_check_does_not_require_credentials_by_default(self):
        from scripts.health_check import check_all_sites

        result = check_all_sites(require_auth=False, sites=["shopping"])
        self.assertTrue(result["ok"] or "error" in result)


class TestEvaluation(unittest.TestCase):
    def test_failure_classification_separates_evaluator_and_format_failures(self):
        from standard_agent.failure_classification import classify_trial

        evaluator_error = classify_trial({"official_success": False, "evaluator_status": "error"})
        self.assertEqual(evaluator_error["failure_category"], "evaluator_failure")
        self.assertFalse(evaluator_error["attributable_to_research"])

        format_error = classify_trial({
            "official_success": False,
            "evaluator_status": "failure",
            "evaluator_assertions": [{"actual_normalized": {"retrieved_data": ["1 commit"]}}],
        }, {"eval": [{
            "expected": {"retrieved_data": [1]},
            "results_schema": {"type": "array", "items": {"type": "number"}},
        }]})
        self.assertEqual(format_error["failure_category"], "answer_format_failure")
        self.assertFalse(format_error["attributable_to_research"])
    def test_official_evaluator_adapter_is_importable(self):
        from standard_agent.webarena_evaluator import evaluate_task_safe

        result = evaluate_task_safe(
            21,
            agent_response_path="/nonexistent/agent_response.json",
        )
        self.assertFalse(result["official_success"])
        self.assertEqual(result["status"], "error")

    def test_null_retrieval_schema_fallback_compares_response(self):
        import json
        import tempfile
        from pathlib import Path
        from standard_agent.webarena_evaluator import _evaluate_null_retrieval_schema

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "response.json"
            path.write_text(json.dumps({
                "task_type": "RETRIEVE",
                "status": "NOT_FOUND_ERROR",
                "retrieved_data": None,
            }))
            result = _evaluate_null_retrieval_schema(
                22, path, "Schema type must be 'array', got: 'null'"
            )
            self.assertIsNotNone(result)
            self.assertTrue(result["official_success"])

    def test_evaluation_is_shared_for_completed_and_answer(self):
        from standard_agent.evaluation import evaluate_answer

        self.assertEqual(
            evaluate_answer(True, "The price is $1299", ["1299"]),
            (True, True),
        )
        self.assertEqual(
            evaluate_answer(True, "Reached max steps (10)", ["10"]),
            (False, False),
        )
        self.assertEqual(
            evaluate_answer(True, "No matching value", []),
            (True, True),
        )


class TestWebArenaVerifiedAdapter(unittest.TestCase):
    def test_response_adapter_preserves_structured_retrieval(self):
        from standard_agent.webarena_verified import make_agent_response

        task = {
            "eval": [{"expected": {"task_type": "RETRIEVE"}}],
        }
        response = make_agent_response(
            task,
            completed=True,
            answer='[{"count": 1}]',
        )
        self.assertEqual(response["task_type"], "RETRIEVE")
        self.assertEqual(response["status"], "SUCCESS")
        self.assertEqual(response["retrieved_data"], [{"count": 1}])
        self.assertIsNone(response["error_details"])

    def test_response_adapter_preserves_unstructured_numeric_answer(self):
        from standard_agent.webarena_verified import make_agent_response

        task = {"eval": [{
            "results_schema": {"type": "array", "items": {"type": "number"}},
            "expected": {"task_type": "RETRIEVE"},
        }]}
        response = make_agent_response(
            task,
            completed=True,
            answer="Kilian made 1 commit on March 5, 2023.",
        )
        self.assertEqual(
            response["retrieved_data"],
            ["Kilian made 1 commit on March 5, 2023."],
        )

    def test_response_adapter_preserves_unstructured_string_array_answer(self):
        from standard_agent.webarena_verified import make_agent_response

        task = {"eval": [{
            "results_schema": {"type": "array", "items": {"type": "string"}},
            "expected": {"task_type": "RETRIEVE"},
        }]}
        response = make_agent_response(task, completed=True, answer="Dibbins, Catso")
        self.assertEqual(response["retrieved_data"], ["Dibbins, Catso"])

    def test_response_adapter_marks_incomplete_tasks(self):
        from standard_agent.webarena_verified import make_agent_response

        task = {"eval": [{"expected": {"task_type": "NAVIGATE"}}]}
        response = make_agent_response(task, completed=False, answer="timeout")
        self.assertEqual(response["task_type"], "NAVIGATE")
        self.assertEqual(response["status"], "UNKNOWN_ERROR")
        self.assertIsNone(response["retrieved_data"])
        self.assertEqual(response["error_details"], "timeout")

    def test_response_adapter_maps_explicit_not_found(self):
        from standard_agent.webarena_verified import make_agent_response

        task = {"eval": [{"expected": {
            "task_type": "RETRIEVE",
            "status": "not_found_error",
            "retrieved_data": None,
        }}]}
        response = make_agent_response(
            task,
            completed=True,
            answer="No reviewer on the current page mentions an under water photo.",
        )
        self.assertEqual(response["status"], "NOT_FOUND_ERROR")
        self.assertIsNone(response["retrieved_data"])
        self.assertIsNotNone(response["error_details"])

    def test_response_adapter_does_not_read_expected_status(self):
        from standard_agent.webarena_verified import make_agent_response

        task = {"eval": [{"expected": {
            "task_type": "RETRIEVE",
            "status": "success",
            "retrieved_data": ["actual"],
        }}]}
        response = make_agent_response(
            task, completed=True, answer="No matching reviewer was found."
        )
        self.assertEqual(response["status"], "NOT_FOUND_ERROR")
        self.assertIsNone(response["retrieved_data"])

    def test_numeric_queries_require_structured_output(self):
        from standard_agent.core.nodes import _output_format_context

        self.assertIn("ONLY valid JSON", _output_format_context(
            "How many commits did Kilian make on March 5?"
        ))

    def test_response_adapter_does_not_mark_unrelated_text_not_found(self):
        from standard_agent.webarena_verified import make_agent_response

        task = {"eval": [{"expected": {
            "task_type": "RETRIEVE",
            "status": "not_found_error",
            "retrieved_data": None,
        }}]}
        response = make_agent_response(task, completed=True, answer="Dibbins")
        self.assertEqual(response["status"], "SUCCESS")
        self.assertEqual(response["retrieved_data"], ["Dibbins"])


class TestTrace(unittest.TestCase):
    def test_trace_roundtrip(self):
        from standard_agent.core.trace import append_trace, get_trace_path, make_entry
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            self.addCleanup(lambda: os.environ.pop("TRACE_DIR", None))
            os.environ["TRACE_DIR"] = tmp
            entry = make_entry(
                "tid1", step=1, task="t", url="http://x",
                thought="think", action="click", args={"element_id": "1"},
                observation="ok", error=False,
            )
            append_trace("tid1", entry)
            path = get_trace_path("tid1")
            self.assertTrue(path.exists())
            import json
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.loads(f.readline())
            self.assertEqual(loaded["thread_id"], "tid1")
            self.assertEqual(loaded["action"], "click")
            self.assertEqual(loaded["step"], 1)

    def test_trace_never_raises_on_bad_dir(self):
        from standard_agent.core.trace import append_trace
        self.addCleanup(lambda: os.environ.pop("TRACE_DIR", None))
        os.environ["TRACE_DIR"] = "/nonexistent-dir-xyz/123"
        # 不应抛异常
        append_trace("tid2", {"step": 1})


if __name__ == "__main__":
    unittest.main()
