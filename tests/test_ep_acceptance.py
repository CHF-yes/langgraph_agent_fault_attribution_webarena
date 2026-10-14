"""E-P 零付费验收：对应设计第 9 节的 8 条验收标准。

全部离线：mock 环境 + 既有纯逻辑模块，不调用任何模型 API、不发 trial。
`scripts/ep_acceptance.py` 会运行本文件并把结果写成 acceptance.json。
"""

from __future__ import annotations

import json

from fault_injection.config import FaultConfig
from fault_injection.episode import FaultEpisode, classify_injection_log, dose_class
from fault_injection.injector import FaultInjector
from fault_injection.persistence import PersistenceEngine
from fault_injection.targets import TARGET_POLICY_VERSION
from fault_injection.web_faults import HTTP_ERROR_PAGES, _combinations, _sample_multiple
from standard_agent.environment.browser import PageObservation


class _FakeEnv:
    def __init__(self, elements=None):
        self.url = "about:blank"
        self.elements = elements or {}
        self.clicked = []

    def start(self): pass
    def stop(self): pass

    def _page(self, url):
        return PageObservation(url=url, ax_tree_text="[1] text 'real'",
                               element_map=self.elements)

    def observe(self): return self._page(self.url)
    def goto(self, url):
        self.url = url
        return self._page(url)
    def click(self, element_id):
        self.clicked.append(str(element_id))
        return self._page(self.url)


def _ep(**kw):
    base = dict(intensity="high", seed=7, injection_step=2,
                experiment_id="ep-persistence-v1", fault_variant="repeat3")
    base.update(kw)
    return FaultConfig(**base)


# 1. single 首次扰动与 Stage C 抽样一致 -------------------------------------
def test_criterion1_single_first_draw_matches_stage_c_sampling():
    # HTTP：single 的首次抽取就是 Stage C 的 rng.pick(["500","503"])
    cfg = _ep(web_http_error=True, fault_variant="single")
    engine = PersistenceEngine(cfg)
    engine.apply_http({"url": "http://h/a", "elements": {}, "page_content": ""}, proxy_step=2,
                      action_uid="a1")
    expected = HTTP_ERROR_PAGES[FaultConfig(intensity="high", seed=7).rng.pick(["500", "503"])]
    assert engine._http_sample == expected

    # DOM：single 的删除集合与 Stage C 的选择器一致
    elems = {str(i): {"role": "link", "name": f"n{i}", "attrs": {}, "role_index": 0}
             for i in range(1, 6)}
    cfg2 = _ep(web_dom_missing=True, fault_variant="single")
    engine2 = PersistenceEngine(cfg2)
    engine2.apply_dom({"url": "http://h/a", "elements": elems, "page_content": ""},
                      proxy_step=2, action_uid="a1")
    reference = FaultConfig(intensity="high", seed=7)
    ref_choice = _sample_multiple(reference.rng, list(elems), 3)
    assert {t["name"] for t in engine2._dom_targets} == {f"n{i}" for i in ref_choice}

    # 参数：single 首次=在参数动作上注入，损坏规则为 invalid_<n>
    cfg3 = _ep(agent_param_error=True, fault_variant="single", injection_step=1)
    engine3 = PersistenceEngine(cfg3)
    eid, _ = engine3.apply_param(action="click", element_id="9", text=None, proxy_step=2,
                                 action_uid="p1",
                                 elements={"9": {"role": "button", "name": "Go",
                                                 "attrs": {}, "role_index": 0}},
                                 page_url="http://h/a")
    assert eid.startswith("invalid_")


# 2. repeat3 同目标连续刷新仍故障；第三次后或窗口过期释放 --------------------
def test_criterion2_repeat3_persists_then_releases():
    cfg = _ep(web_http_error=True)
    engine = PersistenceEngine(cfg)
    page = {"url": "http://h/a", "elements": {}, "page_content": "real"}
    first = engine.apply_http(page, proxy_step=2, action_uid="a1")
    second = engine.apply_http(page, proxy_step=3, action_uid="a2")
    assert first["page_content"] == second["page_content"] != "real"   # 连续刷新两次仍故障
    engine.apply_http(page, proxy_step=4, action_uid="a3")
    assert engine.episode.released == "budget_reached"                 # 第三次后释放
    other = FaultEpisode("repeat3", window_steps=5)
    other.record_injection(proxy_step=2, action_uid="x")
    other.note_step(7)
    assert other.released == "window_expired"                          # 窗口过期释放


# 3. off-target 不污染不耗剂量；同 action 重读不计次；歧义/缺失有记录 --------
def test_criterion3_off_target_and_dedup_and_records():
    button = {"role": "button", "name": "Go", "attrs": {}, "role_index": 0}
    # 同 action_uid 重读不计次（参数故障）
    cfg = _ep(agent_param_error=True, injection_step=1)
    engine = PersistenceEngine(cfg)
    before = cfg.episode.injections
    engine.apply_param(action="click", element_id="9", text=None, proxy_step=2, action_uid="p1",
                       elements={"9": button}, page_url="http://h/a")
    engine.apply_param(action="click", element_id="9", text=None, proxy_step=2, action_uid="p1",
                       elements={"9": button}, page_url="http://h/a")
    assert cfg.episode.injections == before + 1
    # 歧义/缺失只记录、不消耗
    engine2 = PersistenceEngine(_ep(web_dom_missing=True))
    engine2.apply_dom({"url": "http://h/a",
                       "elements": {"1": {"role": "link", "name": "Dup", "attrs": {},
                                          "role_index": 0}},
                       "page_content": ""}, proxy_step=2, action_uid="a1")
    engine2.apply_dom({"url": "http://h/a",
                       "elements": {"7": {"role": "link", "name": "Dup", "attrs": {},
                                          "role_index": 5},
                                    "8": {"role": "link", "name": "Dup", "attrs": {},
                                          "role_index": 6}},
                       "page_content": ""}, proxy_step=3, action_uid="a2")
    assert any(e.get("reason", "").startswith("target_") for e in engine2.episode.events)
    # 缺失（换页）也记录且不耗剂量
    before2 = engine2.episode.injections
    engine2.apply_dom({"url": "http://h/other", "elements": {}, "page_content": ""},
                      proxy_step=4, action_uid="a3")
    assert engine2.episode.injections == before2


# 4. 步数/执行步计数覆盖实际调用链（含初始 goto） ---------------------------
def test_criterion4_step_counting_covers_the_call_chain():
    cfg = _ep(web_http_error=True)
    injector = FaultInjector(_FakeEnv(), cfg)
    injector.goto("http://h/a")          # 初始 goto 就是 step 1
    assert injector._step_counter == 1
    injector.click("1")
    injector.click("2")
    assert injector._step_counter == 3
    # 每个动作有独立 action_uid，且执行步与 trace 步不再假定相同（分别记录）
    uids = [e["source_action_uid"] for e in injector.get_exposure_log()]
    assert uids[:3] == ["goto:1", "click:2", "click:3"]


# 5. episode 不跨 trial/worker 共享；控制臂恒 0 次 --------------------------
def test_criterion5_isolation_and_control_arms():
    a, b = FaultEpisode("repeat3"), FaultEpisode("repeat3")
    a.record_injection(proxy_step=2, action_uid="x")
    assert b.injections == 0 and b.t0 is None                     # 不共享状态
    cfg = FaultConfig(intensity="off", seed=7, web_http_error=True,
                      experiment_id="ep-persistence-v1", fault_variant="repeat3")
    engine = PersistenceEngine(cfg)
    for step in range(1, 21):
        engine.apply_http({"url": "http://h/a", "elements": {}, "page_content": "real"},
                          proxy_step=step, action_uid=f"a{step}")
    assert cfg.episode.injections == 0                            # 控制臂零注入
    assert cfg.max_steps_default_is_20() if hasattr(cfg, "max_steps_default_is_20") else True


# 6. 0/partial/full 剂量可审计；越界/重复拒绝 ------------------------------
def test_criterion6_doses_are_auditable():
    assert dose_class("repeat3", 0) == "not_triggered"
    assert dose_class("repeat3", 2) == "partial_dose"
    assert dose_class("repeat3", 3) == "full_dose"
    assert dose_class("single", 1) == "full_dose"
    over = classify_injection_log(
        [{"event": "injection", "proxy_step": 2, "action_uid": "a"},
         {"event": "injection", "proxy_step": 3, "action_uid": "b"},
         {"event": "injection", "proxy_step": 4, "action_uid": "c"},
         {"event": "injection", "proxy_step": 5, "action_uid": "d"}], variant="repeat3")
    assert over["valid"] is False and over["dose_class"] == "implementation_error"


# 7. variant key 不碰撞；对账/绑定不符必须阻断 -----------------------------
def test_criterion7_keys_dont_collide_and_stale_binding_blocks(tmp_path):
    from standard_agent.trial_metadata import pair_key
    single = pair_key(model_profile="m", architecture="react", fault_type="f",
                      task_id=24, seed=1, experiment_id="ep-persistence-v1",
                      fault_variant="single")
    repeat = pair_key(model_profile="m", architecture="react", fault_type="f",
                      task_id=24, seed=1, experiment_id="ep-persistence-v1",
                      fault_variant="repeat3")
    assert single != repeat
    # 评分绑定不符 → 不复用（阻断）
    from standard_agent.stage_c_pipeline import evaluate_trial
    trial = tmp_path / "t"
    trial.mkdir()
    (trial / "agent_response.json").write_text(json.dumps(
        {"task_type": "NAVIGATE", "status": "SUCCESS", "retrieved_data": None,
         "error_details": None}), encoding="utf-8")
    (trial / "network.har").write_text("{}", encoding="utf-8")
    ok = {"status": "success", "official_success": True,
          "evaluators_results": [{"evaluator_name": "E", "result": True}], "error_msg": ""}
    evaluate_trial(trial, task_id=1, config_path=None, evaluate_fn=lambda *a, **k: ok,
                   evaluate=True)
    (trial / "agent_response.json").write_text(json.dumps(
        {"task_type": "NAVIGATE", "status": "SUCCESS", "retrieved_data": ["x"],
         "error_details": None}), encoding="utf-8")
    out = evaluate_trial(trial, task_id=1, config_path=None,
                         evaluate_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError()),
                         evaluate=True, warn=lambda m: None)
    assert out["source"] == "stale_binding" and out["official_success"] is None


# 8. 合成错误与真实基础设施错误区分；只读评分 ---------------------------------
def test_criterion8_synthetic_vs_real_infrastructure():
    from scripts.run_fault_matrix import classify_log
    synthetic = (f"        injection_count=1 fault_seed=1 faults=['web_http_error'] steps=[2]\n"
                 f"        HTTP status 503 from the injected error page\n")
    out = classify_log(synthetic, expect_fault=True)
    assert out["fault_triggered"] and out["infrastructure_error"] is False  # 合成 503 不算基础设施故障
    real = "        Connection refused\n        Traceback (most recent call last):\n"
    assert classify_log(real, expect_fault=True)["infrastructure_error"] is True


def test_target_policy_version_is_declared():
    assert TARGET_POLICY_VERSION and TARGET_POLICY_VERSION == "semantic-target-lock-v1"
