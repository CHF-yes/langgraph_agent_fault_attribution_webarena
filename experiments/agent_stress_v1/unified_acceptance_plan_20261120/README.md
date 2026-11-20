# Unified-execution acceptance plan (36 trials)

Plan only. No trial has been run and `approved_for_paid_runs` is false.

- Grid: 3 tasks x 2 models x 2 architectures x 3 conditions x 1 seed = **36** cells in **12** blocks.
- Models: qwen38_flash, deepseek_v41_flash
- Architectures: react, plan_execute
- Tasks: 228, 28, 314
- Max execution turns: 20; fault window: 10 proxy steps.
- Each block is one task/model/architecture with clean, single and sustained in a pre-randomised order.

## Blocks

| Block | Task | Model | Architecture | Condition order |
| ---: | ---: | --- | --- | --- |
| 1 | 228 | deepseek_v41_flash | react | single -> control -> sustained |
| 2 | 314 | deepseek_v41_flash | plan_execute | single -> control -> sustained |
| 3 | 228 | qwen38_flash | react | control -> single -> sustained |
| 4 | 228 | deepseek_v41_flash | plan_execute | single -> sustained -> control |
| 5 | 314 | deepseek_v41_flash | react | sustained -> single -> control |
| 6 | 228 | qwen38_flash | plan_execute | control -> sustained -> single |
| 7 | 28 | qwen38_flash | plan_execute | sustained -> single -> control |
| 8 | 28 | qwen38_flash | react | sustained -> control -> single |
| 9 | 28 | deepseek_v41_flash | react | sustained -> single -> control |
| 10 | 314 | qwen38_flash | react | single -> sustained -> control |
| 11 | 314 | qwen38_flash | plan_execute | control -> sustained -> single |
| 12 | 28 | deepseek_v41_flash | plan_execute | sustained -> control -> single |

## Shared execution contract

- One system prompt and one human message; history order is always System, Human, then prior AI/Tool messages.
- Same tool list, AX Tree rules, scroll/navigation guidance, examples, THOUGHT requirement, retrieval guidance and submission contract.
- Same one-tool-per-turn rule, multi-tool truncation, no-tool retry (one retry, then `no_tool_call`), THOUGHT check/record, stop corrections and 20-turn cap.
- Same URL/page/empty-value fallbacks and the same visible tool-error observations (no filtering by architecture).
- Planner and replanner never bind browser tools and never consume execution turns or the fault window; calls, tokens and time are recorded separately.

## Intentional differences

- ReAct sees a high-level plan context and no plan owner.
- The plan-and-execute executor sees only `Current sub-goal` and `Success condition`; the planner/replanner own plan progression.
- A replanner `finish` only requests executor wrap-up; completion still requires a valid `stop(answer)`.
- A rejected submission routes the executor straight back to the same atomic sub-goal; it never advances or replans.

## Preserved prior evidence

- 18-trial pilot: `experiments/agent_stress_v1/http_pilot_20261120/report.md`.
- Qwen clean screening: `experiments/agent_stress_v1/qwen_seed_verify_20261120/summary.json`.
- Prior artifacts keep their original code/version; this plan is the first unified comparison.

