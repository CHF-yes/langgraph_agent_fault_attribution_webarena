# Project Overview And Curated Results

## Scope

This repository studies fault attribution for a LangGraph-based WebArena browser
agent. It keeps the agent implementation, deterministic fault-injection layer,
experiment runners, and compact audit-ready result tables together. Large raw
artifacts such as browser HAR files, JSONL traces, authentication state, and
per-run logs are intentionally excluded from Git.

## Core Design

The measured system is a single-model browser agent with two selectable
LangGraph workflows:

```text
react:
START -> agent -> tools -> agent -> ... -> END

plan_execute:
START -> planner -> executor -> tools -> replanner
                                  |              |
                                  +---- planner -+
```

Both workflows share the same WebArena browser environment, accessibility-tree
observation, nine browser tools, model profile, task prompts, trace format, and
evaluation adapter. `react` is the standard tool-calling baseline. The
`plan_execute` workflow separates planning, execution, and replanning so its
extra model calls can be measured independently.

`fault_injection/` is intentionally outside the standard agent. A transparent
proxy injects one seeded fault at a configured browser action step, preserving a
control-versus-fault comparison with the same task, seed, model profile,
architecture, and maximum executor steps. The currently represented fault
families are DOM omission, popup blocking, HTTP error, timeout, and malformed
agent parameters.

The command path is:

```text
main.py -> standard_agent.core.graph -> browser tools
        -> fault_injection proxy -> WebArena site
        -> WebArena-Verified evaluator
```

## Uploaded Result Sets

| Dataset | Records | What it measures | Evaluation status |
| --- | ---: | --- | --- |
| Frozen ReAct control baseline | 48 | 16 public WebArena-Verified tasks x 3 trials | Official evaluator; 25 successes (52.08%) |
| Behavioral fault matrices | 800 | 5 fault types x 16 tasks x 5 seeds x control/fault | Behavioral completion and efficiency only; HAR unavailable |
| Official fault subset | 400 | 5 fault types x 8 tasks x 5 seeds x control/fault | Official evaluator; 256 successes, 0 evaluator errors |
| Architecture comparison | 54 | ReAct vs plan-and-execute across control and two faults | Official evaluator; 13 successes, 0 evaluator errors |
| Cross-experiment index | 1,248 | Row-level index over the three main datasets | Preserves each dataset's evaluation status |

The row-level files and provenance manifests are committed under:

```text
experiments/curated/
experiments/analysis/fault_matrices_v1/
experiments/analysis/official_subset_v1_final/
experiments/analysis/architecture_gpt54_react_planexecute_v1/
experiments/analysis/project_snapshot_v1/
```

`project_snapshot_v1/manifest.json` records source paths, baseline checksum, and
the commit that generated the snapshot. `experiment_index.csv` preserves the
dataset layer and `evaluator_status` for each indexed record so behavioral
records cannot be mistaken for official success results.

## Official Fault Subset Results

The official subset uses 40 trials for each fault-condition cell. Its aggregate
success rates are:

| Fault | Control | Fault-injected |
| --- | ---: | ---: |
| `web_dom_missing` | 27/40 (67.5%) | 26/40 (65.0%) |
| `web_popup_block` | 23/40 (57.5%) | 24/40 (60.0%) |
| `web_http_error` | 23/40 (57.5%) | 25/40 (62.5%) |
| `web_timeout` | 26/40 (65.0%) | 26/40 (65.0%) |
| `agent_param_error` | 31/40 (77.5%) | 25/40 (62.5%) |

These are observed samples, not claims of a monotonic fault effect. Model calls
remain stochastic, so paired records and the fixed seeds are included for
downstream statistical analysis.

## Behavioral Matrix Results

The 800-record behavioral matrix guarantees exactly one injected fault in all
400 fault trials. It does not retain HAR files and therefore labels every row
`NOT_EVALUATED_NO_HAR`. It should be used for action-level behavior, completion,
step, and latency analysis rather than official task-success claims.

The largest observed cost in this matrix is `web_http_error`: mean steps rise
from 6.06 in control to 10.29 with the fault, and mean time rises from 224.55 s
to 374.41 s. `agent_param_error` similarly raises mean steps from 6.38 to 8.68.
See `experiments/analysis/fault_matrices_v1/summary.csv` for all cells.

## Architecture Comparison

The compact 54-trial comparison covers three tasks and three seeds for each of
control, DOM omission, and HTTP error. The data shows the cost of the additional
planning stages in this configuration: ReAct control succeeds in 4/9 trials
(44.4%) at 77.4 s mean runtime, while plan-and-execute control succeeds in 0/9
trials at 364.8 s. This is a small experiment-specific result, not a general
ranking of agent architectures.

## Reproduction

Run the deterministic offline validation suite:

```bash
PYTHONPATH=. .venv311/bin/python -m unittest discover -s tests -v
```

Rebuild the committed lightweight summaries from local raw outputs:

```bash
PYTHONPATH=. .venv311/bin/python scripts/build_experiment_index.py
PYTHONPATH=. .venv311/bin/python scripts/summarize_architecture_matrix.py
```

Run a new fault matrix with per-job WebArena evaluator inputs retained:

```bash
PYTHONPATH=. .venv311/bin/python scripts/run_fault_matrix.py \
  --output-dir experiments/new_fault_matrix \
  --official-output-root experiments/new_fault_matrix_outputs \
  --fault-types web_dom_missing web_http_error \
  --workers 6 --trials 5 --max-steps 20 \
  --model-profile gpt54 --architecture react
```

## Reporting Limits

- The 48-trial baseline includes 28 compatibility-fallback trials. Its manifest
  records this explicitly; reports must stratify fallback and native evaluator
  outcomes.
- Provider sampling seeds are recorded, but the external model provider does not
  enforce them as deterministic decoding seeds.
- Raw HAR, full browser traces, local authentication, `.env`, and voluminous log
  files are omitted to prevent credential exposure and multi-gigabyte repository
  growth.
- Behavioral matrices without HAR are never included in an official-success
  denominator.
