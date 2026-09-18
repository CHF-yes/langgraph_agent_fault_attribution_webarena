# 模型来源核验与基线 pilot

## 为什么先做这一步

有三件事在花大算力之前必须先确认，而且都很便宜：

1. **端点到底在服务哪个模型。** 你现在怀疑原来的 GPT 端点掺水——这个怀疑**无法回溯验证**：冻结的 850 条产物里只有 `"model_profile": "gpt54"`，没有 served model、没有 base_url、没有 `system_fingerprint`，仓库里也没有 `.env`。所以当时那个端点指向哪里，本地没有任何证据。能做的是让**下一批**可核验。
2. **DeepSeek 一方的模型 id 本身就在变。** `deepseek-chat` / `deepseek-reasoner` 已于 2026-07-24 停服（调用直接报错，不重定向）；`deepseek-v4-pro` 在 2026-09-10 到 09-11 之间被公告下线、推迟、又改回继续提供（"如有变动将另行通知"）。**供应商会在不通知的情况下改动 id 的指向**，所以"请求了什么"和"服务了什么"必须分别记录。
3. **Agent 的协议约束在新模型上是否成立。** ReAct 节点要求回复以 `THOUGHT:` 开头并输出结构化 tool_call；planner / replanner 要求**只输出 JSON**。这些是**代码里的硬约束**（见 `standard_agent/core/nodes.py` 的 `protocol_violation` 分支与 `_parse_plan_steps`）。换模型后如果模型不遵守，Agent 会因为与被注入故障**无关**的原因失败。pilot 能在便宜的时候发现这件事。

---

## 第 0 步：模型来源探针（约 5 分钟，不消耗 trial）

`scripts/probe_model_provenance.py` 独立于 langgraph / langchain / 浏览器运行，只发几次 API 请求：

- 读端点回显的 `model` 与 `system_fingerprint`，与我们**请求**的 id 比对；
- 跑 5 个标准答案固定的探针（乘除法、指令遵循、多步推理、JSON 形状、长上下文里的 needle）——**它们是烟雾报警器，不是 benchmark**：通过不代表端点诚实，只是让某些失真变得可见；
- 长上下文探针把一个 needle 埋在约 59k 字符的伪 AX Tree 里，这正是真实 WebArena 观测的形状，用来抓**静默截断**。

```bash
cd ~/langgraph_agent_fault_attribution_webarena

# 先配好 .env（密钥只走 .env，不要写进代码或命令行）
python3 scripts/probe_model_provenance.py --repeat 3 --json experiments/probe_<date>.json
```

`--repeat 3` 是为了发现**按请求负载均衡**造成的路由漂移——单次调用正常不代表每次都正常。

| 探针结果 | 含义 | 怎么办 |
|---|---|---|
| `served=` 为空 | 端点不回显模型 id | **无法核验**，不要在此端点上跑正式矩阵 |
| `SUBSTITUTION` | 请求的模型与服务的不一致（且不是 `-0813` 这类日期后缀变体） | 端点有问题，先解决 |
| `served model id CHANGED between calls` | 同一批次内被路由到不同后端 | 结果不可归因到模型，先解决 |
| `long_context: FAIL` | 静默截断 | 矩阵不要跑：AX Tree 就是长 prompt |
| 探针项 FAIL | 端点能力异常偏弱 | 换端点，或至少记录在案 |

> 探针退出码非 0 表示有发现，可以直接串在批量脚本前面当闸门：
> `python3 scripts/probe_model_provenance.py && python3 scripts/run_fault_matrix.py ...`

### 运行时的来源记录（已加）

除了这个离线探针，Agent 运行时现在**每次 LLM 调用都会写一条 `llm_provenance` 事件**（`standard_agent/core/nodes.py` 的 `_trace_provenance`），内容来自 `extract_served_metadata`（`standard_agent/llm/provider.py`）：

```
served_model / system_fingerprint / served_model_differs
prompt_tokens / completion_tokens / total_tokens / latency_s
+ model_profile / provider_base_url / model（= 请求值）
```

它**无条件发射**，不像 `TRACE_LLM_IO` 那样默认关闭——只在需要时才记录的信息，在需要的时候是拿不到的。记录本身是诊断用途，任何异常都会被吞掉，不会让 trial 失败。

于是事后可以直接查：

```bash
# 这一批里出现过几个不同的 served model？
grep -ho '"served_model": "[^"]*"' experiments/<run>/**/*.jsonl | sort | uniq -c
```

`served_model_differs` 是**提示不是判决**：供应商合法地会返回带日期后缀的构建（如 `deepseek-v4-pro-0813` 对应 `deepseek-v4-pro`），这也会置位。

---

## 第 1 步：控制臂 pilot（每个模型约 1 小时）

**只跑控制臂（不注入故障）**，用 `run_baseline.py`。目的不是产出论文数字，而是回答三个问题：端点是否健康、每个模型的控制成功率是多少、模型间差距是否大到值得投入 Stage C。

```bash
python3 run_baseline.py \
  --task-ids 22 24 27 28 30 132 133 134 \
  --model-profile deepseek-flash \
  --architecture react \
  --max-steps 20 \
  --trials 3 --run-seed 42 \
  --official-eval \
  --storage-state-dir <storage-state 目录> \
  --webarena-output-dir experiments/pilot_<date>/har_deepseek-flash \
  --output experiments/pilot_<date>/baseline_deepseek-flash.json
```

换 `--model-profile deepseek-v4-pro` 再跑一次。**两个 profile 必须在同一会话内、同一环境下跑**：五个按故障划分的对照臂本是同一配置，平均墙钟却相差 2.17 倍（见英文稿 §6.4），机器级漂移足以伪装成模型差异。

要点：

- `--max-steps 20`：`run_baseline.py` 默认是 **10**，与正式矩阵的 20 不一致，**必须显式覆盖**，否则两边的预算口径对不上。
- `--trials 3`：与论文基线的种子数一致（`BaselineSeeds = 3`）。8 任务 × 3 = **每模型 24 次**。
- `--official-eval` + `--webarena-output-dir`：保留 HAR、走官方原生评估。这一条很关键——英文稿的主结论之一就是**评估器回退路径造成 32.9 pp 的基线差异**，pilot 必须走原生路径才可比。
- 该脚本**没有 `--workers`**，是串行执行：24 次 × 约 160 s ≈ **1 小时/模型**。两个模型约 2 小时。
- 结束后把第 0 步的 `probe_*.json` 与 pilot 结果**放在同一个目录**，作为这一批的来源证据。

---

## 第 2 步：决策规则

> **2026-09-18 口径更正：** DeepSeek 官方已将 V4.1 Flash 描述为在基准、速度、费用和
> 总用时上超过 V4 Pro。因此 pilot 不再用来证成 `4o-mini < Flash < Pro` 的能力梯度。
> 三个 profile 首先是三个**分类模型水平**；pilot 负责确认端点身份、协议健康、
> 地板/天花板与实测成本，不事后生成序数检验的排名。

拿到 pilot 结果后按这张表决定 Stage C 怎么配：

| Pilot 观察 | 解读 | 下一步 |
|---|---|---|
| 控制成功率 ≈ 0 | 模型太弱，或 harness 在新模型下坏了（先看 `protocol_violation` 计数） | 别做 M×A：地板效应会让"模型无差异"成为必然。换更强的模型对，或先修 harness |
| 两模型控制成功率都很高、差距很小 | 同家族模型高度相关（同语料、同分词器） | 模型主效应很可能在 n≈30 下测不出来——这与论文现有结论一致（零结果是**设计**的性质），但如果你要的是**可检测**的模型效应，就得改用过程层指标或把配对数提到约 155 |
| 一个模型显著更慢 | 步数预算 ≠ 时间预算（正是 ReAct vs Plan-and-Execute 的 4.7 倍问题） | 必须同时记录 `llm_calls` / `executor_calls` / `planning_calls` / `replanning_calls` 与 token 数，否则"性能差异"可能是"预算差异" |
| `protocol_violation` 频繁 | 新模型不遵守 `THOUGHT:` / JSON 约束 | 先修 prompt 或 harness，否则注入的故障效应被协议失败淹没 |
| 探针发现替换或路由漂移 | 端点不可信 | 停。这一批的任何数字都不能用 |

**关于"两个 DeepSeek 模型对比"这个设计本身**：同厂商对比避免了跨厂商 confound（服务栈、限流、分词器、认证），这是优点；代价是两模型同源、相关性高，**模型主效应会比跨厂商对比更小**。这不是缺陷，但要事先知道，否则容易得到一个"模型无差异"的零结果却误以为是发现——这正是英文稿 §6.3 讲的 MDE 陷阱。

---

## 附：`.env` 模板

仓库里没有 `.env`（只有 `.env.example`），需要新建。**密钥只从 `.env` 读，绝不进代码或仓库**：

```bash
MODEL_PROFILES=deepseek-flash,deepseek-v4-pro

MODEL_DEEPSEEK_FLASH_API_KEY=...
MODEL_DEEPSEEK_FLASH_BASE_URL=https://api.deepseek.com
MODEL_DEEPSEEK_FLASH_NAME=deepseek-flash
MODEL_DEEPSEEK_FLASH_TEMPERATURE=0.2

MODEL_DEEPSEEK_V4_PRO_API_KEY=...
MODEL_DEEPSEEK_V4_PRO_BASE_URL=https://api.deepseek.com
MODEL_DEEPSEEK_V4_PRO_NAME=deepseek-v4-pro
MODEL_DEEPSEEK_V4_PRO_TEMPERATURE=0.2

TRACE_LLM_IO=0
TRACE_PROMPTS=0
```

profile 名会被转成大写、`-` 转 `_` 后拼成环境变量名（`standard_agent/config.py:58`），所以 `deepseek-v4-pro` → `MODEL_DEEPSEEK_V4_PRO_*`。

**注意两处已失效的默认值**（换模型时应一并处理）：

- `standard_agent/config.py:35` 的默认 `MODEL_NAME = "deepseek-v4-pro"`——在该 id 的去留反复之后，不要再依赖这个默认值，务必在 `.env` 里显式指定。
- `.env.example:10` 用的是 `deepseek-chat`——**已停服，调用会报错**。示例文件需要更新。
