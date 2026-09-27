# 端点空闲延迟实测（2026-09-27）

对应探针：`scripts/probe_endpoint_idle_latency.py`（只发最小补全，**不启动 agent trial**）。
证据：`experiments/endpoint_probe/*.json`。这些数字用于 T0 的排程与预算，不改变实验有效性。

## 1. 闲置后首次调用 vs 连续调用

| 端点 | 稳态（连续） | 闲置后首次 | 判定 |
|---|---|---|---|
| `qwen38_flash`（maas.qianwenaiaipu.com） | 0.9 / 1.2 / 0.9 s（中位 0.94s） | **270.5s**（闲置 300s 后） | 强驱逐 |
| 同上，闲置 **15s** 后 | — | **269.3s** | 阈值远小于 15s |
| `deepseek_v41_flash`（api.deepseek.com） | 0.3–0.9s | **0.5s**（闲置 300s 后） | **不驱逐** |

结论：冷启动是 **Qwen 网关独有**；DeepSeek 闲置 5 分钟也不受影响。

## 2. 固定间隔 ping（120s）无效

```
keepalive_ping        270.6s  slow=True     ← ping 自己先吃冷启动
keepalive_ping        135.2s  slow=True
during_keepalive_real 270.9s  slow=True     ← 真实调用仍慢
after_ping_stopped_idle 270.9s slow=True    ← 负对照：停 ping 后依旧慢
```

120s 间隔的 ping **不能**消除慢调用；结合第 1 项的 15s 阈值，这说明"保温"需要秒级间隔。

## 3. 持续并发确实能保温（决定性）

先做一次预热把首调 270s 与测量分离，然后 4 线程 × 2s 间隔 × 120s：

```
warmup            270.1s（冷，只付一次）
measured          110 次调用，慢调用 0 次
median 1.07s   max 2.08s   p95 1.52s
```

结论：**只要舰队级的调用间隔保持在秒级，端点就不会缩容**。反之，单个 trial 自身调用稀疏
（一个 trial 5–7 次、跨 300–600s），仅靠少量 worker 仍可能超过 15s 空档。

## 4. 对 T0 的直接含义

- Qwen 那一半矩阵的墙钟由冷启动主导：实测 T0 首片 Qwen 均值 **521.9s/trial**，DeepSeek **14.7s/trial**。
- 两种缓解，按优先级：
  1. **提高 worker 数**（推荐）：12–16 worker 时真实流量本身就能把舰队级间隔压到秒级；
     实测 4×2s 已能做到 0 慢调用，真实流量只会更密。
  2. **秒级多线程 pinger**：4 线程 × 2–5s 保持保温。成本约 3,300 次/小时 × ~20 tokens ≈
     66K tokens/小时（约 0.4M tokens/6 小时批次），可接受但会占用网关容量。
- `--fault-injection-step` 与评分链不受影响：慢调用仍然 `ok=True`，是**时间与费用**问题，
  不是数据有效性问题。

## 5. 探针自身的两次失败（已修正，记录在案）

- 首次用 `--concurrency` 时 worker 每轮只调用一次：`stop.wait()` 后的第二轮调用在
  窗口结束、`join(timeout)` 超时后被丢弃。已改为**先预热 + 显式 `join()`**，并在锁内写盘。
- 那两次（`concurrency_qwen_4x10.json`、`concurrency_qwen_4x5.json`）的结论无效，已删除，
  由 `concurrency_qwen_warm.json` 取代。
