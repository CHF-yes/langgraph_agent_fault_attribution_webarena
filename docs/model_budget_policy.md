# 三 backbone 预算执行方针（DeepSeek Pro / DeepSeek Flash / OpenAI）

> **2026-09-18 状态：仅供历史追溯，不再是执行入口。** DeepSeek 官方已报告 V4.1 Flash
> 超过 V4 Pro，本文的 Pro/Flash 档位与 GPT 脊柱假设均已作废。实验次序、模型口径和预算
> 以 [`experiment_roadmap.md`](experiment_roadmap.md) 为唯一执行依据；模型按分类因子分析，不做先验有序编码。

配套 [`model_pilot_plan.md`](model_pilot_plan.md)（先验核验）与
[`supplementary_experiment_plan.md`](supplementary_experiment_plan.md)（阶段与预算）。
本文只回答一个问题：**在 OpenAI 预算不确定时，哪些格子必须跑、哪些可以砍、砍的顺序是什么。**

---

## 0. GPT 在这一版里的角色

不是"第三个 backbone"，而是**归因结论的跨厂商可复现证据**。

所以它不需要全矩阵。它需要的是：**承载论文结论的那些故障条件下、与前两个模型逐格配对的运行**。
买"结论的骨架"，不买"网格的完整"。

一句话方针：**Tier 0 必跑；Tier 1 是目标；Tier 2 只在单价便宜时跑。**

---

## 1. 三条硬约束（违反则这批数据不可用）

1. **配对是刚需。** 每一个 GPT trial 必须存在 `(fault, task, seed)` 完全相同的 DeepSeek
   trial。所有 Δ 与 McNemar 都在**配对单元**上算——没有配对就没有统计量，那批 GPT 数据
   只能当个案描述，进不了正文。
2. **削减必须跨模型一致。** 可以**全体**从 5 故障减到 3（那就是 Stage C 的设计），
   不可以**只给 GPT** 减。只给 GPT 减会把矩阵拆成两个不能 join 的集合。
3. **削减顺序：seed → task → fault，不可颠倒。** 先降 `--trials`（种子数），再减
   `--task-ids`，**最后**才减故障类型。反过来做最省事但最伤：先砍故障，等于砍掉论文的
   自变量。

> 第 3 条的理由：故障类型是**处理变量**，砍它就等于换研究问题；种子只影响精度，
> 任务影响外部效度。失去精度可以如实报告 MDE；失去处理变量没法补救。

---

## 2. 四档设计（按"能否支撑结论"排序，不是按贵贱）

固定 `--fault-intensity high --fault-seed 1 --workers 6`，`react` 架构（模型轴在 react 上）。
wall 用补充计划的实测口径：`trials × 159 s / 6 × 1.25`。

| 档 | 故障 | tasks×seeds | trials | ≈wall | 买到什么 |
|---|---|---|---|---|---|
| **T0** 控制锚 | — | 8×5 | **40** | 0.4 h | 基线可比性、evaluator 路径检查、MDE 的输入 |
| **T1′** 脊柱-精简 | 3 | 8×3 | **144** | 1.3 h | 同 T1，但 MDE 更宽（可如实报告） |
| **T1** 脊柱 | 3 | 8×5 | **240** | 2.2 h | **跨厂商归因**——最小可用设计 |
| **T2** 全对齐 | 5 | 8×5 | **400** | 3.7 h | `web_popup_block` / `web_timeout` 也跨厂商 |

**T1 的三个故障就用 Stage C 已定的机制多样性三元组**：
`web_http_error`（瞬时基础设施）/ `agent_param_error`（参数被污染）/ `web_dom_missing`（观测丢失）。

这样有个额外好处：**T1 的 DeepSeek 格子与 Stage C 是同一配置，直接可比**，不需要为
跨厂商对比另跑一套。GPT 的 T1 就是往 Stage C 里补一个 vendor，而不是新开一条线。

```bash
# T1（GPT 侧；DeepSeek 侧复用 Stage C，不要重跑）
python3 scripts/run_fault_matrix.py \
  --output-dir  experiments/modelaxis_gpt_react_T1_v1 \
  --fault-types web_http_error agent_param_error web_dom_missing \
  --task-ids 22 24 27 28 30 132 133 134 \
  --model-profile <gpt-profile> --architecture react \
  --max-steps 20 --fault-intensity high --trials 5 --fault-seed 1 \
  --workers 6 --job-timeout-minutes 45 --resume
```

`--job-timeout-minutes 45` 与"不要传 `--fault-injection-step`"两条，见补充计划 §0。

> 若某档走 `plan_execute`，wall 要按 **500 s/trial** 重算，不是 159 s——那是 ~4.7× 的差，
> 照 ReAct 的速率估会低估约三分之一。

---

## 3. 价格触发器：量出来，不要猜

"太贵/便宜"必须落到一个数。三步，全部用已有工具，不需要估。

**第 1 步：先跑 T0（40 trials，约 0.4 h）。** 它是三档里最便宜的，且无论如何都要跑。

**第 2 步：从 `llm_provenance` 事件汇总真实 token。**

```bash
# 每个 trial 的 prompt/completion token 与调用次数
grep -ho '"prompt_tokens": [0-9]*' experiments/<run>/**/*.jsonl | ...
```

运行时每个 LLM 调用都写一条 `llm_provenance`，含 `prompt_tokens` / `completion_tokens` /
`total_tokens` / `model_profile`。这是唯一能给出**你自己那套 prompt 形状**下真实单价的来源——
公开价目表只给每 token 价格，不给 WebArena 的 AX Tree 有多长。

**第 3 步：**

```
cost_per_trial = Σ_calls ( prompt_tokens × p_in + completion_tokens × p_out )

可负担 trials = 预算 / cost_per_trial
  ≥ 400 → T2      240 ≤ · < 400 → T1      144 ≤ · < 240 → T1′      40 ≤ · < 144 → T0
  < 40  → 不跑 GPT（见 §5）
```

**两个必须一起量进去的东西：**

- **prompt caching。** WebArena 的观测是成块的 AX Tree，ReAct 连续步之间共享极长前缀。
  缓存命中可以让输入侧成本降一个量级。**必须在缓存开启的状态下量**，否则会把 T2 误判成
  负担不起。两个供应商都支持，跑 T0 前确认它是开的。
- **pro/flash 的实测倍率。** 用 flash 的 T0 实测值去推 pro 的，别用价目表推
  （输出侧名义倍率约 6.75×，输入侧另算，缓存后更不一样）。**倍率以实测为准，价目表只作量级校验。**

> 价目随时会变（本方案写作时的 V4 Pro / Flash 价格来自公开报道，未核）。
> 正式预算前先核官方价目，并把核价日期记进 manifest。

---

## 4. 执行顺序：便宜的先跑，贵的最后

```
DeepSeek Flash  →  DeepSeek Pro  →  OpenAI
   (最便宜)          (旗舰)         (最后决定)
```

三条理由：

1. **Flash 最便宜、最快**，能最早拿到一整套完整矩阵——而分析要先做这一套。
2. **如果设计是退化的，你要用 ¥ 而不是 $ 发现它。** 控制成功率 ≈ 0、或 `protocol_violation`
   频发，说明 harness 在新模型下有与故障无关的问题。这种事在 Flash 上撞到，比在 GPT 上撞到便宜得多。
3. **GPT 的决策要在两个事实之后做**：真实 cost/trial，以及这套设计值不值得延长。
   这正是你选的"先跑小 pilot 再定"。

**按阶段分配模型：**

| 阶段 | 用哪个 | 理由 |
|---|---|---|
| A（换量具） | 沿用 gpt54 原设计 | 它是 1:1 替换，换模型就不可比了 |
| C（Model×Arch） | **Pro**（旗舰，深度格子） | 这是唯一估计模型主效应的阶段 |
| B / E / D（广度：加模型 / 扫步 / 加任务） | **Flash** | 输出侧便宜约 6.75×，广度阶段性价比最高 |
| T0–T2（跨厂商脊柱） | **GPT** | 只在 T0 量完价之后决定档位 |

---

## 5. 明确不跑 GPT 的三个分支

把"没跑"变成**有原则地没跑**，比硬凑一个不完整的矩阵强：

1. **唯一可及的 GPT 端点是中转站，且探针发现 served model 替换或路由漂移。**
   → 停。见 [`model_pilot_plan.md`](model_pilot_plan.md) 第 0 步的判定表。
   （一篇 2026-03 的系统审计在 17 家中转站的过半端点上验出静默换模型，并存污染了 187 篇论文；
   这个风险不是假设。**核完原文再引用到稿子里。**）
2. **两个 DeepSeek 模型的 T0 都退化**（控制成功率≈0 或 `protocol_violation` 频发）。
   → 先修 harness。加 GPT 不会修好一个坏掉的 harness，只会多花一笔钱得到同样的退化。
3. **T0 实测价把 T1 顶出预算。**
   → 只跑 T0，GPT 仅作控制锚，并在稿里写明："跨厂商对比受预算限制未完成，我们报告控制臂的
   基线可比性。"这是可辩护的，而且是**诚实的**。

---

## 6. 预注册削减规则（跑之前写下来）

在看到 GPT 结果**之前**，把削减规则记进 manifest 或一份带日期的笔记：

> 若预算为 X，则 GPT 跑 T1′（3 故障 × 8 任务 × 3 种子）。

理由：先说后跑，与"看到效应出现在哪个子集再决定跑哪个子集"有本质区别。后者是选择性报告，
是审稿人有权拒稿的那类问题。**写下来不花任何成本，却堵住了这个洞。**

同时记下：核价日期、`served_model` 探针结果、以及 T0 实测的 cost/trial。

---

## 7. 合起来看的总预算

| 批次 | 模型 | 档 | trials | ≈wall |
|---|---|---|---|---|
| 来源核验 | 三个 profile | 探针 5 项 × `--repeat 3` | — | ~5 min |
| 控制 pilot | Flash + Pro | T0 | 80 | ~0.8 h |
| 跨厂商锚 | GPT | T0 | 40 | 0.4 h |
| 自主矩阵 | Flash | T2 | 400 | 3.7 h |
| 旗舰矩阵 | Pro | T2 | 400 | 3.7 h |
| 跨厂商脊柱 | GPT | T1 | 240 | 2.2 h |
| | | | **1 160** | **≈11 h** |

这张表是"GPT 只跑脊柱"的方案，比"三个模型都全量"省约 **800 trials ≈ 7.4 h**，
而跨厂商归因这一条仍然成立。

---

## 8. 三条不因加数据而放松的解读规则

沿用补充计划 §10，在跨厂商对比里尤其容易踩：

- **不合并 evaluator 路径。** 报 native 层，pooled 只放在旁边。
- **每个零结果都配功效分析。** 30–31 对 native 上 MDE 是**无定义**，不是"大"。
  跨厂商若得到不显著，那是**设计的性质**，不是"两厂商无差异"。
- **不把成本混进成功率的声明。** `(fault, task, seed)` 键不同的两批数据不能 join 成
  cost-per-success。
