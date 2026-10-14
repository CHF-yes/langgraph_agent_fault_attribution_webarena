"""E-P 持续故障的 episode 状态机（纯逻辑，不依赖浏览器/模型）。

设计依据 ``docs/stage_e_persistence_plan.md``。Stage C 的 ``single`` 只注入一次，
本模块把"注入几次、持续多久、锁定哪个目标、何时释放"独立成可单测的状态机，
使 ``single`` 与 ``repeat3`` 共用一套计数与证据，而不是在注入器里散落 if。

关键不变量（都与设计文本一一对应）：
* 窗口按 **proxy execution_step** 计：``t0`` 是第一次实际注入的 proxy 步，窗口为
  ``[t0, t0 + window_steps - 1]``，``t0 + window_steps`` 起释放；
* 第 ``max_injections`` 次注入后立即释放，与窗口过期取先发生者；
* 同一 ``action_uid`` 内最多注入一次（工具返回与重复读取观察不额外耗次数）；
* 目标在首次注入时**锁定**，之后只对"同一语义目标"重复注入，绝不因效果小而换目标；
* 目标缺失/歧义只记录、跳过，**不消耗剂量、不补足**；
* 所有计数器都是每 episode（即每 trial）独立状态，绝不放模块级全局变量。
"""

from __future__ import annotations

import time
from typing import Optional

EXPERIMENT_ID_STAGE_C = "stage-c"
EXPERIMENT_ID_EP = "ep-persistence-v1"

#: variant → 最多实际注入次数
FAULT_VARIANTS = {"single": 1, "repeat3": 3}
DEFAULT_WINDOW_STEPS = 5

#: 目标匹配状态：只有 ``ok`` 才允许注入
TARGET_OK = "ok"
TARGET_AMBIGUOUS = "ambiguous"
TARGET_ABSENT = "absent"


def max_injections_for(variant: str) -> int:
    try:
        return FAULT_VARIANTS[variant]
    except KeyError:
        raise ValueError(
            f"Unknown fault variant {variant!r}; expected one of {sorted(FAULT_VARIANTS)}"
        ) from None


def dose_class(variant: str, count: int) -> str:
    """把实际注入次数分类。E-P 接受 0..3；``single`` 仍只接受 1。"""
    if count < 0 or count > max_injections_for(variant):
        return "implementation_error"
    if count == 0:
        return "not_triggered"
    if variant == "single":
        return "full_dose"
    return "full_dose" if count == max_injections_for(variant) else "partial_dose"


class FaultEpisode:
    """单 trial 的故障 episode 状态机。"""

    def __init__(self, variant: str = "single", *,
                 window_steps: int = DEFAULT_WINDOW_STEPS,
                 target_policy_version: Optional[str] = None,
                 experiment_id: str = EXPERIMENT_ID_EP):
        self.experiment_id = experiment_id
        self.variant = variant
        self.max_injections = max_injections_for(variant)
        if window_steps < 1:
            raise ValueError("window_steps must be >= 1")
        self.window_steps = int(window_steps)
        self.target_policy_version = target_policy_version
        # 运行时状态
        self.target = None                 # 首次注入时锁定的语义身份
        self.t0: Optional[int] = None      # 第一次实际注入的 proxy 步
        self.injections = 0
        self.released: Optional[str] = None
        self._action_uids: set = set()
        self.events: list = []

    # ---- 只读视图 ----
    @property
    def window_end(self) -> Optional[int]:
        """窗口最后一个 proxy 步（含）；未开始为 None。"""
        return None if self.t0 is None else self.t0 + self.window_steps - 1

    @property
    def active(self) -> bool:
        return self.released is None

    # ---- 推进 ----
    def note_step(self, proxy_step: int) -> None:
        """告知当前 proxy 执行步；过期即释放。"""
        proxy_step = int(proxy_step)
        if self.t0 is not None and proxy_step > self.window_end:
            self._release("window_expired", proxy_step)

    def _release(self, reason: str, proxy_step: Optional[int] = None) -> None:
        if self.released is None:
            self.released = reason
            self.events.append({"event": "release", "reason": reason,
                                "proxy_step": proxy_step})

    # ---- 决策 ----
    def decide(self, *, proxy_step: int, action_uid: str,
               matches_locked: bool = True, target_status: str = TARGET_OK) -> tuple[bool, str]:
        """返回 ``(是否注入, 原因)``。不产生副作用，便于单测与干跑。"""
        self.note_step(proxy_step)
        if not self.active:
            return False, "released"
        if action_uid in self._action_uids:
            return False, "action_already_injected"
        if target_status == TARGET_AMBIGUOUS:
            return False, "target_ambiguous"
        if target_status == TARGET_ABSENT:
            return False, "target_absent"
        if self.target is not None and not matches_locked:
            return False, "off_target"
        if self.injections >= self.max_injections:
            return False, "budget_exhausted"
        return True, "inject"

    # ---- 记录 ----
    def record_injection(self, *, proxy_step: int, action_uid: str,
                         target=None, exposure_index: Optional[int] = None,
                         trace_step: Optional[int] = None,
                         parameter_action_index: Optional[int] = None,
                         detail: Optional[dict] = None) -> dict:
        if self.t0 is None:
            self.t0 = int(proxy_step)
            if target is not None:
                self.target = target
        self.injections += 1
        self._action_uids.add(action_uid)
        entry = {
            "event": "injection",
            "experiment_id": self.experiment_id,
            "variant": self.variant,
            "injection_index": self.injections,
            "proxy_step": int(proxy_step),
            "trace_step": trace_step,
            "parameter_action_index": parameter_action_index,
            "action_uid": action_uid,
            "exposure_index": exposure_index,
            "target": target,
            "target_policy_version": self.target_policy_version,
            "timestamp": time.time(),
            "detail": detail or {},
        }
        self.events.append(entry)
        if self.injections >= self.max_injections:
            self._release("budget_reached", proxy_step)
        return entry

    def record_skip(self, *, proxy_step: int, action_uid: str, reason: str,
                    detail: Optional[dict] = None) -> dict:
        entry = {"event": "skip", "reason": reason, "proxy_step": int(proxy_step),
                 "action_uid": action_uid, "injection_index": self.injections,
                 "timestamp": time.time(), "detail": detail or {}}
        self.events.append(entry)
        return entry

    def snapshot(self) -> dict:
        return {
            "experiment_id": self.experiment_id,
            "variant": self.variant,
            "max_injections": self.max_injections,
            "window_steps": self.window_steps,
            "target_policy_version": self.target_policy_version,
            "target": self.target,
            "t0": self.t0,
            "window_end": self.window_end,
            "injections": self.injections,
            "released": self.released,
            "dose_class": dose_class(self.variant, self.injections),
        }


def classify_injection_log(entries: list, *, variant: str,
                           window_steps: int = DEFAULT_WINDOW_STEPS) -> dict:
    """按 variant 判定注入日志是否**合法**（不只是次数对不对）。

    * ``single`` / Stage C：必须恰好 1 次（沿用旧规则）。
    * ``repeat3``：0..3 次；0=not_triggered、1..2=partial_dose、3=full_dose。
    以下属实现错误，而非"正常未触发"：次数越界、同一 ``action_uid`` 注入多次、
    重复计数同一次动作、注入落在窗口外、预算/窗口不一致。
    """
    injections = [e for e in entries if e.get("event") == "injection"]
    count = len(injections)
    errors: list[str] = []
    limit = max_injections_for(variant)
    if count > limit:
        errors.append(f"count {count} exceeds {limit} for variant {variant}")
    seen: set = set()
    t0 = injections[0].get("proxy_step") if injections else None
    for entry in injections:
        uid = entry.get("action_uid")
        if uid is not None and uid in seen:
            errors.append(f"action_uid injected more than once: {uid}")
        seen.add(uid)
        step = entry.get("proxy_step")
        if t0 is not None and step is not None and step > t0 + window_steps - 1:
            errors.append(f"injection at proxy_step {step} outside window "
                          f"[{t0}, {t0 + window_steps - 1}]")
    # 注意：这里**不**把 single 的 0 次当成错误——"合法未触发"（agent 从未做该动作）
    # 与"实现错误"是两件事。single 的 0 次由上层按 Stage C 契约记为 fault_missing；
    # >limit 才是结构性错误。
    return {
        "variant": variant,
        "count": count,
        "dose_class": dose_class(variant, count) if not errors else "implementation_error",
        "t0": t0,
        "errors": errors,
        "valid": not errors,
    }
