"""
FaultProxy — 通用浏览器环境故障代理。

与 agent 完全解耦。作为 BrowserEnv 的透明代理，在两层注入环境故障：
  注入点A (观测返回): 篡改页面观测 — DOM丢失、弹窗、500错误
  注入点B (动作执行): 注入延迟 — 模拟网络超时

不对 agent 内部状态做任何篡改。agent 是被观测对象，
所有故障都来自"环境"，agent 如何应对这些故障是评测目标。
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
from fault_injection.agent_faults import reset_state_misjudge


class FaultProxy:
    """
    浏览器环境故障代理。

    实现与 SyncBrowserEnv 相同接口，对上层完全透明。
    不对 agent 内部逻辑做任何假设或篡改。

    使用方式:
        env = SyncBrowserEnv(headless=True)
        env.start()
        proxy = FaultProxy(env, FaultConfig(intensity="medium", web_timeout=True))
        use_browser(proxy)  # agent 通过 proxy 操作浏览器
    """

    def __init__(self, env, config: FaultConfig):
        self._env = env
        self.config = config
        self._step_counter = 0
        self._last_obs: Optional[dict] = None

    # ---- 生命周期 ----

    def start(self):
        self._env.start()

    def stop(self):
        self._env.stop()

    # ---- 观测 (注入点A: 环境故障注入到观测) ----

    def observe(self) -> dict:
        raw = self._env.observe()
        obs = self._to_dict(raw)

        # 环境故障链: DOM丢失 → 弹窗 → HTTP错误
        # 顺序重要：先删元素再插弹窗最后可能整个替换为错误页
        return self._cache_faulted_observation(obs)

    def get_obs(self):
        """供 get_current_observation 使用。"""
        from standard_agent.environment.browser import PageObservation
        obs_dict = self._last_obs if self._last_obs is not None else self.observe()
        return PageObservation(
            url=obs_dict.get("url", "about:blank"),
            ax_tree_text=obs_dict.get("page_content", ""),
            element_map=obs_dict.get("elements", {}),
        )

    # ---- 浏览器操作 (注入点B: 网络超时 + 注入点C: 业务故障) ----

    def goto(self, url: str):
        self._step_counter += 1
        self.config.set_execution_step(self._step_counter)
        inject_timeout(self.config)               # B: 网络延迟
        result = self._env.goto(url)
        obs = self._to_dict(result)
        obs = inject_quota_exceeded(              # C: GitLab配额
            self.config, "goto", obs, url=url)
        obs = self._cache_faulted_observation(obs)
        return self._wrap(result, obs)

    def click(self, element_id: str):
        self._step_counter += 1
        self.config.set_execution_step(self._step_counter)
        inject_timeout(self.config)               # B: 网络延迟
        elem_name = self._elem_name(element_id)
        result = self._env.click(element_id)
        obs = self._to_dict(result)
        obs = inject_ci_offline(                  # C: GitLab CI
            self.config, "click", obs, elem_name=elem_name)
        obs = inject_permission_denied(           # C: GitLab 403
            self.config, "click", obs, elem_name=elem_name)
        obs = inject_merge_conflict(              # C: GitLab 冲突
            self.config, "click", obs, elem_name=elem_name)
        obs = self._cache_faulted_observation(obs)
        return self._wrap(result, obs)

    def type_text(self, element_id: str, text: str):
        self._step_counter += 1
        self.config.set_execution_step(self._step_counter)
        inject_timeout(self.config)               # B: 网络延迟
        result = self._env.type_text(element_id, text)
        obs = self._to_dict(result)
        obs = self._cache_faulted_observation(obs)
        return self._wrap(result, obs)

    def scroll(self, direction: str):
        self._step_counter += 1
        self.config.set_execution_step(self._step_counter)
        inject_timeout(self.config)
        result = self._env.scroll(direction)
        obs = self._cache_faulted_observation(self._to_dict(result))
        return self._wrap(result, obs)

    def go_back(self):
        self._step_counter += 1
        self.config.set_execution_step(self._step_counter)
        inject_timeout(self.config)
        result = self._env.go_back()
        obs = self._cache_faulted_observation(self._to_dict(result))
        return self._wrap(result, obs)

    def go_forward(self):
        self._step_counter += 1
        self.config.set_execution_step(self._step_counter)
        inject_timeout(self.config)
        result = self._env.go_forward()
        obs = self._cache_faulted_observation(self._to_dict(result))
        return self._wrap(result, obs)

    def select_option(self, element_id: str, option: str):
        self._step_counter += 1
        self.config.set_execution_step(self._step_counter)
        inject_timeout(self.config)
        result = self._env.select_option(element_id, option)
        obs = self._cache_faulted_observation(self._to_dict(result))
        return self._wrap(result, obs)

    def hover(self, element_id: str):
        self._step_counter += 1
        self.config.set_execution_step(self._step_counter)
        inject_timeout(self.config)
        result = self._env.hover(element_id)
        obs = self._cache_faulted_observation(self._to_dict(result))
        return self._wrap(result, obs)

    # ---- 辅助 ----

    def _to_dict(self, result) -> dict:
        if isinstance(result, dict):
            return result
        return {
            "url": getattr(result, "url", "about:blank"),
            "page_content": getattr(result, "ax_tree_text", ""),
            "elements": getattr(result, "element_map", {}),
        }

    def _cache_faulted_observation(self, obs: dict) -> dict:
        """Apply observation faults once and cache the resulting page."""
        obs = inject_dom_missing(self.config, obs)
        obs = inject_popup(self.config, obs)
        obs = inject_http_error(self.config, obs)
        self._last_obs = dict(obs)
        return obs

    def _wrap(self, original, obs: dict):
        from standard_agent.environment.browser import PageObservation
        if isinstance(original, PageObservation):
            return PageObservation(
                url=obs.get("url", original.url),
                ax_tree_text=obs.get("page_content", original.ax_tree_text),
                element_map=obs.get("elements", original.element_map),
                raw_ax=original.raw_ax,
            )
        return original

    def _elem_name(self, element_id: str) -> str:
        info = (self._last_obs or {}).get("elements", {}).get(str(element_id), {})
        return info.get("name", "") if isinstance(info, dict) else ""

    # ---- 报告 ----

    def get_injection_log(self) -> list[dict]:
        return self.config.log

    def print_report(self):
        log = self.config.log
        if not log:
            print("✅ 无故障注入")
            return
        from collections import Counter
        print(f"\n{'='*50}")
        print(f"📊 故障注入报告 | 强度={self.config.intensity} seed={self.config.seed}")
        print(f"{'='*50}")
        print(f"总注入: {len(log)} 次")
        for name, count in Counter(e["fault"] for e in log).most_common():
            print(f"  {name}: {count} 次")
        total_delay = sum(e.get("detail", {}).get("delay_sec", 0) for e in log)
        if total_delay > 0:
            print(f"总延迟: {total_delay:.1f}s")
        print(f"{'='*50}")

    def reset(self):
        self._step_counter = 0
        self._last_obs = None
        self.config.reset()
        reset_state_misjudge()
