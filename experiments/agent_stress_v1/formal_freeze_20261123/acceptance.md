# 24-task endpoint and reference-path acceptance

All paths are no-model scripted browser actions. `normal` starts from the task
start URL with the shared login state, stays within 20 turns and is scored by
the native evaluator. `recovery` arms the persistent service-level HTTP fault,
confirms one qualified navigation is faulted, spends the 10-step window on
non-target navigations, re-reads the endpoint after release and is scored.

| Task | Site | Template | Difficulty | Endpoint | Normal | Recovery | Injections | Score evidence |
| ---: | --- | ---: | --- | --- | --- | --- | ---: | --- |
| 27 | reddit | 33 | easy | `http://localhost:9999/f/personalfinance` | 3 turns, pass | 13 turns, release=window_expired, pass | 1 | `reference/task27/normal/official_eval.json`; `reference/task27/recovery/official_eval.json` |
| 28 | reddit | 33 | medium | `http://localhost:9999/f/WorcesterMA` | 3 turns, pass | 13 turns, release=window_expired, pass | 1 | `reference/task28/normal/official_eval.json`; `reference/task28/recovery/official_eval.json` |
| 48 | shopping | 197 | easy | `http://localhost:7770/sales/order/history` | 5 turns, pass | 15 turns, release=window_expired, pass | 1 | `reference/task48/normal/official_eval.json`; `reference/task48/recovery/official_eval.json` |
| 49 | shopping | 197 | easy | `http://localhost:7770/sales/order/history` | 5 turns, pass | 15 turns, release=window_expired, pass | 1 | `reference/task49/normal/official_eval.json`; `reference/task49/recovery/official_eval.json` |
| 66 | reddit | 17 | medium | `http://localhost:9999/f/books` | 2 turns, pass | 12 turns, release=window_expired, pass | 1 | `reference/task66/normal/official_eval.json`; `reference/task66/recovery/official_eval.json` |
| 69 | reddit | 17 | hard | `http://localhost:9999/f/books` | 3 turns, pass | 13 turns, release=window_expired, pass | 1 | `reference/task69/normal/official_eval.json`; `reference/task69/recovery/official_eval.json` |
| 148 | shopping | 155 | easy | `http://localhost:7770/sales/order/history` | 7 turns, pass | 17 turns, release=window_expired, pass | 1 | `reference/task148/normal/official_eval.json`; `reference/task148/recovery/official_eval.json` |
| 149 | shopping | 155 | hard | `http://localhost:7770/sales/order/history` | 7 turns, pass | 17 turns, release=window_expired, pass | 1 | `reference/task149/normal/official_eval.json`; `reference/task149/recovery/official_eval.json` |
| 163 | shopping | 136 | medium | `http://localhost:7770/review/product/listAjax/id/38805` | 2 turns, pass | 12 turns, release=window_expired, pass | 1 | `reference/task163/normal/official_eval.json`; `reference/task163/recovery/official_eval.json` |
| 164 | shopping | 136 | easy | `http://localhost:7770/review/product/listAjax/id/6523` | 2 turns, pass | 12 turns, release=window_expired, pass | 1 | `reference/task164/normal/official_eval.json`; `reference/task164/recovery/official_eval.json` |
| 168 | gitlab | 289 | easy | `http://localhost:8023/dashboard/projects` | 2 turns, pass | 12 turns, release=window_expired, pass | 4 | `reference/task168/normal/official_eval.json`; `reference/task168/recovery/official_eval.json` |
| 188 | shopping | 214 | medium | `http://localhost:7770/sales/order/history` | 5 turns, pass | 15 turns, release=window_expired, pass | 1 | `reference/task188/normal/official_eval.json`; `reference/task188/recovery/official_eval.json` |
| 190 | shopping | 214 | easy | `http://localhost:7770/sales/order/history` | 5 turns, pass | 15 turns, release=window_expired, pass | 1 | `reference/task190/normal/official_eval.json`; `reference/task190/recovery/official_eval.json` |
| 206 | gitlab | 320 | hard | `http://localhost:8023/a11yproject/a11yproject.com/-/commits/main` | 2 turns, pass | 12 turns, release=window_expired, pass | 1 | `reference/task206/normal/official_eval.json`; `reference/task206/recovery/official_eval.json` |
| 227 | shopping | 370 | medium | `http://localhost:7770/catalogsearch/result` | 3 turns, pass | 13 turns, release=window_expired, pass | 1 | `reference/task227/normal/official_eval.json`; `reference/task227/recovery/official_eval.json` |
| 228 | shopping | 370 | medium | `http://localhost:7770/catalogsearch/result` | 3 turns, pass | 13 turns, release=window_expired, pass | 1 | `reference/task228/normal/official_eval.json`; `reference/task228/recovery/official_eval.json` |
| 294 | gitlab | 329 | medium | `http://localhost:8023/search` | 3 turns, pass | 13 turns, release=window_expired, pass | 1 | `reference/task294/normal/official_eval.json`; `reference/task294/recovery/official_eval.json` |
| 295 | gitlab | 329 | hard | `http://localhost:8023/search` | 3 turns, pass | 13 turns, release=window_expired, pass | 1 | `reference/task295/normal/official_eval.json`; `reference/task295/recovery/official_eval.json` |
| 305 | gitlab | 321 | hard | `http://localhost:8023/a11yproject/a11yproject.com/-/commits/main` | 2 turns, pass | 12 turns, release=window_expired, pass | 1 | `reference/task305/normal/official_eval.json`; `reference/task305/recovery/official_eval.json` |
| 314 | gitlab | 324 | easy | `http://localhost:8023/primer/design/-/graphs` | 2 turns, pass | 12 turns, release=window_expired, pass | 1 | `reference/task314/normal/official_eval.json`; `reference/task314/recovery/official_eval.json` |
| 316 | gitlab | 324 | medium | `http://localhost:8023/facebook/create-react-app/-/graphs` | 2 turns, pass | 12 turns, release=window_expired, pass | 1 | `reference/task316/normal/official_eval.json`; `reference/task316/recovery/official_eval.json` |
| 384 | shopping | 666 | hard | `http://localhost:7770/catalogsearch/result` | 4 turns, pass | 14 turns, release=window_expired, pass | 1 | `reference/task384/normal/official_eval.json`; `reference/task384/recovery/official_eval.json` |
| 784 | gitlab | 316 | medium | `http://localhost:8023/CellularPrivacy/Android-IMSI-Catcher-Detector/-/graphs` | 2 turns, pass | 12 turns, release=window_expired, pass | 1 | `reference/task784/normal/official_eval.json`; `reference/task784/recovery/official_eval.json` |
| 785 | gitlab | 316 | medium | `http://localhost:8023/CellularPrivacy/Android-IMSI-Catcher-Detector/-/graphs` | 2 turns, pass | 12 turns, release=window_expired, pass | 1 | `reference/task785/normal/official_eval.json`; `reference/task785/recovery/official_eval.json` |

Passed: **24/24**.

## Sensitivity analysis

- Excluded from sensitivity only: 28, 228, 314
- Sensitivity denominator: 21 tasks.
- All three retained tasks are marked `fault_pilot` in `roster.json` and the plan.
