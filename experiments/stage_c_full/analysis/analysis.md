## Stage C 主分析

- 验证模型作用域检查: 通过
- 主模型: deepseek_v41_flash, qwen38_flash
- 主观测数: 768，配对数: 384

| 故障 | 配对 | Δ(pp) | 95% CI | p | p(Holm) |
|---|---|---|---|---|---|
| agent_param_error | 116 | +0.2 | [-3.9, +4.6] | 0.9342 | 1.0000 |
| web_dom_missing | 126 | -4.7 | [-10.9, +0.8] | 0.1911 | 0.5734 |
| web_http_error | 125 | +1.0 | [-4.6, +7.4] | 0.7781 | 1.0000 |

步数耗尽（描述性，不计入成功率）：总体 97/768 = 12.6%，未知 0/768，均值步数 8.3
  - 故障 `agent_param_error`: 36/256 = 14%（未知 0）
  - 故障 `web_dom_missing`: 34/256 = 13%（未知 0）
  - 故障 `web_http_error`: 27/256 = 11%（未知 0）

Holm 家族：per_fault_primary = ['agent_param_error', 'web_dom_missing', 'web_http_error']

逐 (model, architecture, fault) 描述性结果：

| model | arch | fault | n_pair | control | fault | Δ(pp) | 95% CI | p |
|---|---|---|---|---|---|---|---|---|
| deepseek_v41_flash | plan_execute | agent_param_error | 30 | 0.188 | 0.250 | -6.2 | [-15.6, +3.1] | 0.3173 |
| deepseek_v41_flash | plan_execute | web_dom_missing | 32 | 0.281 | 0.375 | -9.4 | [-21.9, +3.1] | 0.3173 |
| deepseek_v41_flash | plan_execute | web_http_error | 32 | 0.250 | 0.406 | -15.6 | [-28.1, -3.1] | 0.0379 |
| deepseek_v41_flash | react | agent_param_error | 28 | 0.469 | 0.469 | +0.0 | [-6.7, +6.7] | 1.0000 |
| deepseek_v41_flash | react | web_dom_missing | 32 | 0.438 | 0.469 | -3.1 | [-9.4, +0.0] | 0.3173 |
| deepseek_v41_flash | react | web_http_error | 32 | 0.562 | 0.469 | +9.4 | [-3.1, +21.9] | 0.2523 |
| qwen38_flash | plan_execute | agent_param_error | 31 | 0.375 | 0.375 | +0.0 | [+0.0, +0.0] | — |
| qwen38_flash | plan_execute | web_dom_missing | 30 | 0.375 | 0.375 | +0.0 | [-12.5, +12.5] | 1.0000 |
| qwen38_flash | plan_execute | web_http_error | 31 | 0.438 | 0.312 | +12.5 | [+0.0, +25.0] | 0.0833 |
| qwen38_flash | react | agent_param_error | 27 | 0.500 | 0.406 | +12.5 | [+0.0, +25.0] | 0.1432 |
| qwen38_flash | react | web_dom_missing | 32 | 0.344 | 0.406 | -6.2 | [-18.8, +6.2] | 0.3173 |
| qwen38_flash | react | web_http_error | 30 | 0.344 | 0.344 | +0.0 | [-12.5, +12.5] | 1.0000 |

> 表中 `—` 表示该对比的聚类稳健标准误为 0（无任务间变异），无法给出 p 值；此时以整群自助区间为准。

- **Model × Architecture 交互**（condition:model:architecture）: +0.1562, p=0.3701, 95% CI [+0.0417, +0.2708]

> 退化量定义为 control 成功率减 fault 成功率（百分点 = ×100）。
> 区间按任务整群、按四个预注册类别分层自助；重复 trial 不是独立样本。
> Holm 家族固定为三个故障级主对比；逐 (model, architecture, fault) 格子为描述性。
> 矩阵不健康（缺格/未配对/评分报错）时不输出 p 值，只给描述性区间；不显著只能报告为功效不足，不能当作等价性证据。
> V4 Pro 仅用于共同 8 任务次级分析，不进入主估计，也不报显著性。
