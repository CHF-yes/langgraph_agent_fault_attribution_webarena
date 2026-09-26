# Stage C 主分析预注册（2026-09-26）

> **状态：先写后跑。** 本文在任何正式 trial 产生之前固定估计量、区间、检验家族与
> 判定语言。冻结的任务集、模型分配与 960 trials 设计见
> [`task_manifest_public16.json`](task_manifest_public16.json) 与
> [`experiment_roadmap.md`](experiment_roadmap.md)，本文不修改它们。

## 1. 数据与单位

- 观测单位：一次 trial。结局是**官方 evaluator 的二元判定**（成功/失败）。
- **完成率（agent 自报 `status == SUCCESS`）不进入任何主分析**，只作为独立的口径单独
  报告。二者由 `scripts/stage_c_pipeline.py` 分别统计，禁止互相替代。
- 只有评测完成的格子参与分析：`native` 与 `compatibility` 计入。不进入分母的四类
  格子分别计数并全部列出，且总和必须等于期望格子数：
  `evaluated + missing + incomplete + unevaluated + error = expected`
  （根本没有产物 / 产物不完整 / 完整但未评分 / 评分自身报错）。**任何一类都不得从
  报告中静默消失**；evaluator 报错与"未评分"是两件事，分开报。
- **正式推断前置检查**：矩阵必须通过健康检查才能输出显著性结论。缺格（没有产物）、
  incomplete（产物不完整）、unevaluated（未评分）、error（评分报错）、unpaired
  （控制/故障臂未配平）任一存在时，`formal_inference_allowed = false`，所有 p 值置空，
  只保留描述性点估计与区间，并在报告顶部列出阻塞原因；判断口径与管道审计一致。
- 配对方案记为 `seed-paired-v2`：控制臂与故障臂共用同一次运行的 job seed，故
  `pair_key` 含同一 seed。历史产物的控制臂是 `control_seed_0` 且无 `trial_record.json`，
  审计将其判为不完整，不参与配对；恢复运行另由 `design_fingerprint` 拦截。

## 2. 主估计量（配对退化）

对每个 `(model, architecture, fault)`：

```
Δ = P(成功 | control) − P(成功 | fault)
```

- 先在**同一 `(task, seed)`** 内配对，取配对差；再对任务取**等权平均**。
- 任务才是独立抽样单位：同一任务的重复不能当成独立样本；跨任务的差异才是方差来源。
- 三个故障各自给出 Δ、control/fault 成功率（含 Wilson 区间）与配对区间。

## 3. 区间与检验

- Δ 的 95% 区间：**按任务整群自助**（cluster bootstrap），且**按四个预注册类别分层**：
  在每个类别内部重采样任务、每类任务数保持不变。固定随机种子（默认 `20260921`，
  10000 次），任何人重跑得到同一区间。
- **960 条 trial 不是 960 个独立样本**：同一任务下的故障、架构、模型与两次重复必须
  整体进出样本；重采样单位是任务，不是 trial（单任务时区间退化为一点，正是这一点的
  直接体现）。
- Δ 的检验：任务等权均值的双侧正态近似（区间以自助为准，两者不一致时以自助为准并
  在报告中标注）。
- 报告退化量时同时给出 control/fault 的 Wilson 区间，避免用点估计掩盖小样本。

## 4. 交互检验（与 Model × Architecture 等价）

以 trial 级二元成功率为因变量，拟合线性概率模型：

```
success ~ condition * model * architecture
```

- 关注的系数是 `condition:model:architecture`：它检验"故障退化量是否随 (model,
  architecture) 组合而不同"，即**退化量上的 Model × Architecture 交互**。
  这与把 Δ 作为因变量、`model * architecture` 作为自变量的三阶交互是同一个检验。
- 推断：按任务聚类的稳健标准误（sandwich），另给出同样**按类别分层**的整群自助区间
  作为交叉验证。
- 仅当主实验恰好有两个模型、两个架构时可估计；其他情况明确报"无法估计"，不做近似。
- 显著性判读：交互不显著只能报告为**功效不足**，不得表述为"模型与架构等价"，也不得
  上升为一般性的能力梯度或厂商效应。

## 5. 多重比较

- **家族定义固定为"三个故障级主对比"**（代码常量 `HOLM_FAMILY_ID = "per_fault_primary"`）：
  每个故障一个检验，把两个主模型与两个架构**合并**后估计该故障的配对退化 Δ，任务为聚类
  单位；用 **Holm** 校正并报告校正前后 p 值与家族成员（`holm_family.members`）。
- 逐 `(model, architecture, fault)` 的格子**不进入家族**，只作描述性展示
  （`in_holm_family = false`）；否则家族大小会随模型/架构数量变化，校正失去意义。
- V4 Pro 的次级分析一律不报显著性（p 值置空）。
- 不因"想显著性"而扩大样本或更换任务；需要改设计时先修订预注册方案。

## 6. 三类故障与任务类别

- 三种故障 `web_http_error`、`agent_param_error`、`web_dom_missing` 分别估计与报告，
  不预先合并成一个"总故障"分数。
- 任务来自四个预注册类别（shopping review / reddit post / gitlab repo / public
  navigation），每类 4 个主任务。类别只用于描述与分层展示，**不作为主检验的聚类因子**；
  主检验的聚类单位是任务本身（类别内部的多个任务已accounted for by 任务级聚类自助）。
- 每个类别、每个故障给出描述性 Δ，正文不得据 4 个任务/格做确证性推断。

## 7. 验证模型（V4 Pro）的作用域

- V4 Pro **只在共同 8 任务**上出现，且只做次级分析：与主模型在**同一 8 任务**上比较，
  不能与主模型在 16 任务上的估计混用。
- 分析代码在 `check_validation_scope` 中强制该约束：若 V4 Pro 出现在 8 任务之外的格子，
  报告 `scope.ok = false` 并列出违规任务，主估计不包含 V4 Pro。
- 三模型对比必须把所有模型限制在那 8 个任务上，且标注为次级/探索性。

## 8. 判定语言（先写死）

| 结果 | 允许的表述 | 禁止的表述 |
|---|---|---|
| 交互显著 | 具体 (model, architecture) 组合的退化量更大 | "弱模型通常更受益"等一般化 |
| 交互不显著 | 设计功效不足 | "模型与架构等价" |
| 某格退化 ≈ 0 | 该格控制率高/故障未生效 | "该故障无害" |
| 完成率变化 | 仅描述 agent 自报行为 | 用完成率替代成功率 |

## 9. 复现入口

```bash
# 1) 完整性 → 官方 evaluator → 审计（不发起任何 agent trial）
python scripts/stage_c_pipeline.py run \
  --root <产物根> --output-dir <审计目录> \
  --config experiments/webarena_local_config.json

# 2) 主分析（读上一步的 rows.json）
python scripts/stage_c_analysis.py \
  --rows <审计目录>/rows.json --output-dir <分析目录>
```

两份产物分别是 `audit.json`（缺失/失败清单 + 两个口径的比率）与 `analysis.json` /
`analysis.md`（配对退化、交互、多重比较）。
