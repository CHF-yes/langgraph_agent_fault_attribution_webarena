# Qwen 48 条本地试跑

执行日期：2026-10-28。状态：本地试跑，不改变 2124 条研究范围，也不替代服务器冻结。

## 依据与范围

- 代码取远端 `origin/codex/agent-stress-preparation @ 5d47f77`（Qwen 先行筛选），另加本分支已有的 `--skip-completed` / `--limit` 批量上限补丁。
- 从 348 条 Qwen control 计划中按既定 shuffle 顺序取前 48 条：`--limit 48`，20 步上限，native 评分，零故障注入。
- 模型 `qwen3.8-flash`，数据集绑定 `webarena-verified.json`（sha256 `d652756608146633…`），localhost 三站点（shopping / reddit / gitlab）。

## 环境与授权

- Preflight 全部通过：数据集/站点/evaluator 绑定、依赖、Chromium、模型 profile 均正常；evaluator package `41334a7a…`，model identity `dba90ace…`，plan `76de200d…`。
- 授权沿用 348 条批次（`max_trials=348`），本次仅实际执行 48 条；prompt 上限 10,000,000。`--limit` 触发后停批，不自动补跑。
- 本次授权已被 `.claimed` 占用；其余 300 条需要新的准备包与授权，不能复用本次授权。

## 结果

48/48 完成，9 成功（18.75%），39 失败；总 prompt tokens 8,748,261，平均 182k/条。

| 架构 | 条数 | 成功 |
| --- | ---: | ---: |
| react | 27 | 4 |
| plan_execute | 21 | 5 |

墙钟时间 10:42:10–11:36:40（约 54.5 分钟），单条中位 63s、均值 68s。

与 2026-10-22 的 DeepSeek run4（31 条有效）对比：

| 每条约 | Qwen | DeepSeek |
| --- | ---: | ---: |
| 墙钟中位 | 63s | 40s |
| LLM 耗时 | 24s（41%） | 6s（18%） |
| 单次调用中位 | 2.3s | 1.2s |
| 调用数 / 步数 | 10.2 / 11 | 8.9 / 9 |
| prompt tokens | 182k | 134k |

变慢主要来自模型侧：单次调用约 2 倍延迟，且步数更多带来更多调用与页面操作；本地评测每条约 5s，不是主要因素。

## 产物位置

本目录只提交 `progress.json`、`authorization.frozen.json`、`preflight.json`。完整 trace、HAR、截图仍在本机：

- 运行目录：`/data/webarena/_qwen48_remote/experiments/agent_stress_v1/screening_qwen48_run1/`
- trace：`/data/webarena/_qwen48_remote/traces/`
- 日志：`/data/webarena/logs/qwen48_run1.log`
