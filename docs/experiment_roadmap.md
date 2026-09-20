# 实验推进大纲

把 [`supplementary_experiment_plan.md`](supplementary_experiment_plan.md)（跑哪些格子）与
[`model_budget_policy.md`](model_budget_policy.md)（每格用哪个模型）合成一份可执行顺序，
并加入本轮排查发现的**先决条件**。

一句话：**先清 Stage 0 五个阻塞项，用已有 400 条 HAR 重评 evaluator，再按 T0 → C → E → F 跑；三个模型是分类因子，不预设能力顺序。**

> **2026-09-18 修订**：冻结的 400 条已全部保留 HAR，Stage A 从“重跑 400 条”改为
> **修 evaluator 后离线重评**。DeepSeek 官方已明确 V4.1 Flash 超过 V4 Pro，因此取消
> `4o-mini < Flash < Pro` 的先验序数编码，模型改为三水平分类因子。

---

## 0. 当前位置（诚实盘点）

| 已有 | 内容 | 状态 |
|---|---|---|
| `fault_matrices_v1` | gpt54 / react / 800 条行为记录 | 行为数据可用，**800 条全部没有 outcome**（`NOT_EVALUATED_NO_HAR`） |
| `official_subset_v1_final` | 400 trial / 8 task | **400/400 均有 HAR**；native 324 / fallback 76，**fallback 通过率 0/76** |
| 架构 pilot | 54（27 react + 27 plan_execute） | 可描述，**无法排名**；缺 per-role 计数器 |
| 正文数字 | `experiments/analysis/paper_v1/` + `official_trial_audit.csv` | ✅ 在，**不需要重跑** |

**所以：这 400 条不再原样重跑。** 先修复上游 `retrieved_data=null` schema 问题，
用现有 `agent_response.json + network.har` 离线重评；只有新 evaluator 无法消费旧产物时才补跑受影响的少量 task。

---

## 1. Stage 0 — 先决条件（跑任何新实验之前）

这四项不是实验，是让实验可用的前提。**0.1 阻塞预算决策，必须最先做。**

### 0.1 确认 trace 真的落盘 ⚠️ 最高优先级

`standard_agent/core/trace.py` 写的是 `os.getenv("TRACE_DIR", "traces")`——**相对于进程 CWD**，
不是仓库根。本轮在服务器上没找到任何 `.jsonl`。

**为什么这是阻塞项，而不是小事：**

1. §6 的**实测核价**（prompt caching + pro/flash 真实倍率）靠 `grep llm_provenance ... jsonl`
   汇总真实 token。**没有 jsonl，T0 报出来的 cost/trial 就不可信。**
2. `_trace_provenance` 写的 `llm_provenance` 事件**落进同一份 trace**。trace 不留，
   换模型后的来源证据也一起丢——而你正要做的正是模型对比。

**动作**：用 `find`（不要用 `**` 通配，非 globstar 的 bash 下它只等于一层 `*`）定位落盘目录；
确认 `TRACE_DIR` 的实际取值；确认一次 trial 到底写没写。

```bash
find / -type d -name traces 2>/dev/null
find / -name '*.jsonl' -not -path '*/site-packages/*' -not -path '*/node_modules/*' 2>/dev/null | head -50
grep -rn 'TRACE_DIR' scripts/ main.py 2>/dev/null
```

**通过标准**：跑 1 task × 1 replicate 之后，`TRACE_DIR` 下确实多出一个 `.jsonl`，且里面能 grep 到
`llm_provenance` 事件。

### 0.2 provenance 层 ✅ 已合入

提交 `fa55e9e` 已加入 `_trace_provenance`、调用点、`provider.extract_served_metadata`
和相应测试。服务器仍须用来源探针和一条真实 trial 验证 trace 实际落盘。

**为什么必须在换模型前完成**：DeepSeek 的模型 id 今年反复横跳（`deepseek-chat`/`deepseek-reasoner`
已停服、V4 Pro 下线又反转）。**没有 `served_model` 字段，三个 profile 是否对应三个目标模型无从证明。**

### 0.3 量具可信化（两个已知缺陷）

| 缺陷 | 影响 | 动作 |
|---|---|---|
| `evaluate_answer` 在 `expected_values` 为空时**任何完成都算成功** | 高估成功率 | 清点有多少 task 走这条分支；要么收紧，要么在正文里明写 |
| evaluator fallback 按**任务 schema** 触发，不是按故障 | native(324) 与 fallback(76) 是**互斥的任务划分**，不是同一批 trial 的两种打分 | 正文里把"分层"改述为"划分"；§10 的"不合并"要升级为"不能合并" |

### 0.4 把已知口径陷阱固化进 manifest

| 陷阱 | 事实 |
|---|---|
| `--fault-injection-step` | **绝不传**（会把 `agent_param_error` 从 step 1 挪走）。仅 Stage E 有意扫步时用 |
| `--job-timeout-minutes` | 默认 **45**。30 分钟只会节省超过 30 分钟的异常/慢作业，并不会降低正常 trial 的 token；却会选择性截断较慢的 `plan_execute`。token 上限由 `max_steps=20` 控制 |
| `--fault-intensity high` | 在确定性模式下对 **10 个故障里的 8 个是空操作**（只有 `web_timeout` 的延迟和 `web_dom_missing` 的删除数真正消费它）。**不要在正文里把强度当自变量** |
| 故障注入记录 | `injection_log` **从未落盘**；权威记录是 stdout 上的正则（`injection_count=(\d+)`）。这是量具的弱点，别把它说成强记录 |
| `agent_param_error` 步号 | 配置的 step 与记录的 step 差一（`config.py:279`）。与上一条同源 |

### 0.5 冻结两架构主设计 ✅

主实验只比较项目中已经定义清楚的 `react` 与 `plan_execute`。两水平架构因子足以检验
Model × Architecture 交互；不再为了增加自由度而构造有序的 `plan_only`。后者若以后实现，
只能作为机制消融，不能事后加入主实验。

正式运行前仍须用同一任务各做一次冒烟，并确认 per-role 计数器
（`planning_calls` / `executor_calls` / `replanning_calls`）完整落盘。

---

## 2. 设计：三个自变量与一个可检验的交互范式

```
M  模型    4o-mini / V4 Pro / V4.1 Flash            3 水平，**分类因子**
A  架构    react / plan_execute                        2 水平，**分类因子**
F  故障    3 种机制多样性三元组（Stage C 用）；已有 5 故障数据 + Stage E 支撑
```

架构不编码为有序强度。两水平设计的 M×A 交互直接回答：具体模型在 ReAct 与
Plan-and-Execute 之间的鲁棒性差异是否不同。计算量差异通过 LLM 调用、token、墙钟和成本单独报告。

**Stage C 是唯一能同时估计 M、A 与 M×A 的阶段**，也就是课题立项时要测的那个责任划分。

### 2.1 模型作为分类因子

DeepSeek 官方已报告 V4.1 Flash 在基准、性能、成本和总用时上超过 V4 Pro，
因此 `Flash < Pro` 不能再作为先验顺序。跨厂商的 4o-mini 也没有足够稳定的先验排名。

- T0 控制成功率用于画图、解释以及地板/天花板检查。
- 确证性分析把 `model` 作为三水平分类因子，检验模型主效应、架构主效应和 `M×A` 交互。
- 不对 T0 结果事后排序再做序数检验，避免循环论证。
- 若日后换成三个有独立先验证据的有序模型，再另行预注册序数对比。

### 2.2 判断范式（口径 a：只谈鲁棒性增益，不带成本）

你要的经验规则，形式化成**条件边际效应**：

| 符号 | 含义 |
|---|---|
| `Δ_M(A; i,j)` | 固定架构 A，比较两个具体模型 $i,j$ 的**鲁棒性差异** |
| `Δ_A(M)` | 固定模型 M，从 `react` 升到 `plan_execute` 买到的**鲁棒性增益** |

规则 = "**在哪个 (M, A) 角落，哪个方向的边际增益更大**"。这不是两句口号——
它就是 M×A 交互的符号与大小，**可检验**。

**三条预注册的落地形式**（看到数据**之前**写进 manifest，否则是选择性报告）：

1. 先检验总体 `M×A` 交互；显著后再报预注册的成对模型对比和架构简单效应。
2. 若某一模型上 `Δ_A(M)` 更大，只说“架构改造对该具体模型更有效”，不上升为“弱模型通常更受益”。
3. 总体交互不显著 → 报告为**设计的功效不足**，不是"模型与架构等价"。

口径 (a) 意味着**不需要任何价格表**，代价是结论只说"鲁棒性从哪来"，不说"划不划算"。

### 2.3 T0 之后的闸门（预注册，看到 T0 之前就写下来）

口径 (a) 的**致命弱点**：控制成功率在两端撞**地板/天花板**，而交点恰好落在两端。

- `4o-mini` 控制率 ≈ 0 → 没有可退化的余地，故障效应被压成 0；
- `Pro` 控制率 ≈ 100% → 没有可提升的余地，架构增益被压成 0。

> **闸门**：T0 跑完立刻判定（此时大钱还没花）。若落在可用带宽 `[15%, 85%]` 内的模型
> **少于 2 个**，则把**主结果变量改为过程量**（步数、重规划次数、工具错误数、恢复时间），
> 成功率降为辅助。过程量的地板效应弱得多，范式仍可识别。

### 2.4 两个前置条件（必须在跑之前成立，事后补不上）

1. **等步预算**：所有臂都用 `--max-steps 20`。
2. **等时预算要如实承认**：等步 ≠ 等时。控制臂里 `plan_execute` 平均 364.8 s vs ReAct 77.4 s，
   **同一名义预算对一臂是 ~4.7× 的时间与 token**。所以产物**必须**记 per-role 计数器
   （`planning_calls` / `executor_calls` / `replanning_calls`）——**现有架构 pilot 一个都没记，
   所以它撑不起"等 LLM 预算"的说法。**这是 Stage C 跑之前要补的埋点。

---

## 3. 阶段（按"改变已有数字的能力"排序，不按新颖度）

### Stage A — evaluator 重评（**0 个新 agent trial**）
- 第一选择始终是锁定版本和校验和的官方 WebArena-Verified evaluator。
- 冻结的 400 条已有完整 HAR；先用最新版官方 evaluator 对
  `agent_response.json + network.har` 离线重评，并比较新旧 verdict。
- 仅当官方 evaluator 对已知 `retrieved_data=null` schema 仍无法执行时，才启用本仓库的
  compatibility fallback。输出必须记录 `evaluator_path=native|compatibility`；论文不得把 fallback
  称为官方评分，并分别报告 native 与 compatibility 结果及敏感性分析。
- 只有 evaluator 无法消费旧产物时才生成定向补跑清单；不预留整体 400 条预算。

### Stage C 任务样本（冻结的 4 个免登录公开任务）

任务清单见 [`task_manifest_noauth4.json`](task_manifest_noauth4.json)：Shopping 2、Reddit 1、
GitLab 公共页面 1；其中 retrieval 2、navigate 2。Map 因服务器无法配置其大型外部数据卷，
在 T0 前预注册排除；同时排除所有需要认证或改变站点状态的任务。

该选择降低认证失效、环境部署和跨 trial 状态污染，但外推范围很窄：正文只能把结果描述为
**三个站点、四个公开只读任务上的小样本研究**。不得外推到整个 WebArena、Map 或登录后
mutation 任务。只有 4 个 task cluster，因此任务层泛化和交互显著性均标为探索性。

### Stage C — Model × Architecture（**范式主实验**）
- **3 模型 × 2 架构** × 3 故障（`web_http_error` / `agent_param_error` / `web_dom_missing`），
  4 任务 × 3 次独立重复 × (故障臂 + 配对控制臂)。任务冻结在
  [`task_manifest_noauth4.json`](task_manifest_noauth4.json)。
- trials **432** / ≈**8.2 h**
- 模型：**三个都要**。它们是三水平分类因子，用来检验交互是否只属于某一对模型。

| 架构 | s/trial | trials | ≈wall |
|---|---|---|---|
| `react` | 159（实测） | 216 | 2.0 h |
| `plan_execute` | 500（实测推导） | 216 | 6.2 h |

**要砍按重复次数 → task → fault：** 3→2 得 288 trials ≈ **5.5 h**。

**故障是最后才动的**：它虽不进入 §2.2 的确证性对比，却是 M×A 效应得以**泛化**的 replicate 维度。
砍到 1 个，"模型-架构责任划分"就降格成"在 `web_http_error` 下的责任划分"。

**三向 M×A×F 交互预注册为探索性**——只有 4 个 task cluster，不能支撑稳定的三向交互推断；
只报描述性模式与逐格置信区间。

### Stage B 已并入 C；Stage D 取消
- Stage B 的存在理由是"单模型下模型效应退化"。C 现在有 3 级模型，该理由消失。
- 原 Stage D 是任务外推检验。服务器无法运行 Map，用户又将当前正式任务冻结为 4 个，因此
  Stage D 没有被 C 吸收，而是明确取消；任务层外推不足作为主要 limitation。

### Stage E — 注入步敏感性（审稿人一定会压的混淆）
- 把 `--fault-injection-step` 扫 **{1,2,3}**，针对 Stage C 的三个故障。step 2 已覆盖，新增两步。
- 4 个冻结任务下新增 trials **240** / ≈**2.2 h** / 模型：**Flash**
- **这是唯一有意传 `--fault-injection-step` 的阶段**，且它统一施加，`agent_param_error` 会离开默认位置——**这是有意的，记进 manifest**。

### Stage F — 修复消融
- 四类候选修复（错误感知重试+退避、参数校验、陈旧观测检测、不可观测状态的终止策略）在现有 harness 里可测。
- **修复臂与未修复臂必须在同一次会话里跑**：五个 per-fault 控制臂是**同一配置**却跨 **2.17×** 的
  平均墙钟（103.7–224.6 s），顺序跑会让机器漂移冒充修复效应。
- 4 个冻结任务下 trials **400** / ≈**3.7 h** / 模型：**Pro 或 Flash**（取决于预算）
- **目标要按 §5.3 定，不是按算力定**：在观测到的 discordance 下，10 pp **成功率**提升需要
  ~181 对/臂——所以这个 n 下现实的目标是**过程成本**改善。

---

## 4. 模型分配

### 4.1 ⚠️ `model_budget_policy.md` 的 GPT 框架和旧能力梯度均已作废

那份文档把 GPT 定位成**第三个 vendor 的复现证据**，为它设计了 T0/T1′/T1/T2 脊柱，
省下约 1160 trials。**这个框架的前提在 `Pro / Flash / 4o-mini` 这个模型集下已经不成立：**

- `4o-mini` 不是可选的 vendor 附加项，而是第三个模型分类水平；
- 它必须与另外两个模型**完全交叉**（同 task、同 replicate、同故障、同架构），
  否则 `M×A` 的估计对象会改变；
- 于是**"省下 GPT 只跑脊柱"这个省法，省掉的正是主实验的一整个能力层级。**

**动作**：`model_budget_policy.md` §0、§2 的四档表、§7 的三个分支，标记为**上一版遗留**；
本文 §4.2 取代之。但在改那份文档之前，先把下面的混同问题定掉。

### 4.2 不设能力顺序，不得声称厂商效应

```
V4 Pro      (DeepSeek) ─┐
V4.1 Flash  (DeepSeek) ─┼─ 三个分类水平，不预设全序
4o-mini     (OpenAI)   ─┘
```

DeepSeek 官方报告 V4.1 Flash 超过 V4 Pro，所以旧文档中 `Flash < Pro` 的标签作废。
同时，4o-mini 与两个 DeepSeek 模型的跨厂商先验排名也不足以支撑序数编码。

- **可以**说三个具体模型在本任务分布上有什么差异；
- **不可以**从这三点推出一般的能力单调趋势；
- **不可以**说任何 `OpenAI vs DeepSeek` 的厂商结论。

**这一条要写进正文的 Limitations**，否则审稿人会替你写。

若日后确实要 vendor 结论，唯一出路是加一个**同能力档的跨厂商模型**（例如另一个厂商的旗舰），
那是新的一层，不在本轮。

### 4.3 阶段 → 模型

| 阶段 | 模型 | 理由 |
|---|---|---|
| A（evaluator 重评） | **现有 gpt54 产物** | 离线重评，不发起新 agent trial |
| C（范式主实验） | **三个全上** | 三水平分类因子，缺一层就改变估计对象 |
| E（注入步） | **Flash** | 混淆检查，只需一个模型；Flash 便宜 |
| F（修复消融） | **Pro 或 Flash** | 取决于 §4.4 |
| 控制锚 T0 | **三个全上** | §2.1 的刻度由它产生，且它是 §2.3 闸门的输入 |
| 来源核验 | 三个 profile | 探针 5 项 × `--repeat 3`，~5 min |

**投入顺序：来源探针 → T0（三模型控制锚）→ 按 T0 实测成本由低到高补齐 Stage C。**

理由：T0 最便宜，且**它是 §2.3 那个闸门的唯一输入**——先花 1.2 h 决定要不要花 45 h。
三模型 T0 之后按**实测便宜到贵**投入，不再用旧产品档位推定顺序：
**设计如果是退化的，你要用 ¥ 而不是 $ 发现它。**

### 4.4 三条硬约束（违反则这批数据不可用）

1. **配对是刚需**——同一 `(fault, task, replicate_id, architecture)` 下三个模型必须都有 trial。
   所有 Δ 与 McNemar 都在**配对单元**上算。
2. **削减必须跨模型一致**——可以全体 5 故障减到 3（那就是 Stage C），不可以只给某个模型减。
3. **削减顺序：重复次数 → task → fault，不可颠倒。** `fault_seed` 只控制故障机制；模型供应商
   未必接受随机 seed，所以论文将这些运行称为独立重复，而不是模型随机种子。

---

## 5. 预算

### 主方案（3 次独立重复）

| Stage | 买什么 | trials | ≈wall |
|---|---|---|---|
| **T0** | 3模型×2架构×4任务×1次控制冒烟（§2.3 闸门输入） | 24 | 约 0.5 h |
| A | 修 evaluator + 对已有 400 条离线重评 | **0 新 trial** | 评估开销，不计 agent wall-clock |
| **C** | **M×A 小样本主实验**（3模型×2架构×3故障×4任务×3重复×2臂） | **432** | **8.2 h** |
| E | 注入步敏感性（仍只在 `react`） | 240 | 2.2 h |
| F | 修复消融 | 400 | 3.7 h |
| ~~B~~ / ~~D~~ | B 并入 C；D 因四任务范围取消 | — | — |
| | **合计（不含 T0；其控制运行可纳入 C）** | **1 072 新 trials** | **≈14.1 h** |

### 唯一被批准的削减：独立重复 3 → 2

| | trials | ≈wall |
|---|---|---|
| 主方案（C 用 3 次重复） | 1 072 新 trials | **≈14.1 h** |
| **削减方案（C 用 2 次重复）** | **928 新 trials** | **≈11.4 h** |

削减**只动独立重复次数**（顺序：replicate → task → fault）。
代价是 MDE 变宽，**必须如实报告**——这正是 §9 那条"每个零结果都配功效分析"的适用场合。

**不要只因为事后控制成功率高/低就删掉某个模型。** 若 T0 显示端点健康，
三个分类水平都应完成相同的配对格子；否则 `M×A` 的估计对象将改变。

**墙钟是 6 worker 的忙碌时间，不是日历时间**，且假设 WebArena 站点、evaluator、API 都在线。

---

## 6. 成本：已决定，不再讨论；只留一处**仍需测量**的东西

模型集已定（`Pro / Flash / 4o-mini`），**没有档位选择问题**。上一版的
"可负担 trials → T2/T1/T1′/T0"判据与 GPT 档位决策**一并作废**。

仍然要量的只有一件，而它**不属于成本决策，属于量具**：

> **prompt caching 与 pro/flash 实测倍率。** WebArena 的观测是成块 AX Tree，
> ReAct 连续步之间共享极长前缀。不核实这一条，**T0 实测的 cost/trial 本身就不可信**。
> 它依赖 `llm_provenance`，而后者依赖 **Stage 0.1**。

核价日期、`served_model` 探针结果、T0 实测 cost/trial 三者仍记进 manifest——
**不是为了砍预算，是为了在稿子里报得出"我们到底跑了什么"。**

---

## 7. ~~明确不跑 GPT 的三个分支~~（上一版遗留）

这三条的前提是"GPT 是可选的第三个 vendor"。在 `Pro / Flash / 4o-mini` 下
**没有可选模型**——三个都是设计的一部分。§4.1 已说明该框架为何作废。

保留其中**唯一仍然成立**的一条：

> **若某个模型的 T0 退化**（控制率 ≈ 0 或 `protocol_violation` 频发），
> **先修 harness，不要继续跑它的大格。** 加数据不会修好坏掉的 harness。
> 且此时 §2.3 的闸门大概率已经触发——主结果变量应转过程量。

`model_budget_policy.md` §0/§2/§7 在改写前**请勿再引用**。

---

## 8. 每阶段后

```bash
python3 scripts/paper_tables.py            # 表、图与正文宏
python3 -m pytest tests/test_stats_core.py # 统计行为不变
```

`scripts/paper/` 读 `experiments/` 下的产物；换阶段只需改
`scripts/paper/make_tables.py` 里的 `official_root` / `matrix_root` / `arch_root` 三个常量。
**正文没有任何手抄数字**，重跑管道即可传播新数据。

---

## 9. 铁律（不因加数据而放松）

- **不合并 evaluator 路径。** 报 native 层，pooled 只放在旁边；且要记住两者是**任务划分**，不是两种打分。
- **每个零结果都配功效分析。** 30–31 对 native 上 MDE 是**无定义**，不是"大"。
  跨厂商若不显著，那是**设计的性质**，不是"两厂商无差异"。
- **计时声明只用配对内差值。**
- **不把成本混进成功率声明**（`(fault, task, replicate_id)` 键不同的两批不能 join 成 cost-per-success）。
- **削减规则预注册**：看到数据**之前**把"独立重复 3→2"这条写进 manifest 或带日期的笔记。

本轮新增三条（以下以 2026-09-18 修订为准）：

- **模型按分类因子检验。** 控制率只用来画图、解释和触发地板/天花板闸门；
  不做 `-1/0/+1` 序数编码，不用 T0 结果事后构造能力顺序。

- ~~旧版：控制率作为有序能力轴~~（已作废）。旧版的问题是：3 个点、每点 5–6 pp 误差，
  对 x 回归会产生 **errors-in-variables 衰减**，把交互效应系统性拉向 0。
  因此确证性检验改为三水平分类模型的总体 `M×A` 交互及预注册的成对对比。
- **地板/天花板闸门预注册。** §2.3 的 `[15%, 85%]` 判据必须在**看到 T0 之前**写进 manifest。
  事后再说"因为撞了地板所以改用过程量"是选择性报告。
- **不得声称能力单调趋势或厂商效应。** 当前三模型只支持具体模型间的分类对比。

---

## 10. 现在立刻可做的四件事

0. 对冻结的 4 个任务做无凭据预检；任何登录重定向或 official evaluator 不可评分都必须在
   T0 前替换并记录，T0 后不得再改任务集。
1. `find` 定位 trace 落盘目录（Stage 0.1）——**它阻塞 provenance 证据，压过一切**
2. 对已有数据跑一次审计：
   ```bash
   grep -h '"event": "replan_decision"' traces/*.jsonl \
     | grep -o '"decision": "[^"]*"' | sort | uniq -c | sort -rn
   ```
   （趁新代码还没产生新数据——旧 trace 里记的是模型原样输出）
3. 把 provenance 层整理成对着 `6277b1c` 的 patch（Stage 0.2）
