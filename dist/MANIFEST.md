# Stage C 故障实验数据包

- 生成时间(UTC): 2026-10-07T09:05:10Z
- 代码版本: 4df685d（branch stagec-exec-chain）
- 内容: 预注册 768 格网格（2 模型 × 2 架构 × 3 故障 × 16 任务 × 2 种子 × 2 条件）
- 单元格(trial record): 768；故障 job 384，触发 367，未触发 17
- 判分: 全部 native（0 缺格 / 0 未配对 / 0 评分错误）

## 目录
- experiments/stage_c_slice{1,1b}: deepseek_v41_flash × react (seed 1/2)
- experiments/stage_c_slice{2b,2c}: deepseek_v41_flash × plan_execute (seed 1/2，含提示词对等修复)
- experiments/stage_c_slice{3,3b}: qwen38_flash × react (seed 1/2)
- experiments/stage_c_slice{4,4b}: qwen38_flash × plan_execute (seed 1/2)
- experiments/stage_c_slice2: 修复前 deepseek × plan_execute seed 1（对照保留，已被 2b 取代）
- experiments/stage_c_full: 768 格合并 rows.json + 预注册分析（analysis.json/md，口径已统一）
- experiments/stage_c_audit: 离线审计（audit.json/md）——分母一致性、交互项口径、地板/天花板、git_dirty
- experiments/stage_c_pe_fixcheck: 提示词修复的单格验证
- experiments/HAR_MANIFEST.tsv: HAR 清单
- traces/stage_c_slice*: 逐调用 provenance JSONL

## traces 内容说明（重要，早前描述有误）
每个 `llm_provenance` 事件并非只有计数，它**完整包含**：任务原文(task/intent)、页面 URL、
页面观察(observation，含 accessibility tree 文本)、模型思维与动作(thought/action)、模型回答(answer)，
以及用量与会话元数据(prompt/completion/total tokens、端点回显 served_model、latency)。
因此 traces 含 WebArena 任务文本、站点内容与 agent 输出。

## 公开性评估
- 密钥扫描：解包后 `sk-*` / api key 模式 0 命中；无 .env。
- 但 traces/答案含站点内容与任务文本，**公开发布前须人工复审**；建议保持私有或仅分支内部共享。

## 未纳入
- 768 个 network.har（约 6.6 GiB，见 HAR_MANIFEST.tsv）；原始页面/AX 快照；.env。

## 审计结论摘要（详见 experiments/stage_c_audit/audit.md）
- 分母：逐格成功率与 Δ 现使用同一批"已触发"配对；未触发 17 对单列 *_itt。
- 交互项：主口径=任务等权，+0.156，CI(boot)[+0.021,+0.292]，p(t15)=0.043；观测加权 OLS 作为敏感性分析（p_normal=0.370）另列，不并入结论。
- 地板/天花板：控制臂 0/24 任务 [21,25,66,102,258,308]，24/24 任务 [118,274]，有区分度 8/16，如实保留。
- git_dirty：86/768 标脏，全部落在修复提交窗口（slice1 84、slice3 2），改动为 thinking/调度/分析，非 agent 决策逻辑。

## 归档文件
- \`stage_c_fault_data_20261007.tar.gz\`（r1，初版）+ SHA256SUMS
- \`stage_c_fault_data_20261007.r2.tar.gz\`（r2，含审计与统一口径，**当前版本**）+ SHA256SUMS.r2
