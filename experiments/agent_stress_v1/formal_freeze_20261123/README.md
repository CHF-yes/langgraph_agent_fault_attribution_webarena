# Formal freeze: 24 tasks / 864 trials

- Roster: **24** tasks, **14** templates, max 2 per template.
- Difficulty: easy 8, medium 10, hard 6.
- Supplements: 69, 149, 168, 206; fault pilot retained: 28, 228, 314.
- Plan: **864** cells in **288** blocks (24 x 2 models x 2 architectures x 3 seeds x 3 conditions).
- Plan SHA-256: `dd6fa60e58a09866b7dd7927b87a6177d5c5938c4d2ab294b8bcf53a9c12cd4d`
- `--check`: **PASS** (no blockers).

## Reference acceptance

| Task | Site | Tpl | Difficulty | Normal steps | Recovery steps | Injections | Passed |
| ---: | --- | ---: | --- | ---: | ---: | ---: | --- |
| 27 | reddit | 33 | easy | 3 | 13 | 1 | yes |
| 28 | reddit | 33 | medium | 3 | 13 | 1 | yes |
| 48 | shopping | 197 | easy | 5 | 15 | 1 | yes |
| 49 | shopping | 197 | easy | 5 | 15 | 1 | yes |
| 66 | reddit | 17 | medium | 2 | 12 | 1 | yes |
| 69 | reddit | 17 | hard | 3 | 13 | 1 | yes |
| 148 | shopping | 155 | easy | 7 | 17 | 1 | yes |
| 149 | shopping | 155 | hard | 7 | 17 | 1 | yes |
| 163 | shopping | 136 | medium | 2 | 12 | 1 | yes |
| 164 | shopping | 136 | easy | 2 | 12 | 1 | yes |
| 168 | gitlab | 289 | easy | 2 | 12 | 4 | yes |
| 188 | shopping | 214 | medium | 5 | 15 | 1 | yes |
| 190 | shopping | 214 | easy | 5 | 15 | 1 | yes |
| 206 | gitlab | 320 | hard | 2 | 12 | 1 | yes |
| 227 | shopping | 370 | medium | 3 | 13 | 1 | yes |
| 228 | shopping | 370 | medium | 3 | 13 | 1 | yes |
| 294 | gitlab | 329 | medium | 3 | 13 | 1 | yes |
| 295 | gitlab | 329 | hard | 3 | 13 | 1 | yes |
| 305 | gitlab | 321 | hard | 2 | 12 | 1 | yes |
| 314 | gitlab | 324 | easy | 2 | 12 | 1 | yes |
| 316 | gitlab | 324 | medium | 2 | 12 | 1 | yes |
| 384 | shopping | 666 | hard | 4 | 14 | 1 | yes |
| 784 | gitlab | 316 | medium | 2 | 12 | 1 | yes |
| 785 | gitlab | 316 | medium | 2 | 12 | 1 | yes |

## Frozen analysis

- Primary outcome: official binary success; sustained minus control is the fault effect;
  compare that effect across the two models and two architectures.
- Secondary: single minus control, injections, steps, token cost, human-adjudicated
  budget exhaustion.
- ITT denominator: legal non-trigger, detour, wrong answer and budget exhaustion stay
  in the allocated arm.
- Repeats are averaged within task first, then within template; uncertainty is
  clustered by template.
- Formal report covers all 24 tasks; a 21-task sensitivity analysis excludes 28, 228, 314.

## Artifacts

- `roster.json`, `plan.json`, `authorization.candidate.json`, `freeze.json`, `check.json`
- Per-task evidence: `reference/taskNN/{normal,recovery}/`
- `acceptance.json` / `acceptance.md`: the 24-task endpoint table.

No formal agent trial was started by this freeze.
