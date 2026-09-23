# Matched-budget economic controls

These four controls address the independent review's question about the
incremental value of PACE's reuse proposal. All definitions are frozen before
new results are generated. The study repeats the exact existing 300 first
grid, 300 second grid, and 12 recorded-arrival conditions. It changes no
costs, outcomes, task mappings, seeds, or sample sizes.

All four controls use the same global 0.25 cost margin, action reservation,
manifest clearing, fallback, and service-before-compilation order as PACE.
The copied serving loop differs only in passing the observed failed spend
and observed program savings to the proposal function. Those quantities are
already maintained by the original loop.

The policy IDs and fixed definitions are:

- `safe_earliest_allowance_025`: the original `earliest_cap` proposal. After
  three completed agent runs, attempt if its previous per-family allowance
  passes, then apply the global budget.
- `safe_arrival10_allowance_025`: the original `fixed10_cap` proposal. It
  requires ten cumulative family arrivals and the same per-family allowance,
  then applies the global budget.
- `safe_projected_allowance_025`: the original `projected_cap` proposal. It
  requires PACE's projected economic test and the per-family allowance,
  then applies the global budget.
- `safe_count_025`: change only the projected reuse count from
  `min(N*age/(age+5),1/h)` to `min(N,1/h)`. It retains the asymmetric attempt
  price `C+(1/p_hat-1)C_fail`, the same posterior mean, and the same routing
  allowance `m*min(T,age)`. If `h=0`, the lifetime cap is absent.

For the first three controls, the unchanged per-family allowance is
`F+C_fail <= max(0,S)+max(0,H_hat*s)`. `F` is cumulative failed-build spend,
`S` is accounted savings on previous program uses, and `H_hat` and `s` are
the original reuse and per-use-saving estimates. This allowance is an
additional economic filter. The global guard supplies cost protection.

The study also reports the retained `safe_history_025`, `safe_once_025`,
`safe_earliest_025`, `earliest_cap`, `fixed10_cap`, `projected_cap`, and
`projected` rows. Their stored outcomes are copied unchanged. Every full
PACE row is rerun and checked for exact equality with the retained outcome.
Every regenerated stream, task mapping, and supplied profile is hashed and
compared with its original record before evaluation.

No model or device calls are used. Every condition and unfavorable result
is retained. No thresholds will be changed after inspecting outcomes.

```
python3 -m unittest discover -s code/t2sim/pace_review_controls -p 'test_*.py' -q
python3 code/t2sim/pace_review_controls/study.py --freeze
python3 code/t2sim/pace_review_controls/study.py --run --workers 6
```

Outputs are under
`experimental-results/guiexp/t2_sim_v3/pace_review_controls_20260923`.
`config.json` freezes all definitions and source hashes. `cells/*.json`
contains raw repetitions and the matching original sources. `evidence.json`
contains all grouped cells with raw paths and hashes for table rendering.
`summary.json` includes condition-wise averages and paired bootstrap
intervals on the twelve recorded-arrival conditions.
