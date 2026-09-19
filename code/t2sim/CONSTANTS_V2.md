# constants.measured.v2.json: where every number comes from

Written 2026-09-11. Source: `experimental-results/guiexp_android/t16_build/constants_table.json`,
14 cells (7 AndroidWorld families x 2 models), sha256 prefix b9d4a841f2cc3940,
headline unit **price-weighted tokens (pw)**:
`pw = (prompt - cached) + r_c*cached + r_o*completion`, with r_c / r_o read off
the model's price sheet (GLM 0.20 / 3.33, DeepSeek 0.0318 / 3.0). Generated
deterministically from that file; nothing here is hand-entered. An earlier
version of this file was built from the same table's bill-based headline
(cost_usd / p_in); that table was regenerated and every number below is from
the price-weighted one.

## 1. What changed relative to constants.measured.json

| block | v1 | v2 |
|---|---|---|
| `cost_sets.android_glm` | 3 layouts (contacts, calendar, markor), cache-adjusted tokens, T1.2/T1.4 | 7 layouts named after the AndroidWorld families, price-weighted tokens, t16_build |
| `cost_sets.android_ds` | same | same |
| `cost_sets.openapps_glm`, `openapps_ds` | measured OpenApps wizard layouts | **unchanged** (this table does not measure them) |
| `trigger.gate_prior` | absent (engine default `"true"`) | `"pi"` |
| `trigger.spend_cap` | absent (default off) | `true`, `spend_cap_mult` 1.0 |
| `e3.cost_sets` | `[openapps_glm, android_glm]` | all four sets |
| `e4.cost_set`, `e5.cost_set` | `openapps_glm` | `android_glm` |
| `e5.gate_rates` | `[0.3, 0.6, 1.0]` | `[0.0, 0.3, 0.6, 1.0]` |

**Unit warning.** The android sets are in *price-weighted tokens* (the
t16_build headline unit). The openapps sets are in *cache-adjusted tokens*
(fresh + r*cached, charged per episode rather than per call). These are
different units. E3 reports both cost sets side by side; absolute token counts
must not be compared across them. Every output JSON still carries
`"units": "cache-adjusted tokens (fresh + r*cached)"` in its meta, which is now
wrong for the android cells; the engine never converts, so no result depends on
the string.

## 2. Per-layout mapping (one layout per AndroidWorld family)

| schema field | table field | notes |
|---|---|---|
| `c` | `headline.c` | mean price-weighted tokens per reactive **attempt**, per-model floor already subtracted |
| `L` | `headline.L_doc` | the doc arm. Documentation only: `cost_set_profiles` never reads it |
| `rho` | `headline.share_doc` = (c - L_doc)/c | documentation only. **Negative in 4 of 14 cells** (the doc arm costs more than a reactive episode), so the schema's `rho` bound [0,1] does not hold. The v1 file already violated it on two android layouts |
| `C` | `headline.C_with_repair` | the price of one compile ATTEMPT that passes. Substituted for cells that never passed, see section 3 |
| `p` | `headline.p` | post-repair gate passes / 5, admitted at 4/5 |
| `d` | `headline.d` | substituted where no deploy stage ran, see section 3 |
| `q0` | `headline.q` | same |
| `pi` | `headline.pi` | demonstration success rate of the k = 3 explorations; read by the pi-stratified gate prior |
| `C_fail_mult` | per-model ratio, section 4 | multiplier, not an absolute: the E4 price ladder overwrites `C` |
| `measured` | the unsubstituted readings | `C_with_repair`, `C_without_repair`, `C_eff`, `c_unsubtracted`, `s_arrival`, `nstar_marginal`, plus the admitted / pending flags. Nothing the substitutions overwrite is lost |

Per cost set: `r_cache` is the corrected price-sheet ratio `p_c / p_in`
(GLM 0.2, DeepSeek **0.0318**; v1 carried 0.318 for DeepSeek, which came from
the stale sheet whose DeepSeek `p_in` was low by 10x). It is reference-only.
`m` = 91 and `tau0` = 368 are carried over from the OpenApps `m_library2`
measurement: the t16_build table measures neither.

## 3. The four substitutions, and why

1. **GLM / FilesMoveFile is `admission_pending_redeploy`.** Its p = 1.0 is the
   initial k = 3 gate (5/5), which is the k the protocol deploys; `q` and `d`
   were not measured. As instructed, it counts as admitted and takes the GLM
   median over the four cells with a measured deploy stage: q = 0.0,
   d = 337.403. The regenerated table still carries no `redeploy` section for
   this cell, so the placeholders stand. Marked in the layout note.
2. **Cells that never passed the gate have no `q`, `d`.** 2 GLM and 5 DeepSeek
   cells never ran a deploy stage. They take the same per-model medians
   (GLM q 0.0 / d 337.403; DS q 0.0333 / d 416.164; q and d are rates and a
   per-use cost, and did not move with the unit change). At p = 0 the engine never
   serves a use from a program, so these two numbers only enter the trigger's
   threshold arithmetic.
3. **Cells that never passed the gate have no price for a compile that
   passes.** Their own `C_with_repair` is the bill for an attempt that FAILED,
   which is `C_fail`, not `C`. So `C` is the per-model median over admitted
   cells (GLM 916,335; DS 106,194) and the failed-attempt price comes from
   `C_fail_mult` as for every other family. Each cell's own measured
   `C_with_repair` is kept under `measured` and is within a factor of 2 of the
   model's `C_fail` for five of the seven such cells.
4. **`C_with_repair` under-counts, by the table's own convention.** Builder
   replies rejected before they became an arm were not recorded. Every `C` and
   `C_fail` here inherits that downward bias.

## 4. Per-model aggregates (the `per_model` block in each cost set)

| quantity | definition | GLM 5.3 Flash | DeepSeek v4 flash vision-exp |
|---|---|---|---|
| cells admitted | incl. the pending GLM cell | 5 / 7 | 2 / 7 |
| `p` per model | admitted / 7 | 0.7143 | 0.2857 |
| `c` | median over admitted cells | 233,195 | 63,397 |
| `c` (all 7) | median | 198,517 | 116,031 |
| `d` | median over cells with a measured deploy | 337.403 | 416.164 |
| `q` | same | 0.0 | 0.0333 |
| `L_doc` | median over all 7 | 167,063 | 88,515 |
| `C` | median `C_with_repair` over admitted cells | 916,335 | 106,194 |
| `C_fail` | median `C_with_repair` over NOT-admitted cells | 1,908,721 | 1,177,449 |
| `C_fail / C` | the ratio the engine takes | **2.0830** | **11.0877** |

The engine takes per-family constants (`cost_set_profiles` builds one profile
per layout, and `real_params` round-robins the profiles over the stream's
family names), so the per-family values above are what E3/E4/E5 actually run
on. The per-model scalars are recorded for the paper and are what a
single-number-per-model reader should use. `C_fail_mult` is the one place a
per-model scalar is written into every layout: the ratio, not the absolute, so
it survives the price ladder.

## 5. pi strata (`extras.pi_population_t16_build`)

| stratum | cells | admitted | mean p |
|---|---|---|---|
| pi = 1 | 7 | 5 | 0.657 |
| pi in [0.5, 1) | 2 | 1 | 0.500 |
| pi < 0.5 | 5 | 1 | 0.200 |

`sim.PI_PRIOR_TABLE` and `sim.PI_POPULATION` were hard-coded to a 6 / 3 / 5
split with prior means 0.8 / 0.5 / 0.2 and carried a comment saying to edit
them when the regenerated table landed. They are now 7 / 2 / 5 with prior
means 0.657 / 0.5 / 0.2 (the stratum's mean gate rate). Two assertions in
`tests/test_spend_cap_pi_prior.py` pinned the old numbers and were updated with
them; the full 180-test suite passes.

## 6. What this file does NOT contain

- Any measurement of `m` or `tau0` (carried over from OpenApps).
- Any update to the OpenApps cost sets (a different instrument, different unit).
- Any change to `cooldown` / `half_life`. methods-v3 section 3 removes the
  cooldown from Algorithm 1; `trigger.cooldown` is still `"inflate"` with
  `half_life` 120 here, as in v1. That change is not part of this task, and the
  E3/E4/E5 `ours` rows therefore still run the inflating threshold.
