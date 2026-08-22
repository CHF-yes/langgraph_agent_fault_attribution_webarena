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

    def test_agent_faults_module(self):
        from fault_injection import agent_faults
        self.assertTrue(callable(agent_faults.inject_state_misjudge))
        self.assertTrue(callable(agent_faults.inject_param_error))

    def test_agent_fault_flags_are_copied(self):
        from fault_injection import FaultConfig

        config = FaultConfig(
            intensity="low",
            agent_state_misjudge=True,
            agent_param_error=True,
        )
        self.assertTrue(config.agent_state_misjudge)
        self.assertTrue(config.agent_param_error)


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
