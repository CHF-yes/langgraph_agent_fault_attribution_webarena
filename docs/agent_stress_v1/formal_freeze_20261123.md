# 24 题正式矩阵冻结（2026-11-23）

本轮在 `codex/agent-stress-preparation` 的 `82d60b5` 上完成逐题浏览器回放、正式计划生成和无模型验收。**未启动任何正式 trial**，授权文件仍是 candidate。

## 1. 候选名单

24 题：`27, 28, 48, 49, 66, 69, 148, 149, 163, 164, 168, 188, 190, 206, 227, 228, 294, 295, 305, 314, 316, 384, 784, 785`。

- 14 个模板，每模板最多 2 题。
- 8 易 / 10 中 / 6 难。
- 站点：shopping、gitlab、reddit。
- 补位题：69、149、206（2/6，难）、168（6/6，易）。
- 28、228、314 保留并标记 `fault_pilot`，只从 21 题敏感性分析中剔除。

机器可读记录：`experiments/agent_stress_v1/formal_freeze_20261123/roster.json`。

## 2. 逐题端点与参考路径

每题执行两种无模型脚本路径，均从任务起始页和共享登录态出发，并用 native evaluator 评分：

1. **正常路径**：固定浏览器导航，≤20 回合，读取任务关键端点，官方评分通过。
2. **故障恢复路径**：先对端点打一次合格导航，确认收到 500/503 观察；用 9 次非目标导航走完 10 步窗口；窗口释放后重读端点并完成官方契约。

结果：**24/24 通过**。逐题端点、回合数、注入次数、评分证据见
`experiments/agent_stress_v1/formal_freeze_20261123/acceptance.md`。
每题的 `actions.jsonl`、页面快照、`agent_response.json`、`official_eval.json`、
`fault_evidence.json` 在 `reference/taskNN/`。原始 HAR 因体积过大不入库，
其 sha256 和字节数记录在 `experiments/HAR_MANIFEST.tsv`。

## 3. 正式计划

- 24 任务 × 2 模型 × 2 架构 × 3 种子 × 3 条件 = **864 条**。
- **288 个三臂块**：每个 task/model/architecture/seed 块内控制、单次、持续随机排序，顺序固定在计划里。
- 20 执行回合；持续窗口 = 首次暴露 + 后续 9 个代理步。
- 每条重置浏览器与登录态；控制臂与同块故障臂同期运行。
- 模型：`qwen38_flash`、`deepseek_v41_flash`；架构：`react`、`plan_execute`。
- 绑定代码指纹、模型服务身份、dataset、evaluator、登录态、端点表、随机种子、产物路径和续跑 cell_key。

计划：`experiments/agent_stress_v1/formal_freeze_20261123/plan.json`
SHA-256：`8c7ce38fec38224f805e9781c6e2bd6cf355cd9eb3ceb692efdadc4bebb917c0`

## 4. 无模型验收

`python scripts/agent_stress_plan_runner.py --plan .../plan.json --run-root .../run --check`

- `ok: true`，864 条、288 块、无重复 cell_key。
- 代码指纹、dataset、evaluator、登录态、模型身份、三站可达性全部通过。
- `paid_trials_executed: 0`。
- 输出：`experiments/agent_stress_v1/formal_freeze_20261123/check.json`。

## 5. 冻结分析

主结果：官方二元成功率；故障效应 = **持续臂 − 控制臂**；比较该效应是否随模型和架构不同。
次要：单次臂对比、实际注入次数、步数、token、人工判定预算耗尽。
合法未触发、绕行、错答和耗尽预算保留在分配臂分母。重复先在任务内汇总，再在模板内等权，最后按模板聚类 bootstrap。
正式报告全 24 题，并按同口径补报剔除 28、228、314 的 21 题敏感性分析。

冻结口径：`docs/agent_stress_v1/formal_matrix_analysis_20261123.json` 和
`experiments/agent_stress_v1/formal_freeze_20261123/analysis_plan.json`。

## 6. 预计用量与阻塞

按已验收 36 条的每条件 prompt token 均值外推：约 **117.1M prompt tokens**
（区间 93.7M–146.4M），总 token 约 124.1M。当前无阻塞；正式 trial 需要显式授权。
