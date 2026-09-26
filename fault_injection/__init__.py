"""
故障注入模块 — 通用环境故障注入，与 agent 实现完全解耦。

层次结构:
  FaultConfig  — 故障配置（概率、强度、seed、开关）
  FaultProxy   — 透明代理，在 BrowserEnv 上注入环境故障
  BenchmarkRunner — A/B 对照实验，度量 agent 在故障下的性能损失

三类环境故障:
  ① Web底层: 超时、500/503、DOM丢失、弹窗
  ② GitLab业务: CI离线、权限不足、合并冲突、配额耗尽

使用方式:
    # 单次跑
    proxy = FaultProxy(env, FaultConfig(intensity="medium", web_timeout=True))
    use_browser(proxy)

    # 对照实验
    runner = BenchmarkRunner(run_fn=my_run_task)
    runner.add_config("control", FaultConfig.off())
    runner.add_config("fault", FaultConfig(intensity="medium", web_timeout=True))
    runner.add_task("t1", "Find price", "https://...")
    report = runner.run(trials_per_config=5)
    runner.print_report(report)
"""

from fault_injection.config import (
    FaultConfig,
    SeededRandom,
    INTENSITY_PRESETS,
    STAGE_C_DEFAULT_INJECTION_STEP,
    STAGE_C_PARAMETER_ACTION_FAULTS,
    injection_step_mode,
    injection_step_units,
    resolve_injection_step,
    stage_c_injection_step,
)
from fault_injection.proxy import FaultProxy
from fault_injection.injector import FaultInjector
from fault_injection.benchmark import BenchmarkRunner, TrialResult, SummaryMetrics
from fault_injection.taxonomy import FAULT_CATALOG, classify_behavior, catalog_rows
