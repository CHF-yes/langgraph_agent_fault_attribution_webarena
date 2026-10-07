# Stage C 网格离线审计

- 配对总数 384；触发 367；未触发 17 （{'web_http_error': 3, 'agent_param_error': 12, 'web_dom_missing': 2}）

## 按分配（ITT，含未触发）
| model | arch | fault | n | control | fault | Δ | CI(boot) | CI(t15) |
|---|---|---|---|---|---|---|---|---|
| deepseek_v41_flash | plan_execute | agent_param_error | 32 | 0.188 | 0.250 | -0.062 | [-0.156,+0.031] | [-0.196,+0.071] |
| deepseek_v41_flash | plan_execute | web_dom_missing | 32 | 0.281 | 0.375 | -0.094 | [-0.219,+0.031] | [-0.294,+0.106] |
| deepseek_v41_flash | plan_execute | web_http_error | 32 | 0.250 | 0.406 | -0.156 | [-0.281,-0.031] | [-0.317,+0.004] |
| deepseek_v41_flash | react | agent_param_error | 32 | 0.469 | 0.469 | +0.000 | [-0.062,+0.062] | [-0.097,+0.097] |
| deepseek_v41_flash | react | web_dom_missing | 32 | 0.438 | 0.469 | -0.031 | [-0.094,+0.000] | [-0.098,+0.035] |
| deepseek_v41_flash | react | web_http_error | 32 | 0.562 | 0.469 | +0.094 | [-0.031,+0.219] | [-0.081,+0.268] |
| qwen38_flash | plan_execute | agent_param_error | 32 | 0.375 | 0.375 | +0.000 | [+0.000,+0.000] | [+0.000,+0.000] |
| qwen38_flash | plan_execute | web_dom_missing | 32 | 0.375 | 0.375 | +0.000 | [-0.125,+0.125] | [-0.138,+0.138] |
| qwen38_flash | plan_execute | web_http_error | 32 | 0.438 | 0.312 | +0.125 | [+0.000,+0.250] | [-0.029,+0.279] |
| qwen38_flash | react | agent_param_error | 32 | 0.500 | 0.406 | +0.094 | [+0.000,+0.188] | [-0.051,+0.239] |
| qwen38_flash | react | web_dom_missing | 32 | 0.344 | 0.406 | -0.062 | [-0.188,+0.062] | [-0.196,+0.071] |
| qwen38_flash | react | web_http_error | 32 | 0.344 | 0.344 | +0.000 | [-0.125,+0.125] | [-0.168,+0.168] |

## 触发后（per-protocol）
| model | arch | fault | n | control | fault | Δ | CI(boot) | CI(t15) |
|---|---|---|---|---|---|---|---|---|
| deepseek_v41_flash | plan_execute | agent_param_error | 30 | 0.200 | 0.267 | -0.062 | [-0.156,+0.031] | [-0.196,+0.071] |
| deepseek_v41_flash | plan_execute | web_dom_missing | 32 | 0.281 | 0.375 | -0.094 | [-0.219,+0.031] | [-0.294,+0.106] |
| deepseek_v41_flash | plan_execute | web_http_error | 32 | 0.250 | 0.406 | -0.156 | [-0.281,-0.031] | [-0.317,+0.004] |
| deepseek_v41_flash | react | agent_param_error | 28 | 0.500 | 0.500 | +0.000 | [-0.067,+0.067] | [-0.104,+0.104] |
| deepseek_v41_flash | react | web_dom_missing | 32 | 0.438 | 0.469 | -0.031 | [-0.094,+0.000] | [-0.098,+0.035] |
| deepseek_v41_flash | react | web_http_error | 32 | 0.562 | 0.469 | +0.094 | [-0.031,+0.219] | [-0.081,+0.268] |
| qwen38_flash | plan_execute | agent_param_error | 31 | 0.387 | 0.387 | +0.000 | [+0.000,+0.000] | [+0.000,+0.000] |
| qwen38_flash | plan_execute | web_dom_missing | 30 | 0.400 | 0.400 | +0.000 | [-0.125,+0.125] | [-0.138,+0.138] |
| qwen38_flash | plan_execute | web_http_error | 31 | 0.419 | 0.323 | +0.125 | [+0.000,+0.250] | [-0.029,+0.279] |
| qwen38_flash | react | agent_param_error | 27 | 0.556 | 0.481 | +0.125 | [+0.000,+0.250] | [-0.057,+0.307] |
| qwen38_flash | react | web_dom_missing | 32 | 0.344 | 0.406 | -0.062 | [-0.188,+0.062] | [-0.196,+0.071] |
| qwen38_flash | react | web_http_error | 30 | 0.367 | 0.367 | +0.000 | [-0.125,+0.125] | [-0.168,+0.168] |

## 任务地板/天花板（控制臂，n=24/任务）
| task | control 成功/总数 | 有区分度 |
|---|---|---|
| 21 | 0/24 | 否 |
| 22 | 8/24 | 是 |
| 24 | 19/24 | 是 |
| 25 | 0/24 | 否 |
| 27 | 17/24 | 是 |
| 28 | 18/24 | 是 |
| 30 | 18/24 | 是 |
| 66 | 0/24 | 否 |
| 102 | 0/24 | 否 |
| 118 | 24/24 | 否 |
| 132 | 8/24 | 是 |
| 133 | 5/24 | 是 |
| 134 | 5/24 | 是 |
| 258 | 0/24 | 否 |
| 274 | 24/24 | 否 |
| 308 | 0/24 | 否 |

## 交互项（统一口径）
- OLS 系数（观测加权）: +0.1562；聚类稳健 SE 0.1743，p(normal)=0.3701，p(t15)=0.3842
- 任务等权系数: +0.1562；SE 0.0705；CI(boot) [+0.021,+0.292]；CI(t15) [+0.006,+0.306]

> 两个估计量设计平衡下点估计一致，但方差口径不同；主报告须固定其一。

## git_dirty：86 条标脏 / 768 条记录（均落在修复提交窗口内）
