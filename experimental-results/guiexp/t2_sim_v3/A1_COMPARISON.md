# A1 comparison: trigger gate prior pi vs add_one (W7b, 2026-09-19)

The W4-W7a reruns carried the trigger constant `gate_prior = "pi"` inherited
from constants v2, while Algorithm 1 and app:mech block (a) state the flat
add-one estimate `p_hat = (passes + 1) / (attempts + 2)` (Beta(1,1)
posterior mean; `sim.GATE_PRIOR_MODES` string **`"add_one"`**, configured as
`sim.mech_with(mech, gate_prior="add_one")` in the E5 `f_gate_prior` block,
`--gate-prior add_one` on run.py, and `trigger.gate_prior` in a constants
file for sweep_env_fragility.py).  W7b reran the three main experiments with
add_one and nothing else changed.  Companion runs beyond the four headline
files (narrow-form contrasts and the eligible-substream analysis) were rerun
too because the draft cites them.

| run | pi file (W4-W7a) | add_one file (W7b) |
|---|---|---|
| E3 full, 12 cells | E3_full_v3.json | E3_full_v3_a1.json |
| E3 narrow (contrast) | E3_narrow_v3.json | E3_narrow_v3_a1.json |
| E4 android_glm, 16 cells | E4_full_v3.json | E4_full_v3_a1.json |
| E4 android_ds, 4 cells | E4_full_v3_e4ds.json | E4_full_v3_a1_e4ds.json |
| E4 DS narrow (contrast) | E4_narrow_v3_e4ds.json | E4_narrow_v3_a1_e4ds.json |
| eligible substream | eligible_fraction_v3.json | eligible_fraction_v3_a1.json |
| E11, 640 cells | E11_env_fragility.{json,csv} (+ per-model) | E11_env_fragility_a1.{json,csv} (+ per-model, .md) |

E11 execution: same sharded geometry as W4 on cs11369a (one grid point per
shard, 24 shards x 4 workers, driver `w4b_run_shards.sh`, constants copy
`constants.measured.v3.a1.json`, outputs in `out_a1/`, W4 trees untouched),
80/80 shards rc=0, campaign wall 26 m 52 s (DS 15 m 25 s, GLM 11 m 27 s),
merged locally by `server_logs/w4b_merge_e11.py` with the true block labels
restored.  Scripts and logs archived under `server_logs/a1/`.

## Headline

1. **E11 does not move at all: bit-identical on all 640 cells, every row,
   every field, and the lower-bound violation list, and all three CSVs are
   byte-identical row-for-row.**  The reason is in the sweep itself:
   `sweep_env_fragility.py` pins its reference row to add-one
   (`BASE_GATE_PRIOR = "add_one"`, forced in `env_constants` regardless of
   the constants file; `meta.mechanisms.gate_prior_base` said so in the W4
   output already).  The pi constant never reached E11's `ours` rows; the
   only pi-bearing E11 row is the deliberate `ours_pi_prior` ablation.  So
   the paper's regime-sweep numbers were already add-one, and the W7b rerun
   doubles as a same-machine determinism check (matching W4's earlier
   cross-machine check).
   - Worst same-regime multiple vs best fixed rule, pi -> add_one:
     deployed rule (`ours_noinflate`) **1.1468 GLM / 1.3799 DS, unchanged**
     (old 1.147 / 1.380).  Cap forms unchanged: horizon 1.7610 / 1.7784,
     epoch 1.3801 / 1.3802, realized 1.1468 / 1.3799.  Medians 1.00, means
     1.0020 GLM / 0.9955 DS.  Failure-price axis unchanged: on the 64 DS
     p=0 cells at C_fail/C = 10.5 the naive rule still averages 81,908
     failed attempts per cell (236,818 worst, BPI 2019 native) against our
     143.7 (918 in that cell), and the naive bill still grows 21.5 -> 226.0
     billion tokens per cell (10.5x) when the multiple is switched on.
2. **E3 moves a lot under the GLM constants and barely under DeepSeek.**
   `ours` mean tokens, add_one vs pi:
   - android_glm: bursty **-34.5%** (7.0M -> 4.6M), poisson **-29.7%**
     (9.7M -> 6.8M), zipf **-34.9%** (8.1M -> 5.3M).  The trigger compiles
     the same seven families earlier (n_lib 6.9 -> 7.0, 6.5 -> 7.0), so it
     stops paying reactive serving on the marginal families sooner.
   - android_ds: poisson **+4.6%**, bursty **+3.1%**, zipf -0.1%.  Fewer
     surviving libraries (bursty n_lib 1.6 -> 0.8): the optimistic flat
     estimate pays more failed attempts on never-verifying families before
     the spend cap stops them.
   - openapps control cells (6 of 12): **0.00% on every field** -- the
     streams compile everything at once, so the prior never matters.
   - Consequently every rel_to_ours column moves through the `ours`
     denominator only: under GLM the fixed rules' ratios rise (always
     reactive 6.4-12 -> 9.1-18; always compile / oracle 0.38-0.53 ->
     0.54-0.80; success count 1.8-2.4 -> 2.6-3.7; ToolPro 3.4-4.8 ->
     5.2-7.4; break-even 1.3-1.7 -> 1.4-2.1) and the caption claim "trigger
     never pays more than N times the best rule" drops **2.6 -> 1.9** (max
     ours/best-fixed 1.85 on GLM poisson).  Under DS the ratios ease down
     (always compile 4.8-7.0 -> 4.8-6.8).
3. **E4 moves a few percent in both directions.**  `ours` mean tokens:
   - GLM 16 cells: -11.7% (wiki_A/5M) to +8.0% (sepsis/autorpa_233k);
     12 of 16 cells get cheaper.  Wiki streams -0.5 to -11.7%, BPI -1.3 to
     +0.8%, sepsis -0.3 to +8.0%.
   - DS 4 cells: -1.3% (sepsis) to +0.5% (wiki_B) -- quiet.
   - Notable single cells: wiki_A/5M compiles 45 -> 53 libraries and pays
     5.25G -> 4.64G; sepsis/autorpa compiles 142 -> 89 and pays +8.0%
     (the flat estimate is slower to give up on marginal families).
   - Trigger-adjacent variant rows move with it (breakeven, ours_g15/g120/
     hfix); ours_trueP, which pins the true rate, is bit-identical.

## Fixed-baseline bit-identity (verified)

Rows that never read the gate prior -- always_reactive, always_compile,
on_second, success_count, toolpro_port, oracle, offline_opt -- are
**bit-identical in every raw field** (mean_tokens, n_reps,
mean_final_library, mean_tau_total, mean_wasted, mean_n_compiles) across
all E3 and E4 cells, pi vs add_one; only their rel_to_ours columns move,
through the `ours` denominator.  The ours_trueP E4 variant (gate_prior
pinned "true" in both runs) is likewise bit-identical, the intended control.
E11 is bit-identical everywhere (above).  Checks live in
`a1_compare.py` (E3/E4, prints `fixed raw-field bit-identity: PASS`) and
the merge-diff in this campaign (E11).  Stream descriptors (_stream,
_n_star) identical.

## Draft impact (docs/sim-refill-draft-2026-09-19.md, refreshed)

Sections 5.3 and 5.4 tables and prose now carry the a1 numbers (see the
draft's own ledger, section 7, for raw values); section 5.5 and app:mech
(a)-(d) are unchanged because E11 and the E5 mechanism files were already
add-one / carry their own per-row prior ablations; app:success and
app:protocol do not read the trigger.  No sentence lost its numerical
support; the retuned sentences are listed in draft section 8.

## Anomalies

- None in the results.  One W7b wrinkle: the first attempt to launch the
  server campaign died with the ssh session (nohup without setsid), leaving
  nothing running; relaunched with setsid, one clean campaign, all 80
  shards rc=0.  A pre-launch smoke shard was killed by its own 300 s
  timeout and rerun by the campaign's resume logic (partial outputs are
  removed before rerun; no mixed state, verified by the merge diff).
- The W7a draft's section 8 range "success_count at 0.85 to 1.07 of ours on
  the GLM real streams" did not match its own source file (true W4 range
  0.38-1.30); corrected in the refreshed draft to the a1 range 0.43-1.20.
- E11 `_config.block` initially came out "probe" in the a1 merge (the
  `--only` flag hardcodes it); the merge restores the true block labels as
  W4 did, after which the diff to W4 is clean.
