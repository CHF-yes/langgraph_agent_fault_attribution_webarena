# Prompt-cache layout A/B (20260921T115928Z)

- model: `deepseek-v4-pro` (profile `deepseek_v4_pro`)
- order: `ab`, tools: 9, steps: 5, repeats: 2, salt: `run-ab`
- text-identity assertion: PASS (identical text; role/boundary equivalence is **not** established by this tool)

| layout | phase | calls | prompt | hit | miss | hit% | mean latency s |
|---|---|---|---|---|---|---|---|
| A | cold | 5 | 46,662 | 5,504 | 41,158 | 11.8% | 1.455 |
| A | warm1 | 5 | 46,662 | 46,336 | 326 | 99.3% | 1.353 |
| B | cold | 5 | 46,663 | 10,112 | 36,551 | 21.7% | 2.036 |
| B | warm1 | 5 | 46,663 | 46,336 | 327 | 99.3% | 1.112 |

## Limits

- Request-level measurement only. It does not show that either layout preserves agent actions, termination, or task success.
- Identical text is not identical roles, boundaries, or `tools` payload; moving an observation changes what the model sees first.
- Read the `cold` rows: warm resends reuse an identical prompt and hit by construction, so they cannot separate the layouts.
- Absolute hit rates are only interpretable together with `--salt`; an unsalted run can inherit warmth from earlier traffic.
