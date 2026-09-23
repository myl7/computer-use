# Matched-budget controls: complete results

The frozen study completed all 612 conditions. Every new policy uses the same
0.25 global cost margin, reservations, and manifest controls as PACE. No
policy or threshold was changed after inspecting outcomes.

## Cost relative to agent-only service

| Policy | Grid 1 mean | Grid 2 mean | Recorded arrivals mean | Recorded arrivals 95% CI |
|---|---:|---:|---:|---:|
| PACE | 0.909518678 | 0.907032630 | 0.781495419 | [0.758530342, 0.803368948] |
| Protected eager + allowance | 0.922572703 | 0.924996848 | 0.782389131 | [0.759288904, 0.801861054] |
| Protected arrival-10 + allowance | 0.913933421 | 0.916268017 | 0.780866860 | [0.758125705, 0.800451313] |
| Protected projected + allowance | 0.911123290 | 0.907379215 | 0.781660491 | [0.758896276, 0.803471361] |
| Protected direct count | 0.908977965 | 0.906602371 | 0.781935510 | [0.758575544, 0.803550955] |
| Protected optimistic history | 1.008625945 | 0.995457039 | 0.851237955 | [0.825984860, 0.874306845] |
| Protected optimistic once | 0.999802373 | 0.982600400 | 0.847321594 | [0.818762924, 0.875595520] |

## Paired cost relative to PACE on recorded arrivals

A ratio below one means the control is cheaper. These are means of
condition-wise ratios, not ratios of aggregate means. Intervals use 2,000
paired bootstrap resamples of the same ten repetitions, with shared indices
across policies and all twelve conditions.

| Control | Mean control / PACE | 95% CI | Cheaper / tied / dearer conditions |
|---|---:|---:|---:|
| Protected eager + allowance | 1.000220724 | [0.994673026, 1.004793680] | 1 / 0 / 11 |
| Protected arrival-10 + allowance | 0.999067411 | [0.993599230, 1.003648154] | 2 / 0 / 10 |
| Protected projected + allowance | 1.000451590 | [0.999761578, 1.001202246] | 4 / 3 / 5 |
| Protected direct count | 1.000346739 | [0.999868124, 1.000830994] | 7 / 1 / 4 |
| Protected optimistic history | 1.081444393 | [1.073305508, 1.090403226] | 1 / 0 / 11 |
| Protected optimistic once | 1.114233995 | [1.075789823, 1.163660440] | 1 / 0 / 11 |

The four new controls are close to PACE on the recorded-arrival conditions.
Every corresponding paired interval covers one. Direct count has slightly
lower aggregate cost in both grids and slightly higher cost on the logs.
Arrival-10 plus allowance has slightly lower aggregate log cost. These
results do not establish a distinct cost advantage of the specific PACE
rate-and-age projection over comparably protected economic rules. The
retained optimistic-history and optimistic-once alternatives cost more on
the recorded logs, but they do not remove this limitation.

## Verification and artifacts

- All 612 condition specifications and 4,920 input realizations are retained.
- All stream, mapping, and profile hashes match the original records.
- All 4,920 full PACE rows exactly match the previously stored outcome rows.
- All 34,440 copied reference rows are unchanged.
- All 59,040 raw rows pass independent cost-component and ratio checks.
- The shared serving loop is an exact source match after removing the two added observed proposal inputs.
- All four new controls satisfy the implemented budget within its existing floating-point tolerance, with zero violations.

`config.json` contains frozen definitions and source hashes.
`cells/*.json` contains all raw repetitions.
`evidence.json` has `cells[group]` lists for `original_grid`, `fresh_grid`,
and `real_arrivals`. Each entry contains key, model, pattern, source path,
source SHA-256, original source path/hash, repetitions, and a policies map.
Each policy has mean_cost, ratio_agent, ratio_pace, mean_quality,
mean_attempts, and max_prefix_ratio. `summary.json` holds all aggregates and
paired intervals. `verification.json` records the independent checks.
