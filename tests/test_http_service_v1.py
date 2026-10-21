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


# ==========================================================================
# 冻结执行门槛：未批准/不符必须在启动子进程之前停止
# ==========================================================================

def _freeze_payload(tmp_path, **overrides):
    from standard_agent.stage_c_pipeline import (EVALUATOR_ADAPTER_VERSION,
                                                 current_evaluator_version)
    import os, hashlib
    dataset = os.getenv("WEBARENA_DATASET")
    sha = hashlib.sha256(Path(dataset).read_bytes()).hexdigest() if dataset else None
    payload = {
        "experiment_id": EXPERIMENT_ID, "status": "frozen",
        "approved_for_paid_runs": True,
        "target_policy_version": "endpoint-path-segment-v1",
        "endpoint": {"scheme": "http", "host": "localhost", "port": 7770,
                     "path": "/catalogsearch/result"},
        "window_steps": 10, "max_steps": 20, "fault_intensity": "high",
        "fault_types": ["web_http_error"],
        "evaluator_adapter_version": EVALUATOR_ADAPTER_VERSION,
        "evaluator_version": current_evaluator_version(),
        "dataset_sha256": sha, "git_commit": "a" * 40,
    }
    payload.update(overrides)
    path = tmp_path / "freeze.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_freeze_gate_rejects_unapproved_and_mismatches(tmp_path, monkeypatch):
    import scripts.http_service_probe as probe

    class Diff:
        returncode = 0
        stdout = "docs/ep_http_service_v1_frozen.json\n"

    monkeypatch.setattr(probe, "git_status",
                        lambda root: {"head": "a" * 40, "dirty": False, "branch": "b"})
    monkeypatch.setattr(probe.subprocess, "run", lambda *a, **k: Diff())
    args = probe.build_parser().parse_args([
        "--experiment-root", str(tmp_path / "ep"), "--endpoint", ENDPOINT,
        "--task-id", "118", "--fault-window-steps", "10", "--max-steps", "20",
        "--freeze", str(_freeze_payload(tmp_path, approved_for_paid_runs=False))])
    endpoint = ServiceEndpoint.from_url(ENDPOINT)
    ok, note = probe.check_freeze(args, endpoint)
    assert ok is False and "未批准" in note

    for overrides, keyword in (
            ({"status": "candidate"}, "不是 frozen"),
            ({"endpoint": {"scheme": "http", "host": "localhost", "port": 7770,
                           "path": "/other"}}, "endpoint"),
            ({"window_steps": 5}, "window_steps"),
            ({"max_steps": 30}, "max_steps"),
            ({"dataset_sha256": "0" * 64}, "数据集")):
        args.freeze = str(_freeze_payload(tmp_path, **overrides))
        ok, note = probe.check_freeze(args, endpoint)
        assert ok is False and keyword in note, (overrides, note)

    # git diff 失败（例如冻结 commit 不存在）→ 拒绝，而不是当作通过
    class BadDiff:
        returncode = 1
        stdout = ""

    monkeypatch.setattr(probe.subprocess, "run", lambda *a, **k: BadDiff())
    args.freeze = str(_freeze_payload(tmp_path, git_commit="b" * 40))
    ok, note = probe.check_freeze(args, endpoint)
    assert ok is False and "git diff 失败" in note

    # 干净且完全匹配 → 通过
    monkeypatch.setattr(probe.subprocess, "run", lambda *a, **k: Diff())
    args.freeze = str(_freeze_payload(tmp_path))
    assert probe.check_freeze(args, endpoint)[0] is True


def test_execute_stops_before_spawning_when_freeze_is_not_approved(tmp_path, monkeypatch):
    import scripts.http_service_probe as probe
    spawned = []

    class Boom:
        def __init__(self, *a, **k):
            spawned.append(True)
            raise AssertionError("must not spawn a trial process")

    monkeypatch.setattr(probe.subprocess, "Popen", Boom)
    freeze = _freeze_payload(tmp_path, approved_for_paid_runs=False, status="candidate")
    default = dict(root=str(tmp_path / "ep"), endpoint=ENDPOINT, task="118",
                   arch="react", freeze=str(freeze), execute=True)
    monkeypatch.setattr(sys, "argv", [
        "http_service_probe.py", "--experiment-root", default["root"],
        "--endpoint", default["endpoint"], "--task-id", default["task"], "--architecture",
        default["arch"], "--fault-window-steps", "10", "--max-steps", "20",
        "--freeze", default["freeze"], "--execute"])
    rc = probe.main()
    assert rc == 2
    assert spawned == []                       # 未启动任何子进程


def test_execute_spawns_only_with_a_valid_freeze(tmp_path, monkeypatch):
    import scripts.http_service_probe as probe
    spawned = []

    class FakeProc:
        def __init__(self, command, cwd=None, stdout=None, stderr=None,
                     text=None, start_new_session=None):
            spawned.append(command)
            self.pid = 1
            self.returncode = 0

        def wait(self, timeout=None): return 0
        def poll(self): return 0

    class Diff:
        returncode = 0
        stdout = "docs/ep_http_service_v1_frozen.json\n"

    monkeypatch.setattr(probe, "git_status",
                        lambda root: {"head": "a" * 40, "dirty": False, "branch": "b"})
    monkeypatch.setattr(probe.subprocess, "run", lambda *a, **k: Diff())
    monkeypatch.setattr(probe.subprocess, "Popen", FakeProc)
    freeze = _freeze_payload(tmp_path)
    monkeypatch.setattr(sys, "argv", [
        "http_service_probe.py", "--experiment-root", str(tmp_path / "ep"),
        "--endpoint", ENDPOINT, "--task-id", "118", "--architecture", "react",
        "--fault-window-steps", "10", "--max-steps", "20",
        "--freeze", str(freeze), "--execute"])
    probe.main()
    assert spawned                            # 通过门槛后才启动
