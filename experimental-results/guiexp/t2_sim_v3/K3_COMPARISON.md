# K3 comparison: one-trace vs three-trace eligibility (W10 D1, 2026-09-19)

User ruling: rerun the simulator under Algorithm 1's three-trace eligibility
(k_min = 3). The engine already carried the parameter (`run_policy`
`params["k_min"]`, the gate at sim.py:1632 applies uniformly to every
compile-deciding policy; the clairvoyant offline optimum ignores it, which
keeps it a lower bound). Injection: `constants["deployment"]["k_min_global"]`
read by `run_experiment` (synthetic cells via `spec["params"]`, real cells via
`spec["deployment"]` -> `real_params`), `--kmin-global` flag on run.py, and the
same flag on eligible_fraction.py; `_cell_paired`'s real branch now forwards
`spec.get("deployment")`. Tests 221 pass unchanged. Zero API calls.

**E11 needed no rerun**: `sweep_env_fragility.build_cells` already wraps
`_e4_deploy_cells(..., "kmin3", ...)`, and its meta has carried
`deployment.k_min: 3` all along, so the §5.5 regime sweep was already
three-trace. The pi->add_one rerun being bit-identical (A1_COMPARISON) and
this rerun being E11-free are consistent: the sweep never ran one-trace.

## Runs (all --gate-prior add_one, --kmin-global 3, reps 20 / seed 7 / B 2000)

| run | file | cells | wall |
|---|---|---|---|
| E3 full | E3_full_v3_a1k3.json | 12 | 0.8s |
| E3 narrow | E3_narrow_v3_a1k3.json | 12 | 0.8s |
| E4 GLM | E4_full_v3_a1k3.json | 16 | 220.4s |
| E4 DS | E4_full_v3_a1k3_e4ds.json | 4 | 122.0s |
| E4 DS narrow | E4_narrow_v3_a1k3_e4ds.json | 4 | 116.4s |
| eligible | eligible_fraction_v3_a1k3.json | 8 | 259.4s |
| E5 glm | mech/E5_v3w5_android_glm_k3.json | 37 | 14.3s |
| E5 ds | mech/E5_v3w5_android_ds_k3.json | 37 | 13.8s |

Old a1 files untouched. `mech/_provenance_numbers.json` regenerated from the
k3 E5 files (glm_v3/ds_v3 columns repointed; old/oa_v3 bit-stable). The
comparison tool is `k3_compare.py` in this directory.

## Invariants verified (review gate)

- `always_reactive` bit-identical old vs new in all five policy files (it
  never compiles, so the eligibility gate cannot touch it).
- `ours` = 1.00 by construction everywhere.
- eligible stream fractions (log-derived) identical: 0.964 / 0.956 / 0.316 /
  0.187.
- E5 cell/row structure and block sizes identical (8/9/8/4/8).
- Under k_min = 3, `on_second` coincides with `always_compile` row for row in
  E3 and E4 (both compile every family at its third arrival; the deciding
  arrival itself counts as a demonstration).

## Headline movement

1. **GLM synthetic cells move a lot; DS cells barely.** The trigger now waits
   for three arrivals, so under the GLM constants (everything verifies, pays
   back fast) it pays more reactive episodes before buying: ours tokens
   x1.5-2.4. Under DS it compiled almost nothing before and still does.
2. **ours / best fixed rule improves**: worst cell 1.85 -> 1.68 (bursty GLM),
   because always_compile also pays the three-demo delay. Abstract and
   tab:policysim caption: 1.9 -> 1.7.
3. **The eligibility floor protects every compile rule from singletons**:
   always_compile on the real streams collapses from 5.1/9.6/18/34 multiples
   to 1.0-1.8, and the oracle's measured-price advantage collapses (bpi
   4.1 -> 1.4; buys 4,809 -> 1,379 vs our 357 -> 325) because everyone now
   buys at the third arrival. Two §5.4 narratives were rewritten honestly:
   the singleton-heavy contrast (now attributed to the floor) and the oracle
   paragraph (no longer dominant at the measured price).
4. breakeven (est.) on the six synthetic cells drops from 1.2-2.1 to
   0.90-1.50 (at or below ours under GLM); its real-stream 5M overtake
   (0.41/0.45) is unchanged.
5. app:mech blocks all shift downward in level; the (a) selection rationale
   is reworded (add-one vs stratified prior now within two percent under GLM;
   add-one still cheaper under DS; cap still inert); (b) priors tie at the
   front on both models; (c)/(d) same orderings with new numbers.
6. Full-vs-narrow buy-formula contrast: sepsis cut 62 -> 11 percent,
   compilations 135 -> 17 to 4; bpi still pays 8 percent more for the full
   form. "11 to 15 per stream" survives rounding; token cut 6.1-18.7 ->
   6.4-18.9 percent.

## Paper surface updated (body.tex, single-writer)

tab:policysim + tab:realstreams cells, both captions, the 1.7 abstract
sentence, §5.3 paragraph (five range updates + breakeven sentence rewrite),
§5.4 (nine passages including the oracle and singleton rewrites), app:mech
(a)-(d), app:sim disclosure (three-trace floor now real; offline-optimum
floor exemption disclosed), app:sim baselines (on_second floor note).
Checker group 17 repointed to the k3 files with want literals updated
failure-driven; three defense lines green after the update.
