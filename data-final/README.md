# Final Experiment Data

This folder contains compact, reviewed experiment outputs. Original HAR files, raw logs, auth state, `.env`, and browser profiles remain outside this folder.

## Contents

- `baseline/`: frozen `gpt54 + react` 16-task control baseline, 48 records.
- `behavioral_faults/`: five complete ReAct fault matrices, 800 trial-level behavioral records. Use for injection, completion, steps, calls, time, and behavior analysis. These records are not official evaluator results.
- `official_subset/`: schema-corrected official evaluation summaries and unique-trial audit for the official representative subset. The source HAR/response/evaluator files remain in the original experiment directories.
- `architecture/`: ReAct versus Plan-and-Execute architecture pilot summaries.
- `model_comparison/`: gpt54 versus DeepSeek ReAct core model comparison summaries.
- `model_architecture/`: balanced 180-trial model-by-architecture subset: 2 models x 2 architectures x 3 tasks x 5 seeds x 3 conditions. The primary set keeps one shared control per model/architecture/task/seed; extra control replicates are excluded from the primary statistics.
- `recovery/`: 45 fault-trial control/fault exports and log-derived recovery labels.
- `provenance/`: project-level experiment index, manifest, summary, and checksums.

## Interpretation

Use `official_subset/` for official evaluator success/failure comparisons. Use `behavioral_faults/` for the complete five-fault behavioral matrix. Do not mix completion metrics with official evaluator success rates. Check each manifest for known fallback, batch, schema, and coverage limitations before reporting results.
