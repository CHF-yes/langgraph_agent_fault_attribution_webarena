"""
故障配置 - FaultConfig dataclass + 三档强度预设 + SeededRandom。

可复现性：相同 seed + 相同 config → 完全相同故障序列。
"""

import random
import time
from dataclasses import dataclass, field
from typing import Optional


# ============================================================
# SeededRandom — 可复现随机数
# ============================================================

class SeededRandom:
    """
    基于 seed 的可复现随机数生成器。

    每个 FaultConfig 持有一个独立实例。相同 seed → 相同随机序列。
    """

    def __init__(self, seed: int):
        self._seed = seed
        self._state = random.Random(seed)
        self._call_count = 0          # 调用计数，用于追踪故障点

    def should_inject(self, probability: float) -> bool:
        """按概率决定是否注入。返回 True/False + 记录调用计数。"""
        self._call_count += 1
        return self._state.random() < probability

    def pick(self, items: list):
        """从列表中随机选一项。"""
        self._call_count += 1
        return self._state.choice(items)

    def uniform(self, low: float, high: float) -> float:
        """生成 [low, high) 区间随机浮点数。"""
        self._call_count += 1
        return self._state.uniform(low, high)

    def randint(self, low: int, high: int) -> int:
        """生成 [low, high] 区间随机整数。"""
        self._call_count += 1
        return self._state.randint(low, high)

    def reset(self):
        """恢复到该实例 seed 对应的初始随机序列。"""
        self._state = random.Random(self._seed)
        self._call_count = 0

    @property
    def calls(self) -> int:
        return self._call_count


# ============================================================
# 三档强度预设
# ============================================================

# (注入概率, 延迟范围秒)
INTENSITY_PRESETS = {
    "low": {
        "probability": 0.12,        # ~12% 概率触发
        "delay_range": (1.0, 3.0),  # 1-3秒延迟
        "label": "低强度 - 轻微干扰",
    },
    "medium": {
        "probability": 0.30,        # ~30%
        "delay_range": (3.0, 8.0),
        "label": "中强度 - 典型生产故障密度",
    },
    "high": {
        "probability": 0.55,        # ~55%
        "delay_range": (5.0, 15.0),
        "label": "高强度 - 压力测试",
    },
}


# ============================================================
# Stage C 注入步口径（单一来源）
# ============================================================
#
# Stage C 的正式矩阵对每一条故障臂只注入一次，注入位置固定，不扫描：
#
#   agent_param_error   → 第 1 个"带参数的动作"（参数动作计数，不是步数）
#   其他故障            → 第 2 个执行步（execution step）
#
# 两者计数单位不同，这是刻意的：参数错误必须在真正携带参数的动作上发生，
# 若按执行步计数，早期步可能只有 goto/stop 之类无参数动作，故障就落空了。
# 需要扫描注入步的是 Stage E（注入步敏感性），它用显式 injection_step 覆盖
# 下面的默认值；显式传入即视为 Stage E 口径，Stage C 运行不得显式覆盖。
STAGE_C_DEFAULT_INJECTION_STEP = 2
STAGE_C_PARAMETER_ACTION_FAULTS = frozenset({"agent_param_error"})

_INJECTION_STEP_UNITS = {
    "agent_param_error": "parameter_action",
}
STAGE_C_DEFAULT_INJECTION_STEP_UNITS = "execution_step"


def injection_step_units(fault_name: str) -> str:
    """Return the unit that ``injection_step`` counts for ``fault_name``."""
    return _INJECTION_STEP_UNITS.get(fault_name, STAGE_C_DEFAULT_INJECTION_STEP_UNITS)


def stage_c_injection_step(fault_name: str) -> int:
    """Return the frozen Stage C injection step for ``fault_name``."""
    if fault_name in STAGE_C_PARAMETER_ACTION_FAULTS:
        return 1
    return STAGE_C_DEFAULT_INJECTION_STEP


def resolve_injection_step(fault_name: str, requested: Optional[int] = None) -> int:
    """Resolve the injection step for ``fault_name``.

    ``requested`` comes from an explicit ``--fault-injection-step``. A value is
    required for Stage E sweeps; leaving it unset selects the frozen Stage C
    default. ``None`` means "Stage C", never "probability mode": formal trials
    always pin the step so a fault arm either fires exactly once or is recorded
    as invalid.
    """
    if requested is not None:
        return int(requested)
    return stage_c_injection_step(fault_name)


def injection_step_mode(requested: Optional[int] = None) -> str:
    """Label the step-selection mode: Stage C default or explicit Stage E sweep."""
    return "stage_e_explicit" if requested is not None else "stage_c_fixed"


# ============================================================
# FaultConfig
# ============================================================

@dataclass
class FaultConfig:
    """
    故障注入配置。

    使用方式:
        # 预设强度
        config = FaultConfig(intensity="medium")

        # 精确控制
        config = FaultConfig(
            intensity="medium", seed=42,
            web_timeout=True, web_dom_missing=True,
            gitlab_permission=True,
        )
    """

    # ---- 基础设置 ----
    intensity: str = "off"           # "off" | "low" | "medium" | "high"
    seed: int = 42                   # 可复现种子
    enabled: bool = True             # 总开关
    injection_step: Optional[int] = None  # 固定 action step；None 保持概率模式

    # ---- ① Web底层故障 ----
    web_timeout: bool = False        # 接口超时（动作前注入延迟）
    web_http_error: bool = False     # 500/503（goto 返回假错误页面）
    web_dom_missing: bool = False    # DOM 元素丢失（aria_snapshot 删元素）
    web_popup_block: bool = False    # 弹窗遮挡（aria_snapshot 插入弹窗）

    # ---- ② GitLab业务故障 ----
    gitlab_ci_offline: bool = False  # CI Runner 离线
    gitlab_permission: bool = False  # 权限不足 403
    gitlab_conflict: bool = False    # 合并冲突
    gitlab_quota: bool = False       # 配额耗尽

    # ---- ③ Agent 行为故障（由 FaultInjector 使用）----
    agent_state_misjudge: bool = False
    agent_param_error: bool = False

    # ---- 运行时状态（自动初始化）----
    _rng: Optional[SeededRandom] = field(default=None, repr=False)
    _intensity_config: dict = field(default_factory=dict, repr=False)
    _injection_log: list[dict] = field(default_factory=list, repr=False)
    _execution_step: int = field(default=0, repr=False)
    _parameter_action_count: int = field(default=0, repr=False)
    _parameter_action_index: int = field(default=0, repr=False)
    _deterministic_injected: bool = field(default=False, repr=False)

    def __post_init__(self):
        if self.intensity not in {"off", *INTENSITY_PRESETS}:
            raise ValueError(
                f"Unknown fault intensity '{self.intensity}'. "
                "Expected one of: off, low, medium, high"
            )
        self._rng = SeededRandom(self.seed)
        self._intensity_config = INTENSITY_PRESETS.get(
            self.intensity, INTENSITY_PRESETS["low"]
        )
        self._injection_log = []
        self._execution_step = 0
        self._parameter_action_count = 0
        self._parameter_action_index = 0
        self._deterministic_injected = False

    # ---- 属性访问 ----

    @property
    def probability(self) -> float:
        """当前强度的故障触发概率。"""
        if self.intensity == "off" or not self.enabled:
            return 0.0
        return self._intensity_config.get("probability", 0.0)

    @property
    def delay_range(self) -> tuple:
        """当前强度的延迟范围 (min_sec, max_sec)。"""
        return self._intensity_config.get("delay_range", (0, 0))

    @property
    def rng(self) -> SeededRandom:
        return self._rng

    @property
    def log(self) -> list[dict]:
        """故障注入日志。"""
        return self._injection_log

    @property
    def enabled_faults(self) -> tuple[str, ...]:
        """Return explicitly enabled faults in a stable order for experiments."""
        fault_names = (
            "web_timeout", "web_http_error", "web_dom_missing", "web_popup_block",
            "gitlab_ci_offline", "gitlab_permission", "gitlab_conflict", "gitlab_quota",
            "agent_state_misjudge", "agent_param_error",
        )
        return tuple(name for name in fault_names if getattr(self, name, False))

    @property
    def fault_label(self) -> str:
        """Return a stable label suitable for trial tables and trace metadata."""
        faults = self.enabled_faults
        return "control" if self.intensity == "off" or not faults else "+".join(faults)

    @property
    def fault_layer(self) -> str:
        """Classify the configured faults by their experimental injection layer."""
        layers = {
            "web_timeout": "environment",
            "web_http_error": "environment",
            "web_dom_missing": "observation",
            "web_popup_block": "observation",
            "gitlab_ci_offline": "environment",
            "gitlab_permission": "environment",
            "gitlab_conflict": "environment",
            "gitlab_quota": "environment",
            "agent_state_misjudge": "observation",
            "agent_param_error": "action",
        }
        configured_layers = {layers[name] for name in self.enabled_faults}
        if not configured_layers:
            return "none"
        return next(iter(configured_layers)) if len(configured_layers) == 1 else "mixed"

    # ---- 注入判断 ----

    def should_inject(self, fault_name: str) -> bool:
        """
        判断是否应注入指定故障。

        综合考虑：总开关 + 故障开关 + 强度概率 + seed 可复现。

        Args:
            fault_name: 故障名称（如 "web_timeout"）

        Returns:
            是否注入
        """
        if not self.enabled or self.intensity == "off":
            return False

        # 检查该故障开关是否打开
        if not getattr(self, fault_name, False):
            return False

        # Formal trials may request exactly one injection at a fixed action step.
        if self.injection_step is not None:
            if self._deterministic_injected or self._execution_step != self.injection_step:
                return False
            self._deterministic_injected = True
            return True

        # Legacy exploratory mode: inject according to the configured probability.
        return self._rng.should_inject(self.probability)

    def should_inject_parameter_action(self, fault_name: str) -> bool:
        """Decide injection for the next tool action that accepts parameters."""
        self._parameter_action_count += 1
        self._parameter_action_index = self._parameter_action_count
        if not self.enabled or self.intensity == "off":
            return False
        if not getattr(self, fault_name, False):
            return False
        if self.injection_step is not None:
            if self._deterministic_injected or self._parameter_action_count != self.injection_step:
                return False
            self._deterministic_injected = True
            return True
        return self._rng.should_inject(self.probability)

    def get_delay(self, fault_name: str = None) -> float:
        """获取当前强度的随机延迟（秒）。"""
        low, high = self.delay_range
        return self._rng.uniform(low, high)

    def record_injection(self, fault_name: str, detail: dict = None):
        """记录一次故障注入。"""
        layer = {
            "web_timeout": "environment",
            "web_http_error": "environment",
            "web_dom_missing": "observation",
            "web_popup_block": "observation",
            "gitlab_ci_offline": "environment",
            "gitlab_permission": "environment",
            "gitlab_conflict": "environment",
            "gitlab_quota": "environment",
            "agent_state_misjudge": "observation",
            "agent_param_error": "action",
        }.get(fault_name, "unknown")
        entry = {
            "fault": fault_name,
            "fault_layer": layer,
            "fault_seed": self.seed,
            "step": self._execution_step or len(self._injection_log) + 1,
            "parameter_action_index": self._parameter_action_index or None,
            "injection_index": len(self._injection_log) + 1,
            "timestamp": time.time(),
            "detail": detail or {},
        }
        self._injection_log.append(entry)
        return entry

    def reset(self):
        """重置运行状态（seed 不变，日志清零，rng 重置）。"""
        self._rng = SeededRandom(self.seed)
        self._injection_log = []
        self._execution_step = 0
        self._parameter_action_count = 0
        self._parameter_action_index = 0
        self._deterministic_injected = False

    def set_execution_step(self, step: int) -> None:
        """Attach the current Agent action step to subsequent fault events."""
        self._execution_step = max(0, int(step))

    @classmethod
    def single_fault(cls, fault_name: str, intensity: str = "medium",
                     seed: int = 42, injection_step: Optional[int] = None) -> "FaultConfig":
        """Build a config with exactly one enabled fault for ablation studies."""
        valid_names = {
            "web_timeout", "web_http_error", "web_dom_missing", "web_popup_block",
            "gitlab_ci_offline", "gitlab_permission", "gitlab_conflict", "gitlab_quota",
            "agent_state_misjudge", "agent_param_error",
        }
        if fault_name not in valid_names:
            raise ValueError(f"Unknown fault '{fault_name}'")
        return cls(intensity=intensity, seed=seed, injection_step=injection_step,
                   **{fault_name: True})

    # ---- 工厂方法 ----

    @classmethod
    def off(cls) -> "FaultConfig":
        """创建无故障配置（对照组）。"""
        return cls(intensity="off", seed=0)

    @classmethod
    def low_all_web(cls, seed: int = 42) -> "FaultConfig":
        """低强度 + 全部 Web 故障。"""
        return cls(
            intensity="low", seed=seed,
            web_timeout=True, web_http_error=True,
            web_dom_missing=True, web_popup_block=True,
        )

    @classmethod
    def high_all(cls, seed: int = 42) -> "FaultConfig":
        """高强度 + 全部环境故障。"""
        return cls(
            intensity="high", seed=seed,
            web_timeout=True, web_http_error=True,
            web_dom_missing=True, web_popup_block=True,
            gitlab_ci_offline=True, gitlab_permission=True,
            gitlab_conflict=True, gitlab_quota=True,
        )
