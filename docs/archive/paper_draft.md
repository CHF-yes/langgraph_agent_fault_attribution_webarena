# 历史草稿：基于 LangGraph 的 Web Agent 故障归因与恢复能力研究

> 本文档仅供研究演进追溯，不是当前投稿或实验执行入口。
> 正式论文见 `paper/iclr2027/`，当前实验计划见 `docs/experiment_roadmap.md`。

> 初稿版本：2026-08-27；`[待补]` 表示当前仓库没有足够数据。

## 摘要

Web Agent 在动态网页、异步加载、弹窗、权限限制和网络异常下执行多步任务时，失败责任不能仅由最终成功率判断。本文构建了一个基于 LangGraph 的 WebArena 浏览器智能体，集成标准 ReAct 与 Planner-Executor-Replanner 工作流、Playwright Accessibility Tree 观测、九类网页工具、透明故障注入、JSONL 轨迹和 WebArena-Verified 官方评估适配器，用于分析故障敏感性、恢复行为和架构成本。

当前仓库包含 48 条控制基线、400 条带官方评估的故障子集、800 条行为矩阵和 54 条架构 pilot。官方故障子集显示，参数错误的成功率由 77.5% 降至 62.5%，DOM 缺失由 67.5% 降至 65.0%；HTTP error、popup 和 timeout 在当前样本中没有显示稳定的成功率下降。由于部分记录使用兼容性 fallback，且外部模型服务不保证执行记录的 seed，这些结果属于初步观察。正式版本将补充 native evaluator-only 实验、配对统计、置信区间和修复前后对照。

**关键词：** Web Agent；LangGraph；WebArena；故障归因；故障恢复；浏览器自动化

## 1 引言

大语言模型驱动的 Web Agent 已能完成搜索、信息检索、论坛操作和管理后台任务，但真实网页具有动态 DOM、异步请求、弹窗和服务异常。Agent 失败时，需要回答：故障来自环境、观测、工具参数、规划还是答案格式？Agent 是否观察到故障并采取恢复动作？更复杂的规划架构是否提高鲁棒性，还是只增加调用成本？

本文研究四个问题：

- **RQ1：** 不同 Web、观测和动作故障如何影响成功率与执行成本？
- **RQ2：** 故障后有哪些可观察的恢复行为？
- **RQ3：** ReAct 与 plan-and-execute 的成功率、调用成本和延迟有何差异？
- **RQ4：** 针对性容错机制是否能提升恢复能力？

当前版本完成 RQ1-RQ3 的工程验证和初步实验，RQ4 留待后续完成。

## 2 系统设计

### 2.1 Agent 工作流

标准 ReAct 图为 `START -> agent -> tools -> agent -> END`。每轮模型读取任务、URL、Accessibility Tree 和历史消息，生成至多一个工具调用。工具返回新的页面观测后进入下一轮。

plan-and-execute 图为 `START -> planner -> executor -> tools -> replanner`，必要时回到 planner。Planner 生成结构化子目标，Executor 每轮执行一个动作，Replanner 决定推进、结束或重规划。该架构是单模型多阶段工作流，不是多模型多 Agent 系统。

### 2.2 环境、工具和轨迹

Playwright 管理真实浏览器，页面信息使用 Accessibility Tree。工具包括 `click`、`type_text`、`scroll`、`goto`、`go_back`、`go_forward`、`select_option`、`hover` 和 `stop`。每步记录任务、URL、模型调用、工具参数、观测、错误、答案和故障日志，并可保存为 JSONL trace。

### 2.3 故障模型

| 故障 | 注入层 | 语义 |
|---|---|---|
| `web_dom_missing` | 观测 | 从 AX Tree 删除元素 |
| `web_popup_block` | 观测 | 插入合成弹窗 |
| `web_http_error` | 结果/观测 | 替换为合成 500/503 页面 |
| `web_timeout` | 动作前 | 注入延迟，不是真实请求超时 |
| `agent_param_error` | 动作 | 篡改工具参数，属于动作层扰动 |

项目还实现 GitLab 权限、CI、冲突和配额故障，但尚未进入当前官方主结果。

**【建议图 1】系统架构图：** 画出任务、LangGraph、工具、Fault Proxy、WebArena、AX Tree、Trace 和 Evaluator 的数据流，并标注三个注入点。

**【建议图 2】故障分类图：** 展示环境、观测、动作、规划/记忆四层及本文已实测故障。

## 3 实验方法

### 3.1 对照设计

正式变量为 `Model × Architecture × Fault × Task × Seed`。任务、起始 URL、Prompt、模型温度、最大 Executor 步数、浏览器环境和 evaluator 应固定。每个 fault trial 只启用一种故障，并记录实际注入次数和注入步骤。

### 3.2 数据集与批次

| 数据层 | 规模 | 用途 | 评估状态 |
|---|---:|---|---|
| ReAct control baseline | 16 任务 × 3 = 48 | 控制能力参考 | 官方 evaluator；25/48 |
| Behavioral fault matrix | 5 故障 × 16 任务 × 5 seed × 2 = 800 | 步数、耗时、完成和行为 | 无 HAR，不纳入官方成功率 |
| Official fault subset | 5 故障 × 8 任务 × 5 seed × 2 = 400 | 主要 fault 结果 | 官方 evaluator；256/400 |
| Architecture pilot | 3 任务 × 3 seed × 2 架构 × 3 条件 = 54 | 初步架构比较 | 官方 evaluator；13/54 |
| Recovery subset | 5 故障 × 3 任务 × 3 seed = 45 | 探索性恢复标签 | 日志启发式 |

**【建议表 1】** 增加每批次的 commit、dataset/prompt hash、evaluator 版本、native/fallback 数量和原始文件位置。

### 3.3 指标和统计

主要指标为 `task_success_rate`、`completion_rate`、`answer_accuracy`、平均步数、LLM calls、耗时、恢复率和恢复延迟。鲁棒性保持率定义为：

```text
R_success = success_rate_fault / success_rate_control
R_steps   = avg_steps_control / avg_steps_fault
R_time    = avg_time_control / avg_time_fault
```

正式版本应使用任务/seed 配对 bootstrap 或 McNemar 检验，并报告 95% CI；当前仓库尚未生成这些统计量。[待补：检验方法、CI 和多重比较校正]

## 4 实验结果

### 4.1 Control baseline

48 条冻结的 ReAct control 记录中，官方 evaluator 成功 25 条，成功率 **52.08%**。其中 28 条使用 compatibility fallback，20 条为 native evaluator。两类记录不能合并解释。

**【建议表 2】Baseline 分层结果**

| 指标 | 全部 | Native | Fallback |
|---|---:|---:|---:|
| Trials | 48 | 20 | 28 |
| Successes | 25 | 17 | 8 |
| Success rate | 52.08% | 85.00% | 28.57% |
| 平均步数 | [待补] | [待补] | [待补] |
| 平均耗时 | [待补] | [待补] | [待补] |

### 4.2 官方 fault subset

每个 fault 的 control 和 fault 各 40 条记录，审计显示每个故障 trial 正好注入一次。

**【建议表 3】主结果**

| 故障 | Control | Fault | 差异 | 配对胜/负/平 |
|---|---:|---:|---:|---:|
| DOM missing | 27/40 (67.5%) | 26/40 (65.0%) | -2.5 pp | 7/8/25 |
| Popup block | 23/40 (57.5%) | 24/40 (60.0%) | +2.5 pp | 6/5/29 |
| HTTP error | 23/40 (57.5%) | 25/40 (62.5%) | +5.0 pp | 7/5/28 |
| Timeout | 26/40 (65.0%) | 26/40 (65.0%) | 0.0 pp | 3/3/34 |
| Parameter error | 31/40 (77.5%) | 25/40 (62.5%) | -15.0 pp | 1/7/32 |

当前最明显的负向信号是参数错误。其他故障的变化不能解释为“故障有益”，更可能受随机采样、任务难度和有限样本影响。

**【建议图 3】** control/fault 成功率点图或森林图，带 95% CI 和零差异线。
**【建议图 4】** 任务 × 故障配对热图，显示每个任务的成功变化。

### 4.3 Behavioral matrix

800 条矩阵没有 HAR，不能作为官方 WebArena 成功率数据，只能用于行为、完成、步数和耗时分析。HTTP error 平均步数由 6.06 增至 10.29，平均耗时由 224.55 秒增至 374.41 秒；参数错误平均步数由 6.38 增至 8.68。其他故障的完整统计见 `[待补]`。

**【建议表 4】** 列出每个故障、条件、完成数、平均步数、LLM calls、耗时和 `NOT_EVALUATED_NO_HAR`。
**【建议图 5】** 每类故障的 control/fault 步数和耗时成对柱状图。

### 4.4 架构 pilot

当前 54 条 pilot 的结果如下：

| 架构 | 条件 | 成功率 | 平均步数 | 平均 LLM calls | 平均耗时 |
|---|---|---:|---:|---:|---:|
| ReAct | Control | 4/9 (44.4%) | 5.00 | 5.00 | 77.4 s |
| Plan-execute | Control | 0/9 (0.0%) | 3.11 | 6.89 | 364.8 s |
| ReAct | DOM missing | 4/9 (44.4%) | 4.89 | 4.89 | 82.6 s |
| Plan-execute | DOM missing | 1/9 (11.1%) | 4.22 | 4.22 | 516.4 s |
| ReAct | HTTP error | 3/9 (33.3%) | 8.00 | 8.00 | 218.9 s |
| Plan-execute | HTTP error | 1/9 (11.1%) | 5.22 | 5.22 | 788.5 s |

该结果只说明当前配置下 plan-execute pilot 成本较高，不能下普遍性架构结论。当前脚本与历史 54 条结果的任务/seed 配置还不完全一致，需修正后再复现。

**【建议表 5】** 分解 Executor、Planner、Replanner 和总调用次数。
**【建议图 6】** 架构 × 条件成功率和耗时图，明确标注 pilot。

### 4.5 恢复行为

45 条 recovery subset 中，36 条最终官方成功，探索性恢复率为 **80.0%**。现有日志规则把相同工具/参数的重复调用近似为 retry 和 stale action repeat，因此标签需要人工复核；最终成功也不自动等于“由故障恢复导致”。

**【建议表 6】** 按故障列出恢复、观测刷新、重试、替代动作和未恢复数量。
**【建议图 7】** 状态流图：`fault injected -> observation refreshed -> retry/alternative action -> success/failure`。

## 5 讨论

参数错误可能更容易造成任务失败，因为 AX Tree 中的元素 ID 和工具参数一旦被篡改，后续动作需要重新定位。HTTP error 在行为矩阵中显著增加步数和耗时，说明最终成功率不能代表完整故障成本。Popup、timeout 和 HTTP error 的官方成功率没有稳定下降，提示模型随机性、注入位置和任务异质性需要纳入分析。Plan-execute 的额外规划调用可能导致明显延迟，并可能在页面状态变化后执行过时计划。

严格归因应同时具备：故障按预定步骤注入、轨迹存在相关观测/工具错误、故障后动作可识别、官方 evaluator 给出最终结果。当前项目已具备注入审计和 evaluator 记录，但恢复行为仍主要依赖日志规则。

## 6 必须修正与补实验

### 6.1 代码和数据修正

1. 修复 benchmark 输出丢失真实任务 `task_type` 和 `results_schema` 的问题；
2. 分离宽松 substring completion 与官方完整结构校验；
3. 统一架构实验脚本、manifest 和历史 54 条结果；
4. 每条 trial 保存 response、HAR、official evaluator、injection log 及 hash；
5. 增加无需外部 LLM 的 golden pipeline test。

### 6.2 初稿最低补实验

| 实验 | 配置 | 数量 |
|---|---|---:|
| Golden pipeline | 1 任务，control + 1 fault，1 seed | 2 |
| Fault matrix | 3 任务 × 3 seed × control/3 faults | 36 |
| Architecture pilot | 3 任务 × 3 seed × 3 条件 × 2 架构 | 54 |

优先故障为 `web_dom_missing`、`web_http_error`、`agent_param_error`；有余力再加 popup 和 timeout。GitLab 故障、多模型、16 任务全矩阵、修复前后 ablation 和人工双标注可留到后续工作。

## 7 局限性

当前版本存在：fallback 与 native evaluator 混用；行为矩阵无 HAR；provider 不保证 seed 确定性；合成故障不等同真实服务故障；架构 pilot 样本较小；恢复标签为日志启发式；原始 HAR、完整 trace 和认证状态未提交；当前代码和历史架构结果配置尚未完全一致。

## 8 结论

本文完成了一个可记录轨迹、支持透明故障注入和官方评估的 LangGraph WebArena Agent 框架。初步结果显示，参数错误具有最明显的成功率下降信号，HTTP error 更明显地增加步数和耗时，plan-execute 在当前配置下带来额外调用和延迟成本。由于评估 fallback、随机采样和样本量限制，这些结果暂不能支持普遍性结论。正式版本应先修复任务 schema、成功判定和实验配置，再补充 native evaluator-only 配对实验、置信区间、任务级分析和容错修复实验。

## 附录：最终建议图表清单

**表格：** 数据集与配置、架构与工具、故障分类、native baseline、fault matrix、任务级配对结果、架构成本、修复前后恢复率。
**图：** 系统架构、故障分类、成功率森林图、任务×故障热图、步数/耗时图、架构成本图、恢复状态流图、修复前后失败类别图。

## 附录：仓库证据

- 项目总览：`docs/project_overview.md`
- 实验设计：`docs/experiment_design.md`
- 官方 subset：`experiments/analysis/official_subset_v1_final/`
- 行为矩阵：`experiments/analysis/fault_matrices_v1/`
- 架构 pilot：`experiments/analysis/architecture_gpt54_react_planexecute_v1/`
- 恢复分析：`experiments/analysis/fault_subset_45/`
- Agent 图与节点：`standard_agent/core/graph.py`、`standard_agent/core/nodes.py`
- 故障注入：`fault_injection/config.py`、`fault_injection/injector.py`
