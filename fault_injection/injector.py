"""
FaultInjector — 故障注入中间件核心。

包装 BrowserEnv，在三个注入点透明注入故障：
  注入点A (观测层): _observe → 篡改 aria_snapshot
  注入点B (动作前): 工具调用 → 注入延迟、改参数
  注入点C (结果层): 工具返回 → 替换观测为错误页

用法:
    env = SyncBrowserEnv(headless=True)
    env.start()

    config = FaultConfig(intensity="medium", seed=42,
                         web_timeout=True, web_dom_missing=True)
    injector = FaultInjector(env, config)
    use_browser(injector)  # 注入到工具层

    # 之后的所有工具调用都会经过 FaultInjector
"""

import time
from typing import Optional

from fault_injection.config import FaultConfig
from fault_injection.web_faults import (
    inject_timeout,
    inject_http_error,
    inject_dom_missing,
    inject_popup,
)
from fault_injection.gitlab_faults import (
    inject_ci_offline,
    inject_permission_denied,
    inject_merge_conflict,
    inject_quota_exceeded,
)
from fault_injection.agent_faults import (
    inject_state_misjudge,
    inject_param_error,
    reset_state_misjudge,
)


class FaultInjector:
    """
    故障注入中间件。

    实现与 SyncBrowserEnv 相同的接口，对上层工具层透明。
    在关键方法中嵌入注入逻辑。

    注入链顺序（每次工具调用）:
      1. 注入点B: 超时延迟 + 参数篡改
      2. 执行真实浏览器操作
      3. 注入点A: 观测篡改 (DOM丢失/弹窗/状态误判)
      4. 注入点C: 结果篡改 (HTTP错误/GitLab业务故障)
    """

    def __init__(self, env, config: FaultConfig):
        """
        Args:
            env: SyncBrowserEnv 实例
            config: FaultConfig 故障配置
        """
        self._env = env
        self.config = config
        self._step_counter = 0           # 当前任务步数
        self._last_obs: Optional[dict] = None
        self._current_action_uid: Optional[str] = None
        self._observation_seq = 0        # 每次"交付给模型的观察"计数
        self._exposure_log: list[dict] = []
        self._raw_url = None
        self._action_start_url = None
        self._action_name = None
        self._http_resource_engine = None
        self._service_engine = None
        if config.experiment_id == "ep-http-persistence-v2":
            from fault_injection.http_persistence import HttpPersistenceEngine
            self._http_resource_engine = HttpPersistenceEngine(config)
        elif config.experiment_id == "ep-http-service-v1":
            from fault_injection.http_service import ServicePersistenceEngine
            self._service_engine = ServicePersistenceEngine(config)
        # E-P 持久化实验走 episode 引擎；Stage C（experiment_id=stage-c）保持原路径。
        self._engine = None
        if getattr(config, "experiment_id", "stage-c") == "ep-persistence-v1":
            from fault_injection.persistence import PersistenceEngine
            self._engine = PersistenceEngine(config)

    # ---- E-P 辅助 ----

    def _begin_action(self, name: str) -> None:
        """推进动作步并标记 action_uid（同一动作内重复读取观察不额外计次）。"""
        self._step_counter += 1
        self.config.set_execution_step(self._step_counter)
        self._current_action_uid = f"{name}:{self._step_counter}"
        self._action_name = name
        self._action_start_url = self._raw_url
        if self._http_resource_engine is not None:
            self.config.episode.note_step(self._step_counter)
        if self._service_engine is not None:
            self.config.episode.note_step(self._step_counter)

    def arm_http_fault(self):
        """Call after clean bootstrap navigation, immediately before agent execution."""
        if self._http_resource_engine is not None:
            self._http_resource_engine.arm(self._step_counter)
        if self._service_engine is not None:
            self._service_engine.arm(self._step_counter)

    def _param_error(self, action: str, *, element_id, text=None):
        """参数动作故障：E-P 走引擎（同目标重放），Stage C 走原实现。"""
        if self._engine is None:
            return inject_param_error(self.config, action, element_id=element_id, text=text)
        obs = self._last_obs or {}
        return self._engine.apply_param(
            action=action, element_id=element_id, text=text,
            proxy_step=self._step_counter, action_uid=self._current_action_uid,
            elements=obs.get("elements") or {}, page_url=obs.get("url", ""),
        )

    # ---- 生命周期 ----

    def start(self):
        self._env.start()

    def stop(self):
        self._env.stop()

    # ---- 观测接口 (注入点A) ----

    def observe(self) -> dict:
        """获取当前页面观测（注入 Web 底层 + Agent 连锁故障）。"""
        raw = self._env.observe()
        obs = self._to_dict(raw)
        return self._cache_faulted_observation(obs, allow_activation=False)

    def get_obs(self):
        """
        获取当前页面观测（供 get_current_observation 使用）。

        返回一个具有 url/ax_tree_text/element_map 属性的对象。
        """
        from standard_agent.environment.browser import PageObservation
        obs_dict = self._last_obs if self._last_obs is not None else self.observe()
        return PageObservation(
            url=obs_dict.get("url", "about:blank"),
            ax_tree_text=obs_dict.get("page_content", ""),
            element_map=obs_dict.get("elements", {}),
        )

    # ---- 浏览器操作 (注入点B + C) ----

    def goto(self, url: str):
        """导航 + 故障注入。"""
        self._begin_action("goto")

        # 注入点B: 超时
        inject_timeout(self.config)

        result = self._env.goto(url)
        obs = self._to_dict(result)

        # 注入点C: GitLab配额
        obs = inject_quota_exceeded(
            self.config, "goto", obs, url=url, elem_name=""
        )

        obs = self._cache_faulted_observation(obs)
        return self._wrap_result(result, obs)

    def click(self, element_id: str):
        """点击 + 故障注入。"""
        self._begin_action("click")

        # 注入点B: 超时 + 参数错误
        inject_timeout(self.config)
        new_eid, _ = self._param_error("click", element_id=element_id)

        # 获取元素名（用于 GitLab 故障匹配）
        elem_name = self._get_element_name(new_eid)

        result = self._env.click(new_eid)
        obs = self._to_dict(result)

        # 注入点C: GitLab 业务故障
        obs = inject_ci_offline(
            self.config, "click", obs, elem_name=elem_name
        )
        obs = inject_permission_denied(
            self.config, "click", obs, url=obs.get("url", ""), elem_name=elem_name
        )
        obs = inject_merge_conflict(
            self.config, "click", obs, elem_name=elem_name
        )

        obs = self._cache_faulted_observation(obs)
        return self._wrap_result(result, obs)

    def type_text(self, element_id: str, text: str):
        """输入文本 + 故障注入。"""
        self._begin_action("type_text")

        # 注入点B: 超时 + 参数错误
        inject_timeout(self.config)
        new_eid, new_text = self._param_error("type_text", element_id=element_id, text=text)

        result = self._env.type_text(new_eid, new_text)
        obs = self._to_dict(result)
        obs = self._cache_faulted_observation(obs)
        return self._wrap_result(result, obs)

    def scroll(self, direction: str):
        """滚动 + 故障注入（超时）。"""
        self._begin_action("scroll")
        inject_timeout(self.config)
        result = self._env.scroll(direction)
        obs = self._cache_faulted_observation(self._to_dict(result))
        return self._wrap_result(result, obs)

    def go_back(self):
        """后退 + 故障注入（超时）。"""
        self._begin_action("go_back")
        inject_timeout(self.config)
        result = self._env.go_back()
        obs = self._cache_faulted_observation(self._to_dict(result))
        return self._wrap_result(result, obs)

    def go_forward(self):
        """前进 + 故障注入（超时）。"""
        self._begin_action("go_forward")
        inject_timeout(self.config)
        result = self._env.go_forward()
        obs = self._cache_faulted_observation(self._to_dict(result))
        return self._wrap_result(result, obs)

    def select_option(self, element_id: str, option: str):
        """选择 + 故障注入。"""
        self._begin_action("select_option")
        inject_timeout(self.config)
        new_eid, _ = self._param_error("select_option", element_id=element_id)
        result = self._env.select_option(new_eid, option)
        obs = self._cache_faulted_observation(self._to_dict(result))
        return self._wrap_result(result, obs)

    def hover(self, element_id: str):
        """悬停 + 故障注入（超时）。"""
        self._begin_action("hover")
        inject_timeout(self.config)
        new_eid, _ = self._param_error("hover", element_id=element_id)
        result = self._env.hover(new_eid)
        obs = self._cache_faulted_observation(self._to_dict(result))
        return self._wrap_result(result, obs)

    # ---- 辅助方法 ----

    def _to_dict(self, result) -> dict:
        """将 BrowserEnv 返回的 PageObservation 转为 dict。"""
        if isinstance(result, dict):
            return result
        return {
            "url": getattr(result, "url", "about:blank"),
            "page_content": getattr(result, "ax_tree_text", ""),
            "elements": getattr(result, "element_map", {}),
        }

    def _cache_faulted_observation(self, obs: dict, *, allow_activation=True) -> dict:
        """Apply observation faults once and cache the resulting page.

        E-P：观察层故障交给 episode 引擎（同目标、限窗口重复）；Stage C 保持原链条。
        """
        self._raw_url = obs.get("url", "")
        if self._service_engine is not None:
            obs = self._service_engine.apply_http(
                obs, proxy_step=self._step_counter, action_uid=self._current_action_uid,
                action=self._action_name, previous_url=self._action_start_url,
                allow_activation=allow_activation)
            evidence = self._service_engine.last_decision
            self._record_exposure(["web_http_error"] if evidence["fault_delivered"] else [],
                                  evidence=evidence)
            self._last_obs = dict(obs)
            return obs
        if self._http_resource_engine is not None:
            obs = self._http_resource_engine.apply_http(
                obs, proxy_step=self._step_counter, action_uid=self._current_action_uid,
                action=self._action_name, previous_url=self._action_start_url,
                allow_activation=allow_activation)
            evidence = self._http_resource_engine.last_decision
            self._record_exposure(["web_http_error"] if evidence["fault_delivered"] else [],
                                  evidence=evidence)
            self._last_obs = dict(obs)
            return obs
        obs = inject_state_misjudge(self.config, obs)
        if self._engine is not None:
            before_dom = obs
            obs = self._engine.apply_dom(obs, proxy_step=self._step_counter,
                                         action_uid=self._current_action_uid)
            dom_delivered = obs != before_dom
            before_http = obs
            obs = self._engine.apply_http(obs, proxy_step=self._step_counter,
                                          action_uid=self._current_action_uid)
            http_delivered = obs != before_http
            delivered = [name for name, hit in (("web_dom_missing", dom_delivered),
                                                ("web_http_error", http_delivered)) if hit]
            self._record_exposure(delivered)
            self._last_obs = dict(obs)
            return obs
        before = len(self.config.log)
        obs = inject_dom_missing(self.config, obs)
        obs = inject_popup(self.config, obs)
        obs = inject_http_error(self.config, obs)
        # 记录本观察实际投递了哪些故障（来自注入日志增量），而不是猜测模型是否察觉。
        delivered = [entry.get("fault") for entry in self.config.log[before:]]
        self._record_exposure([n for n in delivered if n])
        self._last_obs = dict(obs)
        return obs

    def _record_exposure(self, delivered: list, *, evidence=None) -> dict:
        """记录一次"交付给模型的观察"：uid、来源动作、实际含有的故障标记。

        这只证明故障**被交付**，不声称模型在心理上识别了它（设计第 8 节）。
        """
        self._observation_seq += 1
        entry = {
            "observation_uid": f"{self._current_action_uid or 'initial'}#{self._observation_seq}",
            "source_action_uid": self._current_action_uid,
            "experiment_id": getattr(self.config, "experiment_id", "stage-c"),
            "variant": getattr(self.config, "fault_variant", "single"),
            "step": self._step_counter,
            "faults_delivered": list(delivered),
            "delivered": bool(delivered),
        }
        if evidence is not None:
            entry.update(evidence)
        self._exposure_log.append(entry)
        return entry

    def get_exposure_log(self) -> list[dict]:
        return list(self._exposure_log)

    def _wrap_result(self, original, obs: dict):
        """
        将篡改后的 obs dict 包装回原始类型。

        如果 original 是 PageObservation，更新其字段后返回。
        否则返回 dict。
        """
        from standard_agent.environment.browser import PageObservation
        if isinstance(original, PageObservation):
            # 不修改 BrowserEnv 的原始观测。否则 DOM 丢失/弹窗等观测故障
            # 会污染底层 element_map，导致下一次真实操作也使用篡改后的状态。
            return PageObservation(
                url=obs.get("url", original.url),
                ax_tree_text=obs.get("page_content", original.ax_tree_text),
                element_map=obs.get("elements", original.element_map),
                raw_ax=original.raw_ax,
            )
        if (self._http_resource_engine is not None or self._service_engine is not None) \
                and isinstance(original, dict):
            return obs
        return original

    def _get_element_name(self, element_id: str) -> str:
        """从当前观测的元素映射中获取元素名。"""
        obs = self._last_obs or {}
        elements = obs.get("elements", {})
        info = elements.get(str(element_id), {})
        return info.get("name", "") if isinstance(info, dict) else ""

    # ---- 日志导出 ----

    def get_injection_log(self) -> list[dict]:
        """获取本次运行的故障注入日志。"""
        return self.config.log

    def print_report(self):
        """打印故障注入报告。"""
        log = self.config.log
        if not log:
            print("✅ 本次运行无故障注入")
            return

        print(f"\n{'='*50}")
        print(f"📊 故障注入报告")
        print(f"{'='*50}")
        print(f"强度: {self.config.intensity}")
        print(f"Seed: {self.config.seed}")
        print(f"总注入次数: {len(log)}")
        print()

        # 按故障类型统计
        from collections import Counter
        fault_counts = Counter(entry["fault"] for entry in log)
        for name, count in fault_counts.most_common():
            print(f"  {name}: {count} 次")

        print(f"\n--- 详细时间线 ---")
        for entry in log:
            print(f"  Step {entry['step']:2d}: {entry['fault']}")
            if entry.get("detail"):
                for k, v in entry["detail"].items():
                    print(f"           {k}: {v}")
        print(f"{'='*50}\n")

    def reset(self):
        """重置故障注入状态（开始新任务时调用）。"""
        self._step_counter = 0
        self._last_obs = None
        self.config.reset()
        if self._http_resource_engine is not None:
            from fault_injection.http_persistence import HttpPersistenceEngine
            self._http_resource_engine = HttpPersistenceEngine(self.config)
            self._raw_url = self._action_start_url = self._action_name = None
            self._current_action_uid = None
            self._observation_seq = 0
            self._exposure_log = []
        if self._service_engine is not None:
            from fault_injection.http_service import ServicePersistenceEngine
            self._service_engine = ServicePersistenceEngine(self.config)
            self._raw_url = self._action_start_url = self._action_name = None
            self._current_action_uid = None
            self._observation_seq = 0
            self._exposure_log = []
        reset_state_misjudge()
