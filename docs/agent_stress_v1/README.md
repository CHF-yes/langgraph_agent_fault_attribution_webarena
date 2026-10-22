# Agent Stress V1：离线规范包

更新：2026-10-22。状态为specification only，不是已实现或可运行的协议。

## 从这里开始

1. [成本与阶段门槛](成本与阶段闸门.md)：保留2124规模，用量区间和批次门槛。
2. [协议规范](protocol_spec.json)：H/C目标、内容激活、持续性、保护源、证据与事实使用判定。
3. [任务契约Schema](task_contract_schema.json)与[58任务槽位](task_contracts.json)：每任务都需绑定真实内容。当前0份已验证，58份待补。
4. [分析规范](analysis_spec.json)：共享控制相关性、联合模板聚类bootstrap、区间、预算耗尽和缺失。
5. [离线检查结果](offline/offline_audit.json)与[用量测算](offline/cost_estimate.json)。

## 已完成

- 候选名称、仓库/HF来源拆分；15条按任务意图初判需要账号上下文，43条仍未知。
- 开发与留出模板隔离检查，补位冻结点明确定义。
- 登录需求分池：开发 18 条中 6 条 true、12 条 unknown；留出候选 40 条中 9 条 true、31 条 unknown（总计 15/43）。
- 开发六条件调整为clean、H_episode、C_single、C_window10、C_episode、HC_episode，总数仍为432。
- 从348+432+1152+192逐项计算用量，并保留真实单价未知状态。
- 后续实现基线明确为codex/http-service-v1 @ 2cdfea75641ed95f5fa7136def5055e0a8abab01。当前提案分支未合并该运行代码。

## 运行类型的门槛不同

- **干净筛选**：只需要环境、账号、评分链与批次授权；不需要 H/C 引擎或完整任务契约。
- **单任务机制验收**：需要该任务的完整契约、H/C 实现与参考路径。
- **正式留出**：需要正式入组任务全部验证并在故障结果前冻结。

因此 0/58 verified 阻止的是正式故障实验，不是所有模型运行；干净筛选可与 H/C 实现并行。

## Decision impact（污染是否可能改变任务答案）

**声明校验不够；以可复算证据为准。** 每份 C/HC 契约的 `decision_impact` 必须引用
`evidence`：计算脚本、truth/polluted/protected 输入、报告输出、官方结果、HC 实际投递
payload。`scripts/agent_stress_decision.py` 会重跑计算、核对工件 sha256、比较输出、
按**完整任务输出**应用官方规则，并检查 HC 中 H 标记存在且 C 内容仍可见。仅填写布尔
字段而没有 evidence 的已验证契约会被离线审计拒绝。

已提交的两个示例：

| 示例 | 结果 | 说明 |
|---|---|---|
| `decisions/fixture_reviews` | **passed** | 评论文本加入 “ear cups are too small” 后，作者输出 `[] → ["Josef Bürger"]`；官方真值接受未污染、拒绝污染。 |
| `decisions/task118_title` | **rejected** | 改动商品标题后选择的产品 URL 变了，但 task 118 的官方规则接受**任意**匹配的 product 页；同时保护源（另一查询）给出不同但仍合法的产品。说明“标题污染”不能改变该任务的官方结果。 |

另一个筛查发现：task 21 当前页面只有 Jay/Josef 两条评论，而官方期望作者是
Catso/Dibbins/Anglebert/Michelle——**当前环境不满足该任务的官方真值**，不能据此建立
决策影响；需要干净筛选，不能在离线契约里硬写。

**当前没有已验证的任务契约**；以上是机制验证器的正例 fixture 与一个真实负例，不是
正式任务证据。

## 尚未完成

- H/C引擎、content-ready观察适配器和持续机制运行测试。
- 每任务真实资源、entity key、字段取值域、selector和保护源。
- 实际浏览器参考轨迹、HAR、native评分与20步内正确恢复证据。
- 新筛选、新付费批次及正式样本冻结。

任务Schema是字段契约；离线工具检查身份、缺项、已声明参考工件和用量，不是完整JSON Schema实现，更不会执行浏览器恢复。它的structural_checks_passed只表示这些离线检查通过，不表示58任务可解或H/C机制通过。

## 零付费复算

python3 scripts/agent_stress_offline.py --output-dir docs/agent_stress_v1/offline

python3 scripts/agent_stress_offline.py --require-ready

第二条当前应返回2，并报告ready_for_paid_runs=false。脚本不导入模型客户端、不读取密钥、没有trial执行入口。

定向检查：

python3 -m unittest discover -s tests -p test_agent_stress_offline.py -v

本轮检查仅覆盖离线规范/成本工具，不能据此宣称服务分支全套测试数量增加或真实试验通过。
