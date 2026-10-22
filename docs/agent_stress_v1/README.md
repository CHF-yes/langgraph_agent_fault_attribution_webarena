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
- 开发六条件调整为clean、H_episode、C_single、C_window10、C_episode、HC_episode，总数仍为432。
- 从348+432+1152+192逐项计算用量，并保留真实单价未知状态。
- 后续实现基线明确为codex/http-service-v1 @ 2cdfea75641ed95f5fa7136def5055e0a8abab01。当前提案分支未合并该运行代码。

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
