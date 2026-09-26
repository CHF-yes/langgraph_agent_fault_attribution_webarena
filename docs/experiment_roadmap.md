# 实验推进大纲

把 [`supplementary_experiment_plan.md`](supplementary_experiment_plan.md)（跑哪些格子）与
[`model_budget_policy.md`](model_budget_policy.md)（每格用哪个模型）合成一份可执行顺序，
并加入本轮排查发现的**先决条件**。

一句话：**先清 Stage 0 阻塞项，用已有 400 条 HAR 重评 evaluator，再按 T0 → C → E → F 跑；主实验两模型 × 16 任务，第三模型在共同 8 任务上验证。**

> **2026-09-21 修订**：冻结的 400 条已全部保留 HAR，Stage A 从“重跑 400 条”改为
> **修 evaluator 后离线重评**。模型集合中的 4o-mini 改为阿里云百炼 `qwen3.8-flash`；
> 2026-09-21 再修订：DeepSeek Flash 与 Qwen3.8 Flash 进入 16 任务主实验；DeepSeek Pro
> 只在预先固定的 8 个共同任务上验证。三模型不预设能力顺序；不改写历史 GPT 产物。

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

### 0.7 `THOUGHT:` 契约违规口径 ✅ 已裁决（2026-09-21）

`THOUGHT:` 契约违规的完整事实基础、裁决与 T0 放行标准见 **§7**。摘要：违规只计数、不阻断
工具调用；`thought` 由必备归因证据降为**可缺失的辅助字段**；仅当违规**造成执行或评测退化**
时才阻断大实验。T0 起必须按 `(model, architecture)` 报告 `protocol_violation` 率与
`thought` 缺失率。

（2026-09-20 的 T0 冒烟已完成 §0.1/§0.2 的落盘验证：`llm_provenance` 覆盖
planner / executor / replanner 三个 phase，`served_model` 与请求一致。）

---

## 2. 设计：三个自变量与一个可检验的交互范式

```
M  主实验模型 Qwen3.8 Flash / V4.1 Flash             2 水平，**分类因子**
V  第三模型验证 V4 Pro（共同 8 任务）                 次级分析，不并入 16 任务主估计
A  架构    react / plan_execute                        2 水平，**分类因子**
F  故障    3 种机制多样性三元组（Stage C 用）；已有 5 故障数据 + Stage E 支撑
```

架构不编码为有序强度。两水平设计的 M×A 交互直接回答：具体模型在 ReAct 与
Plan-and-Execute 之间的鲁棒性差异是否不同。计算量差异通过 LLM 调用、token、墙钟和成本单独报告。

**Stage C 主矩阵估计两模型的 M、A 与 M×A**；第三模型只用于共同 8 任务上的次级复现，
不能当作 16 任务三模型完整交叉设计。

### 2.1 模型作为分类因子

DeepSeek 官方已报告 V4.1 Flash 在基准、性能、成本和总用时上超过 V4 Pro，
因此 `Flash < Pro` 不能再作为先验顺序。跨厂商的 Qwen3.8 Flash 也没有足够稳定的先验排名。

- T0 控制成功率用于画图、解释以及地板/天花板检查。
- 确证性分析把主实验 `model` 作为两水平分类因子，检验模型主效应、架构主效应和 `M×A` 交互。
- 三模型对比只在预设 8 个共同任务上进行，标为次级/探索性，不外推到主实验全部 16 任务。
- 不对 T0 结果事后排序再做序数检验，避免循环论证。
- 不从当前具体模型差异推出能力单调趋势或一般厂商效应。

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

- 任一模型控制率 ≈ 0 → 没有可退化的余地，故障效应被压成 0；
- `Pro` 控制率 ≈ 100% → 没有可提升的余地，架构增益被压成 0。

> **闸门**：T0 跑完立刻判定（此时大钱还没花）。若两个主模型中落在可用带宽 `[15%, 85%]`
> 内的**少于 2 个**，则把**主结果变量改为过程量**（步数、重规划次数、工具错误数、恢复时间），
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

### Stage C 任务样本（主实验 4 类 × 4 任务；验证 4 类 × 2 任务）

当前任务与模型分配见 [`task_manifest_public16.json`](task_manifest_public16.json)：
Shopping 商品评论检索、Reddit 帖子检索、GitLab 公开仓库检索、公开页面导航各 4 个，
共 16 个主实验 ID，保留已有 8 个历史锚点；每类预先选 2 个进入第三模型验证。
原 24 任务候选及预检记录保留在 [`task_manifest_public24.json`](task_manifest_public24.json)，
不再作为运行清单。Map、需交互式登录及会改变站点状态的任务排除。

> **预检进度（2026-09-21）：** 数据集 24/24 命中；匿名访问 **24/24 通过**（GitLab 的 `start_urls`
> 是站点根路径，匿名访问必然跳 `/users/sign_in`，须按各任务意图的真实目标页判定，不能按根路径
> 判定）。官方 evaluator 对 8 个历史锚点 **8/8 走 native 路径**，其中 22、24 原先被标为 null-schema
> 案例，在配上 `--evaluator-config` 后未再触发 compatibility 回退，该警告已被本次实测取代。
> 服务器随后报告其余 16/16 在 Pro/ReAct 预检中均为 native；该报告尚未纳入仓库，须在
> 正式 T0 前归档核对。Qwen、Flash 与 `plan_execute` 的端点/架构健康仍须验证。
> 原 24 任务预检历史见 [`task_manifest_public24.json`](task_manifest_public24.json)。

该选择降低认证失效、环境部署和跨 trial 状态污染，但外推范围很窄：正文只能把结果描述为
**三个站点、四类公开只读任务的定向分层样本研究**。不得外推到整个 WebArena、Map 或登录后
mutation 任务。同一模板内的任务相关，不能把 16 个 ID 当成 16 个独立模板；
第三模型的 8 任务结果不得外推到 16 任务全集。

### Stage C — Model × Architecture（**范式主实验**）
- **主矩阵**：DeepSeek V4.1 Flash 与 Qwen3.8 Flash × 2 架构 × 3 故障
  （`web_http_error` / `agent_param_error` / `web_dom_missing`）× 16 任务 × 2 次独立重复
  × (故障臂 + 配对控制臂) = **768 trials**。
- **第三模型验证**：DeepSeek V4 Pro × 相同架构/故障/重复/条件 × 预设共同 8 任务
  = **192 trials**。两部分共 **960 trials**；任务及子集见
  [`task_manifest_public16.json`](task_manifest_public16.json)。
- 主结果只作两模型在 16 任务上的比较；三模型对比仅在共同 8 任务上重算，作为次级验证。

| 架构 | s/trial | trials | ≈wall |
|---|---|---|---|
| `react` | 159（旧样本实测） | 480 | 约 3.5 h（6 worker 粗估） |
| `plan_execute` | 500（旧样本推导） | 480 | 约 11.1 h（6 worker 粗估） |

两次独立重复是当前正式设计，不是待执行的削减方案。旧样本时延不能保证适用于新任务；
应以 T0 的实际用时和 API 成本重估。

**故障是最后才动的**：它虽不进入 §2.2 的确证性对比，却是 M×A 效应得以**泛化**的 replicate 维度。
砍到 1 个，"模型-架构责任划分"就降格成"在 `web_http_error` 下的责任划分"。

**三向 M×A×F 交互预注册为探索性**。任务按类别及模板聚类，报告描述性模式与逐格置信区间；
两次重复不能替代跨任务模板的独立证据。

### Stage C 执行契约（2026-09-26 固定）

以下是 Stage C 的**执行层**约定；它们不改变上表的样本量与格子，只规定"怎么跑、怎么留痕、怎么算"。

1. **任务定义必须来自数据集**。生成 `agent_response.json` 时必须传入该任务在数据集里的
   真实 `eval` 定义（`standard_agent/webarena_verified.py:load_task_definition` /
   `write_agent_response`）。用占位符 `eval: []` 生成响应会把 `navigate` 任务写成
   `RETRIEVE`，官方 evaluator 的 `AgentResponseEvaluator` 必然判负——这类格子不是
   "agent 失败"，而是产物不可评分。
2. **注入步口径单一来源**（`fault_injection/config.py:resolve_injection_step`）：
   Stage C 每臂只注入一次且位置固定——`agent_param_error` 落在**第 1 个参数动作**，
   其余两个故障落在**第 2 个执行步**，两者计数单位不同（参数动作 vs 执行步）。
   Stage E 才扫描注入步，并显式传 `--fault-injection-step`；矩阵把该模式记为
   `stage_e_explicit`，Stage C 记为 `stage_c_fixed`。历史复现脚本保留旧步并在文件内标注。
3. **逐 trial 留痕**（`standard_agent/trial_metadata.py`）：每个
   `(model, architecture, fault, task, seed, condition)` 生成 `trial_record.json`，含
   deterministic `trial_id`、控制/故障配对用的 `pair_key`、trace 路径、`agent_response`/
   HAR 路径、代码版本（git sha/branch/dirty）与运行配置；trace 文件名即 trial file stem。
   **配对方案明确记为 `seed-paired-v2`（新方案）**：控制臂与故障臂共用同一次运行的
   job seed，因此控制臂目录是 `control_seed_<jobseed>`。这与历史产物**不同**：旧产物
   的控制臂是 `control_seed_0`（`FaultConfig.off()` 的固定 seed=0），且旧产物没有
   `trial_record.json`。为免把旧目录误当作同一 trial，矩阵在 `manifest.json` 与每个
   `*.status.json` 里写入 `design_fingerprint`（模型/架构/步数/故障集/注入步/重复数/
   任务集/配对方案/schema 的哈希），`--resume` 只接受指纹一致的 status 文件，其余
   计入 `resume_ignored_stale_status_files` 并重跑。
4. **闭环**（`scripts/stage_c_pipeline.py`）：产物完整性检查 → 官方 evaluator →
   `native`/`compatibility`/`error` 审计 → 缺失与失败格子清单。**完成率不是官方成功率**：
   两个口径分别统计、分别报告。未进入官方成功率分母的格子分四类计数，且必须满足
   `evaluated + missing + incomplete + unevaluated + error = expected`（`cell_counts.consistent`）：
   根本没有产物 / 产物不完整 / 完整但未评分 / 评分自身报错。
5. **主分析预注册**：估计量、整群自助区间、交互检验、Holm 家族与判定语言见
   [`stage_c_analysis_plan.md`](stage_c_analysis_plan.md)；实现为
   `standard_agent/stage_c_analysis.py` + `scripts/stage_c_analysis.py`。


- Stage B 的存在理由是"单模型下模型效应退化"。C 主矩阵有 2 个模型，该理由消失。
- 原 Stage D 是任务外推检验。服务器无法运行 Map，当前 16 个任务均来自三个公开站点，
  因此 Stage D 没有被 C 吸收，而是明确取消；跨站点/需登录任务的外推不足作为主要 limitation。

### Stage E — 注入步敏感性（审稿人一定会压的混淆）
- 把 `--fault-injection-step` 扫 **{1,2,3}**，针对 Stage C 的三个故障。
- 计数单位随故障而定（`fault_injection/config.py:injection_step_units`）：
  `agent_param_error` 的 k 指**第 k 个参数动作**，另两个故障的 k 指**第 k 个执行步**。
  Stage C 的取值是 `agent_param_error → 1`、其余 `→ 2`；Stage E 在这三点上扫描，
  因此对 `agent_param_error` 而言 k=1 与 Stage C 重合，k=2/3 为新点。
- 此阶段须按 16 任务新样本重新设计并计费；旧版 240 trials / 2.2 h 预算作废 / 模型：**Flash**
- **这是唯一有意传 `--fault-injection-step` 的阶段**，且它统一施加，`agent_param_error` 会离开默认位置——**这是有意的，记进 manifest**。

### Stage F — 修复消融
- 四类候选修复（错误感知重试+退避、参数校验、陈旧观测检测、不可观测状态的终止策略）在现有 harness 里可测。
- **修复臂与未修复臂必须在同一次会话里跑**：五个 per-fault 控制臂是**同一配置**却跨 **2.17×** 的
  平均墙钟（103.7–224.6 s），顺序跑会让机器漂移冒充修复效应。
- 此阶段须按 16 任务新样本重新设计并计费；旧版 400 trials / 3.7 h 预算作废 / 模型：**Pro 或 Flash**（取决于预算）
- **目标要按 §5.3 定，不是按算力定**：在观测到的 discordance 下，10 pp **成功率**提升需要
  ~181 对/臂——所以这个 n 下现实的目标是**过程成本**改善。

---

## 4. 模型分配

### 4.1 ⚠️ `model_budget_policy.md` 的 GPT 框架和旧能力梯度均已作废

那份文档把 GPT 定位成**第三个 vendor 的复现证据**，为它设计了 T0/T1′/T1/T2 脊柱，
省下约 1160 trials。该旧方案不可执行。当前采用**两模型完整主矩阵＋第三模型共同任务验证**：
第三模型不参与 16 任务确证性 `M×A` 估计，只在预设 8 任务上和主模型逐格对齐。
`model_budget_policy.md` 的旧 GPT 档位和脊柱表仅供历史追溯，不能用来启动运行。

### 4.2 不设能力顺序，不得声称厂商效应

```
V4.1 Flash    (DeepSeek) ─┐ 16 任务主矩阵
Qwen3.8 Flash (Alibaba)  ─┘
V4 Pro        (DeepSeek)    共同 8 任务验证
```

DeepSeek 官方报告 V4.1 Flash 超过 V4 Pro，所以旧文档中 `Flash < Pro` 的标签作废。
同时，Qwen3.8 Flash 与两个 DeepSeek 模型的跨厂商先验排名也不足以支撑序数编码。

- **可以**说两个主模型在 16 任务上的具体差异，以及三个模型在共同 8 任务上的次级差异；
- **不可以**从这三点推出一般的能力单调趋势；
- **不可以**说任何 `Alibaba vs DeepSeek` 的厂商结论。

**这一条要写进正文的 Limitations**，否则审稿人会替你写。

若日后确实要 vendor 结论，唯一出路是加一个**同能力档的跨厂商模型**（例如另一个厂商的旗舰），
那是新的一层，不在本轮。

### 4.3 阶段 → 模型

| 阶段 | 模型 | 理由 |
|---|---|---|
| A（evaluator 重评） | **现有 gpt54 产物** | 离线重评，不发起新 agent trial |
| C（范式主实验） | **Flash + Qwen** | 16 任务完全交叉，估计两模型 `M×A` |
| C（第三模型验证） | **Pro** | 预设共同 8 任务；三模型对比只能用这 8 任务 |
| E（注入步） | **Flash** | 混淆检查，只需一个模型；Flash 便宜 |
| F（修复消融） | **Pro 或 Flash** | 取决于 §4.4 |
| 控制锚 T0 | **Flash/Qwen 各 16 任务；Pro 8 任务** | 与正式任务格子一致 |
| 来源核验 | 三个 profile | 探针 5 项 × `--repeat 3`，~5 min |

**投入顺序：来源探针 → T0（按各模型任务清单）→ 两模型主矩阵 → Pro 共同任务验证。**

理由：T0 是 §2.3 闸门的输入；80 个串行控制运行的用时须实测。
Pro 验证子集在看到正式故障结果前固定，不根据主结果挑任务。

### 4.4 三条硬约束（违反则这批数据不可用）

1. **配对是刚需**——每个 `(model, fault, task, replicate_id, architecture)` 都有故障/控制两臂。
   主模型间在 16 任务配对；三模型只在共同 8 任务配对。
2. **共同格子配置一致**——三个模型在共同 8 任务上使用相同故障、架构、重复和条件；
   不把 Pro 缺失的另外 8 任务当成失败或用于 16 任务模型对比。
3. **如预算仍需削减，必须先修订预注册方案再执行，不得单方减少某模型或任务格子。** `fault_seed` 只控制故障机制；模型供应商
   未必接受随机 seed，所以论文将这些运行称为独立重复，而不是模型随机种子。

---

## 5. 预算

### 当前主方案（2 次独立重复）

| Stage | 买什么 | trials | ≈wall |
|---|---|---|---|
| **T0** | 两主模型×2架构×16任务 + Pro×2架构×8任务，各 1 次控制健康检查 | **80** | 串行，实测后估算 |
| A | 修 evaluator + 对已有 400 条离线重评 | **0 新 trial** | 评估开销，不计 agent wall-clock |
| **C 主矩阵** | 2模型×2架构×3故障×16任务×2重复×2条件 | **768** | T0 后估算 |
| **C 验证** | Pro×2架构×3故障×8共同任务×2重复×2条件 | **192** | T0 后估算 |
| **C 合计** | 主矩阵 + 验证；比旧 1,728 trial 少 44.4% | **960** | T0 后估算 |
| E | 注入步敏感性（仍只在 `react`） | 待重新设计 | 待估 |
| F | 修复消融 | 待重新设计 | 待估 |
| ~~B~~ / ~~D~~ | B 并入 C；D 因公开任务范围取消 | — | — |

T0 的 80 个控制运行不自动计入 C：只有相同配置、相同配对索引且满足正式采集和评估要求时
才可复用，否则另计。E/F 尚未定样本数，不再给出误导性的总预算。
两次重复的 MDE 较宽，零结果须配功效分析和置信区间，不能据此断言无效应。

**不要只因为事后控制成功率高/低就删掉某个模型。** 若 T0 显示端点健康，
两主模型须完成相同的 16 任务格子，Pro 须完成相同的 8 任务格子；否则估计范围改变。

**墙钟是 6 worker 的忙碌时间，不是日历时间**，且假设 WebArena 站点、evaluator、API 都在线。

---

## 6. 成本：已决定，不再讨论；只留一处**仍需测量**的东西

模型集暂按（`Pro / Flash / Qwen3.8 Flash`）配置，**Qwen 端点仍须通过 T0 闸门**。上一版的
"可负担 trials → T2/T1/T1′/T0"判据与 GPT 档位决策**一并作废**。

仍然要量的只有一件，而它**不属于成本决策，属于量具**：

> **prompt caching 与 pro/flash 实测倍率。** WebArena 的观测是成块 AX Tree，
> ReAct 连续步之间共享极长前缀。不核实这一条，**T0 实测的 cost/trial 本身就不可信**。
> 它依赖 `llm_provenance`，而后者依赖 **Stage 0.1**。

**计量已就位（2026-09-21）。** `extract_served_metadata` 现在记录
`prompt_cache_hit_tokens` / `prompt_cache_miss_tokens` / `cache_usage_source`，依次识别
DeepSeek（`prompt_cache_hit_tokens`）、OpenAI（`prompt_tokens_details.cached_tokens`）、
LangChain 归一化（`input_token_details.cache_read`）三种返回形态。**字段缺失记为 `None`
（未知），绝不记 0**——0 是"缓存什么都没返回"的测量结论，与"端点没报"是两回事。

端点实测（`api.deepseek.com`，固定 6094 token 前缀连续三次）：命中 `0 → 6016 → 6016`，
即前缀稳定时命中率约 **98.7%**，说明端点缓存本身有效。

> **但既有 trace 无法回溯核价。** 2026-09-21 之前的 trace 没有这三个字段，因此那批数据的
> 缓存命中率永远是"未知"，其 token 数**不能**直接换算成费用。已知的 `82%` 与 `58%` 都是
> **token 占比**，不是已核实的费用占比；`435 M tokens` 只能作为风险情景，不能作为报价。

核价日期、`served_model` 探针结果、T0 实测 cost/trial 三者仍记进 manifest——
**不是为了砍预算，是为了在稿子里报得出"我们到底跑了什么"。**

---

## 7. 协议违规的处理口径（2026-09-21 修订）

旧版这里只有一条"违规频发就先修 harness"的粗规则；本节把它细化为可执行的 T0 放行标准。
（上一版"明确不跑 GPT 的三个分支"已作废：当前两模型主矩阵与 Pro 验证的分层设计见 §4.1。）

### 7.1 事实基础（观察，不是因果机制）

`plan_execute` 的 executor 要求每次 tool call 前回复文本以 `THOUGHT:` 开头，检查点在
`standard_agent/core/nodes.py` 的 `protocol_violation` 分支；**违规只写事件，不阻断工具调用**，
因此空正文 ≠ 任务失败。

**历史 trace 的 step 级统计**（`python3 scripts/protocol_health.py`，按 `model_profile × architecture`
聚合，`missing` = 工具调用步中 `thought` 为空的占比，`viol` = `protocol_violation` 事件占比）：

| profile（served） | 架构 | traces | 工具调用步 | `thought` 缺失 | 缺失率 | 违规率 |
|---|---|---|---|---|---|---|
| `gpt54`（gpt-5.4） | `react` | 2241 | 14782 | 49 | 0.3% | 0.0% |
| `gpt54`（gpt-5.4） | `plan_execute` | 242 | 845 | 45 | 5.3% | 0.0% |
| `deepseek`（deepseek-v4-pro） | `react` | 28 | 297 | 265 | 89.2% | 86.2% |
| `deepseek`（deepseek-v4-pro） | `plan_execute` | 69 | 352 | 304 | 86.4% | 4.3% |

| 其它观测 | 数值 |
|---|---|
| 2026-09-20 T0 冒烟（deepseek-v4-pro，`plan_execute`，task 124） | executor **15/15** 步 `thought` 缺失，`content` 为空串 |
| 隔离复现同形态请求 | 6 次里 3–4 次合规（约一半省略正文） |
| 把 ReAct 的完整协议段与示例搬进 executor prompt | 3/6，与现版无差异 |
| 空正文时的 `additional_kwargs` | 仅有 `refusal`，**无 `reasoning_content` 可取** |

两条读法上的约束：

- **这是模型差异，不是架构差异。** gpt-5.4 在两个架构上都接近 0%，deepseek-v4-pro 在两个架构上
  都约 86–89% 缺失。不得把它写成"plan_execute 特有的问题"。
- **`viol` 与 `missing` 的差距来自检查点上线的先后。** `agent_node`（`react`）的检查更早，
  `plan_executor_node`（`plan_execute`）的检查是 2026-09-15 才加入，因此历史 `plan_execute`
  的违规计数偏低**不代表当时守约**——跨时间比较一律以 `thought` 缺失率为准。

> **写作要求：真实 trial 的 15/15 与隔离测试的约一半，只能表述为"观察到的关联"。**
> prompt、页面、调用路径、上下文长度或端点状态都可能解释这个差异，本轮证据不足以确定
> 因果机制，不得写成"上下文越长越容易省略"这类已证结论。

### 7.2 裁决（T0 前生效，适用于 T0 与 Stage C）

1. **不自动重试，不为单臂放宽契约。** 自动重试会改变模型动作、调用次数与成本，给架构对比
   增加新的混淆；单独放宽某一臂会让两臂不再用同一把尺子；也不得为缺失的 `thought` 补造"思考"。
2. **`thought` 由必备归因证据降为可缺失的辅助字段。** 主结果只使用官方任务结果与可观测的
   动作、工具错误、步数等过程量；**不跨模型比较依赖 `thought` 的主观归因标签**。
3. **必须分别报告**每个 `(model, architecture)` 的 `protocol_violation` 率与 `thought` 缺失率。
4. **闸门细化：** 违规只有在**造成执行或评测退化**时才阻断大实验——即伴随实际工具调用失败、
   异常停止，或官方 evaluator 无法评分；若仅造成辅助文字缺失，则允许继续，但必须披露缺失率。

### 7.3 T0 放行标准（四条需同时成立）

- 工具调用、HAR 与官方 evaluator 正常；空 `content` 不单独计为执行失败。
- 已按 `(model, architecture)` 报出 `protocol_violation` 率与 `thought` 缺失率——用仓库里的
  `python3 scripts/protocol_health.py --trace-dir traces --json <out>.json` 产出，报告进 manifest。
- 主结果只依赖官方任务结果与可观测的动作 / 工具错误 / 步数。
- 若 DeepSeek Pro 的空正文伴随工具调用失败、异常停止或无法评分，**暂停该模型**，修 harness 后
  重做 T0。

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
  因此确证性检验采用两主模型的分类 `M×A` 交互；三模型仅在共同 8 任务做次级对比。
- **地板/天花板闸门预注册。** §2.3 的 `[15%, 85%]` 判据必须在**看到 T0 之前**写进 manifest。
  事后再说"因为撞了地板所以改用过程量"是选择性报告。
- **不得声称能力单调趋势或厂商效应。** 16 任务主结果只支持两具体模型对比；
  三模型差异只支持共同 8 任务的次级分析。

---

## 10. 现在立刻可做的四件事

0. 核对并归档所选 16 个任务的匿名访问与 native evaluator 证据；其中 8 个共同任务也须供
   Pro 验证使用。任何失败均在 T0 前按有日期修订记录处理，T0 后不得再改任务集。
1. `find` 定位 trace 落盘目录（Stage 0.1）——**它阻塞 provenance 证据，压过一切**
2. 对已有数据跑一次审计：
   ```bash
   grep -h '"event": "replan_decision"' traces/*.jsonl \
     | grep -o '"decision": "[^"]*"' | sort | uniq -c | sort -rn
   ```
   （趁新代码还没产生新数据——旧 trace 里记的是模型原样输出）
3. 把 provenance 层整理成对着 `6277b1c` 的 patch（Stage 0.2）
