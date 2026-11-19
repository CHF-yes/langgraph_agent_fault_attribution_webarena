# Reference-path, native-scoring and fault-target checks

Date: 2026-11-17. Scope: the 240-trial Qwen clean screening (`fc3e329`) and targeted page/API checks. No held-out fault outcome was viewed for selection.

## 1. Prioritised triage: 146, 304, 388

### Task 146 - size unit

- Expected: `width = "16 inches"`, `height = "24 inches"` (regex requires an inch unit).
- Existing successful page evidence: order 175 (`/sales/order/view/order_id/175/`) shows "Forest Canvas Wall Art ... Size16\"x24\"" and option "Mist 16*24". The inch unit is only present as the `"` symbol in the product name; the option text is `16*24`.
- Agent responses (all 6): `[{"width": 16, "height": 24}]` or `16`/`24` without unit. One trial exhausted 20 steps.
- Native scoring with the same evaluator: `16 inches`/`24 inches` -> success (1.0); `16x24` -> failure; `["16x24"]` -> failure.
- Conclusion: the evaluator is not a false negative, but the task is high-risk because the unit is only implicit and the dataset raw annotation is `16x24`. As-is it stays 0/6 and is excluded. A unit-preserving public contract must be verified before re-screening.

### Task 304 - answer format

- Expected: `retrieved_data = [14.0]`.
- Reference evidence: GitLab API `/api/v4/projects/a11yproject%2Fa11yproject.com/repository/commits` for 2023-02-01..2023-06-01 returns 21 commits, 14 by Eric Bailey. This matches the expected value.
- Agent responses: ReAct seed 1 found 14 but submitted a `{commits: 14}` object; other failures submitted prose or exhausted 20 steps.
- Native scoring: `[14]` -> success (1.0); object/prose -> failure.
- Conclusion: valid task and legitimate path; the zero is answer-shape plus step budget, not missing truth. Excluded at 0/6, but a contract-corrected re-screen is a plausible harder/medium candidate.

### Task 388 - empty result

- Expected: `["Evelyn Kurver", "N Randall"]`.
- Existing page evidence: EYZUTAK phone-case reviews contain both `N Randall` and `Evelyn Kurver` (40% review, "gold is already peeling"). plan_execute seed 1 saw both names and wrote prose; seed 3 saw both names yet submitted `[]`; seed 2 saw `N Randall` and submitted `[]`; all three ReAct trials exhausted 20 steps.
- Native scoring: both names -> success (1.0); `[]` -> failure with "Expected retrieved_data to be ... but got None or empty".
- Conclusion: there is no empty-result evaluator false positive/negative. The failure is retrieval termination and plan-execute answer extraction. Excluded at 0/6 until the answer-extraction defect is fixed and the full baseline is re-run.

## 2. Reference-path evidence for the 20-task pool

Every selected task has at least one official clean success, which proves a legitimate browser path and an accepted native score. The table also lists the first non-static HTTP endpoints from that success trace as fault-target candidates. Deterministic Playwright replay is still required before freezing; the three tasks above received targeted page/API checks.

| Task | Site | Tier | 6/6 | Success artefact | Candidate fault target(s) |
| ---: | --- | --- | ---: | --- | --- |
| 28 | reddit | medium | 4/6 | `qwen38_flash_task28_plan_execute_seed3` | GET localhost:9999/; GET localhost:9999/f/Worcester; GET localhost:9999/forums |
| 66 | reddit | medium | 4/6 | `qwen38_flash_task66_plan_execute_seed1` | GET localhost:9999/f/books; GET localhost:9999/; GET localhost:9999/forums |
| 163 | shopping | medium | 4/6 | `qwen38_flash_task163_plan_execute_seed1` | GET localhost:7770/review/product/listAjax/id/38805/; GET localhost:7770/ostent-16gb-memory-card-stick-storage-for-sony-ps-vita-psv1000-2000-pch-z081-z161-z321-z641.html |
| 188 | shopping | medium | 4/6 | `qwen38_flash_task188_plan_execute_seed2` | GET localhost:7770/sales/order/history/; GET localhost:7770/; GET localhost:7770/customer/account/login/ |
| 227 | shopping | medium | 3/6 | `qwen38_flash_task227_react_seed1` | GET localhost:7770/catalogsearch/result/; GET localhost:7770/catalogsearch/searchTermsLog/save/; GET localhost:7770/ |
| 228 | shopping | medium | 3/6 | `qwen38_flash_task228_react_seed1` | GET localhost:7770/; GET localhost:7770/search/ajax/suggest/; GET localhost:7770/catalogsearch/result/ |
| 294 | gitlab | medium | 3/6 | `qwen38_flash_task294_plan_execute_seed1` | GET localhost:8023/; GET localhost:8023/byteblaze/cloud-to-butt/-/avatar; GET localhost:8023/explore |
| 316 | gitlab | medium | 3/6 | `qwen38_flash_task316_plan_execute_seed3` | GET localhost:8023/search/count; POST localhost:8023/api/graphql; GET localhost:8023/facebook/create-react-app/-/graphs/main |
| 784 | gitlab | medium | 3/6 | `qwen38_flash_task784_react_seed1` | POST localhost:8023/api/graphql; GET localhost:8023/CellularPrivacy/Android-IMSI-Catcher-Detector/-/graphs/master; GET localhost:8023/CellularPrivacy/Android-IMSI-Catcher-Detector |
| 785 | gitlab | medium | 3/6 | `qwen38_flash_task785_plan_execute_seed3` | POST localhost:8023/api/graphql; GET localhost:8023/CellularPrivacy/Android-IMSI-Catcher-Detector/-/refs/development/logs_tree/; GET localhost:8023/CellularPrivacy/Android-IMSI-Catcher-Detector/-/blob/development/README.md |
| 295 | gitlab | hard | 2/6 | `qwen38_flash_task295_plan_execute_seed1` | GET localhost:8023/search/count; GET localhost:8023/search/autocomplete; POST localhost:8023/api/graphql |
| 305 | gitlab | hard | 2/6 | `qwen38_flash_task305_react_seed1` | GET localhost:8023/api/v4/projects/174/repository/branches; GET localhost:8023/api/v4/projects/174/repository/tags; GET localhost:8023/a11yproject/a11yproject.com/-/commits/main |
| 384 | shopping | hard | 2/6 | `qwen38_flash_task384_plan_execute_seed1` | GET localhost:7770/; GET localhost:7770/search/ajax/suggest/; GET localhost:7770/catalogsearch/result/ |
| 27 | reddit | easy | 6/6 | `qwen38_flash_task27_plan_execute_seed1` | GET localhost:9999/; GET localhost:9999/forums; GET localhost:9999/f/personalfinance |
| 164 | shopping | easy | 6/6 | `qwen38_flash_task164_plan_execute_seed1` | GET localhost:7770/mineralogie-all-natural-lip-gloss-ruby-rose.html; GET localhost:7770/review/product/listAjax/id/6523/ |
| 314 | gitlab | easy | 6/6 | `qwen38_flash_task314_plan_execute_seed1` | POST localhost:8023/api/graphql; GET localhost:8023/primer/design/-/refs/main/logs_tree/; GET localhost:8023/primer/design/-/blob/main/README.md |
| 48 | shopping | easy | 6/6 | `qwen38_flash_task48_plan_execute_seed1` | GET localhost:7770/sales/order/history/; GET localhost:7770/; GET localhost:7770/customer/account/login/ |
| 49 | shopping | easy | 5/6 | `qwen38_flash_task49_plan_execute_seed1` | GET localhost:7770/sales/order/history/; GET localhost:7770/; GET localhost:7770/wishlist/ |
| 148 | shopping | easy | 5/6 | `qwen38_flash_task148_plan_execute_seed1` | GET localhost:7770/sales/order/history/; GET localhost:7770/; GET localhost:7770/customer/account/login/ |
| 190 | shopping | easy | 5/6 | `qwen38_flash_task190_plan_execute_seed1` | GET localhost:7770/; GET localhost:7770/customer/account/; GET localhost:7770/customer/account/login/ |

## 3. Status and open checks

- Passed now: native scoring for 146/304/388; page/API truth for 304 and 388; page evidence for 146; at least one accepted success for all 20 pool tasks; fault-target endpoint shortlist above.
- Not yet passed: deterministic replay of all 20 reference paths; systematic alternative-path proof for every task; gitlab/reddit login-state freshness at execution time.
- DeepSeek clean controls: not yet run for this pool. The required block after supplementation is 24 tasks x 2 architectures x 3 seeds = 144 clean trials. Historical DeepSeek traces cover different tasks/conditions and are not contemporaneous controls.
