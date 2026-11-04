# Development diagnosis: findings and next fixes

Date: 2026-11-04. Scope: the 108 development trials only; holdout trials were
not used for cause attribution. Machine-readable evidence is in
`development_diagnosis.jsonl`, `summary.json`, and `integrity_audit.json`.

## Headline

- 12/108 official successes; 96 failures.
- 83 trials ended with an answer; 25 exhausted the 20-step cap.
- Primary causes: output contract 40, retrieval incomplete 27, observation or
  tool 26, environment or account 2, reasoning or calculation 1.
- Multi-label causes: retrieval incomplete 61, output contract 47, observation
  or tool 27, termination 25, environment 2, reasoning 1.

The strongest pattern is not hallucination. In almost every structured-answer
case the values in the final answer are exactly the values the agent had already
seen: it omits what it never observed and rarely invents values.

## 1. Output contract is the largest avoidable loss

47 trials violate the public result shape. The dominant form is a prose
explanation placed inside `retrieved_data` instead of a value-only JSON array.

Examples:

- task 21: expected `["catso", "dibbins", ...]`, got one long review summary.
- task 23: expected `["rachel", "t. gannon"]`, got one review paragraph.
- task 231/232/233: expected an order number string, got a sentence containing
  the number.
- task 132/133/134: expected an array of numbers, got `[{"count": 1}]` or a
  status object.

Code inspection explains the concentration:

- `_output_format_context()` only fires when the task text contains markers such
  as "how many", "return a list", or "json". Tasks 21, 23, 231, 232, 233, 169,
  171 and 279-281 contain none of those markers, so both architectures get no
  output-shape instruction.
- `_parse_retrieved_data()` deliberately preserves unstructured output as
  `[text]`. That is a safe parser, but it means a prose answer is submitted as a
  one-element array and is scored as a value mismatch.

The public `results_schema` is available in every task definition and describes
only types and keys, not expected answers. The improved version should derive a
schema contract from it, for example:

- array of strings: one value per element, no prose;
- array of numbers: JSON numbers, not strings or objects;
- array of objects with `min`/`max`/`names`: exact keys, currency as JSON
  numbers, `names` as an array.

A submit-time validator should check only this public shape and allow one
recorded correction attempt. It must not inspect expected values.

## 2. Retrieval is incomplete, often because the agent stops early

61 trials never observed at least one expected item. Of those, 39 ended with an
answer before the step cap and 22 hit the cap. The agent usually returns exactly
the subset it saw, so the actionable defect is coverage, not arithmetic.

Examples:

- task 21: `anglebert dinkherhump` and/or `michelle davis` are missing from the
  observations and from the answer.
- task 124-126: price ranges are answered from a partial product set.
- task 279-281: product-name sets are answered before all result pages are
  covered.
- task 170/171: project IDs are not retrievable without the missing GitLab
  session (see next section).

`_retrieval_completeness_context()` already exists but is gated by
`RETRIEVAL_REPAIR=1` and the screening runner did not request it. The improved
version should enable it for array-schema retrieval tasks and add a behavior
test that the agent visits every page before submitting. Do not add a second
same-purpose prompt.

## 3. GitLab login fails through unstable element IDs, not bad credentials

All 18 trials for tasks 169/170/171 failed. 14 have terminal authentication
evidence. The agent repeatedly received `Invalid login or password`, but the
credentials are valid: a deterministic Playwright reference login with the
standard WebArena `byteblaze` test account reached the GitLab dashboard and
listed the expected projects.

The failure is in the agent's login interaction. After the first failed attempt,
the sign-in page re-renders and the AX element IDs shift. In task 169 seed 2 the
agent typed into element `[13]` instead of the password field `[12]`, then
clicked `[17]` instead of the submit button `[16]`. It never recovered. This is
an observation/tool identity problem, not an unavailable account.

The harness also lacks a pre-authenticated GitLab state in `main.py`:
`run_baseline.py` supports `--storage-state-dir`, but the screening entry point
does not. `webarena_config.py` assumes GitLab has a built-in login state and
returns no auto-login header.

Consequences for the proposed 72-trial comparison:

- tasks 169/170/171 are not intrinsically invalid; a reference login works.
- without pre-authentication or a fix for element identity across form
  re-renders, all three remain high-risk (0/18 in screening).
- add `--storage-state-dir` support to `main.py` and supply a verified GitLab
  state, then confirm 169/170/171 with a deterministic reference path before
  deciding whether template 289 stays in the 72-trial block.
- if pre-authentication cannot be supplied, shrink the block from 6 to 5
  templates (72 to 60 trials) before any paid run.

Tasks 132/133/134 are public GitLab tasks and have some successes, but they also
show unnecessary login attempts and mixed outcomes. They need reference-path
verification before being used as a baseline. Tasks 231-233 mostly work; one
plan-execute trial was terminal-blocked at the shopping login. Pre-authenticate
these tasks so the baseline is not a login test.

## 4. Data integrity is clean

`integrity_audit.json` checks all 348 screening trials:

- 348/348 expected keys present, no duplicates, no unexpected directories;
- zero fault injections; all control;
- 20-step budget and qwen38_flash profile match;
- official result is boolean and consistent with status;
- agent-response SHA-256 matches the evaluator binding for every trial;
- 0 failed checks.

## Recommended order

1. Add storage-state support to `main.py` and supply a verified GitLab state;
   verify 169/170/171 with a deterministic reference path. Also pre-authenticate
   shopping for 231-233. Do not start the 72-trial block until these paths pass.
2. Add the schema-driven public output contract and a one-turn submit validator;
   test on tasks 21, 23, 231, 124, and 281.
3. Enable retrieval completeness for array-schema tasks and add a pagination
   behavior test; compare 20 versus 40 steps only if the diagnosis still points
   to budget exhaustion after the fix.
4. Record the effective `RETRIEVAL_REPAIR` / output-contract flags and the
   model-visible observation hash in the trace. Current traces record the tool
   observation but not the assembled model prompt, so observation coverage is an
   upper bound, not proof of what the model saw.
