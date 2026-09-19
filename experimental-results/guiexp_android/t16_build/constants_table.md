# t16_build constants table

Unit: price-weighted tokens (`pw`), `(prompt - cached) + r_c * cached + r_o * completion`, with r_c and r_o from the OpenRouter list prices fetched 2026-09-11 from https://openrouter.ai/api/v1/models.  `c` is the mean price-weighted cost of one exploration ATTEMPT (every attempt, reflection retries and their reflection calls included), minus the per-model global floor; `L_doc` is floor-subtracted too.  `gate` is the final program's margin, its bindings passed over 5 on the post-repair gate (or, once a cell has been redeployed, the redeploy gate); a program is admitted at 4 of 5.  N*_marg = C_eff / s_arrival and N*_build = (exploration total + C_eff) / s_arrival, with C_eff = C_with_repair for an admitted cell and inf otherwise: one compile attempt is observed per cell, so `gate` is the admitted program's margin and not the probability that an attempt is admitted, and the reading that charged (1/gate - 1) failed attempts is kept in the JSON as *_gate_margin.  The expected price for the NEXT family is the population reading under each table.  s_arrival = (1 - q) c - d.  `—` means undefined, `inf` means the saving was not positive or the cell was not admitted, `pending` marks a cell whose admissible program the repair loop discarded and whose redeploy run is scheduled.  d comes from the deployment bill, the one stage of these cells with no prompt/cached/completion split.  The bill-based unit `usd_over_p_in` with its per-stage pw/USD diagnostic, the raw unit, the deterministic cache-adjusted unit and the 5-of-5 and protocol admission readings are in constants_table.json.

## deepseek/deepseek-v4-flash-vision-exp  (r_c = 0.0318, r_o = 3.00, floor = 2290 raw tokens)

| family | pi | c | C_with_repair | refine | p_k1 | p_k2 | p_k3 | p_after | admitted | q | d | L_doc | share_doc | share_prog | N*_marg | N*_build | cost_usd |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ContactsAddContact | 1.00 | 65.0k | 143.9k | 0 | 1.00 | 1.00 | 1.00 | 1.00 | yes | 0.033 | 430 | 64.5k | 0.009 | 0.960 | 2.305 | 5.539 | 0.1528 |
| FilesMoveFile | 1.00 | 51.6k | 1,709.1k | 5 | 0.00 | 0.80 | 0.00 | — | no | — | — | 40.6k | 0.213 | — | inf | inf | 0.5887 |
| MarkorCreateNote | 0.75 | 154.6k | 1,177.4k | 3 | 0.00 | 0.00 | 0.00 | — | no | — | — | 166.1k | -0.075 | — | inf | inf | 0.7227 |
| MarkorDeleteNote | 1.00 | 10.8k | 68.5k | 0 | 1.00 | 1.00 | 1.00 | 1.00 | yes | 0.033 | 402 | 23.8k | -1.212 | 0.929 | 6.847 | 10.762 | 0.0839 |
| OsmAndFavorite | 0.33 | 49.0k | 1,033.9k | 3 | 0.00 | 0.00 | 0.00 | — | no | — | — | 141.5k | -1.888 | — | inf | inf | 0.4600 |
| OsmAndMarker | 0.14 | 152.8k | 2,502.6k | 3 | 0.00 | 0.00 | 0.00 | — | no | — | — | 88.5k | 0.421 | — | inf | inf | 1.1396 |
| SimpleCalendarAddOneEvent | 1.00 | 146.3k | 1,100.3k | 3 | 0.20 | 0.00 | 0.00 | — | no | — | — | 223.7k | -0.529 | — | inf | inf | 0.6584 |

Per-success appendix (M1 secondary reading).

| family | pi | c_attempt | c_success | reactive/success | program/success | s_success | share_success | N*_success |
|---|---|---|---|---|---|---|---|---|
| ContactsAddContact | 1.00 | 65.0k | 67.3k | 65.0k | 2.6k | 62.4k | 0.960 | 2.305 |
| FilesMoveFile | 1.00 | 51.6k | 53.9k | 51.6k | — | — | — | inf |
| MarkorCreateNote | 0.75 | 154.6k | 62.0k | 206.1k | — | — | — | inf |
| MarkorDeleteNote | 1.00 | 10.8k | 13.1k | 10.8k | 0.8k | 10.0k | 0.929 | 6.847 |
| OsmAndFavorite | 0.33 | 49.0k | 40.9k | 147.0k | — | — | — | inf |
| OsmAndMarker | 0.14 | 152.8k | 37.6k | 1,069.5k | — | — | — | inf |
| SimpleCalendarAddOneEvent | 1.00 | 146.3k | 148.6k | 146.3k | — | — | — | inf |

7 cells, 2 deployed, 2 admitted at 4/5, 0 pending redeploy, 0 redeployed, 5 declared unautomatable; total $3.8062.

| quantity | n | mean | median |
|---|---|---|---|
| headline.pi | 7 | 0.7466 | 1.0000 |
| headline.c | 7 | 90.0k | 65.0k |
| headline.C_with_repair | 7 | 1,105.1k | 1,100.3k |
| verification.refinements | 7 | 2.4286 | 3.0000 |
| gate.p_initial_k3 | 7 | 0.2857 | 0.0000 |
| gate.p_after_repair | 2 | 1.0000 | 1.0000 |
| headline.p | 7 | 0.2857 | 0.0000 |
| headline.q | 2 | 0.0333 | 0.0333 |
| headline.d | 2 | 0.4k | 0.4k |
| headline.L_doc | 7 | 107.0k | 88.5k |
| headline.share_doc | 7 | -0.4374 | -0.0749 |
| headline.share_prog | 2 | 0.9447 | 0.9447 |
| headline.nstar_marginal | 2 | 4.5756 | 4.5756 |
| headline.nstar_build | 2 | 8.1506 | 8.1506 |
| cost_usd.total_summed | 7 | 0.5437 | 0.5887 |

## z-ai/glm-5.3-flash  (r_c = 0.2000, r_o = 3.33, floor = 5090 raw tokens)

| family | pi | c | C_with_repair | refine | p_k1 | p_k2 | p_k3 | p_after | admitted | q | d | L_doc | share_doc | share_prog | N*_marg | N*_build | cost_usd |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ContactsAddContact | 1.00 | 98.7k | 41.0k | 0 | 1.00 | 1.00 | 1.00 | 1.00 | yes | 0.000 | 179 | 89.8k | 0.091 | 0.998 | 0.416 | 3.577 | 0.0649 |
| FilesMoveFile | 0.60 | 233.2k | 1,176.7k | 3 | 0.00 | 0.00 | 1.00 | 1.00 | yes | 0.067 | 450 | 212.6k | 0.088 | 0.931 | 5.418 | 10.903 | 0.4040 |
| MarkorCreateNote | 0.40 | 135.7k | 2,412.6k | 3 | 0.00 | 0.00 | 0.00 | — | no | — | — | 281.5k | -1.075 | — | inf | inf | 0.5354 |
| MarkorDeleteNote | 1.00 | 33.7k | 39.0k | 0 | 0.00 | 0.00 | 1.00 | 0.80 | yes | 0.200 | 200 | 40.6k | -0.204 | 0.794 | 1.456 | 5.805 | 0.0383 |
| OsmAndFavorite | 0.14 | 198.5k | 1,404.9k | 3 | 0.00 | 0.00 | 0.00 | — | no | — | — | 167.1k | 0.158 | — | inf | inf | 0.4077 |
| OsmAndMarker | 0.12 | 279.8k | 966.9k | 2 | 0.00 | 0.40 | 0.60 | 1.00 | yes | 0.000 | 475 | 118.2k | 0.578 | 0.998 | 3.461 | 11.620 | 0.5654 |
| SimpleCalendarAddOneEvent | 1.00 | 313.8k | 916.3k | 2 | 0.00 | 0.20 | 0.00 | 0.80 | yes | 0.000 | 558 | 260.2k | 0.171 | 0.998 | 2.925 | 5.979 | 0.3957 |

Per-success appendix (M1 secondary reading).

| family | pi | c_attempt | c_success | reactive/success | program/success | s_success | share_success | N*_success |
|---|---|---|---|---|---|---|---|---|
| ContactsAddContact | 1.00 | 98.7k | 103.8k | 98.7k | 0.2k | 98.5k | 0.998 | 0.416 |
| FilesMoveFile | 0.60 | 233.2k | 218.4k | 388.7k | 16.4k | 372.2k | 0.958 | 3.161 |
| MarkorCreateNote | 0.40 | 135.7k | 54.7k | 339.2k | — | — | — | inf |
| MarkorDeleteNote | 1.00 | 33.7k | 38.8k | 33.7k | 6.9k | 26.8k | 0.794 | 1.456 |
| OsmAndFavorite | 0.14 | 198.5k | 81.7k | 1,389.6k | — | — | — | inf |
| OsmAndMarker | 0.12 | 279.8k | 63.2k | 2,238.7k | 0.5k | 2,238.2k | 1.000 | 0.432 |
| SimpleCalendarAddOneEvent | 1.00 | 313.8k | 318.9k | 313.8k | 0.6k | 313.3k | 0.998 | 2.925 |

7 cells, 5 deployed, 5 admitted at 4/5, 0 pending redeploy, 1 redeployed, 3 declared unautomatable; total $2.4114.

| quantity | n | mean | median |
|---|---|---|---|
| headline.pi | 7 | 0.6097 | 0.6000 |
| headline.c | 7 | 184.8k | 198.5k |
| headline.C_with_repair | 7 | 993.9k | 966.9k |
| verification.refinements | 7 | 1.8571 | 2.0000 |
| gate.p_initial_k3 | 7 | 0.5143 | 0.6000 |
| gate.p_after_repair | 5 | 0.9200 | 1.0000 |
| headline.p | 7 | 0.6571 | 0.8000 |
| headline.q | 5 | 0.0533 | 0.0000 |
| headline.d | 5 | 0.4k | 0.5k |
| headline.L_doc | 7 | 167.1k | 167.1k |
| headline.share_doc | 7 | -0.0276 | 0.0906 |
| headline.share_prog | 5 | 0.9440 | 0.9982 |
| headline.nstar_marginal | 5 | 2.7353 | 2.9251 |
| headline.nstar_build | 5 | 7.5769 | 5.9792 |
| cost_usd.total_summed | 7 | 0.3445 | 0.4040 |

## Flagged records

- deepseek_deepseek-v4-flash-vision-exp / ContactsAddContact: 1 exploration call(s) sent a shorter prompt than the previous call (charged fresh in full by the deterministic unit)
- deepseek_deepseek-v4-flash-vision-exp / MarkorCreateNote: build.json carries no total_cost_usd (budget stop); the sum here is from the stage records
- deepseek_deepseek-v4-flash-vision-exp / SimpleCalendarAddOneEvent: build.json carries no total_cost_usd (budget stop); the sum here is from the stage records
- z-ai_glm-5.3-flash / FilesMoveFile: gate_per_k k=3 passed 5/5 yet verification declared unautomatable
- z-ai_glm-5.3-flash / FilesMoveFile: admission read off the redeploy gate (5/5, artifact ../experimental-results/guiexp_android/t16_build/z-ai_glm-5.3-flash/FilesMoveFile/artifact_k3_code.py); verification.unautomatable stays true and gate_after_repair stays null by design
- z-ai_glm-5.3-flash / FilesMoveFile: 1 exploration call(s) sent a shorter prompt than the previous call (charged fresh in full by the deterministic unit)
- z-ai_glm-5.3-flash / MarkorDeleteNote: gate_after_repair disagrees with gate_per_k k=3 although no refinement happened (the same program was re-gated with a different outcome)

## Not computable (deterministic secondary unit only)

- verification.resume_episodes, deterministic unit only (per-call token records are not on disk; the headline unit needs only the stage bill)
