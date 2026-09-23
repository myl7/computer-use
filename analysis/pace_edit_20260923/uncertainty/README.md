# Uncertainty for PACE tables

This analysis reads frozen results. It makes no model calls, opens no token file,
runs no simulator, and changes no manuscript or raw experiment file.

Run `python3 analysis/pace_edit_20260923/uncertainty/recompute.py` from the repository
root. Python 3's standard library is sufficient. The script checks source hashes,
shared simulation inputs, overlapping policy costs, original summary estimates,
and matched GUI bindings before writing these files:

- `table-data.json`: compact renderer input with all aggregate estimates, real-log
  cells, sensitivity summaries, and measurement records.
- `online-uncertainty.json`: every policy and individual condition, including
  condition-level paired intervals for a detailed appendix.
- `measurement-uncertainty.json`: measurement estimates, recorded counts, and
  justified uncertainty fields.
- `provenance.json`: the script/output/source hashes and validation counts.

## What the online intervals measure

The estimate in a condition is the policy's mean cost divided by the agent's mean
cost. Aggregate tables average these ratios with equal weight over the fixed
conditions. This preserves the existing table estimand. It does not replace it
with a ratio of aggregate means or an average of per-repetition ratios.

The 2,000 percentile bootstrap draws resample the eight grid repetitions or ten
real-log repetitions. Within each draw, the same repetition indices apply to all
policies, all models, and all fixed conditions. This preserves the design already
used for the real-log intervals. It also preserves comparisons between PACE and
the matched-budget controls. The bootstrap does not resample condition identities.

These are conditional simulation and profile-mapping intervals. They do not
include uncertainty in measured GUI prices, filled missing parameters, benchmark
choice, model versions, or selection of PACE after examining development grids.
They are pointwise intervals, not simultaneous coverage for every reported cell.
Eight or ten repetitions provide a coarse empirical sampling distribution. Small
differences should not be used as deployment-wide ranking claims.

The original four checked aggregate real-log intervals and all 72 sensitivity
agent-ratio intervals are reproduced to numerical tolerance. The main summaries
for PACE are:

| Fixed condition set | Mean ratio to agent | Conditional 95% interval |
| --- | ---: | ---: |
| Original grid, all models | 0.909519 | [0.903874, 0.918666] |
| Fresh grid, all models | 0.907033 | [0.901456, 0.915885] |
| Recorded logs, all models | 0.781495 | [0.758530, 0.803369] |

The maximum column is an observed maximum of the tested condition means. Its
primary `ci95` is `null`. A bootstrap of this maximum is retained under
`bootstrap_range_diagnostic` for analysis, but is not recommended for the paper:
resampling observed repetitions does not turn a tested maximum into a bound on
untested conditions. PACE's prefix guarantee comes from its theorem.

For comparisons, use the paired `mean_ratio_pace` intervals, not whether two
independent-looking ratio-to-agent intervals overlap. In the real logs, all of
Eager + both checks, Arrival-10 + both, Projected + both, and Count + budget have
paired intervals covering equality with PACE. In the grids, Count + budget is
about 0.14% cheaper on this estimand. This should remain visible rather than
being described as a universal advantage of the projection rule.

## Measurement tables require different treatment

| Existing table | What is observed or estimated | Recommended display |
| --- | --- | --- |
| `tab:share` | Adapted exploration mean, 30 program uses, failure fraction, plug-in saving | Keep exploration mean descriptive. Program cost has a binding bootstrap interval. Show failure counts, with working-model intervals in the appendix. |
| `tab:paired-replays` | Thirty matched agent/program task bindings per populated model-family cell | Agent mean with a bootstrap interval, or mean ± sample SD explicitly labeled SD. Keep exact success counts. A saving interval is available but has an important zero-failure limitation. |
| `tab:price` | One initial acquisition bill and gate scores | Report the bill as a recorded charge. Show gate scores as counts out of five. No bill CI and no adaptive-gate test-set CI. |
| `tab:verification-repeat` | Three acquisition outcomes, five new supplied-binding checks, five extracted-binding checks | Report exact counts. Do not put a probability CI on three mixed initial/repeat attempts. New-binding check intervals are available under a Bernoulli working model. |

Initial exploration contains only three to seven episodes, including adaptive
retry/reflection history. Some legacy cache counts are imputed. A mean is useful
for accounting, but an IID confidence interval is not supported by those records.
Initial compilation cost is one actual charge, not an estimate over repeated
independent bills. The repeated compilations reuse traces and translations and
mix different acquisition histories. A CI added to these charges would create an
unsupported population claim.

The original gate bindings influence candidate selection and repair. Their pass
counts have no test-set confidence interval here. This differs from the new
supplied/extracted checks on the original fixed artifact.

For deployment/replay counts, `ci95` is a Clopper-Pearson interval under an
independent Bernoulli working model. It is not proof that repeated GUI uses are
independent. For zero failures out of thirty uses, the two-sided interval is
[0, 0.115703]. A bootstrap of a zero-failure sample would give [0, 0] for the
failure probability, which is why the script does not use that method for counts.

Matched replay costs, extraction charges, and failures are resampled with the
same binding indices. The saving-share bootstrap preserves those correlations.
It is nevertheless conditional on the observed failure types: where no failure
was seen, it cannot estimate the cost of an unseen failure. The saving interval
must be read with the count interval. To keep the main table compact, showing the
agent-cost CI and exact success counts, while keeping saving-share intervals in
the appendix, is preferable to reporting a narrow near-one saving interval alone.

## Compact display

Use `\scriptsize` for every table, centered headers, and a fixed-width colored
slot per model. Keep GLM, DeepSeek, and Qwen in the same left-to-right order.
For online mean ratios, use the estimate on the first line and either `[lo, hi]`
or asymmetric `+upper/-lower` deviations on the second line. The JSON includes
both `ci95` endpoints and `minus`/`plus` deviations from the estimate.

Do not symmetrize a percentile interval by putting half its width after ±.
For PACE's aggregate real-log result, the faithful display is
`0.7815 [+0.0219, -0.0230]`, or `0.7815 [0.7585, 0.8034]`.
If using ± for matched replay variability, write `mean ± SD` in the caption.
For example, DeepSeek Contacts has `67.3 ± 71.5` thousand weighted tokens, while
its bootstrap interval for the mean is `[49.2, 95.5]` thousand.

The agent/agent row is exactly 1 by construction and can omit its zero-width
interval. Do not print `±0.000` for a small nonzero interval rounded to three
decimal places. Use an additional digit or the endpoints where needed.

For a concise main table, retain the named baselines, PACE, and any one central
matched-budget control. Detailed alternative rules and per-condition intervals
can use `online-uncertainty.json` in the appendix. This is a display choice and
does not require rerunning any experiment.

## Renderer field map

In `table-data.json`:

- Grid and aggregate-log means:
  `online_summary[group].by_model[model][policy].mean_ratio_agent`.
- Empirical grid maxima:
  `online_summary[group].by_model[model][policy].max_condition_mean_ratio_agent.estimate`.
- Individual log cells:
  find `real_cells` by `model` and `pattern`, then use
  `policies[policy].ratio_agent`.
- Paired comparisons to PACE:
  use `mean_ratio_pace` in aggregates or `ratio_pace` in individual cells.
- Sensitivity summaries:
  `sensitivity_summary[scenario].by_model[model][policy]` uses the same fields.
- Set `model` to `all_models` for the equal-weight aggregate.
- Measurement:
  `measurement.serving`, `measurement.compilation`,
  `measurement.paired_replays`, and `measurement.repeat_verification` identify
  each entry with `model` and `family`.

Only render an uncertainty term when `ci95` exists and is non-null. For fields
without a CI, `reason` or `reporting` records why a point or count is appropriate.
