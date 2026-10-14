"""E-P 四臂端到端：走真实 `main.py` 接口，只 mock 模型与浏览器。

覆盖用户要求的一致性：身份（experiment_id/variant，含控制臂）、trace 命名、
剂量（injection_count 与 episode 一致）、评分绑定、配对与分析。不发付费 trial。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PAGE = "http://localhost:7770/some-product.html"
MODEL = "deepseek_v41_flash"
ARCH = "react"


def _install_fakes(monkeypatch, navigations: int):
    """替换浏览器与 agent 图；agent 只做 `navigations` 次同页导航后结束。

    同时把 `create_llm` 换成抛错：mock 缺失时**绝不回退到真实 API**。
    """
    import standard_agent.llm.provider as provider_mod
    import standard_agent.core.nodes as nodes_mod
    import standard_agent.core.graph as graph_mod

    def _no_real_llm(*args, **kwargs):
        raise AssertionError("测试不得调用真实模型（mock 缺失即失败）")

    # nodes.py 是 `from ... import create_llm`，只改 provider 不影响它的引用；
    # 两个位置都替换，确保任何路径都不会打到真实 API。
    monkeypatch.setattr(provider_mod, "create_llm", _no_real_llm, raising=False)
    monkeypatch.setattr(nodes_mod, "create_llm", _no_real_llm, raising=False)
    import standard_agent.environment.browser as browser_mod
    import standard_agent.tools.web_tools as web_tools
    from standard_agent.environment.browser import PageObservation

    class FakeEnv:
        def __init__(self, headless=True, extra_http_headers=None, har_path=None):
            self.url = "about:blank"
            self.har_path = har_path
            self.elements = {"1": {"role": "link", "name": "Next", "attrs": {},
                                   "role_index": 0}}

        def start(self): pass
        def stop(self): pass

        def _page(self, url):
            return PageObservation(url=url, ax_tree_text="[1] link 'Next'",
                                   element_map=self.elements)

        def observe(self): return self._page(self.url)

        def goto(self, url):
            self.url = url
            if self.har_path:
                Path(self.har_path).write_text('{"log": {"entries": []}}', encoding="utf-8")
            return self._page(url)

        def click(self, element_id): return self._page(self.url)

    monkeypatch.setattr(browser_mod, "HAS_PLAYWRIGHT", True, raising=False)
    monkeypatch.setattr(browser_mod, "SyncBrowserEnv", FakeEnv, raising=False)

    class StubApp:
        def invoke(self, state, config=None):
            proxy = web_tools._browser_env
            for _ in range(navigations):
                proxy.goto(state["url"])
            return {**state, "done": True, "answer": "ok",
                    "step_count": navigations, "llm_calls": navigations}

    monkeypatch.setattr(graph_mod, "build_graph", lambda architecture: StubApp(), raising=False)


def _run_arm(monkeypatch, variant: str, out_root: Path, navigations: int) -> int:
    import main as main_module
    _install_fakes(monkeypatch, navigations)
    argv = [
        "main.py", "--benchmark", "--site", "shopping", "--url", PAGE,
        "--task", "find the next page", "--task-id", "24",
        "--model-profile", MODEL, "--architecture", ARCH,
        "--max-steps", "20", "--trials", "1",
        "--fault-type", "web_http_error", "--fault-intensity", "high",
        "--fault-seed", "1", "--fault-injection-step", "2",
        "--experiment-id", "ep-persistence-v1", "--fault-variant", variant,
        "--fault-window-steps", "5",
        # 与矩阵一致：webarena_output_dir = <root>/<variant>/outputs/<model>/<arch>/<fault>_task<id>_seed<seed>
        "--webarena-output-dir", str(out_root / variant / "outputs" / MODEL / ARCH
                                     / "web_http_error_task24_seed1"),
    ]
    monkeypatch.setattr(sys, "argv", argv)
    return main_module.main()


def _arm_dir(out_root: Path, variant: str, condition: str) -> Path:
    # 臂目录名用 fault_label：故障臂是故障名，控制臂是 control
    label = "control" if condition == "control" else "web_http_error"
    return (out_root / variant / "outputs" / MODEL / ARCH
            / "web_http_error_task24_seed1" / "24" / f"{label}_seed_1")


def test_four_arm_end_to_end_through_main(tmp_path, monkeypatch):
    out_root = tmp_path / "ep"
    # single：1 次注入；repeat3：窗口内 3 次注入（4 次导航 → step2/3/4 注入）
    assert _run_arm(monkeypatch, "single", out_root, navigations=2) in (None, 0)
    assert _run_arm(monkeypatch, "repeat3", out_root, navigations=4) in (None, 0)

    # ---- 身份（含控制臂）与产物 ----
    for variant, expected_count in (("single", 1), ("repeat3", 3)):
        for condition in ("control", "fault"):
            arm = _arm_dir(out_root, variant, condition)
            assert arm.is_dir(), f"缺少 {arm}"
            record = json.loads((arm / "trial_record.json").read_text())
            assert record["experiment_id"] == "ep-persistence-v1"
            assert record["fault_variant"] == variant           # 控制臂也必须是同 variant
            assert record["condition"] == condition
            assert record["injection_count"] == (expected_count if condition == "fault" else 0)
            # trace 命名带 exp__variant（与记录一致）
            trace = record["paths"]["trace"]
            assert Path(trace).name.startswith(f"ep-persistence-v1__{variant}__")
            # 评分绑定已写入
            from standard_agent.stage_c_pipeline import evaluate_trial
            evaluate_trial(arm, task_id=24, config_path=None, evaluate=True,
                           evaluate_fn=lambda *a, **k: {
                               "status": "success", "official_success": condition == "control",
                               "evaluators_results": [{"evaluator_name": "Stub", "result": True}],
                               "error_msg": ""})
            written = json.loads((arm / "official_eval.json").read_text())
            assert "_evaluation_binding" in written

    # ---- 配对与分析（同一 block 的两个 variant 都齐全） ----
    from standard_agent.ep_analysis import ep_analysis
    from standard_agent.stage_c_pipeline import collect_rows
    rows = collect_rows(out_root, evaluate=False)
    analysis = ep_analysis(rows)
    assert analysis["n_blocks"] == 1                            # 同 (m,arch,fault,task,seed)
    entry = analysis["by_fault"]["web_http_error"]
    # control 臂官方成功、fault 臂官方失败 → 两个 variant 的 Δ 都是 1，D_f = 0
    assert entry["delta_single"]["task_equal_weighted"] == 1
    assert entry["delta_repeat3"]["task_equal_weighted"] == 1
    assert entry["D_f"]["task_equal_weighted"] == 0
    assert entry["D_f"]["n_blocks"] == 1


def test_control_arm_identity_flows_through_the_real_interface(tmp_path, monkeypatch):
    """控制臂不得被默认成 stage-c/single（旧 bug 的最小回归）。"""
    out_root = tmp_path / "ep"
    assert _run_arm(monkeypatch, "repeat3", out_root, navigations=4) in (None, 0)
    control = json.loads((_arm_dir(out_root, "repeat3", "control")
                          / "trial_record.json").read_text())
    assert control["fault_variant"] == "repeat3"
    assert control["experiment_id"] == "ep-persistence-v1"
    assert control["injection_count"] == 0


@pytest.mark.parametrize("architecture", ["react", "plan_execute"])
def test_end_to_end_covers_both_architectures(tmp_path, monkeypatch, architecture):
    """两个架构都走一遍真实 main.py（mock 浏览器/模型），确认身份与剂量一致。"""
    import main as _main
    # 覆盖架构参数
    orig = _main  # noqa: F841 - 仅为可读性

    out_root = tmp_path / f"ep_{architecture}"
    _install_fakes(monkeypatch, navigations=4)
    argv = [
        "main.py", "--benchmark", "--site", "shopping", "--url", PAGE,
        "--task", "find the next page", "--task-id", "24",
        "--model-profile", MODEL, "--architecture", architecture,
        "--max-steps", "20", "--trials", "1",
        "--fault-type", "web_http_error", "--fault-intensity", "high",
        "--fault-seed", "1", "--fault-injection-step", "2",
        "--experiment-id", "ep-persistence-v1", "--fault-variant", "repeat3",
        "--fault-window-steps", "5",
        "--webarena-output-dir", str(out_root / "repeat3" / "outputs" / MODEL
                                     / architecture / "web_http_error_task24_seed1"),
    ]
    monkeypatch.setattr(sys, "argv", argv)
    _main.main()

    fault = out_root / "repeat3" / "outputs" / MODEL / architecture \
        / "web_http_error_task24_seed1" / "24" / "web_http_error_seed_1"
    control = fault.parent / "control_seed_1"
    frecord = json.loads((fault / "trial_record.json").read_text())
    crecord = json.loads((control / "trial_record.json").read_text())
    assert frecord["architecture"] == architecture
    assert frecord["experiment_id"] == "ep-persistence-v1"
    assert frecord["fault_variant"] == "repeat3"
    assert crecord["fault_variant"] == "repeat3"
    assert frecord["injection_count"] == 3      # 窗口内三次
    assert crecord["injection_count"] == 0
    assert Path(frecord["paths"]["trace"]).name.startswith("ep-persistence-v1__repeat3__")
