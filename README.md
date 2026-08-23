# WebArena ReAct Agent

基于 **LangGraph** 构建的 WebArena 浏览器自动化智能体，支持模拟模式和真实浏览器模式，并内置故障注入与 A/B 对照实验框架。

## 功能特性

- **标准 ReAct Agent**：Thought（推理）→ Action（工具调用）→ Observation（页面反馈）循环，LangGraph 编排
- **可切换 Agent 架构**：`react` 标准单循环基线；`plan_execute` 使用 Planner → Executor → Replanner 分阶段循环
- **可归因轨迹**：每步写入 `action_history` 并持久化为 JSONL trace（`experiments/traces/{thread_id}.jsonl`），支持鲁棒性归因分析
- **双模式运行**：模拟模式（内存 PageState，无需浏览器）/ 浏览器模式（Playwright 真实 Chromium）
- **9 个 WebArena 标准工具**：click / type_text / scroll / goto / go_back / go_forward / stop / select_option / hover
- **AX Tree 观测**：解析 Playwright `aria_snapshot()`，生成 `[id=xxx]` 可访问性树供 LLM 使用
- **故障注入**：Web 层（超时、500/503、DOM 丢失、弹窗）+ GitLab 层（CI 离线、403、合并冲突、配额）+ Agent 层（状态误判、参数错误），seed 可复现
- **A/B 对照实验**：BenchmarkRunner 对比 control vs fault 的成功率、步数、耗时；未接入正式 evaluator 时，成功率表示 Agent 正常完成率
- **灵活 LLM 配置**：支持任何 OpenAI-compatible API（OpenAI / DeepSeek / Ollama / 阿里百炼等）

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt

# 浏览器模式需要安装 Chromium
python -m playwright install chromium
python -m playwright install-deps chromium   # Linux 系统依赖
```

### 2. 配置 API Key

```bash
cp .env.example .env
```

编辑 `.env`：

```ini
OPENAI_API_KEY=sk-your-api-key-here
OPENAI_BASE_URL=https://api.deepseek.com
MODEL_NAME=deepseek-v4-pro
TEMPERATURE=0.7
```

> 注意：密钥只从 `.env` / 环境变量读取，不要硬编码到代码中。

### 3. 运行

```bash
# 查看可用站点
python main.py --list-sites

# 模拟模式（快速测试，无需浏览器）
python main.py --task "What is the price of the MacBook Pro?" --page scenario_example.json

# 浏览器模式（真实 Chromium 打开本地测试站点）
python main.py --browser --url file:///root/langgraph_agent_fault_attribution_webarena/test_site/index.html \
  --task "What is the price of iPhone 15 Pro?"

# 浏览器模式（WebArena 站点）
python main.py --browser --site shopping --task "Find the price of MacBook Pro"

# 选择计划-执行架构（后续架构对比用）
python main.py --browser --site shopping \
  --architecture plan_execute \
  --task "Find the price of MacBook Pro"

# 交互模式
python main.py
```

## 交互模式命令

```
/exit       退出程序
/reset      重置会话和页面状态
/help       显示帮助
/sites      列出所有 WebArena 站点
/task       设置任务: /task 你的任务描述
/pagefile   加载页面场景: /pagefile scenario.json
/page       查看当前页面状态
go          开始执行任务
```

## 项目结构

```
项目根目录/
├── main.py                 # CLI 入口（模拟/浏览器/交互/Benchmark）
├── standard_agent/         # 标准 Agent（被测对象，不依赖故障注入）
│   ├── config.py            # 配置加载（.env）
│   ├── core/                # AgentState、图、节点、trace
│   │   ├── state.py         # AgentState + reducer
│   │   ├── graph.py         # StateGraph 构建（ReAct 或计划-执行）
│   │   ├── nodes.py         # ReAct、Planner、Executor、Replanner 节点
│   │   └── trace.py         # JSONL trace 记录（鲁棒性归因数据）
│   ├── environment/         # 浏览器环境与 WebArena 配置
│   │   ├── browser.py       # Playwright 封装 + aria_snapshot 解析
│   │   └── webarena_config.py
│   ├── tools/web_tools.py   # 9 个 WebArena 标准工具
│   └── llm/provider.py      # ChatOpenAI 工厂
├── experiments/            # 实验结果和运行产物（自动生成）
│   ├── results/             # baseline JSON 结果
│   ├── traces/              # JSONL 执行轨迹
│   └── official_outputs/    # WebArena-Verified response/HAR/eval 文件
├── fault_injection/        # 独立故障注入与 A/B benchmark 层
│   ├── config.py            # FaultConfig + SeededRandom
│   ├── proxy.py             # FaultProxy 透明环境代理
│   ├── injector.py          # FaultInjector 中间件
│   ├── web_faults.py        # Web 底层故障
│   ├── gitlab_faults.py     # GitLab 业务故障
│   ├── agent_faults.py      # Agent 层故障
│   └── benchmark.py         # A/B 对照实验
├── test_site/              # 本地测试站点
├── tests/                  # 最小 smoke tests
└── legacy/travel_assistant/ # 旧旅游助手代码（已归档，不再使用）
```

## LangGraph 图拓扑

```
__start__ → agent (LLM 推理 + 工具调用决策)
              ├─ 有 tool_call → tools (执行工具) → 回到 agent
              └─ 无 tool_call → END
```

- 单节点 ReAct 自循环，`tools_condition` 内置路由
- `MemorySaver` checkpointer 支持多轮会话
- 达到 `max_steps` 强制结束
- `max_steps` 只限制 executor 的 LLM 调用次数；`plan_execute` 的 planner/replanner 调用不占 executor 步数，但会计入 `llm_calls`

## Agent 架构对比

当前保留两个可复现实验架构，二者共用 WebArena 环境、9 个工具、模型 profile、任务评估和 trace 格式：

```text
react:
__start__ → agent → tools → agent → ... → END

plan_execute:
__start__ → planner → executor → tools → replanner
                         ↑                 │
                         └──── planner ◄──┘（需要重规划时）
```

`react` 是标准单 Agent ReAct 基线，适合第一阶段 baseline 和故障分类实验。`plan_execute` 将规划、执行和重规划拆成独立 LangGraph 节点：Planner 生成结构化子目标，Executor 每轮只处理当前子目标，Replanner 根据工具结果选择推进、结束或重新生成计划。它仍然是单模型的多阶段工作流，不是多模型多 Agent 系统；`max_steps` 只限制 executor 步数，planner/replanner 的额外调用通过 `llm_calls`、`planning_calls` 和 `replanning_calls` 单独统计。后续架构实验应保持任务、模型、工具、max steps 和 evaluator 一致，只切换 `--architecture`。

```bash
python3 run_baseline.py --architecture react
python3 run_baseline.py --architecture plan_execute
```

## 故障注入

```bash
# 单次运行（浏览器模式 + 低强度 Web 超时故障）
python main.py --browser --site shopping \
  --task "Find the price of MacBook Pro" \
  --fault-intensity low --fault-web-timeout

# A/B 对照实验
python main.py --browser --site shopping \
  --task "Find the price of MacBook Pro" \
  --benchmark --trials 3 --fault-intensity medium --fault-web-timeout --fault-web-http-error

# 带答案匹配的 Benchmark：只有正常完成且答案包含期望值才算成功
python main.py --browser --site shopping \
  --task "Find the price of MacBook Pro" \
  --benchmark --trials 3 --fault-intensity medium \
  --fault-web-timeout --expected-answer '$1,299.00' \
  --expected-answer '1299'
```

`--expected-answer` 可以重复传入多个可接受答案，采用不区分大小写的子串匹配。Baseline 和故障 Benchmark 共用相同的完成与答案匹配逻辑；Baseline 从数据集读取 expected，故障 Benchmark 从 CLI 参数读取 expected。复杂任务或正式实验应接入 WebArena-Verified 的确定性 evaluator。

故障类型：

| 类别 | 故障 | 说明 |
|------|------|------|
| Web | `web_timeout` | 动作前注入 1-15s 随机延迟 |
| Web | `web_http_error` | 返回 500/503 假错误页 |
| Web | `web_dom_missing` | aria_snapshot 随机删除元素 |
| Web | `web_popup_block` | 插入 Cookie/Newsletter/弹窗 |
| GitLab | `gitlab_ci_offline` | CI Runner 离线 |
| GitLab | `gitlab_permission` | 403 权限不足 |
| GitLab | `gitlab_conflict` | 合并冲突 |
| GitLab | `gitlab_quota` | 配额耗尽 |
| Agent | `agent_state_misjudge` | 篡改观测元素名诱导误判 |
| Agent | `agent_param_error` | 篡改工具调用参数 |

## 测试

```bash
python -m unittest discover -s tests -v
```

最小 smoke tests 不依赖外部网络和 LLM API，覆盖：标准 Agent 图构建、工具列表、aria 解析、故障模块导入、reducer、配置安全。

## 当前实验数据

仓库只提交经过筛选的实验汇总，不提交完整 trace、HAR 或认证状态文件。当前可复核的 control 数据位于：

```text
experiments/curated/gpt54_react_public16_control_v1.json
experiments/curated/gpt54_react_public16_control_v1_summary.csv
experiments/curated/gpt54_react_public16_control_v1_manifest.json
```

该数据集包含 16 个 WebArena-Verified 任务、每个任务 3 次试验，共 48 条 `gpt54 + react + control` 记录。48 条记录均有明确的 evaluator success/failure 判定，官方成功 25 条。由于其中 28 条使用了项目的兼容 fallback，manifest 中单独记录了 fallback 覆盖率；这些数据适合用于 control 任务筛选和后续单故障实验的协议验证，不应与未统一批次的历史结果混合计算。

结果 JSON 中保留 `fallback_used`、`fallback_events`、`evaluator_status` 和 `experiment_id` 等审计字段。正式统计时，`evaluator_status=error` 的记录不进入成功率分母；`fallback` 记录应单独报告。

## 标准 Agent 观测与模型配置

当前 Agent 使用 LangGraph 编排的单 Agent ReAct 循环：

```text
任务 + AX Tree → LLM → 一个 tool call → 工具执行 → 新 AX Tree → LLM → ...
                                              └→ stop(answer) / final answer
```

每次运行的工具轨迹保存在 `experiments/traces/{thread_id}.jsonl`，包括任务、URL、步骤、动作参数、观察结果、错误和最终答案。为了支持模型级归因，可在 `.env` 开启：

```ini
TRACE_LLM_IO=1       # 记录每轮模型请求/响应元数据
TRACE_PROMPTS=1      # 额外记录完整 prompt 和 AX Tree，文件可能很大
TRACE_CONSOLE=1      # 在终端实时显示 QUESTION/THOUGHT/ACTION/OBSERVATION
```

不同 OpenAI-compatible 模型统一在项目根目录 `.env` 修改。每个模型使用独立 profile，不能重复写 `OPENAI_API_KEY` 等同名变量：

```ini
OPENAI_API_KEY=your-key
OPENAI_BASE_URL=https://api.openai.com/v1
MODEL_NAME=gpt-4o
TEMPERATURE=0.2
```

论文多模型实验使用以下格式：

```ini
MODEL_PROFILES=deepseek,gpt54,gemini,grok,glm52
MODEL_PROFILE=glm52
MODEL_GLM52_API_KEY=your-key
MODEL_GLM52_BASE_URL=https://hapiopen.cc/v1
MODEL_GLM52_NAME=glm-5.2
MODEL_GLM52_TEMPERATURE=0.2
```

运行 `python3 main.py --list-models` 查看已配置 profile。单次运行用 `--model-profile glm52`；baseline 全部运行用 `python3 run_baseline.py --all-models`，结果会分别写入 `experiments/results/baseline_results_deepseek.json` 等文件。

代码入口是 `standard_agent/llm/provider.py:create_llm`，配置读取在 `standard_agent/config.py`。因此切换 OpenAI、DeepSeek、Qwen、智谱或本地 Ollama，通常只需更换这四个环境变量，不要修改 Agent 核心逻辑。API key 不会写入 trace。

## WebArena 环境部署

WebArena 站点运行在 Docker 容器中（`shopping` :7770、`shopping_admin` :7780、`reddit` :9999、`gitlab` :8023、`wikipedia` :8888、`map` :3000）。

推荐使用 [WebArena-Verified](https://github.com/ServiceNow/webarena-verified) 的优化镜像（比官方镜像小 85-92%）：

```bash
docker compose -f /path/to/webarena-verified/docker-compose.yml up -d shopping shopping_admin reddit gitlab
```

或单独运行：

```bash
docker run -d --name webarena-verified-shopping -p 7770:80 -p 7771:8877 am1n3e/webarena-verified-shopping
docker run -d --name webarena-verified-shopping_admin -p 7780:80 -p 7781:8877 am1n3e/webarena-verified-shopping_admin
docker run -d --name webarena-verified-reddit -p 9999:80 -p 9998:8877 am1n3e/webarena-verified-reddit
docker run -d --name webarena-verified-gitlab -p 8023:8023 -p 8024:8877 am1n3e/webarena-verified-gitlab
```

> 本机 Docker 已安装并配置镜像加速器；镜像拉取成功后即可使用 `--site` 参数运行真实 WebArena 任务。

## 技术栈

- **LangGraph** - 状态图编排
- **LangChain / LangChain-OpenAI** - LLM 调用和消息管理
- **Playwright** - 浏览器自动化
- **python-dotenv** - 环境变量管理
- **Docker** - WebArena 站点容器
