## Stage C 主分析

- 验证模型作用域检查: 通过

> ⛔ **正式显著性推断已被拒绝**（矩阵不健康）：
> - 756 个期望格子没有产物
> - 756 组控制/故障臂未配平
> 以下数值仅为描述性，p 值已置空。
- 主模型: deepseek_v41_flash, qwen38_flash
- 主观测数: 12，配对数: 6

| 故障 | 配对 | Δ(pp) | 95% CI | p | p(Holm) |
|---|---|---|---|---|---|
| agent_param_error | 2 | +0.0 | [+0.0, +0.0] | — | — |
| web_dom_missing | 2 | +0.0 | [+0.0, +0.0] | — | — |
| web_http_error | 2 | +0.0 | [+0.0, +0.0] | — | — |

步数耗尽（描述性，不计入成功率）：总体 5/12 = 41.7%
  - 故障 `agent_param_error`: 2/4 = 50.0%
  - 故障 `web_dom_missing`: 2/4 = 50.0%
  - 故障 `web_http_error`: 1/4 = 25.0%

Holm 家族：per_fault_primary = ['agent_param_error', 'web_dom_missing', 'web_http_error']

逐 (model, architecture, fault) 描述性结果：

| model | arch | fault | n_pair | control | fault | Δ(pp) | 95% CI | p |
|---|---|---|---|---|---|---|---|---|
| deepseek_v41_flash | react | agent_param_error | 2 | 0.500 | 0.500 | +0.0 | [+0.0, +0.0] | — |
| deepseek_v41_flash | react | web_dom_missing | 2 | 0.500 | 0.500 | +0.0 | [+0.0, +0.0] | — |
| deepseek_v41_flash | react | web_http_error | 2 | 0.500 | 0.500 | +0.0 | [+0.0, +0.0] | — |

- 交互检验: 无法估计（interaction requires exactly two models and two architectures）

> 退化量定义为 control 成功率减 fault 成功率（百分点 = ×100）。
> 区间按任务整群、按四个预注册类别分层自助；重复 trial 不是独立样本。
> Holm 家族固定为三个故障级主对比；逐 (model, architecture, fault) 格子为描述性。
> 矩阵不健康（缺格/未配对/评分报错）时不输出 p 值，只给描述性区间；不显著只能报告为功效不足，不能当作等价性证据。
> V4 Pro 仅用于共同 8 任务次级分析，不进入主估计，也不报显著性。
