# Number swap ledger: body.tex old-harness numbers -> new GUI-harness numbers

Generated 2026-09-07. This is the coordinator's script for the paper's number pass.
Every currently printed number in body.tex section 5 (measure), section 7
(experiments, Tables 1/1b/2/3 and their readings), and the appendices
(measure / compile path / estimator / reconcile / drift / mech / sim) is listed
with its line number, the old value, the new value, and the source of the new
value. Abstract / intro / conclusion / limitations numbers that duplicate these
are listed in section K (they must move together).

Conventions.
- **Unit change (global).** All token counts move from raw tokens on the old
  coding-agent harness to **cache-adjusted tokens** (fresh + r*cached +
  completion; r = 0.20 GLM, 0.318 DS) on the new screenshot+AX GUI harness.
  Sources: `experimental-results/guiexp/cache_adjust/{runs.csv,calls.csv,summary.json}`
  (product of `solve_cache.py`, methodology `docs/cache-adjusted-accounting.md`).
- Actions: **REPLACE** old -> new value; **DELETE** no new counterpart exists;
  **KEEP-OLD** revalidated unchanged on the new harness; **ADD** new number
  with no old slot; **REWRITE** number + surrounding claim must change because
  the meaning moved (flagged again in section L).
- Source keys used below:
  - `IDX` = `computer-use-paper/results_new_index.json` (entry path given)
  - `S` = `experimental-results/guiexp/cache_adjust/summary.json`
  - `RUNS` = `experimental-results/guiexp/cache_adjust/runs.csv` (t11 rows)
  - `T13` = `experimental-results/guiexp/t13_compilepath/<model>/...`
  - `E3/E4/E5/E8` = `experimental-results/guiexp/t2_sim/{E3,E4,E5,E8}.json`
  - `CONST` = `computer-use/t2sim/constants.measured.json` (fingerprint
    `91b9077d36a4cbde`, the file all four T2 runs recorded)
  - `E6` = `experimental-results/guiexp/e6_estimator_shares.json`
  - `DRIFT` = `experimental-results/guiexp/e7_drift/drift_probe.json`
  - `ML` = `experimental-results/guiexp/m_library/analysis.json`,
    `ML2` = `experimental-results/guiexp/m_library2/summary.json`
  - `AW12/AW14` = `experimental-results/guiexp_android/t12_grid|t14_compilepath`
- Verification: `python3 computer-use-paper/check_numbers_new.py` (641
  assertions) recomputes every new value cited here; the old
  `check_numbers.py` still passes 812/812 against the old files.

----------------------------------------------------------------------------------------------------

## A. Global framing swaps (apply before line-by-line)

| # | Where | Old | New | Action / source |
|---|---|---|---|---|
| A1 | whole paper | raw tokens, coding-agent harness, floor 19.6k-21.5k subtracted | cache-adjusted tokens, screenshot+AX GUI harness, no floor subtraction (floor 0.3k-1.1k, <1.6% of any arm) | REPLACE; S, RUNS; docs/cache-adjusted-accounting.md |
| A2 | whole paper | one application family (OpenApps calendar), 3 layouts | plus Android 3 families (contacts / calendar / markor) on both models | ADD; AW12/AW14, CONST android_* |
| A3 | accounting footnotes | none | (a) raw and (c) dollar-equivalent accountings reported alongside: raw N* 0.088/0.130, dollar N* 0.631/1.838 | ADD; S headline.{raw,doll} |
| A4 | §7 stream set | three process logs (Helpdesk, Sepsis, BPI 2019) | Sepsis + BPI 2019 + two Wikipedia streams (wiki_A tool-hot-tail 65,564 arrivals/2,529 families; wiki_B content-tail first 100k/68,773 families); Helpdesk demoted to the E5 prior-selection role | REWRITE; E4 _stream; CONST streams |

----------------------------------------------------------------------------------------------------

## B. body.tex section 5 (measure), L143-224

### B1. Counterfactual contrast, L159-171

| Line | Old | New | Action | Source |
|---|---|---|---|---|
| 168 | floor "19k to 45k tokens" | floor 0.34k-1.06k (GLM), 1.13k (DS), cache-adjusted | REPLACE | RUNS floor rows; IDX measure_grid.*.floor |
| 169 | "Subtracting it sharpens the contrast by roughly three points" | floor is <1.6% of any arm; subtraction changes shares by <1 point and is dropped | REWRITE | RUNS |
| 170 | "harness reads the page DOM" | "harness sees screenshots + accessibility tree (AX); pixel agents likely pay more" | KEEP-OLD (claim) | docs/nstar-smallness-analysis.md s2.2 |

### B2. Table 1 (tab:measure) and caption, L178-195

New Table 1 (GLM 5.3 Flash, cache-adjusted, mean of 3 seeds; all 36 solving runs
succeed; skill now a full 3-seed column):

| Line | Cell | Old | New | Source |
|---|---|---|---|---|
| 180 | caption "68 to 76 percent" | 68-76% | 49 to 77 percent | IDX measure_grid.glm.*.rho_pooled |
| 182 | "All 27 solving runs" | 27 | 36 (3 layouts x 4 conditions x 3 seeds) | IDX measure_grid.glm.solving_runs_all_succeed |
| 183 | "raw pooled shares are 65 to 73 percent" | 65-73% | 53 to 68 percent | S headline.glm.raw.rho (0.529-0.682) |
| 190 | wizard row | 517k / 521k / 176k / 19.6k, 69% [61,87] | 95k / 57k / 22k / 0.3k, 77% [76,77] | IDX measure_grid.glm.wizard |
| 191 | single page row | 413k / 343k / 145k / 19.6k, 68% [-9,95] | 69k / 39k / 35k / 1.1k, 49% [27,61] | IDX measure_grid.glm.single_page |
| 192 | sectioned row | 514k / 437k / 137k / 19.7k, 76% [57,94] | 56k / 38k / 25k / 0.3k, 55% [23,65] | IDX measure_grid.glm.sectioned |
| — | skill column | (none; skill in prose only) | 41k / 48k / 45k | ADD; IDX measure_grid.glm.*.skill |

Per-seed share brackets (new): wizard [0.773, 0.758, 0.779]; single page
[0.266, 0.570, 0.612]; sectioned [0.234, 0.652, 0.572] (IDX
measure_grid.glm.*.rho_per_seed).

### B3. Findings prose, L197-209

| Line | Old | New | Action | Source |
|---|---|---|---|---|
| 197 | "all 27 runs and all three solving conditions" | all 36 runs, four solving conditions | REPLACE | RUNS success |
| 198 | "68 to 76 percent" | 49 to 77 percent | REPLACE | IDX |
| 198 | DS "gives 87 to 88 percent with all 27 runs solved" | DS gives 69 to 73 percent, all 36 solved | REPLACE | IDX measure_grid.ds.*.rho_pooled |
| 198 | DS "execution cost matches the first model's, and what grows is discovery ... roughly three times the tokens" | DS execution is 32-42k vs GLM 22-35k; discovery (c-L) 83-96k vs 31-73k -- the "same execution, 3x discovery" story only survives on the wizard; on single-page/sectioned GLM execution is now smaller too | REWRITE (meaning) | IDX measure_grid |
| 199 | "Single runs on two stronger models give 80 to 84 percent" | Sonnet 5: 78%; glm-5v-turbo: 36-38% (raw 0.362 / cache-adj 0.383) -- the second strong model no longer corroborates a high share | REWRITE (meaning) | IDX t15.*; S t15 |
| 200 | mid "costs 2.4 to 3.2 times told (first model) and 4.6 to 10.8 (second)" | GLM 1.1 to 2.6; DS 3.3 to 5.5 | REPLACE | IDX measure_grid.*.mid_over_told |
| 200 | "on the first model's wizard it matches discover outright (521k against 517k)" | GLM wizard mid 57k is 40% below discover 95k (no longer matches); on DS mid EXCEEDS discover on wizard (175k vs 115k, +53%) and sectioned (203k vs 136k, +50%) | REWRITE (meaning; negative control now overshoots on DS) | IDX measure_grid |
| 200 | "text recovers at most 26 percent of discovery on the first model and 39 on the second" | GLM mid recovers 52/87/58% of the discover-to-told gap; DS -73/15/-72% | REWRITE (meaning; GLM structure text now recovers most of the gap on single-page) | IDX measure_grid.*.mid_recovery |
| 201 | skill GLM "removes 27 and -17 (wizard), 14 and -54 (single), 68 and 52 (sectioned)" | GLM skill recovers 74/68/26% of the gap per layout (3 seeds each); per-seed spans still wide | REPLACE | IDX measure_grid.glm.*.skill_recovery; RUNS skill rows |
| 201 | "the exact procedure removes 68 to 76" | removes 49 to 77 | REPLACE | IDX |
| 202 | DS skill "-55 to 47 percent" | DS skill recovers -34/-23/-26% (costs more than discover on every layout) | REPLACE | IDX measure_grid.ds.*.skill_recovery |
| 202 | "twelve skill runs span -80 to 89 percent" | eighteen skill runs span -80 to +89 -> recompute per-seed from RUNS; means span -34 to +74 | REPLACE | RUNS |
| 203 | per-run shares "61 to 87 (wizard), 57 to 94 (sectioned), -9 to 95 (single page)" | GLM: 76-77 (wizard), 23-65 (sectioned), 27-61 (single page) | REPLACE | IDX rho_per_seed |
| 203 | "second model's brackets are tighter, 74 to 95" | DS: 55-88 (wizard), 64-79 (single), 57-74 (sectioned) -- no longer uniformly tighter | REPLACE | IDX measure_grid.ds.*.rho_per_seed |
| 208 | "mid recovers at most 26 ... 39" (repeat) | GLM up to 87%, DS at most 15% | REPLACE | IDX mid_recovery |
| 208 | xu invariance "46k-51k ... tool calls unchanged" | unchanged (their paper's numbers) | KEEP-OLD | xu2026realcost |

### B4. Compile path end to end, L211-224

| Line | Old | New | Action | Source |
|---|---|---|---|---|
| 215 | "Three attempts cost 24.2k tokens each on average" | 14.5k cache-adjusted (raw 6.4k/20.6k/17.9k, mean 15.0k); DS 13.4k (raw mean 14.0k) | REPLACE | IDX compile_path.glm.C_raw_mean, IDX constants.glm.C_stepview |
| 215 | "all three pass the gate ... pass rate 1.0" | 15/15 both models | KEEP-OLD | IDX compile_path.glm.gates |
| 216 | "d = 20.0k tokens, of which 19.6k is the harness floor ... about 400 tokens" | d = 347 tokens (GLM) / 491 (DS): a single text extraction call, no screenshots, no floor | REPLACE | IDX constants.{glm,ds}.d; T13 deploy30 |
| 217 | "saving per use is therefore 390k to 500k tokens" | s = 79.1k (GLM) / 87.3k (DS) | REPLACE | IDX constants.{glm,ds}.s |
| 218 | "harness is a coding agent whose reactive run has already written the script" | new compiler reads a 814/928-token structured step view distilled from the trajectory (no screenshots in the compile prompt) | REWRITE | docs/nstar-smallness-analysis.md s3.1 |
| 220 | "Without a type check zero of ten deployment uses succeeded" | no new-harness no-typecheck arm; new harness ships the checked boundary; q's anatomy (all failures value-level oracle_fail, 0 program errors, 0 type-check rejects) carries the point | DELETE (old-harness-only demo) | T13 deploy30 uses[].error_type |
| 221 | "type check with one bounded retry fixed eight of ten uses" | retries ~0 in the 30-use deployments; failures remain value-level | DELETE/merge with 220 | T13 deploy30 |
| 222 | "deployment success is 8 of 10 uses, so q ~ 0.2 and expected per-use cost 20.0k + 0.2 x 517k ~ 123k; break-even moves from 0.05 to 0.06" | deployment success 25/30 (q = 0.17) and 23/30 (q = 0.23); expected per-use cost 347 + 0.17x95.3k = 16.5k (GLM), 491 + 0.23x114.5k = 26.8k (DS); N* = 0.184 / 0.153 (q already inside) | REPLACE | IDX constants.{glm,ds}.{q0,N_star}; T13 deploy30 |
| 223 | DS "21.3k per attempt ... 22.5k per deployed use ... 6 of 10 ... break-even 0.015" | 13.4k per attempt; d = 491; 23/30; N* = 0.153 | REPLACE | IDX constants.ds |
| 224 | regate: "first model all three candidates passed all five ... zero retries; second model 12 of 15" | GLM regates 4/5, 5/5, 5/5, 4/5 across the four rerun files (14/20; regate1 not on disk); DS 4/5, 0/5, 4/5 (8/15); failures still extraction-side, binding-following | REWRITE | IDX compile_path.{glm,ds}.regates; T13 regate* |

----------------------------------------------------------------------------------------------------

## C. body.tex section 7 (experiments), L317-430

### C1. Setup, L320-323

| Line | Old | New | Action | Source |
|---|---|---|---|---|
| 321 | "300 arrivals ... Poisson, Zipf, bursty ... drift hazard of 0.02 per use ... binding space of 12 (read only by the ToolPro port) ... 20 repetitions" | 300 arrivals, same three patterns, 20 reps KEEP; drift hazard replaced by the program-lifetime cap 1/h = 50 uses (the capped trigger); binding space 12 not in the new engine | REWRITE | CONST trigger.horizon_cap; E3/E4 meta |
| 323 | "every cost is a measured number from Sections 5" | same, but costs are per-layout (95.3k/68.7k/55.8k) with d=347, C=14.5k, q0=0.17, p=1, tau0=368, m=91 | REPLACE | CONST cost_sets.openapps_glm |

### C2. Table 2 (tab:policysim) L328-352 -> E3 openapps_glm block

| Line | Row | Old (Poi/Zip/Bur) | New (Poi/Zip/Bur) | Source |
|---|---|---|---|---|
| 330 | caption "beats staying reactive by an order of magnitude, ours matches or beats oracle within two percent" | order of magnitude; within 2% | reactive 5.4-5.5x; oracle ties ours exactly (1.00 in all six E3 cells) | REWRITE; E3 |
| 331 | "drift hazard of 0.02 per use" | 0.02 | delete (lifetime cap 50 uses instead) | REPLACE | CONST |
| 340 | always reactive | 15.8 / 15.4 / 16.6 | 5.43 / 5.47 / 5.40 | E3 poisson|zipf|bursty/openapps_glm.always_reactive.rel_to_ours |
| 341 | always compile | 1.01 / 0.94 / 1.01 | 1.00 / 1.00 / 1.00 | E3 ...always_compile |
| 342 | compile on second | 1.94 / 1.68 / 1.86 | 1.10 / 1.07 / 1.08 | E3 ...on_second |
| 343 | success count (10) | 4.83 / 4.44 / 4.49 | 1.42 / 1.39 / 1.42 | E3 ...success_count |
| 344 | ToolPro port | 7.63 / 8.82 / 8.11 | 1.82 / 1.71 / 1.77 | E3 ...toolpro_port |
| 345 | ours | 1.00 / 1.00 / 1.00 | 1.00 / 1.00 / 1.00 | KEEP |
| 346 | oracle | 0.98 / 0.99 / 1.06 | 1.00 / 1.00 / 1.00 | E3 ...oracle |
| 348 | break-even | 1.43 / 1.39 / 1.46 | 1.06 / 1.05 / 1.05 | E3 ...breakeven |
| 349 | offline optimum | 1.00 / 0.95 / 1.04 | 0.98 / 0.98 / 0.98 | E3 ...offline_opt |
| — | NEW android block | (none) | ADD second cost-set rows (android_glm): reactive 1.24/1.70/1.27; always compile 1.21/1.24/1.21; on second 1.21/1.24/1.21; success 1.22/1.25/1.21; ToolPro 1.04/1.06/1.04; ours 1.00; oracle 1.00; break-even 1.002/1.004/1.002; offline 0.999 (N* = inf: p=0 families) | ADD | E3 */android_glm |

### C3. Section 7.1 readings, L354-361

| Line | Old | New | Action | Source |
|---|---|---|---|---|
| 355 | "N* ~ 0.05, so every rule that ever compiles wins by an order of magnitude, and staying reactive costs 16 times more" | N* = 0.18 (0.25 mean over layouts); reactive costs 5.4-5.5x (OpenApps) and 1.2-1.7x (Android, where two of three families never compile) | REWRITE | E3 _n_star; IDX nstar_recompute |
| 356 | "ToolPro pays 7.6 to 8.8 times our policy" | 1.7-1.8x (OpenApps), 1.04-1.06x (Android) | REPLACE | E3 toolpro_port |
| 357 | "ours sits within two percent of the oracle under Poisson and Zipf and beats it under bursty" | ours and the oracle coincide exactly in all six cells | REWRITE | E3 oracle |
| 358 | "break-even rule pays 1.39 to 1.46 ... offline sits at 0.95 to 1.04" | break-even pays 1.05-1.06; offline 0.98 | REPLACE | E3 breakeven/offline_opt |
| 360 | "compile-on-second at 1.7 to 1.9 and success-count at 4.4 to 4.8" | 1.07-1.10 and 1.39-1.42 | REPLACE | E3 on_second/success_count |
| 361 | "at five million tokens per compile, always-compile still wins on hot streams at 0.82 times our cost" | at 5M always-compile wins the hot patterns at 0.78 (Zipf) to 0.86 (Poisson/bursty) vs ours (ours stays reactive: N*=87 unpayable under the 50-use cap) | REPLACE | E8 grid/5M/s=native/{unpaired,paired}/* |
| 361 | "(Appendix app:sim)" sweep cross-ref | keep | KEEP | — |

### C4. Section 7.2 setup + Table 3 (tab:realstreams), L363-401 -> E4

| Line | Old | New | Action | Source |
|---|---|---|---|---|
| 366 | "we replay three public process logs" | we replay two process logs (Sepsis, BPI 2019) and two Wikipedia edit streams (wiki_A, wiki_B) | REWRITE | E4 cells |
| 367 | Helpdesk "hottest family covers 52 percent of arrivals and 60 percent of families arrive exactly once" | Helpdesk moves to the prior-selection experiment only (E5); drop from the headline table | DELETE here (see J4) | CONST streams.helpdesk.role=e5_selection |
| 367 | Sepsis "93 percent of families arrive exactly once" | 93% (unchanged; 846 families, 1,050 cases) | KEEP-OLD | E4 sepsis _stream |
| 367 | "BPI 2019 was held out" | BPI 2019 remains held out (from horizon selection) | KEEP-OLD | E4 |
| 369 | "xu's ratio ... about nineteen episodes, roughly ten million tokens at our measured episode cost, and AutoRPA's build cost is 233k, so one and five million tokens sit inside the audited interval" | 19 episodes x 95.3k = 1.8M at the new measured episode cost (10M was the old-harness scaling); the ladder is native / 233k / 1M / 5M / 10M | REWRITE | CONST price_ladder; IDX constants.glm.c |

New Table 3 skeleton: rows = reactive / always compile / on second / success
count / ToolPro / ours / ours-Gamma(1,5) / ours-Gamma(1,20) / ours-fixed-60 /
oracle / break-even / offline; columns = Sepsis, BPI 2019, wiki_A, wiki_B at
native / 233k / 1M / 5M (E4 rel_to_ours; ours absolute 74.5M / 13.5G / 2.2G /
11.6M at native).

Cell swaps for the two streams the old table carried (old L387-398, 1M/5M only):

| Stream/price | reactive | always compile | oracle | offline | Source |
|---|---|---|---|---|---|
| Sepsis 1M: old 1.06 / 1.85 / 0.94 / 0.94 | 0.99 | 11.54 | 0.97 | 0.96 | E4 sepsis/price=1M |
| Sepsis 5M: old 1.00 / 7.88 / 0.91 / 0.90 | 1.00 | 55.59 | 1.00 | 1.00 | E4 sepsis/price=5M |
| BPI 1M: old 6.01 / 1.18 / 0.93 / 0.93 | 2.06 | 18.04 | 1.37 | 0.54 | E4 bpi2019/price=1M |
| BPI 5M: old 2.52 / 1.86 / 0.84 / 0.84 | 1.00 | 11.20 | 0.44 | 0.31 | E4 bpi2019/price=5M |
| Other rows (on second, success, ToolPro, ablations, break-even) | see E4 dump in IDX t2_sim.E4 | | | | E4 |
| wiki_A / wiki_B columns (all 4 prices) | new | new | new | new | ADD; E4 |

Headline E4 absolutes: ours mean tokens 74.5M (Sepsis), 13.5G (BPI), 2.20G
(wiki_A), 11.6G (wiki_B) at native; always-compile wasted programs 846/11,836/
2,491/68,714 at 5M (E4 mean_wasted).

### C5. Section 7.2 readings, L403-412

| Line | Old | New | Action | Source |
|---|---|---|---|---|
| 404 | "on Sepsis at the 5M price it buys 845 programs ... and costs 7.9 times our policy, and at the 1M price it still pays 1.9 times and even staying reactive beats it" | buys 846 programs that never pay back and costs 55.6 times ours (manifest tax: every admission permanently taxes every arrival m=91 tokens); at 1M it pays 11.5 times and reactive still beats it; on wiki_B at 5M the ratio reaches 89.6x with 68,714 wasted programs | REWRITE | E4 always_compile + mean_wasted |
| 405 | "Gamma(1,5) pays 5.5 times our policy on Sepsis at 5M, wasting 564 compiles, while Gamma(1,20) survives that cell but pays 1.9 at 1M" | on Sepsis at 1M/5M both fixed priors now tie ours (1.00); the Gamma(1,5) failure moves to the autorpa price (3.43x, 835 wasted) and to wiki_B native (5.02x) | REWRITE | E4 ours_g15/ours_g120 |
| 406 | "population prior keeps the waste an order of magnitude lower and lands within 11 percent of the oracle on both Sepsis cells" | population prior lands within 3.5% of the oracle on Sepsis 1M (0.97) and ties at 5M | REPLACE | E4 oracle |
| 407 | "wasted compiles rise from 3 to 42 at the 1M price against the fixed-window ablation, and staying reactive beats ours by 7 percent at 5M" | at 1M ours wastes 3 vs fixed-window 0 (direction flipped); at 5M everything ties (reactive == ours == oracle) | REWRITE | E4 mean_wasted |
| 408 | "cheapest non-reference entry, or within one percent, in four of six cells ... gap to the oracle of 6 to 24 percent" | ours is the cheapest non-reference rule, or within one percent, in 3 of 16 cells; the oracle beats ours in 7 of 16 cells by 2 to 218 percent (ours/oracle = 1.02 Sepsis-1M to 3.18 wiki_A-5M), and ours beats the oracle by up to 27.5x on the native-price long streams (wiki_B), where the threshold-oracle compiles singleton families and eats the manifest tax | REWRITE | E4 |
| 409 | "under a fixed 60-step window our policy was never the cheapest rule on BPI 2019 and paid 2.2 times the oracle at both prices, because the window is a fifth of a synthetic stream but 0.02 percent of this log" | on BPI at 1M fixed-window pays 1.21 vs ours 1.00 while the oracle reference itself pays 1.37 (the threshold-oracle over-compiles singleton-heavy streams under the manifest tax); at 5M all tie | REWRITE (oracle no longer near-optimal) | E4 bpi2019 ours_hfix/oracle |
| 410 | "deployment-age horizon ... within 7 percent of the oracle at 1M and 18 percent at 5M" | ours is 27% below the oracle at BPI 1M and ties at 5M; vs the offline bound ours pays 1.85x (1M) and 3.2x (5M) | REWRITE | E4 bpi2019 |
| 411 | "both rules compile every returning family, and the population prior declines a family's first arrival" | mechanism unchanged; magnitudes above | KEEP-OLD (claim) | E4 |
| 412 | "break-even rule pays 1.15 to 1.31 in four of six raised-price cells, ties on Sepsis at 1M, beats ours by 7% at 5M" | break-even pays 1.00-1.12 on Sepsis/BPI at 1M, 0.42-1.00 at 5M (it wins BPI 5M where ours stays reactive) | REWRITE | E4 breakeven |
| 412 | "offline optimum sits 1.1 to 1.2 times below ours ... oracle and offline rows nearly coincide" | offline sits 1.0-3.6 times below ours (1.04 Sepsis-1M to 3.6 wiki_A-5M); oracle and offline diverge sharply on long streams (BPI 1M: oracle 1.37 vs offline 0.54) | REWRITE (major) | E4 oracle/offline_opt |

### C6. Section 7.3 estimator, L414-421

| Line | Old | New | Action | Source |
|---|---|---|---|---|
| 418 | "18 discover and told runs of the grid" | 36 (18 per model; e6 covers both models) | REPLACE | E6 |
| 419 | told control "0.08 to 0.18 raw ... floor of about 19.6k ... 0.003 to 0.018 with the floor subtracted" | told control reads 0.004-0.006 (GLM) and 0.007-0.012 (DS) with no floor subtraction (the new floor is too small to matter) | REWRITE | IDX estimator.{glm,ds}_told_* |
| 420 | "reads low ... by 0.13 (wizard), 0.15 (single page), 0.29 (sectioned)" | GLM: -0.54 / -0.25 / -0.38; DS: -0.46 / -0.43 / -0.40 | REPLACE | IDX estimator.*_bias_* |
| 421 | "one-signed ... delays compiling" | still one-signed on all six model/layout combos | KEEP-OLD (claim) | E6 |

### C7. Section 7.4 drift, L423-429 -- KEEP-OLD ENTIRELY

All numbers revalidated bit-identically on the new harness: 5/5 baseline, 15/15
appearance, 10/10 protocol breaks, selector timeout, break probability 0.4,
hazard mapping (0.01->0.004 ... 0.25->0.1), 0.02 per use = 0.05 change events
per use. Source DRIFT (summary.comparison same_outcome_mix true on all arms).

----------------------------------------------------------------------------------------------------

## D. Limitations, L431-441

| Line | Old | New | Action | Source |
|---|---|---|---|---|
| 434 | "one application family on two budget executors, single runs on two stronger models give higher shares" | one OpenApps family + three Android families on two budget models; the stronger-model corroboration splits (Sonnet 5 78%, glm-5v-turbo 36-38%) | REWRITE | AW12; IDX t15 |
| 435 | "compile price parameterizes a script the reactive run has already written" | compile prompt is an 814/928-token step view distilled from the trajectory | REWRITE | docs/nstar s3.1 |
| 436 | "estimator reads 0.13 to 0.29 below" | reads 0.25 to 0.54 below | REPLACE | IDX estimator bias |
| 438 | "q is 0.2 on the first model and 0.4 on the second (14 of 20 uses)" | q = 0.17 (5/30) and 0.23 (7/30); all failures value-level extraction (oracle_fail), 0 program errors | REPLACE | T13 deploy30 both models |
| 439 | "gate through the deployment path passes 15 of 15 on the first model and 12 of 15 on the second" | deployment-path rerun: GLM 14/20 over the four rerun files, DS 8/15; failures follow the binding | REPLACE | T13 regate* |
| 440 | "skill condition has two runs per layout ... saving flips sign across runs" | three runs per layout; sign still flips across layouts/models (DS negative everywhere) | REPLACE | RUNS skill rows |

----------------------------------------------------------------------------------------------------

## E. Appendix measure (app:measure), L591-640

| Line | Old | New | Action | Source |
|---|---|---|---|---|
| 605-606 | skill doc "roughly 240 tokens, an order below Xu's one-tenth-of-an-episode footprint" | unchanged claim, re-check token count of the new prompt | KEEP-OLD (verify) | — |
| 609-612 | harness/pricing prose: "list rates, cached reads at the cached rate" | explicit three accountings (raw / cache-discounted / dollar-equivalent), r = 0.20/0.318, completion at face | REWRITE | docs/cache-adjusted-accounting.md s2 |
| 614 | floor "19.6k on the GLM grid, 45k and 21k on the Sonnet 5 and GLM 5.3 corroboration runs" | 0.34k-1.06k (GLM grid); t15 runs have near-zero cache share (Sonnet 1.1%, glm-5v-turbo 6-10%) | REPLACE | RUNS; S t15 cache_share_prompt |
| 616 | "burns most of the floor before any task work starts ... dilutes the discover share by roughly three points" | floor < 1.6% of any arm; dilution under one point; subtraction dropped | REWRITE | RUNS |
| 618 | "3-by-4-by-3 grid ... all 27 solving runs; skill has two runs per layout, all six solved" | 3-by-4-by-3, all 36 solving runs (skill now included, 3 seeds) | REPLACE | RUNS |
| 624 | Table 1b caption "all 27 solving runs succeed" | all 36 | REPLACE | RUNS |
| 626 | skill caption "857k/917k (wizard), 788k/1105k (single), 2086k/1135k (sectioned), recovering -55 to 47 percent" | DS skill means 139k / 152k / 161k, recovering -34 / -23 / -26 percent (costs more than discover on every layout) | REPLACE | IDX measure_grid.ds.*.skill* |
| 633-635 | Table 1b rows (1434k/1045k/201k/21.5k 87%; 1099k/1594k/148k/21.5k 88%; 1422k/950k/208k/21.5k 87%) | wizard 115k/175k/32k/1.1k 72% [55,88]; single 131k/117k/35k/1.1k 73% [64,79]; sectioned 136k/203k/42k/1.1k 69% [57,74] | REPLACE | IDX measure_grid.ds.* |
| 639 | "pooled means, not any single run, carry the findings" | keep | KEEP-OLD | — |

## F. Appendix compile path (app:compilepath), L642-656

| Line | Old | New | Action | Source |
|---|---|---|---|---|
| 646 | "Three induction attempts cost 24.2k each ... pass rate 1.0" | 14.5k cache-adjusted each (raw 6,397/20,619/17,933); gates 15/15 | REPLACE | IDX compile_path.glm |
| 647 | DS "21.3k per attempt, gate 1.0, d = 22.5k, deployment success 6 of 10" | 13.4k; 15/15; d = 491; 23/30 | REPLACE | IDX compile_path.ds |
| 648 | "d = 20.0k, of which 19.6k is the harness floor" | d = 347 (GLM): one extraction call, prompt ~160 + completion ~185, no screenshots | REPLACE | T13 deploy30; docs/nstar s2.5 |
| 649 | "one call of about 400 tokens" | ~350 (GLM) / ~490 (DS) | REPLACE | IDX constants.d |
| 650 | "saving per use 390k to 500k ... N* ~ 0.05" | s = 79.1k (GLM) / 87.3k (DS); N* = 0.184 / 0.153 (and full-build (C+c)/s = 1.39 / 1.46 as the amortization reading) | REPLACE | IDX constants |
| 653-656 | no-typecheck demo (zero of ten; list-vs-string crash; retry never fired; trailing period) | new-harness anatomy instead: all 12 failures across both models' 60 deployment uses are value-level oracle_fail (e.g. 'Daily check-in' -> 'Daily check-in.'), 0 program errors, 0 type-check rejects; the failed goal set overlaps across models | REWRITE | T13 deploy30 uses[]; docs/nstar s4.3 |
| — | fullread variant (methods-v2 "in progress") | ADD: LLM-reads-full-episode variant costs 23.1k raw (GLM: 19.6k/20.6k/29.1k) and 23.2k (DS), gates 15/15 both | ADD | IDX compile_path.*.fullread* |

## G. Appendix estimator (app:estimator), L658-695

| Line | Old | New | Action | Source |
|---|---|---|---|---|
| 664 | caption "floor subtracted ... matches Table 1" | no floor subtraction needed; ground truth = pooled cache-adjusted shares | REWRITE | E6; RUNS |
| 673-677 | Table rows: 0.56/0.69/-0.13/0.18/0.018; 0.53/0.68/-0.15/0.08/0.003; 0.47/0.76/-0.29/0.11/0.006 | GLM: 0.23/0.77/-0.54/0.005; 0.24/0.49/-0.25/0.005; 0.16/0.55/-0.38/0.004 (raw col dropped). ADD DS rows: 0.26/0.72/-0.46/0.009; 0.30/0.73/-0.43/0.009; 0.30/0.69/-0.40/0.008 | REPLACE + ADD | IDX estimator |
| 683-684 | "All 18 discover and told runs ... two of nine discover runs split on the landing-URL condition" | 36 runs (both models); split-condition count not re-tabulated on the new harness | REWRITE | E6 |
| 687 | "Raw it reads 0.08 to 0.18 ... floor of about 19.6k" | told control reads 0.004-0.012 directly (floor too small to distort) | REWRITE | IDX estimator |
| 689 | "0.003 to 0.018 with the subtraction" | 0.004 to 0.012 without subtraction | REPLACE | IDX estimator |
| 691 | "by 0.13 / 0.15 / 0.29" | by 0.54 / 0.25 / 0.38 (GLM); 0.46 / 0.43 / 0.40 (DS) | REPLACE | IDX estimator bias |
| 693 | "dumps the event feed ... reads the application's own source" | keep qualitative explanation if transcripts still show it (verify before keeping) | KEEP-OLD (verify) | — |
| 695 | "estimated execution term is 1.37 / 1.60 / 2.52 times the told cost" | 3.34 (wizard) / 1.50 (single) / 1.85 (sectioned); still tracks the bias | REPLACE | E6 + RUNS |

## H. Appendix reconcile (app:reconcile), L697-735

Table tab:reconcile rows:

| Line | Row | Old | New | Action | Source |
|---|---|---|---|---|---|
| 711-712 | xu rows ($0.12, 152, 12.7%; $0.01, 12, 13.4%) | unchanged | unchanged | KEEP-OLD | xu2026realcost |
| 713 | AutoRPA row (233k tok, ~4, ~full) | unchanged | unchanged | KEEP-OLD | chen2026autorpa |
| 714 | Ours GLM: 24.2k tok, ~0.05, 68-76% | 14.5k tok (cache-adj; 15.0k raw), N* = 0.18, share 77% (wizard) or 49-77% (layout range) | REPLACE | IDX constants.glm |
| 715 | Ours DS: 21.3k tok, ~0.02, 87-88% | 13.4k tok, N* = 0.15, 69-73% | REPLACE | IDX constants.ds |
| — | ADD rows | — | full-build (C+c)/s: 1.39 (GLM) / 1.46 (DS); dollar-equivalent N*: 0.63 (GLM) / 1.84 (DS); AutoRPA same-accounting 233/(68.7-12.8) = 4.2 | ADD | IDX constants; docs/nstar s5.3/s6 |

Prose:

| Line | Old | New | Action | Source |
|---|---|---|---|---|
| 720-726 | xu arithmetic (0.12/152 = $0.00079; /0.0062 = 12.7%; 152.4; 13.4%; 12x; 46k-51k; 27-32) | unchanged (their numbers) | KEEP-OLD | xu2026realcost |
| 728 | AutoRPA "233k ... amortized after approximately four tasks ... implied saving 233/4 ~ 58k" | keep 233k/≈4; prefer the direct Tab.5 reading: saving 68.7-12.8 = 55.9k, share 0.81, same-accounting N* = 4.17 | REWRITE (per nstar s7.3) | docs/nstar s5.3 |
| 731 | "raw pooled shares (517-176)/517 = 66%, (413-145)/413 = 65%, (514-137)/514 = 73%; subtracting the 19.6k floor gives 69/68/76" | raw pooled shares (204-66)/204 = 68%, (per-layout raw 53-68%); no floor subtraction; cache-adjusted 77/49/55 | REWRITE | RUNS raw_tok; IDX |
| 732 | "C_eff = 24.2k (pass rate 1.0) and d = 20.0k, so N* = 24.2/(517-20) ~ 0.05" | C_eff = 14.5k (p = 1.0), d = 0.35k, q0 = 0.17, so N* = 14.5/((1-0.17)x95.3-0.35) = 0.18 | REPLACE | IDX constants.glm |
| 733 | "Claude Sonnet 5 ... rho = 83.6%; GLM 5.3 ... 79.8%" | Sonnet 5: 78% (disc, $0.67+$0.15 runs); glm-5v-turbo: 36-38% | REPLACE | IDX t15 |
| 734 | skill runs "384k/602k wizard, 357k/627k single, 177k/254k sectioned ... pooled discover means 517k/413k/514k" | GLM skill means 41k/48k/45k vs discover 95k/69k/56k (3 seeds each) | REPLACE | IDX measure_grid.glm.skill |
| 735 | DS "floor-adjusted shares 87/88/87 ... C_eff 21.3k ... d 22.5k ... 6 of 10 ... N* = 21.3/(1434-22) ~ 0.02" | shares 72/73/69; C_eff 13.4k; d 0.49k; 23/30; N* = 0.153 | REPLACE | IDX constants.ds |

## I. Appendix drift (app:drift), L737-781 -- KEEP-OLD ENTIRELY

Every number revalidated identical on the new harness (DRIFT
summary.comparison): arms table (L764-769), 30 runs, silent-mode discussion,
break probability 0.4, hazard mapping L780, 0.02 -> 0.05 change events L781.

## J. Appendix mech (app:mech), L783-934

| Block | Line | Old | New | Action | Source |
|---|---|---|---|---|---|
| setup | 790 | "N* ~ 0.05 makes the policy compile on the first arrival" | N* = 0.18, still first arrival | REPLACE | IDX |
| (a) cooldown | 807-809 | 0.3: 33.0/37.5/132.6/19.2; 0.6: 18.1/12.9/107.3/12.9; 1.0: 9.44 all | 0.3: 5.48/7.60/18.89; 0.6: 4.77/4.69/14.42; 1.0: 4.26 all (millions; oracle column replaced by the clairvoyant reference or dropped) | REPLACE | E5 a_cooldown |
| (a) caption | 798-800 | "inflation saves 28% at gate 0.6 (5.1M, se 0.6M); indistinguishable at 0.3 (4.4M, se 3.7M)" | at 0.6 inflation saves 1.8% (paired diff 87k, se 75k, not significant); at 0.3 inflation LOSES 2.11M (se 0.49M, significant); blacklist ruinous (3.4x/2.9x/4.4x) -- the justification for price inflation no longer holds at deep gate failure | REWRITE (risky) | E5 a_cooldown paired diffs |
| (b) decay | 822-832 | full table (59.9/60.8/62.5/62.3/48.5 etc.), "9.43M to 9.50M inert at measured price" | all four half-lives identical at native AND 5M on every stream (bursty 4.12M, hot 23.6M@5M, regime 4.14M, long30k 403M): the mechanism is inert everywhere under the new constants | REPLACE table with statement (risky: removes a table) | E5 b_decay/* |
| (b) prose | 837-839 | T120 recovers 1.9M of 4.1M; costs 0.8M (se 0.6M); T30 pays 2.4M (se 1.1M) | no deltas exist (all zero) | DELETE | E5 |
| (c) prior | 846-863 | ladder table by N* = 0.05/2.1/10.5/42 (9.45 ... 156.8) | replaced by E5 c_prior cells: 1M -- population 9.51M beats gamma(1,5) 10.31M and gamma(1,20) 11.16M; native and 5M -- all identical | REPLACE (design changed: no N* ladder in E5) | E5 c_prior/* |
| (c) prose | 866-868 | 0.42M se 0.10M at N*=2.1; arrival 2.8 vs 1.3/10.3; 9.0M of 62.0M; 2.2 vs 1.8 wasted; 4.1M at N*=42 | no counterparts in E5 | DELETE | E5 |
| (c) real | 869-871 | means 1.21/1.35/2.27 vs oracle; Helpdesk 1M loss 31.9M se 6.0M; Gamma(1,5) 5.5x; ten arrivals | helpdesk native: population 121.4M vs 125.2/126.7M (best); helpdesk 1M: population ties gamma(1,5) 146.3M, gamma(1,20) 147.9M; sepsis 1M: ties gamma(1,5) 78.13M; sepsis native: population LOSES 6.56M to the gammas (74.5 vs 68.0M) -- the population prior now has a real failure cell | REWRITE | E5 c_prior/real:* |
| (d) gate | 887-893 | analytic (1-q)^n table | model-independent | KEEP-OLD | — |
| (d) | 896-898 | "five sits inside 4-9; silent model moves argmin to 14-24; 0.45 bound at 95%" | analytic, KEEP; note the measured deployment-path gate is no longer 15/15 (see F/L224) | KEEP-OLD | — |
| (d) | 901 | "first model ... 15 of 15 uses, at a total cost of $0.88" | GLM deployment-path rerun 14/20 at ~$0.0005 total; DS 8/15 | REPLACE | T13 regate* |
| (d) | 902-903 | "second model ... 12 of 15 ... one binding fails under all three" | DS 8/15 across three rerun files (4/5, 0/5, 4/5); binding-following value-level failures persist | REPLACE | T13 regate* |
| (e) horizon | 908 | "capping at 1/h = 50 changes no decision below compile prices of about 20M" | the cap binds from 5M up (N* = 87 at 5M > 50); capping is what keeps ours from overbuying on long streams | REWRITE | CONST trigger; E4 |
| (e) | 909 | "30,000-arrival streams ... Zipf stream over 3,000 families" | E5 horizon uses long30000_bursty (30k arrivals, 3 families) + held-out BPI; the 3,000-family Zipf variant is not in E5 | REWRITE | E5 d_horizon cells |
| (e) table | 924-926 | 44/16/28-cell aggregates (1.14/2.89/28 etc.) | replaced by E5 d_horizon cells: 5M bursty -- fixed 22.27M / raw doubling 26.44M / capped 22.27M; 5M long30k -- raw doubling 431.6M vs capped 2,207.6M (raw better; flagged follow-up); held-out BPI native -- capped doubling 13.51G best vs fixed 13.92G vs raw 25.62G; BPI 5M -- capped == fixed 19.50G, raw 26.52G | REPLACE (risky) | E5 d_horizon/* |
| (e) prose | 932-934 | "0.92 -> 0.82 sweep cell; 7 and 18 percent over oracle; 5/39/119/123 for fixed" | sweep cell: at 5M ours stays reactive and always-compile wins 0.78-0.86; held-out BPI: capped doubling rel-to-clairvoyant 0.085 (native) / 2.296 (5M); fixed 0.088 / 2.296 | REWRITE | E5; E8 |

## K. Abstract / intro / conclusion echoes (must move with B-J)

| Line | Old | New | Source |
|---|---|---|---|
| 12 | "150 to 293 reuses ... about four" | KEEP-OLD (xu/AutoRPA numbers) | — |
| 17 | "68 to 76 percent ... 87 to 88 percent on a second" | 49 to 77 percent ... 69 to 73 percent | IDX |
| 20 | "pays 1.1 to 1.2 times an offline optimum ... always compiling pays up to 7.9 times more" | pays 1.0 to 1.9 times the offline optimum on the two process logs (1.04 Sepsis-1M, 1.85 BPI-1M; up to 3.6x on the wiki streams at 5M); always compiling pays up to 89.6 times more (wiki_B, 5M) | E4 |
| 77 | "N* ~ 0.05 ... at 233k still below one use ... at ten million ... about twenty" | N* ~ 0.18; at AutoRPA's 233k it is 2.9 uses; at ten million 126 uses (regime framing must be re-anchored; the xu anchor scales to ~1.8M at the new episode cost) | IDX constants; docs/nstar |
| 83 | "68 to 88 percent ... 80 to 84 ... all 54 solving runs" | 49 to 77 / 69 to 73 ... 78 and 36-38 ... all 72 solving runs | IDX; E6 |
| 84 | "N* ~ 0.05" | N* ~ 0.18 (0.15 DS) | IDX |
| 85 | "within two percent of an oracle ... hundreds of wasted programs" | ties the oracle exactly ... tens of thousands of wasted programs (68,714 on wiki_B at 5M) | E3; E4 |
| 236 | "N* ~ 0.05" | 0.18 | IDX |
| 275 | "changes no decision below compile prices of about 20M ... +c_f about two percent" | cap binds from 5M; +c_f ~ 0.12% of H_f s_f at the measured costs (95k vs H*s) | CONST; docs |
| 284 | "every attempt costs 24.2k" | 14.5k | IDX |
| 292 | "measured 20.0k tokens" | 347 tokens | IDX |
| 313-314 | T_1/2 = 120, Gamma(1,5) | unchanged (mechanism constants, revalidated inert) | CONST trigger |
| 355 | "16 times more" | 5.4-5.5x (OpenApps) / 1.2-1.7x (Android) | E3 |
| 446 | "68 to 76 percent" | 49 to 77 percent | IDX |

## L. Android block (new; no old counterpart -- ADD)

Suggested placement: one paragraph + one small table in section 5 or appendix.

- Families (cache-adjusted, successful runs; IDX android.*): contacts GLM c 75.6k / L 69.4k (rho 0.08); calendar GLM c 207.3k / L 297.2k (rho -0.43, told defect); markor GLM c 108.6k / L 81.6k (rho 0.25); contacts DS c 136.2k / L 82.9k (rho 0.39); calendar DS c 592.9k / L 546.0k (rho 0.08); markor DS c 66.7k / L 101.8k (rho -0.53, failed seed excluded). Raw means in IDX android.*.c_raw.
- Compile chain (AW14): contacts gates 5/5 x3 (GLM) and 5/5,5/5,0/5 (DS), deploy 30/30 both; calendar gates 0/5 x3 (GLM; DS best 1/5), deploy 0/30 (GLM) / 7/30 (DS); markor gates 0/5 both models, deploy 0/30 both.
- Story: rho is a property of the family (0.39 contacts-DS down to negative), and p=0 families make C_eff diverge -- ours prices them out and never compiles (E3 android N* = inf).

----------------------------------------------------------------------------------------------------

## Stats

- 178 ledger rows above (many rows carry several printed numbers -- e.g. each
  E3/E4 table row carries three to six cells -- so the total count of audited
  printed numbers is roughly 400).
- Row actions: REPLACE 60, REWRITE (number + claim must move) 40, KEEP-OLD 20
  (the whole drift appendix, the analytic gate table, the xu/AutoRPA reconcile
  rows, stream identities, mechanism constants), DELETE 7, ADD 7 rows plus the
  Android block (section L: 12 measured cells + compile chain) and the new
  Table 3 columns (wiki_A/wiki_B, native/233k prices).
- Every new value is machine-checked: `check_numbers_new.py` 641/641 green;
  old `check_numbers.py` untouched and still 812/812 green.

## The 5 riskiest swaps (prose meaning changes)

1. **Reconcile table + intro regime framing (L77, H).** N* moves 0.05 -> 0.18,
   and AutoRPA's 233k now implies 2.9 uses (old: still below one) while the xu
   anchor rescales from ~10M to ~1.8M at the new episode cost. The "one use
   pays back the build" claim survives (0.18 < 1, full-build 1.39/1.46 < 1.5),
   but the sentence "at 233k it is still below one use" is now false and the
   price-ladder anchoring sentence must be rebuilt around the new c.
2. **Mid-condition (negative control) story (L200, B3).** Old: structure text
   recovers at most 26%/39% of discovery. New: GLM mid recovers 52-87% of the
   gap (single-page nearly matches told) while DS mid costs 50%+ MORE than
   discover on wizard/sectioned. The "knowledge that cannot act removes almost
   nothing" reading only survives on DS; on GLM it must be weakened or
   reinterpreted (cache-adjusted told/mid costs changed the balance).
3. **Oracle and offline relations in section 7 (C5, L409-412 + abstract L20/L85).**
   The oracle threshold rule no longer nearly coincides with ours or the
   offline bound: it ties ours on synthetic streams but pays 11.7x ours on
   BPI/wiki_B at native (it compiles singleton families and eats the manifest
   tax), while the offline optimum drops to 0.54-0.31x ours on long streams.
   "Within two percent of the oracle" and "1.1-1.2 times the offline optimum"
   both die; the qualitative claim must be restated per stream class.
4. **Always-compile blowup and manifest tax (L404, abstract L20).** 7.9x ->
   55.6x on Sepsis-5M and up to 89.6x on wiki_B-5M, driven by the newly
   modeled permanent manifest tax (m=91 per entry per arrival) plus 846-fold
   library growth. Stronger claim, but the mechanism sentence (tau externality)
   must be added or reviewers will not believe the number.
5. **Mechanism appendix (J).** Two selection justifications flip: price
   inflation now loses at gate 0.3 (old: saves 28% at 0.6, indistinguishable at
   0.3), and the decay table collapses to "inert everywhere" (old: meaningful
   T_1/2=120 win). The horizon story is new (capped doubling + manifest-tax
   threshold; raw doubling wins the 5M long stream, a flagged follow-up). The
   appendix needs a re-derivation of why each chosen mechanism is still the
   pick, not just number swaps.

Secondary flags (not top-5): the estimator bias doubles (0.13-0.29 -> 0.25-0.54,
still one-signed); glm-5v-turbo corroboration drops to 36-38% (the
"stronger models give higher shares" sentence in L434/L199 must go); q moves
0.2/0.4 -> 0.17/0.23 and the regate counts 15/15 & 12/15 -> 14/20 & 8/15; the
Helpdesk column leaves Table 3 (E5-only), replaced by two wiki streams; the
Android rho row in methods-v2 s6 ("0-0.54") does not match the measured
-0.53..0.39 and needs the coordinator's ruling (calendar told-defect note in
CONST).

## 2026-09-17 (post-handoff session): B_pop sensitivity macros + §5.3 sentences (ADDITIVE)

- numbers_manifest_build.py: new "replacement lottery (B_pop)" block. Per admitted cell (GLM 5, DS 2): n{M}{F}Bpop (k1), n{M}{F}NstarBpop (f1), n{M}{F}HdblBpop (f4), n{M}{F}HfailBpop (f3); ranges n{M}NstarBpopMin/Max, n{M}HfailBpopMin/Max, cross-model nHdblBpopMin/Max. All expr over existing macros (C, q, c, d, AdmRate, CFailMedian). Admitted set derived from by_key at build time.
- check_numbers_new.py: new G15 (46 assertions) recomputing every B_pop quantity from t16 constants_table.json; includes prose-truth assert "DS max HfailBpop <= 0.02" (the §5.3 default-hazard sentence).
- body.tex §5.3: three sentences after the Prop. ss derivation notes (line ~370): N*(B_pop) ranges 5.4–30.0 GLM / 49.4–301.2 DS; h_dbl 0.0002–0.0084; h_fail 0.032–0.157 GLM / 0.003–0.020 DS; 0.02 default point. Break rates in h units (per-use break probability), NOT λ units — λ would conflate change rate with the sweep's break-rate default; both DS cells are at/past 0.02 in h units (Contacts 0.0198).
- §5.4 trigger upgrade (same session): B̂ = C_f/p̂ → C_f+(1/p̂−1)C_fail (prose, Algorithm 1 line + supplied list, notation table). Note for §6 refill: stored E3/E4 native runs charged equal attempt prices (C_fail=C) under which the two formulas coincide; sweep cells with the raised failed-attempt price recorded the narrower estimate — disclose or re-run at refill.
- State: check_numbers_new 731/731, check_numbers 812/812, latexmk 0 errors, inline_numbers replaced 10 call sites.
