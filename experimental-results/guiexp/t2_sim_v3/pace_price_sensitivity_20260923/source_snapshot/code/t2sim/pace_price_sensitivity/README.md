# Prespecified PACE price and dependence sensitivity

This local simulation addresses the unobserved conditional compilation
prices and the synthetic independence between costs, verification, and
recurrence. It reuses the exact twelve model/log conditions, ten repetitions,
arrival sequences, profile-assignment random seeds, and outcome seeds from
the retained main experiment. It adds no model or device calls.

The policies are the unchanged `safe_projected_025` and the existing
`pace_narrow_price` implementation. The latter changes only the proposal's
expected attempt price to `C / p_hat`. Both retain actual charges, maximum
attempt reservation, the same 25% cumulative budget, and all other actions.
ReAct uses the same supplied profile charges and empty-manifest router fee.

## Frozen conditions

There are 72 conditions, each with ten paired repetitions. Every condition
is reported, including losses. No parameter is selected using these results.

1. **Missing-price sensitivity (48 conditions).** For each of the twelve
   model/log pairs, use `r = C_fail / C` in `{0.5, 1, 2, 5}`. Preserve the
   branch price actually observed in each source profile. For an accepted
   cell, keep `C` and set `C_fail = r * C`. For a completed rejected cell,
   keep its observed failed spend and set `C = C_fail / r`. Qwen Calc was
   stopped early. Its successful price remains the previously imputed
   model median, and its failed price is `max(retained_partial_spend, r*C)`.
   This is a lower-bound stress rule, not a measured complete failure cost.
   Its realized ratio can therefore exceed the nominal `r`. All `c`, `d`,
   `q`, agent success probabilities, hidden compilation probabilities,
   stream order, and original profile assignments remain unchanged.
2. **Adverse dependence (24 conditions).** Keep the original profile costs.
   Rank each model's base profiles in ascending `C/c`, using profile name
   for ties. With zero-based rank `j` among `M` profiles, assign hidden
   verification probability `0.9 - 0.8*j/(M-1)`. All copies of one profile
   get the same probability. The first scenario retains the original
   profile assignment. The second preserves its exact multiset of copied
   profiles, sorts these copies by decreasing `C/c` (profile name and source
   family ID break ties), and assigns them to family IDs in decreasing
   full-stream frequency (family ID breaks ties). The simulator alone uses
   full-stream counts to construct the stress environment. Neither policy
   sees those counts or the hidden probability. These scenarios test a
   specified adverse joint distribution. They are not measured calibration.

The rank definition and remapping operate on the original supplied costs,
not on outcomes or on prices from the missing-price sensitivity. The two
sensitivity families are separate, not crossed. Public-log family counts
are used only by the second environment construction.

## Identification and checks

The measured-price branch is identified from the immutable measurement
table's admission and `C_partial` flags. Accepted cells use their `C`.
Completed rejected cells use `measured.C_with_repair` if present, otherwise
their `C`, matching the existing profile reader. The 20 source profiles
contain 15 observed successful prices, four observed complete failed prices,
and one partial spend. No profile has both observed conditional prices.
Qwen OsmAnd must use its recorded `measured.C_with_repair`, not its already
imputed top-level `C`. Every source profile is checked against the retained
study specification before any conditions run. Unsupported identification
stops the study.

Each source cell's SHA-256 and each repetition's original stream, mapping,
and profile hashes must match the retained results. Each repetition stores
its transformed input hashes. Policy costs must equal the sum of agent,
extraction, compilation, and router components. ReAct is rerun in the same
environment, and both policies' reference accounts must match it. Unless
the profile remapping changes agent charges, ReAct must also match the
original retained cost and quality. Both policies check the cumulative
budget at every arrival, with the unchanged engine tolerance. Compact
outcomes and the full accounting metrics are retained.

The output directory is write-once per condition. The design, study source,
tests, imported engines, measurement inputs, stream inputs, and source
outcomes are hashed before running. The manifest and source/input snapshots
are written by `--freeze`. Running refuses any later hash change. Interrupted
runs may resume only the unchanged manifest. Results are summarized over
all conditions in each scenario, with equal condition weights. Report
means, ranges, win/tie/loss counts, attempts, and maximum prefix ratios.
The paired 95% percentile bootstrap uses 2,000 shared resamples of the ten
repetition indices and seed `20260923041`. It is conditional on these
profiles and logs, not a deployment uncertainty interval.

## Commands

```
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s code/t2sim/pace_price_sensitivity -p 'test_*.py' -q
PYTHONDONTWRITEBYTECODE=1 python3 code/t2sim/pace_price_sensitivity/study.py --freeze
PYTHONDONTWRITEBYTECODE=1 python3 code/t2sim/pace_price_sensitivity/study.py --run --workers 4
```

Raw repetitions and scenario summaries are written to
`experimental-results/guiexp/t2_sim_v3/pace_price_sensitivity_20260923/`.
The main deliverable is `evidence.json`, which retains every condition,
source hash, observed-price rule, pairing check, and group summary.
