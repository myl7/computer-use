# E3 / E4 rerun under constants.measured.v3 + full buy formula — comparison vs the old published runs

Generated 2026-09-19 (W2+W3). All runs are pure local CPU, default `buy_formula="full"`
(W1.5's Algorithm 1 B̂ = C + (1/p̂ − 1)·C_fail), trigger otherwise as shipped in
`constants.measured.v3.json` (gate_prior=pi, spend_cap=on, cooldown=inflate, half_life=120).
No old file under `experimental-results/guiexp/t2_sim/` was touched.

## 1. What was run (commands, wall time, outputs)

All invocations from `/Users/myl/app/computer-use`, interpreter
`.venv-gui/bin/python`, output dir `experimental-results/guiexp/t2_sim_v3/`
(new). `--reps 20 --seed 7` everywhere; `--jobs` left at default (all cores).

| step | command (abbreviated) | wall | output |
|---|---|---|---|
| W2 E3 full | `computer-use/t2sim/run.py --constants computer-use/t2sim/constants.measured.v3.json --exp E3 --reps 20 --seed 7 --tag full_v3 --out-dir .../t2_sim_v3` | 0.8 s total (cells 0.3 s; 12 cells = 3 patterns × 4 cost sets, n=300, 20 reps) | `E3_full_v3.json` + `.log` |
| W2 E3 narrow ablation | same + `--buy-formula narrow --tag narrow_v3` | 0.8 s | `E3_narrow_v3.json` + `.log` |
| W3 E4 full (incl. BPI) | `... --exp E4 --reps 20 --seed 7 --tag full_v3 ...` | 230.7 s (cells sum 1,569 s; 16 cells = 4 streams × 4 prices, BPI at full 20 reps) | `E4_full_v3.json` + `.log` |
| W3 DS-side E4 | `... --constants computer-use/t2sim/constants.measured.v3.e4ds.json --exp E4 --tag full_v3_e4ds ...` | 126.7 s (4 cells: wiki_A/wiki_B/sepsis/bpi2019 at native) | `E4_full_v3_e4ds.json` + `.log` |
| W3 DS-side E4 narrow ablation | same + `--buy-formula narrow --tag narrow_v3_e4ds` | 121.5 s | `E4_narrow_v3_e4ds.json` + `.log` |
| W3 eligible fraction | `experimental-results/guiexp/t2_sim_v3/eligible_fraction.py --constants ...v3.json --reps 20 --seed 7 --prices native,5M --out .../eligible_fraction_v3.json` | 267.5 s (8 cells, serial) | `eligible_fraction_v3.json` + `.log` |
| pytest | `.venv-gui/bin/python -m pytest tests/ -q` in `computer-use/t2sim` | 4.9 s | **221 passed** (baseline unchanged) |

Old references compared against (read-only, in `../t2_sim/`):

- `E3_v2.json` — the only old E3 whose constants fingerprint (`a4e38c482b6e10cf`)
  matches an extant constants file (`constants.measured.v2.json`) exactly; ran
  with the pre-W1.5 narrow behaviour, 2026-09-11.
- `E4_v2.json` — closest old full-E4 with the plain E4 row set (android_glm
  v2-era constants, 16 cells incl. BPI, 2026-09-11). No old E4 exists for
  android_ds, so the DS-side run is new-only (compared internally via the
  narrow ablation instead).
- `E3_v3.json` / `E4_tax_v3.json` (2026-09-12) predate the v3 constants build
  (different fingerprints and an older trigger: gate_prior=population,
  spend_cap="realized") and are NOT used as numeric baselines.

## 2. Determinism / no-regression checks

- **openapps_glm / openapps_ds cells are bit-identical** across
  `E3_full_v3`, `E3_narrow_v3` and the old `E3_v2.json` (every row, every
  cell). Same constants, and at C_fail = C the full formula is bitwise the
  narrow one — the engine and the W1.5 switch leave published C_fail=C rows
  unmoved.
- **android_glm full == narrow, bitwise** (E3 and by construction E4:
  `C_fail_mult = 1.0` for all seven v3 GLM layouts ⇒ C_fail = C). Every E4
  change vs the old run is therefore attributable to the v2→v3 constants
  alone; the buy formula only moves android_ds rows.

## 3. E3 (synthetic, n=300, 20 reps) — what changed

`ours` mean tokens per cell. "full vs narrow (v3)" isolates the W1.5 buy
formula; "vs E3_v2" is the total change against the published v2-constants run.

| cell | full (v3) | narrow (v3) | full vs narrow | old E3_v2 | full vs old | n_compiles full / narrow / old |
|---|---|---|---|---|---|---|
| bursty/android_ds | 53,308,481 | 63,239,872 | **−15.7%** | 40,134,505 | +32.8% | 5.05 / 12.15 / 15.35 |
| poisson/android_ds | 56,084,297 | 69,474,527 | **−19.3%** | 41,945,661 | +33.7% | 4.90 / 13.75 / 17.15 |
| zipf/android_ds | 111,442,368 | 116,862,254 | **−4.6%** | 26,743,517 | +316.7% | 4.85 / 10.05 / 13.10 |
| bursty/android_glm | 6,992,243 | 6,992,243 | 0.0% | 26,800,400 | **−73.9%** | 6.90 / 6.90 / 6.75 |
| poisson/android_glm | 9,692,253 | 9,692,253 | 0.0% | 30,661,620 | **−68.4%** | 7.00 / 7.00 / 7.25 |
| zipf/android_glm | 8,099,675 | 8,099,675 | 0.0% | 17,972,822 | **−54.9%** | 6.50 / 6.50 / 4.95 |
| openapps_* (4 cells) | — | — | 0.0% (bit-identical) | — | 0.0% (bit-identical) | 3.00 / 3.00 / 3.00 |

Reading:

- **W1.5 expectation verified in direction.** The full B̂ makes android_ds
  `ours` cheaper (−15.7 / −19.3 / −4.6% at the full n=300 scale; the W1.5
  n=60 smoke had suggested 16–40%) and cuts n_compiles by ~2.4–2.8×
  (e.g. 12.15→5.05 bursty, 13.75→4.90 poisson). The narrow form was
  under-pricing DS buys and re-paying ~10.5×C failures on p=0 families;
  full prices those in and stops them.
- **android_glm got much cheaper purely from the v3 constants** (median c
  198,517→123,884 pw, median C 916,335→254,492 pw, all 7 families admitted):
  `ours` −55…−74% vs E3_v2 with the formula inert. New headline shape at
  native price: `always_compile` beats `ours` on android_glm (rel 0.53–0.63)
  because compiling is now cheap and every GLM family passes the gate — the
  interesting regime moved to "when is reactive still right", not "compile
  sparingly".
- **android_ds vs the old run is more expensive overall** (+33 / +34 / +317%)
  despite the formula savings: the v3 DS block is pricier (median c over all
  7 families 65,045 pw, C_fail_mult 10.51, and only 4/7 families admitted),
  and zipf's hot families now map onto the expensive never-admitted layouts
  (N* = inf). This is a constants effect, not a formula effect.

## 4. E4 (real streams, android_glm, 4 prices × 4 streams incl. BPI, 20 reps)

`ours` mean tokens, new vs old `E4_v2.json` (all 16 cells cheaper):

| cell | old ours | new ours | Δ | n_compiles old → new |
|---|---|---|---|---|
| bpi2019/native | 19,358,395,117 | 16,906,544,171 | −12.7% | 365 → 360 |
| bpi2019/autorpa_233k | 19,778,197,050 | 17,927,264,555 | −9.4% | 499 → 413 |
| bpi2019/1M | 19,873,391,875 | 16,042,210,991 | −19.3% | 308 → 282 |
| bpi2019/5M | 37,427,505,855 | 24,167,568,132 | −35.4% | 120 → 116 |
| sepsis/native | 227,580,070 | 193,700,272 | −14.9% | 74 → 75 |
| sepsis/autorpa_233k | 196,986,234 | 158,293,236 | −19.6% | 136 → 142 |
| sepsis/1M | 232,817,399 | 202,735,704 | −12.9% | 6 → 8 |
| sepsis/5M | 235,431,795 | 221,083,918 | −6.1% | 0 → 2 |
| wiki_A/native | 3,138,901,891 | 1,739,207,463 | **−44.6%** | 137 → 145 |
| wiki_A/autorpa_233k | 3,102,941,468 | 1,724,420,073 | **−44.4%** | 213 → 158 |
| wiki_A/1M | 3,072,755,020 | 1,632,723,270 | **−46.9%** | 107 → 97 |
| wiki_A/5M | 6,641,869,259 | 5,251,588,050 | −20.9% | 27 → 45 |
| wiki_B/native | 22,065,949,934 | 19,693,056,934 | −10.8% | 561 → 588 |
| wiki_B/autorpa_233k | 23,446,602,496 | 20,816,510,756 | −11.2% | 1001 → 751 |
| wiki_B/1M | 21,326,641,930 | 18,981,134,319 | −11.0% | 437 → 460 |
| wiki_B/5M | 22,101,034,942 | 19,650,863,289 | −11.1% | 106 → 193 |

Notable row-level shifts (rel_to_ours, new run):

- **wiki_A native/autorpa/1M**: `ours` dominates massively — reactive is
  10.7× ours (was 3.8×); oracle 2.1–2.5× ours (was 1.3–1.5×). Only
  `success_count` stays close (0.85–0.95).
- **bpi2019**: `success_count` now **beats** ours at every price
  (rel 0.66–0.83; old run had 1.6–4.0), and `on_second` improved to
  1.5–1.8 (old 2.1–4.8). At 5M everything collapses to reactive except
  oracle/breakeven, as before.
- **wiki_B native/autorpa**: oracle is now ~5–7× ours (was ~3–4×) — the v3
  GLM oracle over-compiles relative to ours; ours_g15 (Gamma(1,5) prior)
  degrades to 5.3–5.9× ours at those prices, same qualitative failure as
  the old run.
- Per-stream absolute scales shift non-uniformly because profiles are
  assigned round-robin over each stream's sorted family names and the v3
  family set changed: always_reactive wiki_A +58%, bpi −37%, wiki_B/sepsis
  ≈ −4…−7% vs the old run. Compare rows within a file, not across files.

Attribution: android_glm has C_fail_mult = 1, so the full buy formula is
bitwise the narrow one here — **all E4 movement is the v2→v3 constants**
(cheaper GLM c/C; 7/7 admitted; the three old families replaced by
CalcTableSave/CommentPost/WriterMemoSave).

## 5. BPI 2019 (held-out fourth stream)

- No special switch was needed: the v3 `e4.bpi_heldout = true` already makes
  `_e4_cells` append the `bpi2019` cells (4 prices), and `bpi_reps = null`
  falls back to the run's `--reps`. A 1-rep probe measured 5.4 s per BPI cell
  (251,734 arrivals, 11,973 families, 13 policy rows + offline optimum), so
  full 20 reps ≈ 108 s per cell — no subsampling or `bpi_reps` reduction was
  necessary. BPI therefore ships **inside `E4_full_v3.json` at the full 20
  reps, on equal footing with the other three streams** (cells
  `bpi2019/price={native,autorpa_233k,1M,5M}`).
- BPI headline (new): ours 16.9B tok at native (reactive 2.70×, oracle 4.09×,
  offline_opt 0.19× ours); at 5M ours ~ reactive (1.00×) with oracle 0.49×.
  `success_count` is the strongest simple rule on BPI (0.66–0.83× ours).
- The DS-side run (`E4_full_v3_e4ds.json`) also carries `bpi2019/price=native`.

## 6. DS-side E4 (android_ds, native price)

Config: `computer-use/t2sim/constants.measured.v3.e4ds.json` — a copy of v3
with only `e4.cost_set = "android_ds"`, `e4.prices = ["native"]`,
`bpi_heldout` kept true (fingerprint `ec19937027ea184f`; v3 itself untouched).
`run.py` has no `--cost-set` flag, hence the config copy.

`ours` at native, full vs narrow (same v3 DS constants — the formula effect
on real streams):

| cell | full | narrow | Δ | n_compiles full / narrow | wasted full / narrow |
|---|---|---|---|---|---|
| wiki_A/native | 19,432,057,047 | 19,724,402,778 | −1.5% | 89 / 255 | 82 / 236 |
| wiki_B/native | 17,748,190,463 | 19,618,372,360 | **−9.5%** | 333 / 1074 | 317 / 1031 |
| sepsis/native | 199,740,434 | 514,865,304 | **−61.2%** | 6 / 135 | 6 / 132 |
| bpi2019/native | 28,478,078,923 | 26,378,003,930 | +8.0% | 250 / 642 | 237 / 605 |

n_compiles and wasted compiles drop ~2.5–4× everywhere (the W1.5 direction);
net tokens improve on 3 of 4 streams, with BPI +8% because the surviving
compiles sit on the never-admitted 10.5×-C-failure layouts (N* = inf for all
DS cells). Relative to reactive at native, DS `ours` is ≈1.0× on every
stream (0.997–1.067) — under the v3 DS constants the honest message is "the
DS trigger correctly buys almost nothing at native price", vs GLM
ours/reactive 0.09–0.94×.

## 7. Eligible fraction (paper §5.4)

E4's JSON does not carry eligible metrics (only `_stream` summaries), so
`eligible_fraction.py` (next to the outputs, listed in §1) computes them from
the constants' `streams` block and re-scores the full E4 rule set on the
eligible substream with the identical cell construction (reps 20, seed 7,
android_glm, prices native and 5M).

Eligible = arrival belongs to a family with ≥ 3 arrivals in the full stream
(compiling needs three attempts on the family):

| stream | eligible fraction (arrivals) | eligible / total arrivals | families eligible |
|---|---|---|---|
| wiki_A | **0.9638** | 63,193 / 65,564 | 315 / 2,529 (12.5%) |
| wiki_B | **0.3157** | 31,575 / 100,000 | 1,734 / 68,773 (2.5%) |
| sepsis | **0.1867** | 196 / 1,050 | 27 / 846 (3.2%) |
| bpi2019 | **0.9559** | 240,622 / 251,734 | 1,902 / 11,973 (15.9%) |

Eligible-substream scores (`eligible_fraction_v3.json`, rel to substream
`ours`): on the eligible substream `ours` stays competitive where it was
(wiki_A native: reactive 16.3× ours, ours 1.11M tok over 63,193 arrivals),
sepsis-eligible flips compile-friendly (always_compile 0.60× ours at
native, oracle 0.59×), and BPI-eligible at native reproduces the full-stream
ordering (ours 14.07B, success_count 0.74×, breakeven 1.24×, oracle 1.95×).
At 5M the substream behaves like the full stream: wiki_A collapses toward
reactive/breakeven (ours 4.69B, breakeven 0.29×), sepsis compiles nothing.

## 8. pytest

`computer-use/t2sim`: `.venv-gui/bin/python -m pytest tests/ -q` →
**221 passed in 4.92s** — identical to the pre-W2/W3 baseline (211 + 10
buy-formula tests from W1.5). Nothing in `t2sim/*.py`, `constants.*.json`
(except the new `constants.measured.v3.e4ds.json` scratch copy) or the old
results dir was modified.

## 9. Output manifest (this directory)

- `E3_full_v3.json` / `E3_full_v3.log` — W2 headline (12 cells)
- `E3_narrow_v3.json` / `E3_narrow_v3.log` — formula ablation
- `E4_full_v3.json` / `E4_full_v3.log` — W3 headline (16 cells incl. BPI at 20 reps)
- `E4_full_v3_e4ds.json` / `E4_full_v3_e4ds.log` — DS-side E4 (4 cells, native)
- `E4_narrow_v3_e4ds.json` / `E4_narrow_v3_e4ds.log` — DS-side formula ablation
- `eligible_fraction.py`, `eligible_fraction_v3.json`, `eligible_fraction_v3.log` — §5.4 metrics
- `COMPARISON.md` — this file

Constants fingerprints: v3 `78c184013b94e8b2` (E3_full_v3, E4_full_v3,
eligible analysis), e4ds copy `ec19937027ea184f`; both recorded in each
file's `meta.constants_fingerprint`.
