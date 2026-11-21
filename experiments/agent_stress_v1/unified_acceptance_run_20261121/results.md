# Unified acceptance results (36 trials)

- Plan: `experiments/agent_stress_v1/unified_acceptance_plan_20261120/plan.json`
- Plan SHA-256: `13496a19116c6700841ae7e190d10eae59bc0ec0d883dcbedcef22014e9f6737`
- Code head: `32e969a56dc0051c5dc9e551bf187607e6d4237e`
- Code fingerprint: `3d4d0e4d0762bac8b816cde54a65c54b5608a66b355d4e939df4d73c969b47b1`
- Completed: **36/36**; official successes **32**; execution failures **0**; prompt tokens **4,880,064**.

## Per-cell results (plan order)

| Block | # | Task | Model | Architecture | Condition | Official | Injections | Steps | Prompt tok | Plan tok | Exec tok | Replan tok |
| ---: | ---: | ---: | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 1 | 228 | deepseek_v41_flash | react | single | success (1.0) | 1 | 5 | 38,462 | 0 | 39,087 | 0 |
| 1 | 2 | 228 | deepseek_v41_flash | react | control | success (1.0) | 0 | 4 | 31,402 | 0 | 31,922 | 0 |
| 1 | 3 | 228 | deepseek_v41_flash | react | sustained | success (1.0) | 2 | 10 | 98,142 | 0 | 99,005 | 0 |
| 2 | 1 | 314 | deepseek_v41_flash | plan_execute | single | success (1.0) | 1 | 7 | 88,650 | 24,295 | 72,455 | 5,926 |
| 2 | 2 | 314 | deepseek_v41_flash | plan_execute | control | success (1.0) | 0 | 5 | 90,276 | 38,131 | 57,876 | 5,656 |
| 2 | 3 | 314 | deepseek_v41_flash | plan_execute | sustained | success (1.0) | 3 | 16 | 189,511 | 48,079 | 156,661 | 25,788 |
| 3 | 1 | 228 | qwen38_flash | react | control | success (1.0) | 0 | 6 | 56,024 | 0 | 56,574 | 0 |
| 3 | 2 | 228 | qwen38_flash | react | single | success (1.0) | 1 | 5 | 43,825 | 0 | 44,254 | 0 |
| 3 | 3 | 228 | qwen38_flash | react | sustained | success (1.0) | 3 | 10 | 95,542 | 0 | 96,109 | 0 |
| 4 | 1 | 228 | deepseek_v41_flash | plan_execute | single | success (1.0) | 1 | 5 | 55,008 | 20,170 | 39,813 | 13,115 |
| 4 | 2 | 228 | deepseek_v41_flash | plan_execute | sustained | success (1.0) | 2 | 8 | 84,635 | 12,657 | 67,178 | 8,864 |
| 4 | 3 | 228 | deepseek_v41_flash | plan_execute | control | success (1.0) | 0 | 4 | 46,796 | 19,705 | 32,720 | 12,904 |
| 5 | 1 | 314 | deepseek_v41_flash | react | sustained | failure (0.0) | 5 | 20 | 226,605 | 0 | 229,220 | 0 |
| 5 | 2 | 314 | deepseek_v41_flash | react | single | success (1.0) | 1 | 5 | 42,334 | 0 | 42,875 | 0 |
| 5 | 3 | 314 | deepseek_v41_flash | react | control | failure (0.0) | 0 | 4 | 36,574 | 0 | 36,968 | 0 |
| 6 | 1 | 228 | qwen38_flash | plan_execute | control | failure (0.0) | 0 | 5 | 60,573 | 3,387 | 51,687 | 6,450 |
| 6 | 2 | 228 | qwen38_flash | plan_execute | sustained | success (1.0) | 3 | 13 | 215,627 | 15,816 | 187,983 | 14,175 |
| 6 | 3 | 228 | qwen38_flash | plan_execute | single | success (1.0) | 1 | 8 | 105,940 | 7,013 | 91,256 | 9,248 |
| 7 | 1 | 28 | qwen38_flash | plan_execute | sustained | success (1.0) | 7 | 19 | 225,066 | 20,793 | 194,087 | 15,438 |
| 7 | 2 | 28 | qwen38_flash | plan_execute | single | success (1.0) | 1 | 11 | 151,883 | 12,903 | 128,463 | 13,590 |
| 7 | 3 | 28 | qwen38_flash | plan_execute | control | success (1.0) | 0 | 9 | 122,868 | 14,330 | 99,851 | 11,173 |
| 8 | 1 | 28 | qwen38_flash | react | sustained | success (1.0) | 9 | 20 | 223,620 | 0 | 224,884 | 0 |
| 8 | 2 | 28 | qwen38_flash | react | control | success (1.0) | 0 | 9 | 98,634 | 0 | 99,272 | 0 |
| 8 | 3 | 28 | qwen38_flash | react | single | success (1.0) | 1 | 12 | 168,644 | 0 | 169,429 | 0 |
| 9 | 1 | 28 | deepseek_v41_flash | react | sustained | failure (0.0) | 7 | 20 | 295,410 | 0 | 297,758 | 0 |
| 9 | 2 | 28 | deepseek_v41_flash | react | single | success (1.0) | 1 | 12 | 174,516 | 0 | 175,946 | 0 |
| 9 | 3 | 28 | deepseek_v41_flash | react | control | success (1.0) | 0 | 10 | 130,430 | 0 | 131,493 | 0 |
| 10 | 1 | 314 | qwen38_flash | react | single | success (1.0) | 1 | 7 | 76,537 | 0 | 77,033 | 0 |
| 10 | 2 | 314 | qwen38_flash | react | sustained | success (1.0) | 4 | 19 | 354,320 | 0 | 355,672 | 0 |
| 10 | 3 | 314 | qwen38_flash | react | control | success (1.0) | 0 | 4 | 48,144 | 0 | 48,445 | 0 |
| 11 | 1 | 314 | qwen38_flash | plan_execute | control | success (1.0) | 0 | 4 | 56,664 | 4,184 | 48,648 | 4,470 |
| 11 | 2 | 314 | qwen38_flash | plan_execute | sustained | success (1.0) | 4 | 19 | 314,220 | 16,085 | 285,593 | 16,744 |
| 11 | 3 | 314 | qwen38_flash | plan_execute | single | success (1.0) | 1 | 6 | 81,297 | 4,663 | 71,288 | 6,342 |
| 12 | 1 | 28 | deepseek_v41_flash | plan_execute | sustained | success (1.0) | 7 | 17 | 295,479 | 75,982 | 230,317 | 31,987 |
| 12 | 2 | 28 | deepseek_v41_flash | plan_execute | control | success (1.0) | 0 | 13 | 215,377 | 66,244 | 166,735 | 23,316 |
| 12 | 3 | 28 | deepseek_v41_flash | plan_execute | single | success (1.0) | 1 | 12 | 241,029 | 68,406 | 177,619 | 32,066 |

## Execution-rule consistency

- task_input_same: **True**
- login_state_same: **True**
- browser_tools_same: **True**
- fault_params_same: **True**
- submission_correction_same: **True**
- max_steps_same: **True**
- official_scoring_rule_same: **True**
- prompt_base_same: **True**
- Plan-field violations: none
- Runtime-field violations (excluding outcome): none
- Outcome differences (official status): ['28/deepseek_v41_flash/sustained:runtime:official_status', '228/qwen38_flash/control:runtime:official_status', '314/deepseek_v41_flash/control:runtime:official_status', '314/deepseek_v41_flash/sustained:runtime:official_status']
- Calls after the last execution turn: none
- Executor-call/step mismatches: none
- Shared prompt contract: `a5b1896a1c81414718b3a413b284346c203feaa3c4bb5aa74daa936a15bd6917`
- Plan-Execute-only prompt fields: ['Current sub-goal', 'Success condition']
- Tool list: `42df11472f0e6e18680b85c4532600f2436d9c8b8b1876515e8891a9976e719e` (9 tools)

## Official failures (valid results, kept)

- `deepseek_v41_flash_task314_react_seed1_sustained`: status=failure, termination=cap_exhausted, steps=20, injections=5, answer=None
- `deepseek_v41_flash_task314_react_seed1_control`: status=failure, termination=stop, steps=4, injections=0, answer=['Shawn Allen (95 commits)', 'Inayaili León (77 commits)', 'Aurora Pleguezuelo (66 commits)']
- `qwen38_flash_task228_plan_execute_seed1_control`: status=failure, termination=stop, steps=5, injections=0, answer=[{'min': 10.99, 'max': 94.99}]
- `deepseek_v41_flash_task28_react_seed1_sustained`: status=failure, termination=cap_exhausted, steps=20, injections=7, answer=None

## Fault exposure

- Control injections: **0**
- Single untriggered: none
- Sustained untriggered: none
- Sustained with only one exposure: none
- Repeated exposure covered:
  - task 28 / deepseek_v41_flash / plan_execute
  - task 28 / deepseek_v41_flash / react
  - task 28 / qwen38_flash / plan_execute
  - task 28 / qwen38_flash / react
  - task 228 / deepseek_v41_flash / plan_execute
  - task 228 / deepseek_v41_flash / react
  - task 228 / qwen38_flash / plan_execute
  - task 228 / qwen38_flash / react
  - task 314 / deepseek_v41_flash / plan_execute
  - task 314 / deepseek_v41_flash / react
  - task 314 / qwen38_flash / plan_execute
  - task 314 / qwen38_flash / react

## Evidence paths

- `--check`: `experiments/agent_stress_v1/unified_acceptance_run_20261121/check.json`
- Audit: `experiments/agent_stress_v1/unified_acceptance_run_20261121/final_audit.json`
- Progress: `experiments/agent_stress_v1/unified_acceptance_run_20261121/progress.json`
- Summary: `experiments/agent_stress_v1/unified_acceptance_run_20261121/summary.json`
- Prompt-contract test: `experiments/agent_stress_v1/unified_acceptance_run_20261121/first6_prompt_contract_test.txt`
- Per-cell `official_eval.json`, `agent_response.json`, `network.har`, `trial_record.json`, `fault_evidence.json` are listed in the audit `cells` entries.
