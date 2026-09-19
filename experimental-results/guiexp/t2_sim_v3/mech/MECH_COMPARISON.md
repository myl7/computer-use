# W5: Mechanism re-selection under constants v3 + full-B̂ trigger (app:mech recheck)

Rerun of the paper's Appendix "Mechanism Selection" (app:mech) candidate list under
`constants.measured.v3.json` (three datasets, 7 Android families each; android_glm 7/7
admitted, C_fail_mult 1.0; android_ds 4/7 admitted, C_fail_mult 10.5094) with the
W1.5 trigger buy formula (default `full`: B̂ = C + (1/p̂−1)·C_fail).

- Server: `cs11369a:~/app/guiexp/sim_w5/` (self-contained mirror; local t2sim untouched).
- Experiments rerun exactly as the scripts define them: `run.py --exp E5` (the E5
  mechanism-stress blocks a/b/c/d/f are the app:mech source; 37 cells each,
  reps 20, seed 7) on cost sets `android_glm`, `android_ds`, plus two controls; and
  `sweep_env_fragility.py` (E11 rows `ours_spend_cap` / `ours_cap_epoch` /
  `ours_cap_realized`) for the spend-cap three-form worst-cell comparison.
- Every number is mean cumulative tokens / clairvoyant reference, averaged over the
  block's cells (the paper's aggregation, per numbers_manifest `E5_*` entries).

## Provenance and the run_revision.py v3 pin

`run_revision.py:18` in the server copy only (one-line diff, minimal change, option "换成 v3"):

```diff
-CONSTANTS=Path(__file__).with_name('constants.measured.v2.json')
+CONSTANTS=Path(__file__).with_name('constants.measured.v3.json')
```

- The pin is numerically inert for the revision sim: `make_config` reads only
  `constants['streams']` from that file, and v2-final and v3 stream blocks are
  dict-identical (verified). The full rerun (`out/revision_v3`, 70 cells, reps 20,
  seed 20260913) reproduces `revision_20260913` to within 0.63% on 30 of 490
  summary fields; the drift is the `revision_sim.py` edit lineage after Sep 13
  (source_sha256 in both configs records it), not the constants swap.
- Controls validating the v3 rerun machinery:
  - E5 on `openapps_glm` under v3 vs under v2-final: **bitwise identical** (max
    relative deviation 0.0 over all cells/variants/fields) — the W1 constants swap
    alone moves nothing on an untouched cost set.
  - The recorded `E5_v2.json` (the old app:mech source) itself came from a
    superseded v2-draft constants file (fingerprint `c12a834387f21d8f`, no longer on
    disk; final v2 is `a4e38c482b6e10cf`). The old-vs-new tables below therefore mix
    constants era and code era; the control column `oa_v3` isolates part of it.

## (a) Verification estimate × spend cap — E5 `f_gate_prior`

Mean rel_clairvoyant over the block's 8 cells (2 prices × gate {0.0, 0.3, 0.6, 1.0}):

| variant | old (paper) | oa_v3 control | android_glm v3 | android_ds v3 |
|---|---:|---:|---:|---:|
| true p handed to trigger | 1.24 | 1.13 | **1.57** | **1.02** |
| add-one estimate | 1.47 | 1.16 | **1.72** | **1.68** |
| pi-stratified prior | 1.68 | 1.13 | **1.81** | **1.72** |
| add-one + cap | 1.47 | 1.16 | 1.72 | 1.68 |
| pi prior + cap | 1.68 | 1.13 | 1.81 | 1.72 |

- **Held**: trueP < add-one < pi-prior ordering, on both new datasets; the cap is
  bit-identical to no-cap in this block on both (add_one==add_one_cap and
  pi_prior==pi_prior_cap exactly), as before.
- Per-cell: on android_glm at gate=1.0/native the pi prior is now much worse
  (2.27 vs add-one 1.53) but at gate=0.3 it is the best (2.74 vs 2.85 native;
  1.31 vs 1.47 at 5M) — the pi strata (OsmAndMarker pi=0.125) misprice first
  attempts at high gate, help at low gate. On android_ds the gap trueP vs add-one
  is now huge at native/gate=1.0 (1.18 vs 4.10): knowing p=0 families exist is
  worth ~3.5×.

### Spend-cap three forms — E11 worst cell (max over sweep cells of row / better naive rule)

| cap form | old GLM | GLM v3 | old DS | DS v3 |
|---|---:|---:|---:|---:|
| projected-only (`horizon`) | 1.63 | **1.76** | 1.58 | **1.78** |
| break-reset (`epoch`) | 1.32 | **1.38** | 1.38 | **1.38** |
| credit realized saving (`realized`) | 1.18 | **1.15** | 1.38 | **1.38** |

- **Held**: realized ≤ epoch < projected-only on both models; "credit the realized
  saving" stays the deployed choice (strictly best on GLM 1.15, tied-best on DS).
- Grid note: v3 identifies no GLM failure premium (7/7 admitted ⇒ C_fail_mult 1.0),
  so the GLM sweep ran with `--c-fail-ratio 1.0` and its C_fail axis collapsed
  (30 grid points / 240 cells vs the old 50/400). DS ran the full axis with the
  measured 10.5094 (old run used the v2 value 11.0877). Worst-cell locations moved
  (GLM realized: bpi2019/native h=0.1; both epoch ties at wiki_A cells).
- Cross-validation: these sweeps are bitwise identical (max deviation 0.0 over
  720 + 1200 row-cells) to the parallel W4 sharded runs in
  `../E11_env_fragility_android_{glm,ds}.json` — same seed/config reached
  independently by two agents.

## (b) Arrival prior — E5 `c_prior`

Mean over 9 cells (3 synthetic prices + 2 real streams × 3 prices):

| prior | old | oa_v3 | android_glm v3 | android_ds v3 |
|---|---:|---:|---:|---:|
| population | 1.43 | 1.30 | **1.49** | **1.08** |
| Gamma(1,20) | 1.54 | 1.31 | **1.70** | **1.08** |
| Gamma(1,5) | 1.64 | 1.30 | **1.68** | **1.45** |

- **Held on android_glm**: population clearly best (all synthetic cells separate at
  z>13; Gamma(1,20) pays +8–46% per cell).
- **Weakened to a tie on android_ds**: population 1.082 vs Gamma(1,20) 1.080 —
  Gamma(1,20) is marginally better on the three synthetic cells (0.99–1.01, z>2),
  the two priors tie on the real streams. Gamma(1,5) stays worst everywhere.
  "No fixed prior near the front on both halves" no longer holds strictly:
  Gamma(1,20) is at the front on DS.

## (c) Projection horizon — E5 `d_horizon`

Mean over 8 cells (bursty / regime_shift / 30k bursty / bpi2019 heldout × native/5M):

| horizon | old | oa_v3 | android_glm v3 | android_ds v3 |
|---|---:|---:|---:|---:|
| deployment age (doubling) | 1.34 | 1.32 | **1.47** | **1.55** |
| + lifetime cap | 1.48 | 1.68 | **4.32** | **1.10** |
| fixed 60-step window | 1.79 | 1.68 | **5.22** | **1.09** |
| fixed + lifetime cap | 1.79 | 1.68 | 5.22 | 1.09 |

- **Held on android_glm**: deployment age beats the fixed window, now by more
  (1.47 vs 5.22). The fixed window's worst cell is again the fragmented
  30,000-arrival stream (30.9× at 5M: the 60-step projection never clears the 5M
  buy price, so the hot families are never compiled — never_compiled_hot 0.80 —
  and the stream pays reactive c≈300k on ~30k arrivals).
- **Flipped on android_ds**: fixed 60 ≈ capped doubling (1.09) beat plain
  deployment age (1.55); the worst doubling cell is bpi2019/5M (3.89 vs capped 1.15).
- **Flipped (both models)**: the lifetime cap is no longer inert — old claim "the
  capped variant coincides with its base at every audited price" does not survive
  v3: on android_glm the cap turns the 30k/5M cell from 1.5× into 24.6×; on
  android_ds the cap is what fixes bpi2019 (3.89 → 1.15).

## (d) Retired mechanisms — E5 `a_cooldown`, `b_decay`

Cooldown after a failed attempt, mean over 4 gate-rate cells:

| form | old | oa_v3 | android_glm v3 | android_ds v3 |
|---|---:|---:|---:|---:|
| fixed block of 3 | 2.03 | 1.06 | **1.86** | **2.01** |
| price doubling per failure | 2.02 | 1.15 | **2.20** | **2.04** |
| blacklist | 2.16 | 2.27 | **3.19** | **2.23** |

- **Held**: blacklist is ruinous at lowered verification rates (4.2–5.2 on GLM,
  1.5–1.9 on DS at gate 0.3/0.6); it "wins" only at gate=0.0, where giving up
  immediately is trivially right. Mild reorder: fixed-3 now edges out price-doubling
  on both new datasets (old: 2.03 vs 2.02, a tie). Retired either way.

Arrival-counter decay, mean over 8 cells; worst within-cell spread across the four
half-lives: GLM ≤ 0.035, DS ≤ 0.020:

| half-life | old | android_glm v3 | android_ds v3 |
|---|---:|---:|---:|
| none (T_inf) | 1.54 | 4.55 | 1.116 |
| 120 | 1.52 | 4.55 | 1.116 |
| 60 | 1.52 | 4.55 | 1.117 |
| 30 | 1.54 | 4.55 | 1.119 |

- **Held**: differences inside seeding noise on both datasets (the GLM block mean
  4.55 is one 30k/5M cell worth 24.6× shared identically by all four variants).
  Decay stays dropped.

## Stress-condition separation check ("咬得动")

Stress = raised compile price (5M) × lowered verification rate (gate 0.6/0.3),
paired seeds, z = |Δmean|/paired SE (SEP = z>2):

- android_glm: the (a) comparisons separate in 5/8 cells (add-one vs pi-prior z up
  to 11.3 at native/gate=1.0; trueP vs add-one z up to 49.9); (b) separates in 9/9;
  (c) separates in 5/8. **Both stress axes bite.** Because v3 gives GLM p_pop=1.0
  (initial pass-through), the *lowered verification rate* is the axis that creates
  the interesting regime for GLM — at gate=1.0 the block is near-inert for the
  estimator question (everyone compiles), and at gate 0.3–0.6 the estimator choice
  moves costs by 5–25%.
- android_ds: separation concentrates at **native price + lowered gate** (trueP vs
  add-one z=4.1 at gate 0.6, z=32.9 at gate 1.0; add-one vs pi z=2.9). At the 5M
  price the DS cells are inert (all estimators identical: nothing is worth buying),
  so for DS the *price* axis saturates and the *verification-rate* axis is the one
  that bites.
- Conclusion: the app:mech stress conditions still produce separated comparisons
  for every candidate question, but the biting axis differs by model — lowered
  verification rate for GLM (its p_pop=1.0 baseline makes native gate a
  no-decision regime), price×verification jointly for DS (5M alone makes every
  rule abstain).

## Conclusions kept vs flipped (vs the published app:mech text)

Kept:
1. (a) add-one over pi-stratified (block means, both datasets); true-p reference
   remains cheapest; cap inert inside the (a) block.
2. Cap form: credit realized saving (GLM 1.15 < epoch 1.38 < projected 1.76; DS
   epoch = realized 1.38, both < projected 1.78).
3. (b) population prior on GLM; Gamma(1,5) worst everywhere.
4. (c) deployment age over fixed window on GLM (now by a wider margin, same worst
   cell type: fragmented 30k stream).
5. (d) blacklist ruinous at lowered rates; decay within noise.

Flipped / weakened (needs paper attention):
1. (c) on android_ds the fixed 60-step window and capped doubling beat plain
   deployment age (1.09 vs 1.55; bpi2019/5M 1.15 vs 3.89) — the horizon conclusion
   is now model-dependent, not universal.
2. (c) the lifetime cap is no longer inert at audited prices (GLM 30k/5M: 1.5× →
   24.6× with the cap; DS bpi2019: 3.89× → 1.15 with the cap). Direction of the
   cap's effect is opposite across the two models.
3. (b) on android_ds population and Gamma(1,20) tie (1.082 vs 1.080) — "no fixed
   prior near the front on both halves" no longer holds.
4. (d) among cooldown forms fixed-3 now beats price-doubling on both datasets
   (was a tie); retired either way.
5. Scale: absolute ratios moved because v3's android c/C values (cache-adjusted,
   floor-subtracted) differ from the openapps-era draft; the E5_v2 source constants
   file itself no longer exists on disk.

## Outputs

Mac: `/Users/myl/app/computer-use/experimental-results/guiexp/t2_sim_v3/mech/`
- `E5_v3w5_android_glm.json` (37 cells) — primary new (a)(b)(c)(d), android_glm
- `E5_v3w5_android_ds.json` (37 cells) — primary new (a)(b)(c)(d), android_ds
- `E5_v3w5_openapps_glm.json`, `E5_v3w5_openapps_v2final.json` — controls
- `E11_env_fragility_v3w5_glm.{json,csv,md}` (240 cells) — cap three forms, GLM
- `E11_env_fragility_v3w5_ds.{json,csv,md}` (400 cells) — cap three forms, DS
- `revision_v3/` (70 cells) — revision sim under the v3-pinned runner
- `_provenance_numbers.json` — the block-mean table above, machine-readable
- `_server_logs/` — server stdout of every run

Server: `cs11369a:~/app/guiexp/sim_w5/` (code mirror with the one-line
run_revision.py pin; `out/mech`, `out/revision_v3`, `logs/`).

Not rerun (out of W5 scope, existing artifacts only): E5's `gamma_05_10` prior
variant (dropped from the current builder), the E11 mixed-regime block
(`--mixed`, where the population gate-prior candidate lives), and E5's
`bpi_reps`-scaled heldout variants beyond the builder defaults.
