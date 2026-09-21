# Web Agent 鲁棒性归因与架构优化实验设计

> 本文是通用设计说明。当前可执行矩阵、冻结任务和预算以
> [`experiment_roadmap.md`](experiment_roadmap.md) 与
> [`task_manifest_noauth4.json`](task_manifest_noauth4.json) 为准。

## 1. 研究目标

本课题研究 Web Agent 在 WebArena 任务中的故障敏感性、恢复行为和责任归因。核心问题：

1. 不同 LLM 对同一类 Web 故障的敏感性是否不同？
2. 不同 Agent 架构对故障恢复能力的影响有多大？
3. 环境、观测、规划、推理和动作层故障分别如何导致任务失败？
4. 针对观察到的失败模式进行修复后，成功率和恢复率是否提升？

核心实验变量为：

```text
Model × Architecture × Fault × Task × Replicate
```

正式结论必须基于同一任务、同一故障、同一独立重复下的配对结果；`fault_seed` 仅描述故障机制的随机状态，不等同于模型生成 seed。

## 2. 标准 Agent 基线

第一阶段冻结一个不带修复机制的标准基线，不在基线中加入针对性容错逻辑。

```text
框架：LangGraph StateGraph
架构：单 Agent ReAct
观测：Playwright Accessibility Tree
工具：WebArena 标准 9 工具
环境：WebArena-Verified
模型：实验指定模型
温度：固定
最大执行步数：固定
Prompt：固定版本
评估器：WebArena-Verified 确定性 evaluator
```

当前项目中的 `react` 作为标准基线；`plan_execute` 定义为单模型的 Planner → Executor → Replanner 多阶段工作流，不表述为多模型多 Agent 系统。两种架构比较时必须固定模型、任务集、故障、步数预算和 evaluator。

## 3. 故障分类

故障按照作用位置划分，避免把环境故障和 Agent 自身错误混为一类。

### 3.1 环境层故障

环境真实返回异常，但不修改 Agent 的内部状态。

| 故障 | 含义 | 当前对应实现 |
|---|---|---|
| `web_timeout` | 动作前网络延迟 | 已有 |
| `web_http_error` | 500/503 页面错误 | 已有 |
| `web_dom_missing` | 观测中元素缺失 | 已有 |
| `web_popup_block` | 弹窗遮挡任务页面 | 已有 |
| `gitlab_permission` | GitLab 权限拒绝 | 已有 |
| `gitlab_conflict` | 合并冲突 | 已有 |
| `gitlab_ci_offline` | CI Runner 离线 | 已有 |
| `gitlab_quota` | 配额耗尽 | 已有 |

### 3.2 观测层故障

Agent 接收到的页面信息不完整或不准确，但浏览器真实状态不被修改。

| 故障 | 典型表现 |
|---|---|
| `ax_tree_truncated` | AX Tree 截断 |
| `stale_observation` | 返回上一步页面观测 |
| `wrong_element_label` | 元素名称与真实名称不一致 |
| `critical_info_loss` | 删除任务所需关键文本 |

当前 `agent_state_misjudge` 实际更接近观测扰动，后续应归入这一层，而不是直接称为 Agent 内部故障。

### 3.3 Agent 执行层故障

Agent 的策略或工具调用出现错误。

| 故障 | 典型表现 |
|---|---|
| `tool_selection_error` | 选择错误工具 |
| `parameter_filling_error` | element_id 或参数错误 |
| `tool_format_error` | 工具参数格式错误 |
| `repeated_action` | 重复同一失败动作 |
| `premature_stop` | 未完成任务即调用 stop |
| `stale_element_retry` | 继续使用失效元素 ID |

当前 `agent_param_error` 应暂记为动作扰动；只有在注入 Agent policy 或模型输出后，才可称为 Agent 内部故障。

### 3.4 规划、记忆和上下文层故障

| 故障 | 典型表现 |
|---|---|
| `inexecutable_plan` | 计划与当前页面状态不一致 |
| `plan_not_updated` | 故障后仍执行旧计划 |
| `memory_loss` | 丢失任务约束或已完成步骤 |
| `context_length_violation` | 历史过长导致关键信息丢失 |
| `instruction_ambiguity` | 任务目标不清晰 |

## 4. 实验阶段

### 阶段 A：无故障 baseline

固定一个模型和 `react` 架构，运行代表性任务集，确认 Agent 的正常能力。

每个任务记录：

```text
task_success
completed
steps
llm_calls
tool_calls
tool_errors
invalid_actions
answer
time_sec
trace
```

如果 baseline 成功率过低，先不要进行故障归因。故障实验要求 control 具有足够的成功样本。

### 阶段 B：单故障敏感性实验

一次只启用一种故障：

```text
control
web_timeout
web_http_error
web_dom_missing
web_popup_block
gitlab_permission
gitlab_conflict
tool_selection_error
parameter_filling_error
critical_info_loss
```

每个条件使用多个 seed。主实验建议使用固定事件注入，例如第 2、4 或 6 个 Agent action 注入一次；概率注入可作为补充实验。

### 阶段 C：模型对比

固定：

```text
architecture = react
task set = same
fault = same
fault event/seed = same
max_steps = same
temperature = same
```

只改变模型：

```text
Model A
Model B
```

### 阶段 D：架构对比

固定模型，只改变架构：

```text
react
plan_execute
```

必须报告两种预算：

1. 相同 executor 步数预算。
2. 相同总 LLM 调用预算。

否则 `plan_execute` 多出 planner/replanner 调用，会同时改变架构和成本。

### 阶段 E：针对性修复

根据阶段 B-D 的失败轨迹选择修复，不预先加入所有机制。

| 失败模式 | 候选修复 |
|---|---|
| DOM 缺失后使用旧 ID | 强制刷新观测并校验元素 |
| 弹窗阻塞 | 弹窗识别和关闭策略 |
| HTTP 错误后停止 | 有限重试和退避 |
| 参数错误重复出现 | 工具参数校验和失败反馈 |
| 重复动作 | 动作循环检测 |
| 计划过时 | 重新规划或计划局部修订 |
| 上下文过长 | 历史摘要和状态压缩 |

修复前后必须使用相同任务、模型、架构、故障和 seed。

## 5. 行为和容错标签

每次故障后，基于 trace 为 Agent 标注一个主要行为：

```text
fault_detected
fault_ignored
stale_action_repeated
observation_refreshed
alternative_action_selected
plan_revised
retry_executed
task_abandoned
premature_stop
recovered
```

再标注容错来源：

```text
mechanism_level
rule_level
prompt_level
reasoning_level
no_tolerance
```

> **`thought` 的可用性限定（2026-09-21）：** 上述标签必须能从**可观测字段**（动作、工具结果、
> 步数、官方评测）导出。`thought` 已降为**可缺失的辅助字段**——推理型模型可能返回空正文
> （事实基础与裁决见 `experiment_roadmap.md` §7）。因此：
>
> - 不得存在**只靠 `thought` 才能判定**的标签；
> - `reasoning_level` 这类依赖正文的归因，只在有正文的 trial 上报告，并同时披露 `thought` 缺失率；
> - 主结果不跨模型比较依赖 `thought` 的主观标签。

示例：

```text
DOM missing
  -> observation_refreshed
  -> mechanism_level
  -> recovered
```

或：

```text
HTTP error
  -> retry_executed
  -> rule_level
  -> recovered
```

## 6. 指标

### 6.1 结果指标

```text
task_success_rate
completion_rate
answer_accuracy
state_accuracy
failure_rate
```

### 6.2 恢复指标

```text
fault_recovery_rate
recovery_steps
recovery_latency
first_recovery_action
retry_count
```

### 6.3 效率指标

```text
avg_steps
avg_llm_calls
avg_executor_calls
avg_planning_calls
avg_time_sec
tool_error_rate
invalid_action_rate
repeated_action_rate
```

建议把鲁棒性定义为故障条件相对于 control 的性能保持程度：

```text
R_success = success_rate_fault / success_rate_control
R_steps = avg_steps_control / avg_steps_fault
R_time = avg_time_control / avg_time_fault
```

同时报告原始值和相对值，不只报告一个综合分数。

## 7. 责任归因

不要直接使用“模型提升百分比 / 总提升百分比”作为责任比例，因为模型和架构存在交互作用。

最小实验设计为 2×2：

```text
Model A + react
Model A + plan_execute
Model B + react
Model B + plan_execute
```

每个故障分别计算：

```text
模型主效应
架构主效应
模型 × 架构交互效应
模型 × 故障交互效应
架构 × 故障交互效应
```

论文中可将责任表述为“效应贡献”而不是绝对因果责任：

```text
model_effect
architecture_effect
interaction_effect
fault_effect
```

当前正式矩阵每格使用 3 次独立重复；正式结果报告效应量和 95% 置信区间。相同任务上的模型/架构对比使用配对统计，并按 task 聚类或 bootstrap。

## 8. 实验数据格式

单次实验结果建议包含：

```text
experiment_id
site
model_profile
model_name
architecture
fault_layer
fault_type
fault_seed
injection_step
injection_count
behavior_category
task_success
completed
answer_correct
steps
llm_calls
planning_calls
executor_calls
invalid_actions
retries
recovery_steps
error_type
browser_version
```

建议：

```text
JSONL：保存完整逐步 trace
CSV：保存单次实验汇总，便于统计
```

目录结构可以采用：

```text
experiments/
├── raw_traces/
├── trial_results.csv
├── behavior_labels.csv
├── fault_catalog.csv
├── summaries/
└── figures/
```

## 9. 当前项目的实施优先级

当前代码已经具备：

```text
LangGraph ReAct 图
AX Tree 观测
9 个 Web 工具
多模型 profile
基础故障注入
JSONL trace
基础 Benchmark
```

下一步优先级：

1. 冻结并记录标准 ReAct baseline。
2. 将字符串答案匹配替换或补充为 WebArena 确定性 evaluator。
3. 将实验从组合故障改为单故障矩阵。
4. 让故障日志与 action、observation、recovery action 对齐。
5. 增加行为和容错标签。
6. 建立 `Model × Architecture × Fault × Seed` 批量运行配置。
7. 再实现针对性修复并进行同 seed 对照。

在第 1-6 步完成前，不建议对“模型责任比例”或“架构责任比例”做正式结论。
