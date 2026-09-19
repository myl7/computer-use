# t2_sim status — methods-v2 T2 simulation layer

Updated 2026-09-07. Everything here is zero-API, CPU-only replay code:
`computer-use/t2sim/` (engine + experiments + CLI + tests), results in this
directory. Spec: `docs/methods-v2.md`.

## 0. HORIZON fix (2026-09-07, second pass)

E4's first full run had `ours` losing to always-reactive on the long streams
(BPI/1M 0.61x, wiki_B/1M 0.40x).  Isolation (isolation_a/a2/c.py in this
directory) proved the port bitwise-faithful — old and new engines produce
identical tokens/compiles on the full BPI stream and on wiki_B-20k at old
constants — and pinned the flip on the manifest-tax channel: the raw
deployment-age projection `lambda_hat * t` (old-engine semantics, validated
only where families are born early and N* ~ 2) compiles late-blooming rare
families (81% wasted compiles on BPI/1M), and every admission is a permanent
`m`-per-arrival tax the tax-blind trigger never prices (tau 25.4G of ours'
31.9G vs reactive's 92.6M).  Fix in `sim.py` (deployed `capped_doubling`):
the doubling window is the FAMILY's deployment age `t - first_seen`, the
projection is capped at the program lifetime (`horizon_cap` 50 = 1/h at the
old validated h=0.02), and the threshold carries the manifest externality
`m * t` (methods-v2 section-2 one-line extension; inert at m=0, so
validate.py stays 27/27 bitwise).  Regression test:
`tests/test_longstream_regression.py` (BPI, tau off, ours <= 1.05x reactive
at every ladder price, + projection guards).  After the fix BPI reads
reactive/ours = 1.44/1.89/2.06/1.00 (native/233k/1M/5M); the 5M tie is the
Theorem-2 cap making N*=87 compiles unpayable (E5 d_horizon documents it;
breakeven/oracle win there — flagged follow-up).

## 1. What is ready

| Piece | State |
|---|---|
| `computer-use/t2sim/sim.py` | stream-account engine: τ(n)=m·n+τ₀ per arrival, q(n)=ε(n)+(1−ε(n))·q₀, permanent manifest entries, C_eff with gate coin p and inflate-cooldown; policies always_reactive / always_compile / on_second / success_count / toolpro_port / ours (Algorithm 1) / oracle / breakeven + offline-optimum DP lower bound |
| `computer-use/t2sim/streams.py` | synthetic patterns (verbatim port of old `gen_stream`) + loaders for the Wikipedia JSONL streams (`datasets/wiki-stream/stream_{tool_hot_tail,content_tail}.jsonl`, ts-sorted, windowable) and the old process logs (`experimental-results/openapps/real-streams/real_streams.json`: sepsis 1,050/846, helpdesk 4,580/226, bpi2019 251,734/11,973; windowable) |
| `computer-use/t2sim/experiments.py` | E3/E4/E5/E8 cell builders, serial or multiprocessing execution, bootstrap CIs, table assembly + printing |
| `computer-use/t2sim/run.py` | CLI (`--constants --exp E3,E4,E5,E8|all --reps --seed --jobs --quick --out-dir`) |
| `computer-use/t2sim/validate.py` | faithfulness check vs the old paper Table 2 (old constants, τ/ε/q(n) off) |
| `computer-use/t2sim/stats.py` | seeded percentile bootstrap (mean CI; ratio CI with joint resample indices), paired SEs |
| `computer-use/t2sim/tests/` | 56 tests, all green (see §5) |

Finished this pass (was half-written after the crash): `old_real_streams`
loader now honors `window`; E4 real cells now seed each policy/variant with
the same fresh RNG (true paired seeding, which the joint-index bootstrap
assumes); E8 τ/ε sensitivity now reads `e8.tau_modes`/`e8.eps_modes` from
the constants (quick mode shrinks them); `--quick` now shrinks the E5 long
stream via `e5.long_n`; fixed `offline_opt` KeyError in the E3/E4 assemblers;
absolute-token CIs print with token formatting instead of ratio formatting.

## 2. Validation vs the old Table-2 (old constants)

`python3 computer-use/t2sim/validate.py` → `validate_old_constants.json`:
**verdict PASS — 27/27 cells bitwise-identical (worst deviation 0.000%),
15/15 qualitative ordering checks match.** With τ=0, ε off, q₀=0 the new
engine reproduces the old submitted table draw-for-draw:

| pattern | old ordering (rel_to_ours) | reproduced |
|---|---|---|
| poisson | reactive 15.85 ≫ toolpro 7.63 > success 4.83 > on_second 1.94 > breakeven 1.43 > always_compile 1.01 ≈ ours 1.0; oracle 0.983, offline 0.998 | identical |
| zipf | reactive 15.35 ≫ toolpro 8.82 > success 4.44 > on_second 1.68 > breakeven 1.39 > ours 1.0 > always_compile 0.936; oracle 0.991, offline 0.955 | identical |
| bursty | reactive 16.58 ≫ toolpro 8.11 > success 4.49 > on_second 1.87 > breakeven 1.46 > always_compile 1.01 ≈ ours 1.0; oracle 1.063, offline 1.041 | identical |

Documented deltas (all inert in old-constants mode, full list in
`validate_old_constants.json` → `deltas_note`): τ(n) manifest tax, ε(n)
selection cliff and q(n) per-use failure are new channels (off here); the
legacy drift hazard h is kept as a validation-only key; the macro/two-tier
rule and EB-shrink/loss-aversion audition variants were dropped (the old
table ran `tier="off"`). Under methods-v2 constants the deltas that will
move numbers: every arrival pays τ(n) (always-compile on Sepsis-scale
streams pays it 846-fold), program uses fail at q(n) with fallback c but
stay admitted, and selection failure can be swept past the cliff.

## 3. Constants to fill when measurement lands

File: `computer-use/t2sim/constants.template.json` (schema:
`constants.schema.json`; set `"status": "placeholder"` → `"measured"` per
cell as numbers land — every output JSON records which statuses were used).

Already measured and in place (methods-v2 §6, OpenApps wizard, cache-adjusted):

- `cost_sets.openapps_glm.layouts.wizard` = c 95,296, L 22,018, ρ 0.769,
  C(stepview) 14,549, p 1.0, d 347, q₀ 0.17; `m` 91
- `cost_sets.openapps_ds.layouts.wizard` = c 114,518, L 31,626, ρ 0.724,
  C(stepview) 13,361, p 1.0, d 491, q₀ 0.23; `m` 91
- `price_ladder`, `epsilon_cliff` (off, headline), `trigger` (Algorithm-1
  defaults), stream registry.

Still to fill (exact keys):

| Key | Value when it lands | Source |
|---|---|---|
| `cost_sets.openapps_{glm,ds}.tau0` | prompt intercept 368 + assumed completion ≈ 199 ⇒ **τ₀ ≈ 567** cache-adjusted tokens (m slope 90.035 confirms m≈91) | `experimental-results/guiexp/m_library2/summary.json` → `stream_tax`; confirm with d_full |
| `cost_sets.openapps_{glm,ds}.layouts.single_page.{c,L,rho,C,p,d,q0}` | per-layout T1.1 numbers (currently provisional = wizard) | T1.1 |
| `cost_sets.openapps_{glm,ds}.layouts.sectioned.{...}` | same | T1.1 |
| `cost_sets.openapps_{glm,ds}.layouts.*.C_fullread` | fullread compile price (optional key, reported alongside C; expected ≈ stepview + ~46k) | compile-variant experiment (running) |
| `cost_sets.android_glm.layouts.{contacts,calendar,markor}` | per-family c, L, ρ from T1.2; C, d, q₀ from T1.4. Known so far: contacts p = 1.0 (deploy 30/30); calendar p ≈ 0.2 (DS best 1/5), 0 (GLM); markor p = 0 (both) | T1.2 / T1.4 |
| `cost_sets.android_ds.layouts.{contacts,calendar,markor}` | same, DeepSeek side | T1.2 / T1.4 |

Android caveat: p = 0 families (markor both models, calendar-GLM) make
C_eff = C/p diverge — `ours` prices them out and never compiles (correct),
but `always_compile` retries every arrival and pays C each time. Either
keep that as the adversarial semantics or floor p (e.g. p ≥ 0.05) and note
it; the schema requires p > 0, so set the floor when filling.

## 4. Command lines

All serial-friendly; after the crash use `--jobs 1` (or a small `--jobs`)
until the machine is trusted. Outputs land here
(`experimental-results/guiexp/t2_sim/`).

```bash
cd /Users/myl/app/computer-use

# smoke (tiny, all four experiments, <1 s total):
python3 computer-use/t2sim/run.py \
    --constants computer-use/t2sim/constants.template.json \
    --exp all --reps 2 --jobs 1 --quick

# full runs (defaults: reps 20, seed 7, B 2000; jobs = all cores — pass --jobs 1 to stay serial)
python3 computer-use/t2sim/run.py --constants computer-use/t2sim/constants.template.json --exp E3 --reps 20 --jobs 1
python3 computer-use/t2sim/run.py --constants computer-use/t2sim/constants.template.json --exp E4 --reps 20 --jobs 1
python3 computer-use/t2sim/run.py --constants computer-use/t2sim/constants.template.json --exp E5 --reps 20 --jobs 1
python3 computer-use/t2sim/run.py --constants computer-use/t2sim/constants.template.json --exp E8 --reps 20 --jobs 1

# validation against the old Table 2 (old constants):
python3 computer-use/t2sim/validate.py
```

E3 = synthetic headline (3 patterns × 2 cost sets, 8 policies + offline
optimum, rel-to-ours with 95% bootstrap CIs). E4 = real streams (wiki_A
tool-hot-tail 65,564; wiki_B content-tail first 100k; sepsis; BPI-2019
held-out) × prices {native, autorpa 233k, 1M, 5M} + ours ablations
(Gamma(1,5), Gamma(1,20), fixed-horizon) + oracle/offline/break-even.
E5 = mechanism stress (cooldown × gate rate; decay half-life incl. the
30k stream; arrival prior incl. real selection streams; horizon mode incl.
BPI held-out) with paired seeding and paired SEs. E8 = price ×
artifact-strength grid (both seedings) + τ and ε sensitivity.

## 5. Tests and timings

```bash
/Users/myl/app/computer-use/.venv-gui/bin/python3 -m pytest \
    computer-use/t2sim/tests -q        # sequential; 56 passed in ~0.3 s
```

Coverage: τ accounting per arrival (exact small-stream identities), q(n)
composition (cliff sigmoid, engine fallback costs), library growth
(always-compile → every distinct family vs ours bounded; manifest entries
permanent), CI computation (mean/ratio bootstrap, joint indices, paired SE),
stream loaders (ts sort, windows, cache), port-equivalence vs the old
`openapps-exp/policy_sim.py` (draw-for-draw, all 8 policies, incl. gate
failures), and end-to-end smokes (E3–E8 on dummy constants; CLI wiring).
The E3+E4 smoke asserts its own <30 s budget — it runs in ~0.15 s.

Timings on this machine (serial, `--jobs 1`):

| Run | Wall |
|---|---|
| full test suite (56 tests) | 0.3 s |
| `validate.py` (old Table-2 rerun, 27 cells) | 0.2 s |
| **smoke E4** (quick, template constants: 8 cells = 4 streams × {native,1M}, reps 2, ~3.8k arrivals/cell) | **0.32 s cell wall (0.4 s process total)** |
| smoke E3+E5+E8 (quick, template constants: 6+12+60 cells) | 0.37 s total |
| full E4 (est., reps 20, 4 prices, ~418k arrivals/rep-row set) | ≈ 11 min serial; divides over `--jobs` |
| full E3 / E5 / E8 (est., reps 20) | < 10 s / ≈ 1 min / ≈ 20 s serial |

## 6. Current smoke outputs in this directory

`E3_quick.json`, `E4_quick.json`, `E5_quick.json`, `E8_quick.json`
(placeholder-flagged cells recorded in `meta.cost_set_status`),
`validate_old_constants.json`. Sanity highlights from the smoke E4 at the
measured constants: native price N*=0.185 → ours == always_compile ==
oracle (compile-on-first-use is optimal); at 1M price (N*=12.7) ours keeps
n_lib ≈ 8 vs always-compile 479 on wiki_B and beats it 8.2×, with the
manifest tax τ visible (13.4M tokens of always-compile's 30.1M on wiki_B
is τ). These are quick-mode 2-rep numbers — indicative only.
