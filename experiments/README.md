# Experiment Artifacts

This directory contains reviewed, compact experiment artifacts. Raw browser
profiles, HAR files, authentication state, and full logs are intentionally not
tracked; the manifests and CSV/JSON summaries point to their local source
directories where needed.

## Data Layers

| Directory | Contents | Use |
|---|---|---|
| `curated/` | Frozen `gpt54 + react` 16-task control baseline | Baseline reference |
| `analysis/project_snapshot_v1/` | Cross-experiment index and provenance | Dataset inventory |
| `analysis/fault_matrices_v1/` | 800 five-fault behavioral records | Completion, steps, time, and behavior analysis; no official score |
| `analysis/official_subset_v1_final/` | 400 official-evaluator records for 8 representative tasks | Official control/fault comparison |
| `analysis/architecture_gpt54_react_planexecute_v1/` | 54-trial architecture pilot | Initial ReAct vs Plan-and-Execute comparison |
| `analysis/fault_subset_45/` | 45 fault trials and log-derived recovery labels | Recovery-label review subset |

## Reproducibility

Every reviewed dataset includes a manifest or source paths. The frozen
baseline manifest records the model, endpoint, temperature, architecture,
dataset hash, prompt hash, evaluator versions, and known fallback/batch
limitations. The behavioral matrix does not contain HAR/network traces and
must not be presented as WebArena official-success data. The official subset
contains `agent_response.json`, `network.har`, and `official_eval.json` for
each trial in the source output directories.

The compact artifacts are suitable for repository review. Large raw run
directories remain local and are excluded from version control.
