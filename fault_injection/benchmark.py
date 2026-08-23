"""
BenchmarkRunner — agent-agnostic A/B 对照实验框架。

度量 agent 在故障注入下的性能损失。
不依赖任何特定 agent 实现，只需提供 (task → result) 的执行函数。

输出指标:
   - success_rate: 评估器判定的任务成功率；未提供评估器时等同于正常完成率
  - avg_steps: 平均步数
  - avg_time_sec: 平均耗时
  - avg_llm_calls: 平均 LLM 调用次数（含 planner）
  - degradation: 相对于对照组的性能损失百分比
"""

import time
import statistics
import uuid
from dataclasses import asdict
from dataclasses import dataclass, field
from typing import Callable, Optional

from fault_injection.config import FaultConfig
from fault_injection.proxy import FaultProxy
from fault_injection.taxonomy import classify_behavior


# ============================================================
# 单次试验结果
# ============================================================

@dataclass
class TrialResult:
    """单次试验结果"""
    task_id: str
    config_label: str            # "control" / "fault_low" / ...
    success: bool
    steps: int
    time_sec: float
    completed: bool = False      # Agent 是否正常结束，不代表答案正确
    architecture: str = "react"
    llm_calls: int = 0
    planning_calls: int = 0
    executor_calls: int = 0
    replanning_calls: int = 0
    answer: str = ""
    injection_count: int = 0
    total_delay_sec: float = 0.0
    error: Optional[str] = None
    experiment_id: str = ""
    model_profile: str = ""
    fault_layer: str = "none"
    fault_type: str = "control"
    fault_seed: Optional[int] = None
    behavior_category: str = ""
    tolerance_layer: str = ""
    recovery_steps: int = 0
    action_history: list[dict] = field(default_factory=list)
    injection_log: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Return a JSON/CSV-friendly record for one experiment trial."""
        return asdict(self)


# ============================================================
# 汇总指标
# ============================================================

@dataclass
class SummaryMetrics:
    """一组试验的汇总指标"""
    config_label: str
    num_trials: int
    success_rate: float         # 0.0 - 1.0
    avg_steps: float
    median_steps: float
    avg_time_sec: float
    avg_llm_calls: float = 0.0
    avg_planning_calls: float = 0.0
    avg_executor_calls: float = 0.0
    avg_replanning_calls: float = 0.0
    avg_injections: float = 0.0
    avg_delay_sec: float = 0.0

    # 性能损失（与 control 对比后填入）
    degradation_success: Optional[float] = None   # 成功率下降 (百分点)
    degradation_steps: Optional[float] = None     # 步数增加 (%)
    degradation_time: Optional[float] = None      # 耗时增加 (%)
    degradation_llm_calls: Optional[float] = None # LLM 调用增加 (%)

    @classmethod
    def from_trials(cls, label: str, trials: list[TrialResult]) -> "SummaryMetrics":
        if not trials:
            return cls(config_label=label, num_trials=0,
                       success_rate=0, avg_steps=0, median_steps=0, avg_time_sec=0)

        n = len(trials)
        successes = sum(1 for t in trials if t.success)
        steps = [t.steps for t in trials]
        times = [t.time_sec for t in trials]
        injections = [t.injection_count for t in trials]
        delays = [t.total_delay_sec for t in trials]
        llm_calls = [t.llm_calls for t in trials]
        planning_calls = [t.planning_calls for t in trials]
        executor_calls = [t.executor_calls for t in trials]
        replanning_calls = [t.replanning_calls for t in trials]

        return cls(
            config_label=label,
            num_trials=n,
            success_rate=successes / n,
            avg_steps=statistics.mean(steps),
            median_steps=statistics.median(steps),
            avg_time_sec=statistics.mean(times),
            avg_llm_calls=statistics.mean(llm_calls),
            avg_planning_calls=statistics.mean(planning_calls),
            avg_executor_calls=statistics.mean(executor_calls),
            avg_replanning_calls=statistics.mean(replanning_calls),
            avg_injections=statistics.mean(injections) if any(injections) else 0,
            avg_delay_sec=statistics.mean(delays) if any(delays) else 0,
        )


# ============================================================
# BenchmarkRunner
# ============================================================

class BenchmarkRunner:
    """
    Agent-agnostic 对照实验运行器。

    使用方式:
        def run_task(task, url, config):
            '''执行单次任务的函数，返回 TrialResult'''
            ...

        runner = BenchmarkRunner(run_fn=run_task)
        runner.add_config("control", FaultConfig.off())
        runner.add_config("fault_low", FaultConfig(..., web_timeout=True))
        runner.add_task("task1", "Find the price", "https://...")
        report = runner.run(trials_per_config=5)
        runner.print_report(report)
    """

    def __init__(self, run_fn: Callable, env_factory: Callable = None,
                 evaluator: Callable[[TrialResult], bool] = None,
                 experiment_id: str = "", model_profile: str = ""):
        """
        Args:
            run_fn: (task_id, task_desc, url, fault_config, trial_index) → TrialResult
                    执行单次任务并返回结果的函数
            env_factory: () → BrowserEnv, 创建新浏览器环境的工厂函数
            evaluator: 可选的正式评估器。传入 TrialResult，返回答案是否正确。
        """
        self._run_fn = run_fn
        self._env_factory = env_factory
        self._evaluator = evaluator
        self._experiment_id = experiment_id or f"exp_{uuid.uuid4().hex[:10]}"
        self._model_profile = model_profile
        self._configs: list[tuple[str, FaultConfig]] = []
        self._tasks: list[tuple[str, str, str]] = []  # (id, description, url)

    def add_config(self, label: str, config: FaultConfig):
        """添加一组故障配置。第一个添加的是 control。"""
        self._configs.append((label, config))

    def add_task(self, task_id: str, task_description: str, url: str):
        """添加一个评测任务。"""
        self._tasks.append((task_id, task_description, url))

    def run(self, trials_per_config: int = 3) -> dict:
        """
        运行全部对照实验。

        Returns:
            {
                "configs": {"control": SummaryMetrics, "fault_low": SummaryMetrics, ...},
                "tasks": {"task1": {"control": [...trial_results], "fault_low": [...]}},
                "degradation_matrix": {...},
            }
        """
        if not self._configs:
            raise ValueError("BenchmarkRunner requires at least one config")
        if trials_per_config < 1:
            raise ValueError("trials_per_config must be at least 1")

        report = {"configs": {}, "tasks": {}, "degradation": {}}

        all_trials_by_config: dict[str, list[TrialResult]] = {
            label: [] for label, _ in self._configs
        }

        for task_id, task_desc, url in self._tasks:
            task_trials: dict[str, list[TrialResult]] = {}

            for config_label, fault_config in self._configs:
                trials = []
                for trial_idx in range(trials_per_config):
                    # 每个 trial 重置 seed 以保持可复现
                    trial_config = FaultConfig(
                        intensity=fault_config.intensity,
                        seed=fault_config.seed + trial_idx,  # 不同 trial 不同 seed
                        enabled=fault_config.enabled,
                        web_timeout=fault_config.web_timeout,
                        web_http_error=fault_config.web_http_error,
                        web_dom_missing=fault_config.web_dom_missing,
                        web_popup_block=fault_config.web_popup_block,
                        gitlab_ci_offline=fault_config.gitlab_ci_offline,
                         gitlab_permission=fault_config.gitlab_permission,
                         gitlab_conflict=fault_config.gitlab_conflict,
                         gitlab_quota=fault_config.gitlab_quota,
                         agent_state_misjudge=fault_config.agent_state_misjudge,
                         agent_param_error=fault_config.agent_param_error,
                     )

                    print(f"  [{config_label}] {task_id} trial {trial_idx+1}/{trials_per_config} ...", end=" ")
                    result = self._run_fn(task_id, task_desc, url, trial_config, trial_idx)
                    # run_fn 只负责执行任务，报告分组标签以 runner 配置为准。
                    result.config_label = config_label
                    result.experiment_id = self._experiment_id
                    result.model_profile = result.model_profile or self._model_profile
                    result.fault_layer = trial_config.fault_layer
                    result.fault_type = trial_config.fault_label
                    result.fault_seed = trial_config.seed
                    if self._evaluator is not None:
                        try:
                            result.success = bool(self._evaluator(result))
                        except Exception as exc:
                            result.success = False
                            result.error = f"evaluator error: {exc}"
                    labels = classify_behavior(
                        fault_type=result.fault_type,
                        injection_log=result.injection_log,
                        action_history=result.action_history,
                        success=result.success,
                        completed=result.completed,
                        architecture=result.architecture,
                        replanning_calls=result.replanning_calls,
                    )
                    result.behavior_category = labels["behavior_category"]
                    result.tolerance_layer = labels["tolerance_layer"]
                    result.recovery_steps = labels["recovery_steps"]
                    status = "✅" if result.success else "❌"
                    print(f"{status} steps={result.steps} time={result.time_sec:.1f}s")
                    trials.append(result)

                task_trials[config_label] = trials
                all_trials_by_config[config_label].extend(trials)

            report["tasks"][task_id] = task_trials

        # 汇总每个 fault config
        for config_label, _ in self._configs:
            report["configs"][config_label] = SummaryMetrics.from_trials(
                config_label, all_trials_by_config[config_label]
            )

        # 计算 degradation（以第一个 config 为 control）
        control_label = self._configs[0][0]
        control_metrics = report["configs"][control_label]

        for config_label, metrics in report["configs"].items():
            if config_label == control_label:
                continue
            if control_metrics.success_rate > 0:
                metrics.degradation_success = round(
                    (control_metrics.success_rate - metrics.success_rate) * 100, 1
                )
            if control_metrics.avg_steps > 0:
                metrics.degradation_steps = round(
                    (metrics.avg_steps - control_metrics.avg_steps) / control_metrics.avg_steps * 100, 1
                )
            if control_metrics.avg_time_sec > 0:
                metrics.degradation_time = round(
                    (metrics.avg_time_sec - control_metrics.avg_time_sec) / control_metrics.avg_time_sec * 100, 1
                )
            if control_metrics.avg_llm_calls > 0:
                metrics.degradation_llm_calls = round(
                    (metrics.avg_llm_calls - control_metrics.avg_llm_calls)
                    / control_metrics.avg_llm_calls * 100, 1
                )

        report["degradation"] = {
            config_label: {
                "success_drop_pp": m.degradation_success,
                "steps_increase_pct": m.degradation_steps,
                "time_increase_pct": m.degradation_time,
                "llm_calls_increase_pct": m.degradation_llm_calls,
            }
            for config_label, m in report["configs"].items()
            if config_label != control_label
        }

        return report

    def print_report(self, report: dict):
        """格式化打印对照实验报告。"""
        control_label = self._configs[0][0]

        print(f"\n{'='*70}")
        print(f"📊 Agent 故障注入性能评估报告")
        print(f"{'='*70}")
        print(f"对照组: {control_label} | 任务数: {len(self._tasks)}")
        print()

        # 按 task 明细
        for task_id, task_trials in report["tasks"].items():
            print(f"┌─ 任务: {task_id}")
            for config_label, trials in task_trials.items():
                successes = sum(1 for t in trials if t.success)
                avg_steps = statistics.mean([t.steps for t in trials])
                avg_time = statistics.mean([t.time_sec for t in trials])
                avg_llm_calls = statistics.mean([t.llm_calls for t in trials])
                marker = " (control)" if config_label == control_label else ""
                print(f"│  {config_label}{marker}: {successes}/{len(trials)} 成功, "
                      f"avg {avg_steps:.1f} 步, avg {avg_llm_calls:.1f} LLM calls, "
                      f"avg {avg_time:.1f}s")
            print(f"└{'─'*50}")

        # 汇总
        print(f"\n┌{'─'*65}")
        print(f"│ 📈 汇总指标")
        print(f"├{'─'*65}")

        headers = ["Config", "成功%", "均步数", "均LLM", "均耗时", "Δ成功↓", "Δ步数↑", "Δ耗时↑"]
        print(f"│ {'':<12} {'':>6} {'':>7} {'':>7} {'':>7} {'':>8} {'':>7} {'':>7}")
        print(f"│ {headers[0]:<12} {headers[1]:>6} {headers[2]:>7} {headers[3]:>7} {headers[4]:>7} {headers[5]:>8} {headers[6]:>7} {headers[7]:>7}")
        print(f"│{'-'*65}")

        for config_label, metrics in report["configs"].items():
            marker = " ←" if config_label == control_label else ""
            d_succ = f"{metrics.degradation_success:+.1f}pp" if metrics.degradation_success is not None else "-"
            d_step = f"{metrics.degradation_steps:+.1f}%" if metrics.degradation_steps is not None else "-"
            d_time = f"{metrics.degradation_time:+.1f}%" if metrics.degradation_time is not None else "-"
            print(f"│ {config_label+marker:<12} "
                  f"{metrics.success_rate*100:>5.0f}% "
                  f"{metrics.avg_steps:>6.1f} "
                  f"{metrics.avg_llm_calls:>6.1f} "
                  f"{metrics.avg_time_sec:>6.1f}s "
                  f"{d_succ:>8} "
                  f"{d_step:>7} "
                  f"{d_time:>7}")

        print(f"└{'─'*65}")

        # 性能损失总结
        print(f"\n📉 性能损失 (Performance Degradation)")
        for config_label, deg in report["degradation"].items():
            print(f"  {config_label}:")
            if deg["success_drop_pp"] is not None:
                print(f"    成功率下降: {deg['success_drop_pp']:.1f} 百分点")
            if deg["steps_increase_pct"] is not None:
                print(f"    步数增加:   {deg['steps_increase_pct']:+.1f}%")
            if deg["time_increase_pct"] is not None:
                print(f"    耗时增加:   {deg['time_increase_pct']:+.1f}%")
            if deg["llm_calls_increase_pct"] is not None:
                print(f"    LLM调用增加: {deg['llm_calls_increase_pct']:+.1f}%")
        print()
