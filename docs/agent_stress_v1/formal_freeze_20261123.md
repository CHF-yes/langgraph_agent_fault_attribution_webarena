# 24 题正式矩阵冻结（2026-11-23，2026-11-25 重新绑定）

初版冻结在 `codex/agent-stress-preparation @ 82d60b5`。2026-11-25 在 **`6a0a302`** 上把 36 条验收的严格预算/故障证据校验接入正式 schema，并按该干净代码提交重新绑定 864 条计划、分析文件和候选授权。**未启动任何正式 trial**，授权文件仍是 candidate。

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
SHA-256：`dd6fa60e58a09866b7dd7927b87a6177d5c5938c4d2ab294b8bcf53a9c12cd4d`

代码绑定：`code.head = 6a0a302e262f1053a8793f3ea0b05308b34e441f`，
`code.fingerprint_sha256 = 225c114fbb023fceebcf2ae2aca2097cd2d58aa0d063313cd4f4b4c33ccbcbcc`。

## 4. 无模型验收与严格校验

`python scripts/agent_stress_plan_runner.py --plan .../plan.json --run-root .../run --check`

- `ok: true`，864 条、288 块、无重复 cell_key。
- 代码指纹、dataset、evaluator、登录态、模型身份、三站可达性全部通过。
- `paid_trials_executed: 0`。
- 输出：`experiments/agent_stress_v1/formal_freeze_20261123/check.json`。

正式 schema 现在与 36 条验收使用同一套 `verify_completed` 严格校验，新跑和续跑都经过该入口。回归测试 `tests/test_plan_runner.py` **21 passed, 6 subtests passed**，覆盖：

- 拒绝：超过 20 回合、预算耗尽后仍有 planner 调用、故障臂缺少 `fault_evidence.json`、单次臂注入超过一次、控制臂出现注入。
- 接受：有完整证据的合法零暴露、官方错答、预算耗尽。

## 5. 端点自然暴露复核

复核方法：只看已有成功轨迹中 Agent 自己的浏览器动作；动作后的 URL（或 `goto` 目标）命中冻结端点才算自然命中。仅同 URL 的页内交互/后台 AJAX 不算自然命中。逐轨迹路径和动作步见
`experiments/agent_stress_v1/formal_freeze_20261123/endpoint_natural_exposure.json` 和 `.md`。

- **23/24** 端点已在成功轨迹中自然命中。
- **163**：4/4 成功轨迹命中。Agent 先点 Reviews 页内标签，再点评论第 2 页，URL 变为 `/review/product/listAjax/id/38805/?p=2`，属于自然命中。
- **164**：**0/6 命中**。Agent 只点 Reviews 页内标签，URL 始终停留在商品页，评论由同 URL 后台请求取得。当前服务级协议要求导航动作，因此正式故障臂可能零暴露。
- **168**：1/6 命中 `/dashboard/projects`；多数成功轨迹走 `/` 的 `personal=true&sort=stars_desc` 列表。端点可达但覆盖偏低。
- **294**：1/3 命中 `/search`；其余成功轨迹走首页/dashboard/explore 的项目筛选。端点可达但覆盖偏低。
- 其余端点（含 295）成功率覆盖 100%。

因此本轮仍有自然暴露疑问的任务 ID：**164（实质问题）、168、294（覆盖问题）**。本轮未改端点或题目；如需处理，依据是上面的轨迹动作步和覆盖比例。可选方案：164 替换题目、改到自然导航的任务关键页，或协议增加“同 URL 后台请求激活”；168/294 改到更常用的任务关键页或保留并注明低覆盖。

## 6. 冻结分析

主结果：官方二元成功率；故障效应 = **持续臂 − 控制臂**；比较该效应是否随模型和架构不同。
次要：单次臂对比、实际注入次数、步数、token、人工判定预算耗尽。
合法未触发、绕行、错答和耗尽预算保留在分配臂分母。重复先在任务内汇总，再在模板内等权，最后按模板聚类 bootstrap。
正式报告全 24 题，并按同口径补报剔除 28、228、314 的 21 题敏感性分析。

冻结口径：`docs/agent_stress_v1/formal_matrix_analysis_20261123.json` 和
`experiments/agent_stress_v1/formal_freeze_20261123/analysis_plan.json`。

## 7. 预计用量与剩余问题

按已验收 36 条的每条件 prompt token 均值外推：约 **117.1M prompt tokens**
（区间 93.7M–146.4M），总 token 约 124.1M。无运行阻塞；正式 trial 需要显式授权。
自然暴露疑问见第 5 节，授权保持 candidate。
