# Candidate rebuild after the 240-trial Qwen clean screening

Date: 2026-11-17. Source run: `experiments/agent_stress_v1/screening_qwen_240_run2` (commit `fc3e329`).

This directory freezes the candidate analysis **before** any held-out fault result is inspected. The selection rule is the 2026-11-17 request: combined six clean trials per task, tiers 2/6 hard, 3-4/6 medium, 5-6/6 easy; 1/6 exploratory; 0/6 diagnosis-first. The old "all four combinations >= 2/3" gate is retired.

## Deliverables

- `candidate_table.md` / `candidate_table.csv`: all 40 tasks with site, template, operation, ReAct/P&E successes, combined tier and reviewed failure reason.
- `tiers.json`: machine-readable table plus success artefact per task.
- `selection.json`: frozen 20-task pool, template/site counts, gaps, easy backups, supplement list.
- `reference_checks.md`: targeted 146/304/388 evidence, native-scoring probes, success-path evidence and fault-target endpoints.
- `fault_targets.json`: non-static HTTP endpoints from each selected task's first success trace.
- `scripts/agent_stress_candidate_v2.py`: reproducible table/tier/selection builder.
- `supplement_screening/`: 48-cell Qwen probe for eight future candidates (running).
- `deepseek_core_controls/plan.json`: prepared 120-cell DeepSeek clean control for the 20-task core.

## Tier distribution (40 tasks)

- Easy 5-6/6: 11 - 27, 29, 48, 49, 148, 164, 165, 189, 190, 314, 315
- Medium 3-4/6: 10 - 28, 66, 163, 188, 227, 228, 294, 316, 784, 785
- Hard 2/6: 3 - 295, 305, 384
- Exploratory 1/6: 4 - 67, 293, 303, 786
- Diagnosis 0/6: 12 - 47, 68, 146, 147, 226, 304, 308, 309, 310, 385, 387, 388

## Frozen primary pool (20 tasks, 12 templates, <=2 per template)

| # | Task | Site | Template | Operation | Tier | 6-trial successes |
| ---: | ---: | --- | ---: | --- | --- | ---: |
| 1 | 28 | reddit | 33 | 评论统计 | medium | 4/6 |
| 2 | 66 | reddit | 17 | 帖子筛选/提取 | medium | 4/6 |
| 3 | 163 | shopping | 136 | 评论提取 | medium | 4/6 |
| 4 | 188 | shopping | 214 | 订单统计/订单信息 | medium | 4/6 |
| 5 | 227 | shopping | 370 | 商品聚合 | medium | 3/6 |
| 6 | 228 | shopping | 370 | 商品聚合 | medium | 3/6 |
| 7 | 294 | gitlab | 329 | 仓库信息检索 | medium | 3/6 |
| 8 | 316 | gitlab | 324 | 贡献者检索 | medium | 3/6 |
| 9 | 784 | gitlab | 316 | 贡献者检索 | medium | 3/6 |
| 10 | 785 | gitlab | 316 | 贡献者检索 | medium | 3/6 |
| 11 | 295 | gitlab | 329 | 仓库信息检索 | hard | 2/6 |
| 12 | 305 | gitlab | 321 | 代码提交统计 | hard | 2/6 |
| 13 | 384 | shopping | 666 | 评论提取 | hard | 2/6 |
| 14 | 27 | reddit | 33 | 评论统计 | easy | 6/6 |
| 15 | 164 | shopping | 136 | 评论提取 | easy | 6/6 |
| 16 | 314 | gitlab | 324 | 贡献者检索 | easy | 6/6 |
| 17 | 48 | shopping | 197 | 订单统计/订单信息 | easy | 6/6 |
| 18 | 49 | shopping | 197 | 订单统计/订单信息 | easy | 5/6 |
| 19 | 148 | shopping | 155 | 订单统计/订单信息 | easy | 5/6 |
| 20 | 190 | shopping | 214 | 订单统计/订单信息 | easy | 5/6 |

Counts: easy 7, medium 10, hard 3; sites {'reddit': 3, 'shopping': 10, 'gitlab': 7}; templates [17, 33, 136, 155, 197, 214, 316, 321, 324, 329, 370, 666].

## Gaps against the 24-task target

- Need 6 hard / 10 medium / 8 easy. Pool delivers 3 hard / 10 medium / 7 easy, i.e. **missing 3 hard and 1 easy = 4 tasks**. Template count is already sufficient (12).
- The template cap is binding: all 11 easy tasks plus all 10 medium tasks cannot coexist under <=2 per template, which is why the pool caps at 20.
- Easy backups: 165, 315, 29, 189. Medium has no backup (entire 3-4/6 tier is used). There is no hard backup in the pool.

## Supplement screening

Eight tasks are being screened with the same 6-trial Qwen protocol: 69, 141, 149, 205, 206, 306, 311, 168. They target the hard gap and weak templates (reddit post filter, shopping spend/review, GitLab commit/contributor, repo contribution). Selection after the probe keeps the same tier/template rules; only tasks landing at 2/6 (hard), 5-6/6 (easy) or 3-4/6 (medium replacement) are eligible.

## Four-combination clean controls

- Qwen clean controls already exist for the 20-task core (240 total; per-task counts in the table).
- DeepSeek clean controls for the same 20 tasks are prepared but not yet run: `deepseek_core_controls/plan.json`, 120 cells (20 x 2 architectures x 3 seeds). The four newly supplemented tasks need 24 additional DeepSeek + 24 additional Qwen cells if enrolled.
- The acceptance reference is an overall clean success rate near 50-70%, with zero success allowed for a single task/combination. A combination that fails almost everywhere across the group triggers an environment/execution/selection audit before any fault run.

## Reference checks and exclusions

- 146: correct 16/24 values, but the inch unit is only implicit (`Size16"x24"` / option `16*24`); the evaluator requires `16 inches`/`24 inches`, while the dataset raw annotation is `16x24`. Excluded at 0/6; unit-preserving contract required before re-screening.
- 304: truth is 14 (GitLab API: 14 Eric Bailey commits in window) and `[14]` scores 1.0; failures are object/prose submissions plus step caps. Excluded at 0/6 but a good contract-fix re-screen candidate.
- 388: both expected reviewers are visible on the page and the names score 1.0; `[]` correctly fails. The empty result is an agent answer-extraction/termination defect, **not** an evaluator false negative. Excluded at 0/6 pending a runtime fix and unified baseline.

## Status

- Done: 40-task table, tiers, 20-task frozen pool, gap analysis, targeted 146/304/388 evidence, success-path evidence and fault-target endpoints for all 20 pool tasks.
- Running: 48-cell Qwen supplement probe.
- Pending: supplement selection, 120-cell DeepSeek core controls, deterministic replay of all selected reference paths, alternative-path checks, then final 24-task freeze and fault matrix.
