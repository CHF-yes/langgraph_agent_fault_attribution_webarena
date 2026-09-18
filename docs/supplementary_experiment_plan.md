# Supplementary experiment plan

> **2026-09-18 execution correction:** this document preserves the original staged plan for
> paper provenance, but its Stage A rerun and two-model/two-architecture Stage C are superseded
> by [`experiment_roadmap.md`](experiment_roadmap.md). Do not launch commands from this file
> without reconciling them with the roadmap. The frozen 400-trial official subset already has
> 400/400 HAR files; Stage A is now an evaluator repair plus offline re-evaluation, not a rerun.
> The current three models are treated as categorical levels, not as an ordered capability axis.

Staged design that turns the current single-cell study into a genuine
`Model × Architecture × Fault` attribution. Stages are ordered by **how much
they change the numbers already published**, not by novelty.

Read [§0](#0-two-traps-that-will-silently-corrupt-a-rerun) before running
anything: two of the flags below fail quietly rather than loudly.

---

## 0. Two traps that will silently corrupt a rerun

**(a) Never pass `--fault-injection-step` when reproducing the frozen design.**

`scripts/run_fault_matrix.py:74-76` sets the injection step per fault:

```python
injection_step = args.fault_injection_step
if injection_step is None:
    injection_step = 1 if fault_type == "agent_param_error" else 2
```

`--fault-injection-step` is a single scalar applied to **every** fault in the
matrix. Passing `--fault-injection-step 2` therefore moves `agent_param_error`
off its default step 1 — the injector fires one action later, the fault lands on
a different observation, and the run no longer reproduces the frozen cell. The
flag is only for the deliberate step sweep in Stage E, where you vary it on
purpose and say so.

**(b) Raise `--job-timeout-minutes` to 45.**

The default is 30 minutes. Measured `plan_execute` runs under `web_http_error`
have a **maximum of 2756 s (46 min)** and a mean of 652 s; the timeout kills the
whole matrix when a single job exceeds it. At the default this discards roughly
one in five of the slowest `plan_execute` jobs — and it discards them
*non-randomly*, since the slow jobs are the ones where the fault actually bit.

---

## 1. Measured basis for the wall-clock estimates

Taken from the committed artifacts, not assumed:

| Quantity | Value | Source |
|---|---|---|
| Trial wall-clock, `react` (mean) | **159.0 s** | `fault_matrices_v1/trials.json`, n=800 |
| Trial wall-clock, `react` (median) | 96.1 s | same |
| Trial wall-clock, `react` (p90 / max) | 382.5 s / 1465.9 s | same |
| `plan_execute` control (mean / max) | 365 s / 1175 s | architecture pilot, n=9 |
| `plan_execute` fault (mean / max) | **652 s / 2756 s** | architecture pilot, n=18 |
| Official subset timing fields | **absent** | the frozen 400 carry no `steps`/`time_sec` |

Estimates below use `wall ≈ trials × s_per_trial / workers × 1.25`, with 6
workers and a 25% overhead for browser start-up, evaluator calls and retries.
`plan_execute` cells use 500 s/trial rather than 159 s — it is ~4.7× slower than
ReAct, and assuming the ReAct rate for them would understate those stages by
about a third.

## 2. Shared flags

```bash
--max-steps 20 --fault-intensity high --trials 5 --fault-seed 1 \
--workers 6 --resume
```

`--resume` skips any job that already has a `*.status.json` with `returncode == 0`,
no `infrastructure_error` and no `fault_invalid`, so a stage can be interrupted and
restarted. The manifest records the automatic stop policy (`infrastructure_errors: 2`,
`fault_missing_in_last_8: 2`) — a matrix that trips it is telling you the injector
stopped firing, which is a result, not an inconvenience.

Note `total_trials_including_control = jobs × 2`: the runner runs the control arm
itself, so `--trials 5` over 5 faults × 8 tasks means 200 jobs and **400 trials**.

Frozen task set (do not substitute): `--task-ids 22 24 27 28 30 132 133 134`.

---

## 3. Stage A — repair and re-run the evaluator (no new agent trials)

The frozen official subset already contains the complete design
(5 faults × 8 tasks × 5 seeds × 2 conditions = 400), and every row has a
non-empty `agent_response.json`, `network.har`, and `official_eval.json`. The
76 fallback verdicts are triggered by the upstream `retrieved_data=null` schema
exception, not by missing HAR. Re-running the same agents would therefore repeat
the cost without repairing the instrument.

Required action:

1. repair or upgrade the evaluator's null-retrieval handling;
2. re-evaluate the existing 400 response/HAR pairs offline;
3. compare old and new verdicts and retain an audit table;
4. create a targeted rerun list only if the repaired evaluator cannot consume an
   existing artifact, or if tasks 22/24 are deliberately replaced.

- New agent trials: **0 by default**.
- Agent wall-clock: **0 by default**; only evaluator compute is required.
- The old 400-trial command is intentionally removed to prevent an accidental duplicate run.

## 4. Stage C — the smallest design that identifies M, A and M×A

Two models × two architectures × three faults on the same tasks and seeds,
reusing the `(gpt54, react)` cell from Stage A. This is the **only** stage that
estimates a model main effect, an architecture main effect, and their
interaction — i.e. the responsibility split the project set out to measure.

Faults chosen for mechanism diversity, not convenience: `web_http_error`
(transient infrastructure), `agent_param_error` (corrupted argument), and
`web_dom_missing` (observation loss).

```bash
python3 scripts/run_fault_matrix.py \
  --output-dir  experiments/formal_stageC_modelXarch_v1 \
  --fault-types web_http_error agent_param_error web_dom_missing \
  --task-ids 22 24 27 28 30 132 133 134 \
  --model-profile <model2> --architecture plan_execute \
  --max-steps 20 --fault-intensity high --trials 5 --fault-seed 1 \
  --workers 6 --job-timeout-minutes 45 --resume
# then repeat for (<model2>, react) and (gpt54, plan_execute)
```

- Trials: **960** total, of which **720 new** (240 are the reused `gpt54/react` cell).
- Wall-clock: **≈11 h** — the `plan_execute` cells run at ~500 s/trial, not 159 s.

Two conditions must hold for this to be interpretable, and both need checking
*before* the run: the arms must have **equal step budgets** (hence `--max-steps 20`
everywhere), and the artifact must record per-role call counters
(`planning_calls` / `executor_calls` / `replanning_calls`). The existing architecture
pilot records none of them, which is why it cannot support an equal-LLM-budget
claim. Equal *step* budgets are not equal *time* budgets: for the same nominal
budget `plan_execute` averaged 364.8 s against 77.4 s for ReAct in the control
arm, so a fixed step budget is a ~4.7× larger time and token budget for one arm.

## 5. Stage B — make the model effect non-vacuous

Adds two more models at `react` only. With one model, "model effect" is identified
from a single contrast and cannot be separated from that model's idiosyncrasies.

- Trials: **480 new** (2 models × 1 arch × 3 faults × 8 × 5 × 2). Wall-clock **≈4.4 h**.

## 6. Stage E — injection-step sensitivity (the confound a reviewer will press on)

The injector holds the step fixed throughout (`agent_param_error` at 1, everything
else at 2). Every reported effect is therefore conditional on one injection
position, and nothing in the current study bounds how much that choice matters.

Sweep `--fault-injection-step` over **{1, 2, 3}** for the three Stage-C faults.
Step 2 is already covered, so this adds two steps per fault.

- Trials: **480 new** (3 faults × 2 steps × 8 × 5 × 2). Wall-clock **≈4.4 h**.
- This is the one stage where `--fault-injection-step` **is** the point — and
  note it applies uniformly, so `agent_param_error` will not be at its default
  position in the swept arms. That is intended here; record it in the manifest.

## 7. Stage D — task clusters

8 tasks → 16. Tests whether the effects are a property of the fault or of this
particular task sample, which is the main external-validity threat at `n=8`.

- Trials: **800** total, **400 new**. Wall-clock **≈3.7 h**.

## 8. Stage F — repair ablation

The paper's four candidate repairs (error-aware retry with backoff, argument
validation against the current observation, stale-observation detection, and a
termination policy for unobservable state) are all testable in the existing
harness by re-running the paired design with the repair enabled in both arms.

**Run the repaired and unrepaired arms in the same session.** The five per-fault
control arms are the *same* configuration yet span a factor of 2.17× in mean
wall-clock (103.7–224.6 s). Sequentially-run arms would let that machine-level
drift masquerade as a repair effect.

- Trials: **800** (5 faults × 8 tasks × 5 seeds × 2 conditions × 2 arms).
- Wall-clock **≈7.4 h**.
- Size the target against §5.3, not against available compute: at the observed
  discordance a 10 pp *success-rate* improvement needs ~181 pairs per arm, so a
  **process-cost** improvement is the realistic target at this `n`.

## 9. Budget

| Stage | What it buys | Trials (total / new) | Wall-clock @6 workers |
|---|---|---|---|
| **A** | evaluator repair + offline re-evaluation of existing HAR | 400 existing / 0 new | evaluator-only |
| **C** | Model × Architecture + interaction | 960 / 720 | ≈11 h |
| **B** | model effect beyond one model | 480 / 480 | ≈4.4 h |
| **E** | injection-step sensitivity | 480 / 480 | ≈4.4 h |
| **D** | task-cluster generalisation | 800 / 400 | ≈3.7 h |
| **F** | repair ablation | 800 / 800 | ≈7.4 h |
| | **legacy-plan total after removing duplicate Stage A** | **3 520 / 2 880 new** | **≈31 h** |

Wall-clock, not calendar time: it is the 6-worker busy time and assumes the
WebArena sites, the evaluator and the API are all up. Add re-run margin for
`plan_execute` jobs that hit the 45-minute timeout.

## 10. After each stage

```bash
python3 scripts/paper_tables.py            # tables, figures and prose macros
python3 -m pytest tests/test_stats_core.py # statistics still behave
```

`scripts/paper/` reads the artifacts under `experiments/`; repointing it at a new
stage's output directory is a constant change in `scripts/paper/make_tables.py`
(`official_root`, `matrix_root`, `arch_root`). Nothing in the prose is hand-copied
— every number is a macro — so regenerating is sufficient to propagate new data.

Interpretation rules that do not relax with more data:

- **Never pool the evaluator paths.** Report the native stratum, and report the
  pooled number only beside it.
- **Report the power analysis with every null.** At 30–31 native pairs the MDE is
  *undefined*, not merely large — a non-significant result there is a statement
  about the design, not about the agent.
- **Report within-pair deltas only** for any timing claim.
- **Never combine the official subset and the behavioural matrix** into a
  cost-per-success statement: they are different runs over different task sets and
  their `(fault, task, seed)` keys do not join.

## 11. Credentials

Model profiles come from `.env` only, never from the command line or source:

```bash
MODEL_PROFILES=gpt54,<model2>
MODEL_GPT54_API_KEY=...        # MODEL_<NAME>_{API_KEY,BASE_URL,NAME,TEMPERATURE}
```

`.env` is gitignored and must stay that way; HAR/JSONL traces, auth state and the
`--official-output-root` trees are never committed. Check what is wired up with
`python3 main.py --list-model-profiles` (prints whether each key is set, not the
key).
