"""E-P 持久化引擎：把一次性故障变成"同一目标、有限窗口"的重复故障。

本模块只处理**观察/动作字典**，不碰浏览器，因此可离线单测。它把
``episode``（预算/窗口/去重/释放）与 ``targets``（语义身份/匹配）组合到三种故障上：

* ``web_http_error``：首次沿用 500/503 抽样并**锁定扰动前的真实 URL**；窗口内每次
  产生该页的新观察都重放**同一种**错误样本（刷新不能清除），其他 URL 不受影响。
* ``web_dom_missing``：首次抽 up to N 个元素并锁定其**语义身份**；后续同页观察重新
  识别、屏蔽**同一批**元素。任一锁定目标 absent/ambiguous 时**跳过本次且不耗剂量**，
  绝不删别的元素补足。
* ``agent_param_error``：首次在参数动作上锁定"页面 + 动作类型 + 目标元素语义身份"
  并记录损坏规则；窗口内再次指向同一目标时**重放同一条损坏规则**。

Stage C（``variant == 'single'``）不走这里：旧注入器行为原样保留。
"""

from __future__ import annotations

from typing import Optional

from fault_injection.episode import FaultEpisode, TARGET_ABSENT, TARGET_OK
from fault_injection.targets import (TARGET_AMBIGUOUS, TARGET_POLICY_VERSION,
                                     dom_identity, match_identity, normalize_url,
                                     param_identity)
from fault_injection.web_faults import (HTTP_ERROR_PAGES, _combinations,
                                        _sample_multiple)


def _error_page(sample: dict) -> dict:
    return {"url": sample["url"], "page_content": sample["content"],
            "elements": dict(sample["elements"])}


class PersistenceEngine:
    """单 trial 的持久故障引擎。所有状态都在实例上，绝不放模块级。"""

    def __init__(self, config, *, episode: Optional[FaultEpisode] = None,
                 target_policy_version: str = TARGET_POLICY_VERSION):
        self.config = config
        self.episode = episode or config.episode
        self.target_policy_version = target_policy_version
        self._http_sample: Optional[dict] = None
        self._dom_targets: Optional[list] = None
        self._dom_remove_count: int = 0
        self._param_target: Optional[dict] = None
        self._param_corruption: Optional[dict] = None
        self._param_action_index: int = 0
        self._enabled = {name for name in ("web_http_error", "web_dom_missing",
                                           "agent_param_error")
                         if getattr(config, name, False)}

    # ---- HTTP ----
    def apply_http(self, obs: dict, *, proxy_step: int, action_uid: str) -> dict:
        if "web_http_error" not in self._enabled:
            return obs
        page_url = obs.get("url", "")
        if self._http_sample is not None:
            target = self.episode.target or {}
            matches = normalize_url(page_url) == target.get("page_url")
            allowed, reason = self.episode.decide(proxy_step=proxy_step, action_uid=action_uid,
                                                  matches_locked=matches)
            if not allowed:
                self.episode.record_skip(proxy_step=proxy_step, action_uid=action_uid,
                                         reason=reason)
                return obs
            self.episode.record_injection(proxy_step=proxy_step, action_uid=action_uid,
                                          target=target, detail={"replayed": True})
            return _error_page(self._http_sample)
        # 首次抽取：仍由固定注入步把关（与 Stage C 一致）
        if proxy_step != self.config.injection_step:
            return obs
        # 复用 Stage C 的抽样顺序（同 seed 首次扰动与 single 相同）
        error_type = self.config.rng.pick(["500", "503"])
        sample = HTTP_ERROR_PAGES[error_type]
        self._http_sample = sample
        self.episode.record_injection(
            proxy_step=proxy_step, action_uid=action_uid,
            target={"kind": "http", "page_url": normalize_url(page_url)},
            detail={"sample": sample["content"].splitlines()[0]})
        return _error_page(sample)

    # ---- DOM ----
    def apply_dom(self, obs: dict, *, proxy_step: int, action_uid: str) -> dict:
        if "web_dom_missing" not in self._enabled:
            return obs
        elements = obs.get("elements") or {}
        page_url = obs.get("url", "")
        if self._dom_targets is not None:
            matched, status = [], TARGET_OK
            for identity in self._dom_targets:
                eid, state = match_identity(identity, elements, page_url)
                if state != TARGET_OK:
                    status = state
                    break
                matched.append(str(eid))
            if status != TARGET_OK:
                # 目标缺失/歧义：只记录、跳过，不消耗剂量、不换元素
                self.episode.record_skip(proxy_step=proxy_step, action_uid=action_uid,
                                         reason=f"target_{status}")
                return obs
            allowed, reason = self.episode.decide(proxy_step=proxy_step, action_uid=action_uid)
            if not allowed:
                self.episode.record_skip(proxy_step=proxy_step, action_uid=action_uid, reason=reason)
                return obs
            self.episode.record_injection(proxy_step=proxy_step, action_uid=action_uid,
                                          detail={"replayed_ids": matched,
                                                  "removed_count": len(matched)})
            return self._hide(obs, matched)
        if proxy_step != self.config.injection_step:
            return obs
        count = {"low": 1, "medium": 2}.get(self.config.intensity, 3)
        all_ids = list(elements.keys())
        if not all_ids or len(all_ids) <= 1:
            self.episode.record_skip(proxy_step=proxy_step, action_uid=action_uid,
                                     reason="target_absent")
            return obs
        count = max(1, min(count, len(all_ids) - 1))
        chosen = (self.config.rng.pick(_combinations(all_ids, count))
                  if count == 1 else _sample_multiple(self.config.rng, all_ids, count))
        if not isinstance(chosen, list):
            chosen = [chosen]
        self._dom_targets = [dom_identity(elements[eid], page_url) for eid in chosen]
        self._dom_remove_count = len(chosen)
        self.episode.record_injection(proxy_step=proxy_step, action_uid=action_uid,
                                      detail={"removed_ids": chosen})
        return self._hide(obs, [str(eid) for eid in chosen])

    @staticmethod
    def _hide(obs: dict, ids: list) -> dict:
        import re
        keep_ids = {str(i) for i in ids}
        elements = {k: v for k, v in (obs.get("elements") or {}).items() if k not in keep_ids}
        content = obs.get("page_content", "")
        lines = [line for line in content.split("\n")
                 if not any(re.search(rf"\[{i}\]", line) for i in keep_ids)]
        return {"url": obs.get("url", ""), "page_content": "\n".join(lines), "elements": elements}

    # ---- 参数动作 ----
    def apply_param(self, *, action: str, element_id, text, proxy_step: int, action_uid: str,
                    elements: dict, page_url: str):
        if "agent_param_error" not in self._enabled:
            return element_id, text
        current = elements.get(str(element_id)) if isinstance(elements, dict) else None
        if self._param_target is not None:
            eid, status = match_identity(self._param_target, elements or {}, page_url)
            if status != TARGET_OK:
                self.episode.record_skip(proxy_step=proxy_step, action_uid=action_uid,
                                         reason=f"target_{status}")
                return element_id, text
            allowed, reason = self.episode.decide(proxy_step=proxy_step, action_uid=action_uid)
            if not allowed:
                self.episode.record_skip(proxy_step=proxy_step, action_uid=action_uid, reason=reason)
                return element_id, text
            self.episode.record_injection(proxy_step=proxy_step, action_uid=action_uid,
                                          detail={"replayed": True, "new_element_id":
                                                  self._param_corruption["element_id"]})
            new_text = (f"{text}{self._param_corruption['text_suffix']}"
                        if text is not None and self._param_corruption.get("text_suffix") else text)
            return self._param_corruption["element_id"], new_text
        if not isinstance(current, dict):
            return element_id, text
        # 首次抽取：按"参数动作计数"把关（与 Stage C 的注入单位一致）
        self._param_action_index += 1
        if self._param_action_index != self.config.injection_step:
            return element_id, text
        # 沿用当前动作变换与 rng，记录损坏规则以便重放
        invalid = f"invalid_{self.config.rng.randint(1000, 9999)}"
        suffix = " [输入确认失败，请重试]" if text is not None else None
        self._param_target = param_identity(action, current, page_url)
        self._param_corruption = {"element_id": invalid, "text_suffix": suffix}
        self.episode.record_injection(proxy_step=proxy_step, action_uid=action_uid,
                                      target=self._param_target,
                                      detail={"original_element_id": str(element_id),
                                              "new_element_id": invalid})
        new_text = f"{text}{suffix}" if text is not None and suffix else text
        return invalid, new_text

    def snapshot(self) -> dict:
        data = self.episode.snapshot()
        data.update({
            "target_policy_version": self.target_policy_version,
            "http_target": None if self._http_sample is None else self.episode.target,
            "dom_targets": self._dom_targets,
            "param_target": self._param_target,
        })
        return data

    @property
    def dom_target_absent_status(self):
        """便于上层记录：DOM 是否因目标缺失/歧义而未注入。"""
        return TARGET_ABSENT if self._dom_targets is None else TARGET_OK
