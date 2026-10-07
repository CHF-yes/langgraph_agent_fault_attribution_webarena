# Stage C 故障实验数据包 (r3)

- 生成时间(UTC): 2026-10-07T09:15:24Z
- 代码版本: e97ba38（branch stagec-exec-chain）
- 网格: 768 格（2 模型 × 2 架构 × 3 故障 × 16 任务 × 2 种子 × 2 条件）
- trial record: 768；故障 job 384，触发 367，未触发 17；判分全 native

## 目录
- experiments/stage_c_slice{1,1b}: deepseek_v41_flash × react (seed 1/2)
- experiments/stage_c_slice{2b,2c}: deepseek_v41_flash × plan_execute (seed 1/2)
- experiments/stage_c_slice{3,3b}: qwen38_flash × react (seed 1/2)
- experiments/stage_c_slice{4,4b}: qwen38_flash × plan_execute (seed 1/2)
- experiments/stage_c_slice2: 修复前对照（已被 2b 取代）
- experiments/stage_c_full: 合并 rows.json + 预注册分析（口径已统一）
- experiments/stage_c_audit: 离线审计（含交互协方差回归、脏记录敏感性）
- experiments/HAR_MANIFEST.tsv: HAR 清单
- traces/stage_c_slice*: 逐调用 provenance JSONL

## traces 内容（重要）
每个 `llm_provenance` 事件含任务原文、页面 URL、页面观察（accessibility tree 文本）、
thought/action、模型回答，以及 token 用量与端点回显 served_model。

## 公开性（已核实）
- 本仓库经未认证 GitHub API 查询为 **public**（HTTP 200）。
- 因此 r1/r2 归档（含 traces 与回答）**已在公开分支上发布**；本节是"已公开内容的复审"，
  不是发布前步骤。若需撤回，仅删除文件不够（历史仍在），须改写历史并强推——请先决定。

## 统计口径（r3 修复）
- **协方差计算修复**：`cluster_robust_cov` 曾把三明治写成 `meat @ (X'X)^{-2}`，
  交互项 SE 被放大到 0.1743；改为 `(X'X)^{-1} meat (X'X)^{-1}` 后为 **0.0705**，
  与任务等权 SE 一致。此前"两法结论相反"的表述**撤回**。
- **交互主口径**：任务等权，预注册**四类分层**自助。全量 +0.1562，CI [+0.042,+0.271]，
  p(t15)=0.0425；观测加权聚类稳健 SE 0.0705，p_t15=0.0425（一致）。
- **稳健性**：剔除 86 条 `git_dirty` 记录后点估计不变（+0.1562），但 CI [+0.021,+0.281]、
  p=0.078 → **不显著**。故交互项应作"提示性/功效不足"报告，不宜称显著。
- **分母**：逐格成功率与 Δ 同用"已触发"配对；未触发 17 对单列 *_itt。
- **地板/天花板**：控制臂 0/24 [21,25,66,102,258,308]、24/24 [118,274]，有区分度 8/16。

## git_dirty（修正表述）
- 86/768 标 `git_dirty=true`（84 slice1、2 slice3）。该标志**只证明被跟踪文件≠HEAD**。
- 两个修复窗口同时改了代码（7、2 个文件：thinking 开关、调度、分析）与被跟踪**实验产物**
  （308、345 个文件），逐 trial 未记录具体清单；thinking 改动**可能影响 LLM 行为**。
- 因此**不能**断言"改动仅代码且不影响 Agent"。已给出剔除这 86 条的敏感性结果（见上）。

## 公开内容复审（2026-10-07T10:55:48Z）
对 r3 归档（4463 个文件）扫描：api key / Bearer / Authorization / Cookie / session id / 私钥 → **0 命中**。
命中并逐类核对：
- email 227 个文件：域名为 example.com 占位、github.com 的 WebArena 基准公开账号、kilianvalkhof.com（公开作者站点）。
- 14–16 位数字 28 个文件：WebArena 测试夹具（测试卡号/商品码）。
- password 11 个文件：全部为占位 `password: 'password'`（任务文本/计划中的示例）。
结论：**未见真实密钥、会话令牌或非公开个人数据**；按决定保留归档，不改写历史。

## 交互项正式报告口径
- 全量：+0.1562，分层 CI [+0.042, +0.271]，p(t15)=0.0425。
- 剔除 86 条 git_dirty 记录：点估计不变 +0.1562，CI [+0.021, +0.281]，p≈0.078。
- 结论：**对数据筛选敏感的提示性发现**，不作为稳健的确认性结论；亦不因一次 p>0.05 直接断言"功效不足"。
- 主分析 notes 已同步此口径。
