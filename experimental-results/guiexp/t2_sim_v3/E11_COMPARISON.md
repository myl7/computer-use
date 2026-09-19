# E11 regime sweep rerun under constants.measured.v3 + full B_hat — comparison vs the archived sweep

Generated 2026-09-19 (W4). Full-grid rerun of `computer-use/t2sim/sweep_env_fragility.py`
on cs11369a (144 cores), sharded one grid point per process, with

- `constants.measured.v3.json` (fingerprint `78c184013b94e8b2`): android_glm /
  android_ds rebuilt to the 7-family three-dataset table (W1),
- the W1.5 default `buy_formula="full"`: B_hat = C + (1/p_hat - 1) * C_fail
  (the archived runs predate W1.5 and priced attempts with the narrow C/p_hat),
- `--c-fail-ratio` at each model's own measured multiple: **10.5094** for
  android_ds (`per_model.C_fail_over_C_ratio`), **1.0** for android_glm
  (GLM has no rejected compilation in the 2026-09-18 table, so the multiple is
  unidentified and set to 1 — W1),

everything else as the script defines it: 10 reps, seed 7, 2000 bootstrap
resamples per cell, streams wiki_A / wiki_B / sepsis / bpi2019 (held out) at
prices native + autorpa_233k, reference row `ours_noinflate` with the flat
add-one gate prior and no spend cap, variant rows layered on it.

## 1. What was run (cells, shards, wall time)

| model | grid points | cells | shards | phase wall | sum of shard walls |
|---|---|---|---|---|---|
| android_ds | 50 (cf axis = {1, 10.5094}) | 400 | 50 | 27 m 31 s | 33 444 s |
| android_glm | 30 (cf axis degenerates: measured ratio is 1, so the {1, ratio} pair dedups and B_hat = C/p_hat exactly) | 240 | 30 | 15 m 21 s | 16 382 s |
| **total** | | **640** | 80 | **42 m 52 s** (09:48:37–10:31:29, 24 concurrent shards x 4 workers = 120 procs) | |

Server layout `~/app/guiexp/sim_w4/` (code at `app/computer-use/t2sim`, stream
data at `app/datasets/wiki-stream` and `app/experimental-results/openapps`,
mirroring `streams.py`'s REPO_ROOT resolution so nothing outside `sim_w4/` was
touched). Outputs (this directory):

- `E11_env_fragility_android_ds.{json,csv,md}` — 400 cells
- `E11_env_fragility_android_glm.{json,csv,md}` — 240 cells
- `E11_env_fragility.{json,csv}` — both models merged (keys prefixed
  `<cost_set>|`, csv gains a `model` column)
- `server_logs/` — campaign log, shard master log (start/end per shard,
  resumable), all 80 shard logs, the driver `w4_run_shards.sh`, the merger
  `w4_merge_e11.py`, the analysis `w4_analysis.py`, the two shard spec files.

**Determinism.** One shard (ds_00) rerun single-process on the Mac reproduced
the server shard bit-identically (all cells, all fields; only `wall_s`
differs). Additionally, W5's independent single-invocation run of the same
grid (`mech/E11_env_fragility_v3w5_{glm,ds}.json`, --jobs 40, no sharding)
matches all 640 cells of the merged files **bit-identically, every field** —
two machines, two parallelizations, one answer.

**Baseline (read-only).** `../t2_sim/E11_env_fragility_v3_android_glm.json`
and `..._v3_android_ds.json` (2026-09-12, constants v2 fingerprint
`a4e38c482b6e10cf`, pre-W1.5 narrow buy price, cf_high = 2.0830 / 11.0877,
400 cells each). These are the runs the paper's mechanism-selection numbers
(1.63 / 1.32 / 1.18 and 1.58 / 1.38 / 1.38) come from. The plain
`../t2_sim/E11_env_fragility.json` is the older 22-point v1-constants sweep
(pre p=0, pre C_fail axis) and is not cell-comparable.

## 2. The failed-attempt price axis: what the B_hat fix does (android_ds)

The prediction was that under full B_hat the DS p=0 families' repeated
pay-out gets blocked by the trigger's higher estimated price. Confirmed, and
it is the single biggest change in the sweep. Mean over the 64 cf-high cells
with p = 0 (4 grid points x 4 streams x 2 prices, per-cell means over 10 reps):

| row | failed attempts old (narrow, cf=11.09) | new (full, cf=10.51) | failed tokens old | new |
|---|---|---|---|---|
| `ours_noinflate` (reference) | 1 313.5 | **143.7** (−89%) | 2.12 G | **0.35 G** (−83%) |
| `ours_cap_realized` | 411.1 | **143.7** | 0.67 G | **0.35 G** |
| `always_compile_evict` (naive) | 81 907.5 | 81 907.5 (unchanged by construction) | 154 G | 226 G |

Under narrow pricing the trigger's attempt count was *independent of the cf
level* (1 313.5 at cf=1 and cf=11.09 alike — C/p_hat never sees C_fail); under
full B_hat the (1/p_hat − 1) term with C_fail = 10.5 C prices a p=0 family out
after ~2 attempts per family, and the spend cap stops binding (reference and
realized-cap rows are now identical at p=0).

Representative cell `h=0.02/q0=0.4/p=0/r=1/sig=0/cf=high | bpi2019/price=native`
(251 734 arrivals; 3 of 7 v3 DS families never pass the gate):

| row | old tokens | new tokens | old failed attempts | new |
|---|---|---|---|---|
| always_reactive | 26.86 G | 28.36 G | 0 | 0 |
| always_compile_evict | 297.8 G | 786.6 G (236 818 attempts, 758 G on failures) | 236 818 | 236 818 |
| ours_noinflate | 39.21 G (+46% over reactive) | **30.58 G (+7.8%)** | 10 256 | **918** |
| ours_cap_realized | 29.82 G | 30.58 G | 2 468 | 918 |

Second-order observations on the same axis:

- At cf = 1 full and narrow are the same formula by construction; the
  old-vs-new movement there isolates the constants swap: trigger attempts at
  p=0 move 1 313.5 -> 1 392.2 (+6%, different family mix with different
  c/C/q0), nothing dramatic.
- After the fix the trigger's ratio to the best fixed rule at p<1 cells is
  **identical at the cf-low and cf-high levels** (1.012 / 1.024 / 1.019 /
  1.013 for noinflate / horizon / epoch / realized; means over 160 cells):
  the failure channel is now priced in before it is paid, so the axis no
  longer moves the trigger at all. Only the naive rule still pays it (per
  cell above: 100.5 G at cf=1 vs 786.6 G at cf=10.5).
- GLM has no cf-high level at all under v3 (ratio identified as 1), so for
  GLM this rerun changes nothing through the formula — the GLM deltas below
  are pure constants swap.

## 3. Worst same-regime cell vs the best fixed rule (the paper's 1.18–1.38 claim)

Worst over cells of `variant mean_tokens / min(always_reactive,
always_compile_evict)`, matching the numbers quoted in Appendix "Mechanism
Selection" (old values 1.63 / 1.32 / 1.18 GLM and 1.58 / 1.38 / 1.38 DS are
reproduced exactly from the archived files):

| variant | GLM old | GLM new | DS old | DS new |
|---|---|---|---|---|
| ours_spend_cap (horizon) | 1.627 | 1.761 | 1.583 | **1.778** |
| ours_cap_epoch | 1.318 | 1.380 | 1.378 | 1.380 |
| **ours_cap_realized (deployed)** | **1.178** | **1.147** | **1.377** | **1.380** |

- **The deployed trigger's worst case is unchanged in magnitude: 1.15 (GLM) /
  1.38 (DS)** — squarely inside the paper's claimed 1.18–1.38 band (GLM
  actually improves 1.178 -> 1.147). The claim survives the constants
  generation and the B_hat fix.
- The worst GLM cell *moved regime*: old worst was a failure-pricing cell
  (h=0, p=0, cf=2.08, wiki_B native); the new worst is
  `h=0.1/p=1/bpi2019/native` — a never-miss gate where the trigger is simply
  slower than always-compile. The DS worst stays a low-gate cell
  (`h=0/p=0.3/wiki_A/autorpa_233k`, 13.42 G vs 9.72 G for the best fixed
  rule).
- The **horizon** cap is now clearly the worst variant on both models
  (1.76 / 1.78, worst cells at h=0.02, p=0.5): under full B_hat, capping
  failed spend against the projected saving alone strands the budget at the
  wrong time. This strengthens, rather than weakens, the paper's choice of
  the realized-saving credit.
- The reference row itself (`ours_noinflate`) tightened dramatically on DS:
  mean 1.081 -> 0.995, median 1.003 -> 1.000, **max 2.155 -> 1.380**. The old
  catastrophic cell (`h=0.1/q0=0.17/p=0.3/cf=11.09/bpi2019/233k`, 2.15x the
  best fixed rule) is gone; the trigger now on average *undercuts* the best
  fixed rule across regimes (0.995) instead of paying an 8% premium.

## 4. What the constants swap alone does (7-family v2 -> v3)

Matched cf=1 cells only (formula-invariant), shared keys:

- **android_glm** (240 shared cells): trigger ratios essentially unmoved
  (reference mean 1.002 -> 1.001, max 1.144 -> 1.147). The composition
  changed the *decision difficulty*, not the trigger's relative performance:
  cells where some deployable rule beats naive-compile by >5% drop 151 ->
  118. v3 GLM has all 7 families admitted with 5 of 7 at q0 = 0 (uses never
  fail), so always-compile is more often within 5% of the best rule and the
  "non-trivial" flag fires less.
- **android_ds** (400 shared cells): beats-naive unchanged at 299/400. The
  naive rule's failure bill at p=0 grew with the new prices (139 G -> 215 G
  per cell at cf=1, 154 G -> 226 G at cf high, driven by C_fail = 10.51 x
  C_median 238k), which is the world the B_hat fix prices correctly.
- best/naive (best deployable vs always_compile_evict): DS mean 0.661,
  median 0.757; GLM mean 0.852, median 0.953 — under v3 the compile decision
  remains clearly non-trivial on DS and marginal on GLM, as in the archived
  sweep.

## 5. Anomalies and how they were handled

1. **Offline-bound flags up in DS (4 -> 190 rows below `offline_opt_tax`,
   172 beyond 2 paired SE).** All 172 significant flags are float-summation
   noise: ratio >= 1 - 2.3e-12, in degenerate p=0 cells where the trigger,
   reactive and the bound coincide (v3 + full B_hat makes more cells exactly
   degenerate). The largest *material* gap is 0.69% (oracle_tax in sepsis
   r=2.2 / sigma=0.3 cells) and is inside paired SE (not significant) — the
   same class the archived runs already carried (4 rows / 1 significant).
   No action needed; the check's own criterion is "materially and
   significantly negative".
2. **First campaign launch died silently** (driver called `run_shards.sh`,
   file was `w4_run_shards.sh`): 0 shards ran, nothing written; fixed and
   relaunched 09:48:37. The shard master log + skip-if-output-exists rule
   make any interruption resumable; none occurred after.
3. **Co-tenant load**: W5 ran the same sweep concurrently in
   `~/app/guiexp/sim_w5` (~80 procs, `--jobs 40` x 2). Combined load peaked
   ~207 on 144 cores; memory stayed trivial (180 G / 1007 G). No file or
   process interference (disjoint directories); W5's outputs were later used
   as the independent bit-identical cross-check reported in section 1.
4. The calibration probe (`server_logs/probe_ds.log`, tag `probe_ds`) ran
   without `--c-fail-ratio`, so its *meta* says 3.0; its cells embedded the
   correct cf via `--only` and it is excluded from the merge anyway.

## 6. Verdict for the paper's numbers

- Worst-cell magnitudes for the deployed rule (ours_cap_realized) stay at
  **1.15 (GLM) / 1.38 (DS)** — the 1.18–1.38 claim holds under v3 constants
  + full B_hat.
- The horizon-cap worst cells (1.76 / 1.78) and the epoch/realized gap
  narrowing on DS make the realized-credit choice *more* pronounced; if the
  mechanism-selection sentence is updated, quote 1.76 / 1.38 / 1.15 (GLM)
  and 1.78 / 1.38 / 1.38 (DS).
- The B_hat fix removes the trigger's one catastrophic family of cells
  (DS reference max 2.15x -> 1.38x) and cuts its wasted failure spend at
  p=0 by ~85%; the C_fail axis now moves only the naive rule, never the
  trigger.
