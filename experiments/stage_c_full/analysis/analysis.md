## Stage C 主分析

- 验证模型作用域检查: 通过
- 主模型: deepseek_v41_flash, qwen38_flash
- 主观测数: 768，配对数: 384

| 故障 | 配对(触发/ITT) | Δ_触发(pp) | 95% CI | p | p(Holm) | Δ_ITT(pp) |
|---|---|---|---|---|---|---|
| agent_param_error | 116/128 | +0.2 | [-3.9, +4.6] | 0.9353 | 1.0000 | +0.8 |
| web_dom_missing | 126/128 | -4.7 | [-10.9, +0.8] | 0.2108 | 0.6325 | -4.7 |
| web_http_error | 125/128 | +1.0 | [-4.6, +7.4] | 0.7819 | 1.0000 | +1.6 |

步数耗尽（描述性，不计入成功率）：总体 97/768 = 12.6%，未知 0/768，均值步数 8.3
  - 故障 `agent_param_error`: 36/256 = 14%（未知 0）
  - 故障 `web_dom_missing`: 34/256 = 13%（未知 0）
  - 故障 `web_http_error`: 27/256 = 11%（未知 0）

Holm 家族：per_fault_primary = ['agent_param_error', 'web_dom_missing', 'web_http_error']

逐 (model, architecture, fault) 描述性结果：

| model | arch | fault | n_pair | control | fault | Δ(pp) | 95% CI | p |
|---|---|---|---|---|---|---|---|---|
| deepseek_v41_flash | plan_execute | agent_param_error | 30 | 0.200 | 0.267 | -6.2 | [-15.6, +3.1] | 0.3332 |
| deepseek_v41_flash | plan_execute | web_dom_missing | 32 | 0.281 | 0.375 | -9.4 | [-21.9, +3.1] | 0.3332 |
| deepseek_v41_flash | plan_execute | web_http_error | 32 | 0.250 | 0.406 | -15.6 | [-28.1, -3.1] | 0.0555 |
| deepseek_v41_flash | react | agent_param_error | 28 | 0.500 | 0.500 | +0.0 | [-6.7, +6.7] | 1.0000 |
| deepseek_v41_flash | react | web_dom_missing | 32 | 0.438 | 0.469 | -3.1 | [-9.4, +0.0] | 0.3332 |
| deepseek_v41_flash | react | web_http_error | 32 | 0.562 | 0.469 | +9.4 | [-3.1, +21.9] | 0.2702 |
| qwen38_flash | plan_execute | agent_param_error | 31 | 0.387 | 0.387 | +0.0 | [+0.0, +0.0] | — |
| qwen38_flash | plan_execute | web_dom_missing | 30 | 0.400 | 0.400 | +0.0 | [-12.5, +12.5] | 1.0000 |
| qwen38_flash | plan_execute | web_http_error | 31 | 0.419 | 0.323 | +12.5 | [+0.0, +25.0] | 0.1038 |
| qwen38_flash | react | agent_param_error | 27 | 0.556 | 0.481 | +12.5 | [+0.0, +25.0] | 0.1639 |
| qwen38_flash | react | web_dom_missing | 32 | 0.344 | 0.406 | -6.2 | [-18.8, +6.2] | 0.3332 |
| qwen38_flash | react | web_http_error | 30 | 0.367 | 0.367 | +0.0 | [-12.5, +12.5] | 1.0000 |

> 表中 `—` 表示该对比在任务间没有变异（任务等权 SE 为 0），无法给出 p 值；此时以整群自助区间为准。

- **Model × Architecture 交互**（预估量: `task_equal_weighted_interaction`）: +0.1562, p=0.0425, 95% CI [+0.0417, +0.2708] （任务等权口径，n_tasks=16）
  - 交叉验证（观测加权 OLS，任务聚类稳健）: 系数 +0.1562，SE 0.0705，p_t15=0.0425；修复三明治乘法顺序后与主口径一致，不再构成相反结论。
  - 未分层任务自助（敏感性）: [+0.0208, +0.2917]

任务地板/天花板（控制臂 n=24/任务）：地板 [21, 25, 66, 102, 258, 308]，天花板 [118, 274]；有区分度 8/16。这些任务保留在预注册分析中，仅标注其无区分度。

> 退化量定义为 control 成功率减 fault 成功率（百分点 = ×100）。
> 区间按任务整群、按四个预注册类别分层自助；重复 trial 不是独立样本。
> 逐格成功率与 Δ 使用同一批配对（故障真正注入者）；未注入配对的按分配结果在 *_itt 字段单列，两者不得混用同一个 n。
> 故障级与逐格的 p 值用任务等权 t(df=n_tasks-1)，与所附自助区间同口径；交互项主口径为任务等权，观测加权 OLS 见 sensitivity_observation_weighted。
> floor_ceiling 如实列出控制臂 0/n 与 n/n 的任务：它们对退化没有区分度，保留在预注册分析中，不做事后剔除。
> Holm 家族固定为三个故障级主对比；逐 (model, architecture, fault) 格子为描述性。
> 矩阵不健康（缺格/未配对/评分报错）时不输出 p 值，只给描述性区间；不显著只能报告为功效不足，不能当作等价性证据。
> V4 Pro 仅用于共同 8 任务次级分析，不进入主估计，也不报显著性。
