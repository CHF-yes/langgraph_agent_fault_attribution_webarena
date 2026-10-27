# 首批 348 条先用 Qwen

修订日期：2026-10-27。按用户本次请求，首批干净筛选使用仓库已有 `qwen38_flash` profile；服务器继续使用其现有凭证和端点。本机没有启动模型运行。

## 范围

- 58 个原候选任务 × react / plan_execute × seed 1/2/3 = 348 条。
- 全部 control、20 步上限、native 评分；开发候选 108 条、留出候选 240 条。
- 只把首批筛选改用 Qwen，后续开发/留出/40 步阶段的模型范围尚未改变。
- 两架构各至少成功 2/3 的门槛只描述本批 Qwen 的可完成性。若后续仍用 DeepSeek，进入故障实验前还需 DeepSeek 自己的干净验证；不能把 Qwen 成功当作 DeepSeek 成功。
- 2124 条仍是原矩阵算术总数。若另加 DeepSeek 确认，它是尚未计入的前瞻性追加，不能悄悄并入原 348；也不因本次请求启动完整 2124 条。

## 已接通的模型选择

`scripts/agent_stress_screening.py` 默认从矩阵 screening 阶段读取 `qwen38_flash`，也支持显式 `--model-profile`。

模型名进入 plan/cell/preflight/授权/执行命令/产物校验/progress。两个模型有不同实验 ID、cell key 和 plan 哈希，旧 DeepSeek 计划/授权不能用于启动 Qwen。执行前还绑定实际 profile 的模型名、端点、temperature、thinking、timeout 和 retries 的身份哈希；不输出凭证。

`experiments/agent_stress_v1/preparation_local_20261022/` 是旧 DeepSeek 历史准备包，不是本次 Qwen 执行授权。新的本机 Qwen 计划位于 `preparation_qwen_local_20261027/`，尚未绑定服务器环境。服务器必须生成自己的准备包。

## 服务器 Agent 接续

先拉取 `codex/agent-stress-preparation`，使用服务器实验 Python 执行：

```bash
python3 scripts/agent_stress_screening.py --model-profile qwen38_flash --check-environment --output-dir experiments/agent_stress_v1/screening_qwen_preflight_server
```

沿用服务器实际的 `WEBARENA_DATASET`、`WEBARENA_HOST` 和 evaluator config；非默认配置用 `--dataset` / `--host` / `--evaluator-config` 显式传入。准备目录必须是新目录。

确认 plan 为 348 条 Qwen control，绑定真实数据、native evaluator 和模型配置，再按本次 348 条范围落地服务器冻结清单及整批用量上限，不能复用旧 DeepSeek 冻结。具体字段见 [执行准备与服务器交接](执行准备与服务器交接.md)。不需要每四条重新讨论研究范围。

```bash
python3 scripts/agent_stress_screening.py --model-profile qwen38_flash --authorization experiments/agent_stress_v1/screening_qwen_preflight_server/authorization.frozen.json --execute --run-root experiments/agent_stress_v1/screening_qwen_run1
```

若准备时显式指定了 dataset/host/config，执行时传入完全相同的参数。首条也是 348 条的一部分；按现有证据链检查模型标识、控制零注入、native 评分和实际用量。故障、污染或基础设施问题按既定规则停批，不按成功率高低停止或补跑成功样本。

原成本规划来自 DeepSeek 四条服务实验，不是 Qwen 的实际 token/费用均值；本批用真实 trace 更新，沿用已有整批停止线，不另外添加逐条确认。
