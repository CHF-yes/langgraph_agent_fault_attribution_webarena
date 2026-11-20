# 18 条千问 Flash + ReAct 服务级 HTTP 故障预试报告

实验目录：`experiments/agent_stress_v1/http_pilot_20261120`  
计划哈希：`2bce513e7869f5d03fe16989686f36abfc838c5510a32fb294e0441d64dd24f2`  
协议：`ep-http-service-v1`，窗口 10 步，上限 20 步，模型 `qwen38_flash`（实际服务模型 `qwen3.8-flash`），架构 `react`，登录态 `experiments/auth_20261120`；评分器 `stage-c-eval-adapter/1`（分布 `1.2.3.post1.dev0`，包哈希 `41334a7a…`）。

## 1. 开跑前核验

### 1.1 候选与端点

| 任务 | 站点 | 官方起始页 | 注入端点 | 端点选择/调整理由 | 受扰时的合法替代路径 |
| ---: | --- | --- | --- | --- | --- |
| 228 | shopping | http://localhost:7770 | `http://localhost:7770/catalogsearch/result` | 干净臂实际经过搜索端点，未调整 | 目录/商品页、`/search/ajax/suggest`、`/catalogsearch/advanced` 不在故障范围 |
| 28 | reddit | http://localhost:9999 | `http://localhost:9999/f/WorcesterMA` | 干净臂实际进入 WorcesterMA 板块（`/f/Worcester` 是死路径，未采用） | `/forums/all`、`/forums`、其他板块与 `/search` 保持正常 |
| 314 | gitlab | http://localhost:8023 | `http://localhost:8023/primer/design/-/graphs` | 原候选 `/api/graphql` 只由 XHR 触发，观察层注入不会投递；改用干净臂实际导航的 graphs 数据页（含 `?format=json` 变体） | 项目树页、commits 页、REST `/api/v4/projects/180/repository/contributors` 保持正常 |

登录态实测：shopping 已登录（My Account/Sign Out）；gitlab 已登录（dashboard/projects）；reddit 为设计内的匿名空状态（任务 28 为公开只读）。三个站点的服务端口 7770/8023/9999 均在监听。

### 1.2 无模型固定动作注入核验（9 条）

| 任务 | 条件 | 注入次数 | 首次命中显示 500/503 | 同端点换查询仍报错 | 其他端点正常 | 窗口结束后回访正常 | 协议判决 |
| ---: | --- | ---: | --- | --- | --- | --- | --- |
| 28 | control | 0 | 否 | 否 | 是 | 是 | valid |
| 28 | single | 1 | 是 | 否 | 是 | 是 | valid |
| 28 | sustained | 2 | 是 | 是 | 是 | 是 | valid |
| 228 | control | 0 | 否 | 否 | 是 | 是 | valid |
| 228 | single | 1 | 是 | 否 | 是 | 是 | valid |
| 228 | sustained | 2 | 是 | 是 | 是 | 是 | valid |
| 314 | control | 0 | 否 | 否 | 是 | 是 | valid |
| 314 | single | 1 | 是 | 否 | 是 | 是 | valid |
| 314 | sustained | 2 | 是 | 是 | 是 | 是 | valid |

结论：干净臂 0 注入；单次臂仅首次命中报错、之后同端点与回访正常；持续臂在窗口内 2 次同端点（含换查询/子路径）均报错，其他端点与窗口结束后正常。三条任务的端点匹配与释放规则均通过。

## 2. 6 行配对表（任务 × 种子）

注入步为 Agent 步（首步=1；协议内部 proxy 步多计一次 bootstrap）。绕行=首次注入后访问过目标端点之外的页面（行为标记，不保证定位到了替代完成路径）。

| 任务×种子 | 干净 | 单次 | 持续 |
| --- | --- | --- | --- |
| 228 × seed1 | 成功 / 5步 / 0次 / 绕行:否 | 成功 / 5步 / 1次@3 / 绕行:否 | 成功 / 10步 / 2次@3,4 / 绕行:是 |
| 228 × seed2 | 成功 / 4步 / 0次 / 绕行:否 | 成功 / 7步 / 1次@3 / 绕行:是 | 成功 / 9步 / 2次@3,4 / 绕行:是 |
| 28 × seed1 | 成功 / 9步 / 0次 / 绕行:否 | 成功 / 10步 / 1次@5 / 绕行:否 | 成功 / 17步 / 8次@5,6,7,8,9,11,12,13 / 绕行:是 |
| 28 × seed2 | 成功 / 9步 / 0次 / 绕行:否 | 成功 / 10步 / 1次@6 / 绕行:否 | 成功 / 20步 / 10次@6,7,8,9,10,11,12,13,14,15 / 绕行:否 |
| 314 × seed1 | 成功 / 7步 / 0次 / 绕行:否 | 成功 / 5步 / 1次@3 / 绕行:否 | 成功 / 17步 / 4次@3,4,8,10 / 绕行:是 |
| 314 × seed2 | 成功 / 4步 / 0次 / 绕行:否 | 成功 / 5步 / 1次@2 / 绕行:是 | 失败 / 14步 / 5次@3,4,5,7,11 / 绕行:是 |

### 2.1 汇总

- 官方成功数：干净 **6/6**，单次 **6/6**，持续 **5/6**。
- 干净成功 → 故障失败：单次 0 块，持续 1 块（任务 314 / seed2）。故障成功 → 干净失败：0 块。
- 额外步数（相对同块干净臂）：单次合计 +4 步（均值 +0.67）；持续合计 +49 步（均值 +8.17）。
- 持续臂重复暴露：**6/6** 条均 ≥2 次，合计 31 次，单条 2–10 次（最多为任务 28 / seed2 的 10 次）。单次臂 6/6 条恰好 1 次。
- 额外步数集中：持续臂为任务 28/seed2（+11）、任务 314/两个种子（各 +10）、任务 28/seed1（+8）；购物 228 两个种子均为 +5。单次臂额外步数很小（最大 +3），任务 314/seed1 反而少 2 步，属模型波动。
- 首次错误类型：6 个块的 single 与 sustained 均为 `500`，一致；6 个干净臂 0 注入、0 投递。
- 干净路径命中：6/6 个控制格在正常任务流中实际命中目标端点（证据中 `matched_endpoint=true`），但投递 0 次，确认零控制污染且注入点确实在正常路径上。
- 单次臂按契约只暴露一次；两条在首次注入后较快结束（228/seed1、314/seed1 均为 5 步）。持续臂 6/6 条重复暴露，详见上表。
- 投递证据：12 条故障臂的每条注入都在轨迹中出现 `500 Internal Server Error`，控制臂 0 条；所有 `fault_episode` 通过端点/窗口/证据哈希校验。

### 2.2 机制观察

- 单次臂：Agent 看到一次 500 后普遍重试同一端点（重试已释放），多数立即恢复；任务 228/seed2 改用 Advanced Search，任务 314/seed2 先点项目树再结束，均成功。
- 持续臂：Agent 在窗口内反复重试同端点（28/seed2 连续 10 次），或转而访问目录页、Advanced Search、commits/analytics、REST contributors 等替代路径；窗口结束后回到目标端点即可恢复。
- 唯一掉分：任务 314/seed2 持续臂 14 步、5 次注入后提交 `retrieved_data: []`，官方判失败；同块干净 4 步成功、单次 5 步成功。此处是“干净成功 → 持续失败”，但仅 1 个任务，未达到“至少两个不同任务”的掉分信号门槛。
- 成功率方向：干净 6/6、单次 6/6、持续 5/6；方向与步骤膨胀一致，但本批只判断注入是否正常、影响方向和行为机制，不做显著性推断。

## 3. 逐条轨迹与评分记录

| 任务×种子 | 条件 | 轨迹 | 官方评分 | 故障证据 |
| --- | --- | --- | --- | --- |
| 228×s1 | control | `traces/20261120T183528-941fa5/ep-http-service-v1__single__qwen38_flash__react__web_http_error__task228__seed1__control__rep0.jsonl` | `experiments/agent_stress_v1/http_pilot_20261120/cells/qwen38_flash_task228_react_seed1_control/outputs/web_http_error_task228_seed1/228/control_seed_1/official_eval.json` | `experiments/agent_stress_v1/http_pilot_20261120/cells/qwen38_flash_task228_react_seed1_control/outputs/web_http_error_task228_seed1/228/control_seed_1/fault_evidence.json` |
| 228×s1 | single | `traces/20261120T183510-e0920a/ep-http-service-v1__single__qwen38_flash__react__web_http_error__task228__seed1__fault__rep0.jsonl` | `experiments/agent_stress_v1/http_pilot_20261120/cells/qwen38_flash_task228_react_seed1_single/outputs/web_http_error_task228_seed1/228/web_http_error_seed_1/official_eval.json` | `experiments/agent_stress_v1/http_pilot_20261120/cells/qwen38_flash_task228_react_seed1_single/outputs/web_http_error_task228_seed1/228/web_http_error_seed_1/fault_evidence.json` |
| 228×s1 | sustained | `traces/20261120T183550-b4a8a3/ep-http-service-v1__persistent__qwen38_flash__react__web_http_error__task228__seed1__fault__rep0.jsonl` | `experiments/agent_stress_v1/http_pilot_20261120/cells/qwen38_flash_task228_react_seed1_sustained/outputs/web_http_error_task228_seed1/228/web_http_error_seed_1/official_eval.json` | `experiments/agent_stress_v1/http_pilot_20261120/cells/qwen38_flash_task228_react_seed1_sustained/outputs/web_http_error_task228_seed1/228/web_http_error_seed_1/fault_evidence.json` |
| 228×s2 | control | `traces/20261120T184753-7e2bd4/ep-http-service-v1__single__qwen38_flash__react__web_http_error__task228__seed2__control__rep0.jsonl` | `experiments/agent_stress_v1/http_pilot_20261120/cells/qwen38_flash_task228_react_seed2_control/outputs/web_http_error_task228_seed2/228/control_seed_2/official_eval.json` | `experiments/agent_stress_v1/http_pilot_20261120/cells/qwen38_flash_task228_react_seed2_control/outputs/web_http_error_task228_seed2/228/control_seed_2/fault_evidence.json` |
| 228×s2 | single | `traces/20261120T184843-4e6ff4/ep-http-service-v1__single__qwen38_flash__react__web_http_error__task228__seed2__fault__rep0.jsonl` | `experiments/agent_stress_v1/http_pilot_20261120/cells/qwen38_flash_task228_react_seed2_single/outputs/web_http_error_task228_seed2/228/web_http_error_seed_2/official_eval.json` | `experiments/agent_stress_v1/http_pilot_20261120/cells/qwen38_flash_task228_react_seed2_single/outputs/web_http_error_task228_seed2/228/web_http_error_seed_2/fault_evidence.json` |
| 228×s2 | sustained | `traces/20261120T184813-695090/ep-http-service-v1__persistent__qwen38_flash__react__web_http_error__task228__seed2__fault__rep0.jsonl` | `experiments/agent_stress_v1/http_pilot_20261120/cells/qwen38_flash_task228_react_seed2_sustained/outputs/web_http_error_task228_seed2/228/web_http_error_seed_2/official_eval.json` | `experiments/agent_stress_v1/http_pilot_20261120/cells/qwen38_flash_task228_react_seed2_sustained/outputs/web_http_error_task228_seed2/228/web_http_error_seed_2/fault_evidence.json` |
| 28×s1 | control | `traces/20261120T183727-e0271f/ep-http-service-v1__single__qwen38_flash__react__web_http_error__task28__seed1__control__rep0.jsonl` | `experiments/agent_stress_v1/http_pilot_20261120/cells/qwen38_flash_task28_react_seed1_control/outputs/web_http_error_task28_seed1/28/control_seed_1/official_eval.json` | `experiments/agent_stress_v1/http_pilot_20261120/cells/qwen38_flash_task28_react_seed1_control/outputs/web_http_error_task28_seed1/28/control_seed_1/fault_evidence.json` |
| 28×s1 | single | `traces/20261120T183648-9e5970/ep-http-service-v1__single__qwen38_flash__react__web_http_error__task28__seed1__fault__rep0.jsonl` | `experiments/agent_stress_v1/http_pilot_20261120/cells/qwen38_flash_task28_react_seed1_single/outputs/web_http_error_task28_seed1/28/web_http_error_seed_1/official_eval.json` | `experiments/agent_stress_v1/http_pilot_20261120/cells/qwen38_flash_task28_react_seed1_single/outputs/web_http_error_task28_seed1/28/web_http_error_seed_1/fault_evidence.json` |
| 28×s1 | sustained | `traces/20261120T183801-6dbd61/ep-http-service-v1__persistent__qwen38_flash__react__web_http_error__task28__seed1__fault__rep0.jsonl` | `experiments/agent_stress_v1/http_pilot_20261120/cells/qwen38_flash_task28_react_seed1_sustained/outputs/web_http_error_task28_seed1/28/web_http_error_seed_1/official_eval.json` | `experiments/agent_stress_v1/http_pilot_20261120/cells/qwen38_flash_task28_react_seed1_sustained/outputs/web_http_error_task28_seed1/28/web_http_error_seed_1/fault_evidence.json` |
| 28×s2 | control | `traces/20261120T183900-c8dd5e/ep-http-service-v1__single__qwen38_flash__react__web_http_error__task28__seed2__control__rep0.jsonl` | `experiments/agent_stress_v1/http_pilot_20261120/cells/qwen38_flash_task28_react_seed2_control/outputs/web_http_error_task28_seed2/28/control_seed_2/official_eval.json` | `experiments/agent_stress_v1/http_pilot_20261120/cells/qwen38_flash_task28_react_seed2_control/outputs/web_http_error_task28_seed2/28/control_seed_2/fault_evidence.json` |
| 28×s2 | single | `traces/20261120T183932-2630ca/ep-http-service-v1__single__qwen38_flash__react__web_http_error__task28__seed2__fault__rep0.jsonl` | `experiments/agent_stress_v1/http_pilot_20261120/cells/qwen38_flash_task28_react_seed2_single/outputs/web_http_error_task28_seed2/28/web_http_error_seed_2/official_eval.json` | `experiments/agent_stress_v1/http_pilot_20261120/cells/qwen38_flash_task28_react_seed2_single/outputs/web_http_error_task28_seed2/28/web_http_error_seed_2/fault_evidence.json` |
| 28×s2 | sustained | `traces/20261120T184004-6e45a6/ep-http-service-v1__persistent__qwen38_flash__react__web_http_error__task28__seed2__fault__rep0.jsonl` | `experiments/agent_stress_v1/http_pilot_20261120/cells/qwen38_flash_task28_react_seed2_sustained/outputs/web_http_error_task28_seed2/28/web_http_error_seed_2/official_eval.json` | `experiments/agent_stress_v1/http_pilot_20261120/cells/qwen38_flash_task28_react_seed2_sustained/outputs/web_http_error_task28_seed2/28/web_http_error_seed_2/fault_evidence.json` |
| 314×s1 | control | `traces/20261120T184702-1eff4c/ep-http-service-v1__single__qwen38_flash__react__web_http_error__task314__seed1__control__rep0.jsonl` | `experiments/agent_stress_v1/http_pilot_20261120/cells/qwen38_flash_task314_react_seed1_control/outputs/web_http_error_task314_seed1/314/control_seed_1/official_eval.json` | `experiments/agent_stress_v1/http_pilot_20261120/cells/qwen38_flash_task314_react_seed1_control/outputs/web_http_error_task314_seed1/314/control_seed_1/fault_evidence.json` |
| 314×s1 | single | `traces/20261120T184616-5d92f3/ep-http-service-v1__single__qwen38_flash__react__web_http_error__task314__seed1__fault__rep0.jsonl` | `experiments/agent_stress_v1/http_pilot_20261120/cells/qwen38_flash_task314_react_seed1_single/outputs/web_http_error_task314_seed1/314/web_http_error_seed_1/official_eval.json` | `experiments/agent_stress_v1/http_pilot_20261120/cells/qwen38_flash_task314_react_seed1_single/outputs/web_http_error_task314_seed1/314/web_http_error_seed_1/fault_evidence.json` |
| 314×s1 | sustained | `traces/20261120T184418-f07559/ep-http-service-v1__persistent__qwen38_flash__react__web_http_error__task314__seed1__fault__rep0.jsonl` | `experiments/agent_stress_v1/http_pilot_20261120/cells/qwen38_flash_task314_react_seed1_sustained/outputs/web_http_error_task314_seed1/314/web_http_error_seed_1/official_eval.json` | `experiments/agent_stress_v1/http_pilot_20261120/cells/qwen38_flash_task314_react_seed1_sustained/outputs/web_http_error_task314_seed1/314/web_http_error_seed_1/fault_evidence.json` |
| 314×s2 | control | `traces/20261120T184341-6f158d/ep-http-service-v1__single__qwen38_flash__react__web_http_error__task314__seed2__control__rep0.jsonl` | `experiments/agent_stress_v1/http_pilot_20261120/cells/qwen38_flash_task314_react_seed2_control/outputs/web_http_error_task314_seed2/314/control_seed_2/official_eval.json` | `experiments/agent_stress_v1/http_pilot_20261120/cells/qwen38_flash_task314_react_seed2_control/outputs/web_http_error_task314_seed2/314/control_seed_2/fault_evidence.json` |
| 314×s2 | single | `traces/20261120T184105-ed0fa4/ep-http-service-v1__single__qwen38_flash__react__web_http_error__task314__seed2__fault__rep0.jsonl` | `experiments/agent_stress_v1/http_pilot_20261120/cells/qwen38_flash_task314_react_seed2_single/outputs/web_http_error_task314_seed2/314/web_http_error_seed_2/official_eval.json` | `experiments/agent_stress_v1/http_pilot_20261120/cells/qwen38_flash_task314_react_seed2_single/outputs/web_http_error_task314_seed2/314/web_http_error_seed_2/fault_evidence.json` |
| 314×s2 | sustained | `traces/20261120T184150-aa7b59/ep-http-service-v1__persistent__qwen38_flash__react__web_http_error__task314__seed2__fault__rep0.jsonl` | `experiments/agent_stress_v1/http_pilot_20261120/cells/qwen38_flash_task314_react_seed2_sustained/outputs/web_http_error_task314_seed2/314/web_http_error_seed_2/official_eval.json` | `experiments/agent_stress_v1/http_pilot_20261120/cells/qwen38_flash_task314_react_seed2_sustained/outputs/web_http_error_task314_seed2/314/web_http_error_seed_2/fault_evidence.json` |

每格的 `trial_record.json`、`agent_response.json`、`network.har` 与上表同目录；运行日志为 `cells/<cell_key>/trial.log`。

## 4. 口径与限制

- 本批 18 条全部完成，0 条基础设施失败；所有格子 `max_steps=20`，无步数耗尽。
- 块 1 首次执行后，分析脚本在控制臂空 `snapshot.target` 上抛错（仅元数据落盘失败，trial 本身完整）；修复后整块 3 条重跑，最终块 1 数据全部来自重跑，未混用首轮产物。
- 端点故障为观察层模拟（真实导航已发生，HAR 可能仍是 200），不是真实断连。
- reddit 使用设计内匿名状态；shopping/gitlab 使用已验证的登录态，并写入计划哈希。
- 块内条件顺序已预随机化：
  - 块 1：任务 228 / seed1 → ['single', 'control', 'sustained']
  - 块 2：任务 28 / seed1 → ['single', 'control', 'sustained']
  - 块 3：任务 28 / seed2 → ['control', 'single', 'sustained']
  - 块 4：任务 314 / seed2 → ['single', 'sustained', 'control']
  - 块 5：任务 314 / seed1 → ['sustained', 'single', 'control']
  - 块 6：任务 228 / seed2 → ['control', 'sustained', 'single']
