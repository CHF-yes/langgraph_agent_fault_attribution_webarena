# webarena_iclr —— 投稿产出

大模型 Web Agent 鲁棒性归因与架构优化研究（ICLR 2027 投稿）的**交付副本**。

> 本目录是产物快照，**不是工作副本**。实验与出表管道仍在原仓库
> `~/langgraph_agent_fault_attribution_webarena` 下运行；
> `scripts/paper_tables.py` 会写回 `paper/iclr2027/tables/`，
> 因此不要把本目录当作源目录编辑——改动请回到原仓库，再重新复制。

## 目录

| 路径 | 内容 |
|---|---|
| `paper/iclr2027/` | **英文投稿（ICLR 2027）**：`main.tex` + `sections/00–12` + `tables/T1–T8` + `figures/tex/` + `main.pdf` |
| `paper/zh/正文.md` | **中文稿（可复用版）**：已清理硬伤，数字与英文稿同源 |
| `paper/zh/清理说明.md` | 中文稿**硬伤清单**（源文件+行号+改法）与**数字↔宏名对照表** |
| `docs/experiment_roadmap.md` | **当前唯一执行入口**：Stage A 改为对已有 HAR 离线重评，模型按分类因子分析 |
| `docs/supplementary_experiment_plan.md` | **历史补实验方案**：保留原始阶段设计；执行前必须以 roadmap 的 2026-09-18 更正为准 |
| `docs/model_pilot_plan.md` | **模型来源核验与基线 pilot**：换模型前的检查清单与决策规则 |
| `docs/model_budget_policy.md` | **三 backbone 预算执行方针**：Pro/Flash/OpenAI 的档位、削减顺序、价格触发器 |
| `scripts/probe_model_provenance.py` | **端点来源探针**：查端点实际服务的模型 id、指纹、长上下文截断 |
| `tests/test_stats_core.py` | 统计自检（22 项断言，纯标准库） |
| `tests/test_provenance.py` | 来源提取器自检（12 项断言，纯标准库） |
| `standard_agent/{llm/provider.py, core/nodes.py}` | 被改动过的运行时文件，供对照（每次 LLM 调用写 `llm_provenance` 事件） |

## 当前状态

- 英文稿：**15 页**，正文 **9 页**（`REPRODUCIBILITY STATEMENT` 起于第 9 页），
  0 error / 0 overfull hbox / 0 undefined ref / 0 身份泄漏。
  `\iclrfinalcopy` 保持注释（匿名送审）。
- 统计自检：22 passed。
- 中文稿：T-VAE 段、身份与基金行、`[待填]`、越界声称均已清除。

## 重新编译英文稿

```bash
cd paper/iclr2027
pdflatex main && bibtex main && pdflatex main && pdflatex main
```

无 `pdftotext` / `pdfinfo` 时，用 ghostscript 抽文本与渲页：

```bash
gs -q -sDEVICE=txtwrite -o - main.pdf
gs -q -sDEVICE=png16m -dFirstPage=9 -dLastPage=9 -r70 -o page9.png main.pdf
```

## 重新生成表格与图（需回到原仓库）

```bash
cd ~/langgraph_agent_fault_attribution_webarena
python3 scripts/paper_tables.py        # 重算 tables/ + macros.tex + figures/tex/
python3 -m pytest tests/test_stats_core.py
```

正文中**没有任何数字是手工誊写的**：全部来自 `tables/macros.tex` 的宏，
图由 `scripts/paper/figures.py` 从同一份统计量生成，因此表、图、正文不会互相漂移。

## 提交前待办

1. `paper/zh/清理说明.md` 中标注「（出版信息待核）」的参考文献条目 [3]–[9] —— 需补齐作者、会议、年份。
2. 确认 `\iclrfinalcopy` 仍为注释状态（匿名送审）。
3. 若更换基线模型，需重跑实验并重新生成全部表图（见 `docs/supplementary_experiment_plan.md`）。
