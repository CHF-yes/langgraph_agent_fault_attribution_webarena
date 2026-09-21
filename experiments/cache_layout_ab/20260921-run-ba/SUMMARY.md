# Prompt-cache layout A/B (20260921T120006Z)

- model: `deepseek-v4-pro` (profile `deepseek_v4_pro`)
- order: `ba`, tools: 9, steps: 5, repeats: 2, salt: `run-ba`
- text-identity assertion: PASS (identical text; role/boundary equivalence is **not** established by this tool)

| layout | phase | calls | prompt | hit | miss | hit% | mean latency s |
|---|---|---|---|---|---|---|---|
| A | cold | 5 | 46,667 | 5,504 | 41,163 | 11.8% | 3.779 |
| A | warm1 | 5 | 46,667 | 46,336 | 331 | 99.3% | 3.293 |
| B | cold | 5 | 46,668 | 10,112 | 36,556 | 21.7% | 4.324 |
| B | warm1 | 5 | 46,668 | 46,336 | 332 | 99.3% | 3.434 |

## Limits

- Request-level measurement only. It does not show that either layout preserves agent actions, termination, or task success.
- Identical text is not identical roles, boundaries, or `tools` payload; moving an observation changes what the model sees first.
- Read the `cold` rows: warm resends reuse an identical prompt and hit by construction, so they cannot separate the layouts.
- Absolute hit rates are only interpretable together with `--salt`; an unsalted run can inherit warmth from earlier traffic.
