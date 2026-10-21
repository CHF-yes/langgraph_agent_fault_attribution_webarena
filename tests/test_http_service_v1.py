"""Service-level HTTP persistence (ep-http-service-v1) offline tests.

Fault object: a frozen search endpoint. Query values and extra parameters must not
affect matching; path matching is segment-boundary; non-search pagination is clean.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

from fault_injection.config import FaultConfig
from fault_injection.http_service import (
    EXPERIMENT_ID, ServiceEndpoint, ServicePersistenceEngine,
    classify_service_episode,
)
from fault_injection.injector import FaultInjector

ENDPOINT = "http://localhost:7770/catalogsearch/result/"
SEARCH = "http://localhost:7770/catalogsearch/result/?q=mouth+guard"


class Browser:
    def __init__(self):
        self.url = "about:blank"

    def observe(self):
        from standard_agent.environment.browser import PageObservation
        return PageObservation(url=self.url, ax_tree_text="[1] link 'Search'",
                               element_map={"1": {"role": "link", "name": "Search"}})

    def goto(self, url):
        self.url = url
        return self.observe()


def proxy(variant="persistent", *, endpoint=ENDPOINT, intensity="high", **kwargs):
    config = FaultConfig.single_fault(
        "web_http_error", intensity=intensity, seed=1, experiment_id=EXPERIMENT_ID,
        fault_variant=variant, fault_window_steps=5,
        fault_target_endpoint=ServiceEndpoint.from_url(endpoint).spec(), **kwargs)
    return FaultInjector(Browser(), config)


def evidence(injector):
    ep = injector.config.episode
    return {"experiment_id": EXPERIMENT_ID, "variant": ep.variant,
            "window_steps": ep.window_steps, "snapshot": ep.snapshot(),
            "events": list(ep.events),
            "injections": [e for e in ep.events if e["event"] == "injection"]}


def test_endpoint_matching_is_deterministic():
    ep = ServiceEndpoint.from_url(ENDPOINT)
    assert ep.matches(SEARCH)[0]
    assert ep.matches("http://localhost:7770/catalogsearch/result/?q=night+guard")[0]
    assert ep.matches("http://localhost:7770/catalogsearch/result/?q=night+guard&p=2")[0]
    assert ep.matches("http://localhost:7770/catalogsearch/result/?q=x&sort=price")[0]
    assert ep.matches("http://localhost:7770/catalogsearch/result/?q=x#frag")[0]
    # scheme / host / port / path boundary
    assert not ep.matches("https://localhost:7770/catalogsearch/result/?q=x")[0]
    assert not ep.matches("http://localhost:7771/catalogsearch/result/?q=x")[0]
    assert not ep.matches("http://other/catalogsearch/result/?q=x")[0]
    assert not ep.matches("http://localhost:7770/catalogsearch/resulting?q=x")[0]
    # non-search pagination stays clean
    assert not ep.matches("http://localhost:7770/reviews?p=2")[0]
    # /search, /search/... same endpoint; /searching excluded
    search = ServiceEndpoint.from_url("http://localhost:9999/search")
    assert search.matches("http://localhost:9999/search?q=x")[0]
    assert search.matches("http://localhost:9999/search/advanced?q=x")[0]
    assert not search.matches("http://localhost:9999/searching?q=x")[0]


def test_query_change_and_search_pagination_stay_faulted():
    p = proxy()
    p.goto("http://localhost:7770/")           # bootstrap, before arming
    p.arm_http_fault()
    first = p.goto(SEARCH)
    assert "unavailable" in first.ax_tree_text
    second = p.goto("http://localhost:7770/catalogsearch/result/?q=night+guard")
    assert "unavailable" in second.ax_tree_text          # 换 query 仍是同一服务
    third = p.goto("http://localhost:7770/catalogsearch/result/?q=night+guard&p=2")
    assert "unavailable" in third.ax_tree_text           # 搜索端点分页仍受影响
    reviews = p.goto("http://localhost:7770/reviews?p=2")
    assert "unavailable" not in reviews.ax_tree_text     # 非搜索分页正常
    assert p.config.episode.injections == 3


def test_single_injects_once_and_releases():
    p = proxy("single")
    p.goto("http://localhost:7770/")
    p.arm_http_fault()
    assert "unavailable" in p.goto(SEARCH).ax_tree_text
    # 新动作（换 query）在 single 下恢复正常
    assert "unavailable" not in p.goto(
        "http://localhost:7770/catalogsearch/result/?q=night+guard").ax_tree_text
    assert p.config.episode.injections == 1
    assert p.config.episode.released == "single_completed"


def test_window_release_and_control_arm():
    p = proxy()
    p.goto("http://localhost:7770/")
    p.arm_http_fault()
    for q in ("a", "b", "c", "d", "e"):
        p.goto(f"http://localhost:7770/catalogsearch/result/?q={q}")
    # t0=2, window=5 → 窗口末步 6；第 7 步释放并恢复
    after = p.goto("http://localhost:7770/catalogsearch/result/?q=after")
    assert p.config.episode.released == "window_expired"
    assert "unavailable" not in after.ax_tree_text
    control = proxy(intensity="off")
    control.goto("http://localhost:7770/")
    control.arm_http_fault()
    assert "unavailable" not in control.goto(SEARCH).ax_tree_text
    assert control.config.episode.injections == 0


def test_service_evidence_validates_and_detects_tampering():
    p = proxy()
    p.goto("http://localhost:7770/")
    p.arm_http_fault()
    p.goto(SEARCH)
    p.goto("http://localhost:7770/catalogsearch/result/?q=night+guard")
    data = evidence(p)
    assert classify_service_episode(data)["valid"]

    bad_target = copy.deepcopy(data)
    bad_target["snapshot"]["target"]["path"] = "/other"
    assert not classify_service_episode(bad_target)["valid"]

    bad_release = copy.deepcopy(data)
    bad_release["snapshot"]["released"] = "single_completed"
    assert not classify_service_episode(bad_release)["valid"]


def test_service_config_validation():
    with pytest.raises(ValueError):
        FaultConfig.single_fault("web_http_error", experiment_id=EXPERIMENT_ID,
                                 fault_variant="persistent", intensity="high")
    with pytest.raises(ValueError):
        FaultConfig.single_fault("web_http_error", experiment_id=EXPERIMENT_ID,
                                 fault_variant="persistent", intensity="high",
                                 injection_step=2,
                                 fault_target_endpoint=ServiceEndpoint.from_url(ENDPOINT).spec())
    with pytest.raises(ValueError):
        FaultConfig.single_fault("web_dom_missing", experiment_id=EXPERIMENT_ID,
                                 fault_variant="persistent", intensity="high",
                                 fault_target_endpoint=ServiceEndpoint.from_url(ENDPOINT).spec())


def test_service_probe_plan_is_four_arms_and_does_not_execute(tmp_path):
    import subprocess
    root = Path(__file__).resolve().parents[1]
    out = tmp_path / "plan.json"
    env = dict(**__import__("os").environ)
    env["PYTHONPATH"] = str(root) + ":" + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "scripts/http_service_probe.py",
         "--experiment-root", str(tmp_path / "ep"), "--endpoint", ENDPOINT,
         "--task-id", "118", "--architecture", "react"],
        cwd=root, env=env, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-2000:]
    plan = json.loads(proc.stdout)
    assert plan["plan_only"] is True and plan["trials"] == 4
    variants = {(c[c.index("--fault-variant") + 1], c[c.index("--condition") + 1])
                for c in plan["commands"]}
    assert variants == {("single", "control"), ("single", "fault"),
                        ("persistent", "control"), ("persistent", "fault")}
    assert all("--fault-target-endpoint" in c for c in plan["commands"])
    assert all("--fault-injection-step" not in c for c in plan["commands"])
    assert not (tmp_path / "ep").exists()        # 计划模式不写文件、不启动
