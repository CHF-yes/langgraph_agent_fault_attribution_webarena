# Endpoint natural-exposure review

A **natural hit** means the Agent's own browser action changed the page URL (or
issued a `goto`) to the frozen endpoint. A same-URL click that only triggers a
background request is not counted as a natural hit.

| Task | Site | Template | Endpoint | Success traces | Hit traces | Coverage | Classification | First hit |
| ---: | --- | ---: | --- | ---: | ---: | ---: | --- | --- |
| 27 | reddit | 33 | `http://localhost:9999/f/personalfinance` | 6 | 6 | 100% | confirmed_natural_hit | `qwen38_flash__react__control__task27__seed1__control__rep0.jsonl` step 1 goto -> `http://localhost:9999/f/personalfinance` |
| 28 | reddit | 33 | `http://localhost:9999/f/WorcesterMA` | 15 | 15 | 100% | confirmed_natural_hit | `qwen38_flash__plan_execute__control__task28__seed3__control__rep0.jsonl` step 5 click -> `http://localhost:9999/f/WorcesterMA` |
| 48 | shopping | 197 | `http://localhost:7770/sales/order/history` | 6 | 6 | 100% | confirmed_natural_hit | `qwen38_flash__react__control__task48__seed2__control__rep0.jsonl` step 1 goto -> `http://localhost:7770/sales/order/history/` |
| 49 | shopping | 197 | `http://localhost:7770/sales/order/history` | 5 | 5 | 100% | confirmed_natural_hit | `qwen38_flash__plan_execute__control__task49__seed1__control__rep0.jsonl` step 5 click -> `http://localhost:7770/sales/order/history/` |
| 66 | reddit | 17 | `http://localhost:9999/f/books` | 4 | 3 | 75% | confirmed_natural_hit | `qwen38_flash__react__control__task66__seed1__control__rep0.jsonl` step 2 scroll -> `http://localhost:9999/f/books` |
| 69 | reddit | 17 | `http://localhost:9999/f/books` | 2 | 2 | 100% | confirmed_natural_hit | `qwen38_flash__react__control__task69__seed3__control__rep0.jsonl` step 2 click -> `http://localhost:9999/f/books/59421/friendly-reminder-bookshop-org-exists` |
| 148 | shopping | 155 | `http://localhost:7770/sales/order/history` | 5 | 5 | 100% | confirmed_natural_hit | `qwen38_flash__plan_execute__control__task148__seed3__control__rep0.jsonl` step 7 click -> `http://localhost:7770/sales/order/history/` |
| 149 | shopping | 155 | `http://localhost:7770/sales/order/history` | 2 | 2 | 100% | confirmed_natural_hit | `qwen38_flash__react__control__task149__seed2__control__rep0.jsonl` step 6 click -> `http://localhost:7770/sales/order/history/` |
| 163 | shopping | 136 | `http://localhost:7770/review/product/listAjax/id/38805` | 4 | 4 | 100% | confirmed_natural_hit | `qwen38_flash__plan_execute__control__task163__seed2__control__rep0.jsonl` step 2 click -> `http://localhost:7770/review/product/listAjax/id/38805/?p=2` |
| 164 | shopping | 136 | `http://localhost:7770/review/product/listAjax/id/6523` | 6 | 0 | 0% | existing_trajectory_no_hit | — |
| 168 | gitlab | 289 | `http://localhost:8023/dashboard/projects` | 6 | 1 | 17% | confirmed_natural_hit | `qwen38_flash__react__control__task168__seed3__control__rep0.jsonl` step 5 goto -> `http://localhost:8023/dashboard/projects?sort=stars_desc` |
| 188 | shopping | 214 | `http://localhost:7770/sales/order/history` | 4 | 4 | 100% | confirmed_natural_hit | `qwen38_flash__react__control__task188__seed2__control__rep0.jsonl` step 9 click -> `http://localhost:7770/sales/order/history/` |
| 190 | shopping | 214 | `http://localhost:7770/sales/order/history` | 5 | 4 | 80% | confirmed_natural_hit | `qwen38_flash__plan_execute__control__task190__seed3__control__rep0.jsonl` step 5 click -> `http://localhost:7770/sales/order/history/` |
| 206 | gitlab | 320 | `http://localhost:8023/a11yproject/a11yproject.com/-/commits/main` | 2 | 2 | 100% | confirmed_natural_hit | `qwen38_flash__plan_execute__control__task206__seed1__control__rep0.jsonl` step 2 goto -> `http://localhost:8023/a11yproject/a11yproject.com/-/commits/main` |
| 227 | shopping | 370 | `http://localhost:7770/catalogsearch/result` | 3 | 3 | 100% | confirmed_natural_hit | `qwen38_flash__react__control__task227__seed2__control__rep0.jsonl` step 3 click -> `http://localhost:7770/catalogsearch/result/?q=EYZUTAK` |
| 228 | shopping | 370 | `http://localhost:7770/catalogsearch/result` | 14 | 14 | 100% | confirmed_natural_hit | `qwen38_flash__react__control__task228__seed2__control__rep0.jsonl` step 3 click -> `http://localhost:7770/catalogsearch/result/?q=sephora` |
| 294 | gitlab | 329 | `http://localhost:8023/search` | 3 | 1 | 33% | confirmed_natural_hit | `qwen38_flash__plan_execute__control__task294__seed3__control__rep0.jsonl` step 7 click -> `http://localhost:8023/search?search=ChatGPT&nav_source=navbar` |
| 295 | gitlab | 329 | `http://localhost:8023/search` | 2 | 2 | 100% | confirmed_natural_hit | `qwen38_flash__plan_execute__control__task295__seed2__control__rep0.jsonl` step 3 click -> `http://localhost:8023/search?search=metaseq&nav_source=navbar` |
| 305 | gitlab | 321 | `http://localhost:8023/a11yproject/a11yproject.com/-/commits/main` | 2 | 2 | 100% | confirmed_natural_hit | `qwen38_flash__react__control__task305__seed3__control__rep0.jsonl` step 1 goto -> `http://localhost:8023/a11yproject/a11yproject.com/-/commits/main` |
| 314 | gitlab | 324 | `http://localhost:8023/primer/design/-/graphs` | 16 | 16 | 100% | confirmed_natural_hit | `qwen38_flash__react__control__task314__seed2__control__rep0.jsonl` step 2 goto -> `http://localhost:8023/primer/design/-/graphs/main` |
| 316 | gitlab | 324 | `http://localhost:8023/facebook/create-react-app/-/graphs` | 3 | 3 | 100% | confirmed_natural_hit | `qwen38_flash__react__control__task316__seed2__control__rep0.jsonl` step 11 goto -> `http://localhost:8023/facebook/create-react-app/-/graphs/main` |
| 384 | shopping | 666 | `http://localhost:7770/catalogsearch/result` | 2 | 2 | 100% | confirmed_natural_hit | `qwen38_flash__plan_execute__control__task384__seed1__control__rep0.jsonl` step 3 click -> `http://localhost:7770/catalogsearch/result/?q=EYZUTAK+phone+case` |
| 784 | gitlab | 316 | `http://localhost:8023/CellularPrivacy/Android-IMSI-Catcher-Detector/-/graphs` | 3 | 3 | 100% | confirmed_natural_hit | `qwen38_flash__react__control__task784__seed1__control__rep0.jsonl` step 1 goto -> `http://localhost:8023/CellularPrivacy/Android-IMSI-Catcher-Detector/-/graphs/master` |
| 785 | gitlab | 316 | `http://localhost:8023/CellularPrivacy/Android-IMSI-Catcher-Detector/-/graphs` | 3 | 3 | 100% | confirmed_natural_hit | `qwen38_flash__react__control__task785__seed3__control__rep0.jsonl` step 1 goto -> `http://localhost:8023/CellularPrivacy/Android-IMSI-Catcher-Detector/-/graphs/gh-page` |

## Questions

- **164**: successful trajectories never navigated to the frozen endpoint; the data was reached through a same-URL interaction/background request
  - Recommendation: replace the task, move the endpoint to a naturally navigated task-critical page, or amend the protocol to activate on the same-URL background request
- **168**: the endpoint is naturally reachable, but only in a minority of successful trajectories
  - Recommendation: consider a more frequently used task-critical endpoint or retain the task with an explicit low-coverage note
- **294**: the endpoint is naturally reachable, but only in a minority of successful trajectories
  - Recommendation: consider a more frequently used task-critical endpoint or retain the task with an explicit low-coverage note

Per-trace action steps and paths are in `endpoint_natural_exposure.json`.
Scripted `goto` and HAR requests remain mechanism evidence; they are not
counted as natural Agent navigation.
