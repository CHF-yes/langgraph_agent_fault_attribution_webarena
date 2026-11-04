# Development screening diagnosis

Generated: 2026-11-04T03:02:58.665526+00:00

Scope: 108 development trials only. Holdout trials were not used for cause attribution.

## Outcome

- Trials: 108
- Official success: 12
- Official failure: 96
- End modes: {'answer': 83, 'step_cap': 25}

## Primary causes

| Cause | Trials |
| --- | ---: |
| output_contract | 40 |
| retrieval_incomplete | 27 |
| observation_or_tool | 26 |
| environment_or_truth | 2 |
| reasoning_or_calculation | 1 |

## Multi-label causes

| Cause | Trials |
| --- | ---: |
| retrieval_incomplete | 61 |
| output_contract | 47 |
| observation_or_tool | 27 |
| termination | 25 |
| environment_or_truth | 2 |
| reasoning_or_calculation | 1 |

## By architecture

| Architecture | Trials | Success |
| --- | ---: | ---: |
| plan_execute | 54 | 4 |
| react | 54 | 8 |

## By task

| Task | Split | Site | Trials | Success | Step cap | Auth evidence | Primary causes |
| ---: | --- | --- | ---: | ---: | ---: | ---: | --- |
| 21 | development | shopping | 6 | 0 | 0 | 0 | output_contract=6, retrieval_incomplete=6 |
| 23 | development | shopping | 6 | 0 | 0 | 0 | output_contract=6, retrieval_incomplete=3 |
| 25 | development | shopping | 6 | 0 | 3 | 0 | retrieval_incomplete=6, observation_or_tool=4, termination=3, output_contract=2 |
| 124 | development | shopping | 6 | 0 | 0 | 0 | retrieval_incomplete=6 |
| 125 | development | shopping | 6 | 1 | 1 | 0 | retrieval_incomplete=5, termination=1 |
| 126 | development | shopping | 6 | 3 | 2 | 0 | retrieval_incomplete=3, termination=2 |
| 132 | development | gitlab | 6 | 1 | 0 | 3 | output_contract=5, observation_or_tool=2, environment_or_truth=1 |
| 133 | development | gitlab | 6 | 1 | 2 | 3 | output_contract=3, observation_or_tool=3, termination=2, environment_or_truth=1 |
| 134 | development | gitlab | 6 | 1 | 1 | 1 | output_contract=4, observation_or_tool=2, termination=1 |
| 169 | development | gitlab | 6 | 0 | 3 | 5 | observation_or_tool=3, retrieval_incomplete=3, termination=3, output_contract=2, reasoning_or_calculation=1 |
| 170 | development | gitlab | 6 | 0 | 6 | 6 | observation_or_tool=6, retrieval_incomplete=6, termination=6 |
| 171 | development | gitlab | 6 | 0 | 5 | 5 | retrieval_incomplete=6, observation_or_tool=5, termination=5, output_contract=1 |
| 231 | development | shopping | 6 | 0 | 0 | 0 | output_contract=6, retrieval_incomplete=1 |
| 232 | development | shopping | 6 | 0 | 0 | 0 | output_contract=6, retrieval_incomplete=2 |
| 233 | development | shopping | 6 | 0 | 0 | 1 | output_contract=6, observation_or_tool=1, retrieval_incomplete=1 |
| 279 | development | shopping | 6 | 0 | 0 | 0 | retrieval_incomplete=6 |
| 280 | development | shopping | 6 | 0 | 2 | 0 | retrieval_incomplete=6, termination=2, observation_or_tool=1 |
| 281 | development | shopping | 6 | 5 | 0 | 0 | retrieval_incomplete=1 |

## Integrity audit

- Trials found: 348 / expected 348
- Missing: 0; unexpected: 0
- Failed checks: 0

## Limitations

- Traces record tool observations and assistant content, not the full assembled model prompt.
- Observation coverage is an upper bound on what the model could have seen.
- No reference-path verification was performed in this offline pass.
- Automated cause labels need manual review for trials with only wrong-answer evidence.
