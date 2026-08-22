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
        """重置随机状态（重新设定相同 seed 即可恢复初始状态）。"""
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

    def __post_init__(self):
        self._rng = SeededRandom(self.seed)
        self._intensity_config = INTENSITY_PRESETS.get(
            self.intensity, INTENSITY_PRESETS["low"]
        )
        self._injection_log = []

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

        # 按概率决定
        return self._rng.should_inject(self.probability)

    def get_delay(self, fault_name: str = None) -> float:
        """获取当前强度的随机延迟（秒）。"""
        low, high = self.delay_range
        return self._rng.uniform(low, high)

    def record_injection(self, fault_name: str, detail: dict = None):
        """记录一次故障注入。"""
        entry = {
            "fault": fault_name,
            "step": len(self._injection_log) + 1,
            "timestamp": time.time(),
            "detail": detail or {},
        }
        self._injection_log.append(entry)
        return entry

    def reset(self):
        """重置运行状态（seed 不变，日志清零，rng 重置）。"""
        self._rng = SeededRandom(self.seed)
        self._injection_log = []

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
