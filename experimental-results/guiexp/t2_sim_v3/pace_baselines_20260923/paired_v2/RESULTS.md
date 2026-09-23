# PACE additional baseline and ablation results

All 612 frozen conditions completed. No model or device calls were made.
Every one of 4,920 input realizations matched its retained stream, mapping,
and profile hashes. Recomputed full PACE rows matched the retained records
exactly. All 19,680 rows passed independent component-sum checks.

## Cost relative to agent-only service

| Policy | Grid 1 mean / max | Grid 2 mean / max | Recorded arrivals mean / max |
|---|---:|---:|---:|
| AutoRPA (build once) | 1.844577 / 14.084575 | 1.890558 / 19.269096 | 1.061654 / 1.226886 |
| ToolPro (cost adaptation) | 0.997056 / 1.025822 | 0.996700 / 1.010980 | 0.995139 / 1.000000 |
| PACE with equal attempt prices | 0.980292 / 1.249226 | 0.970117 / 1.249395 | 0.884431 / 1.229175 |

The named baselines are decision-rule adaptations under the shared compiler
and serving model, not end-to-end reproductions of the cited systems. The
AutoRPA adapter has a finite build phase and never retries after a failed
build or drift. ToolPro compares one-use saving with expected acquisition
cost and uses no future-arrival forecast. See the frozen definitions in
`config.json` and `code/t2sim/pace_baselines/README.md`.

## Paired comparison with PACE

| Policy | Grid 1 cheaper / tied / dearer | Grid 2 cheaper / tied / dearer | Recorded arrivals cheaper / tied / dearer |
|---|---:|---:|---:|
| AutoRPA (build once) | 33 / 0 / 267 | 39 / 0 / 261 | 1 / 0 / 11 |
| ToolPro (cost adaptation) | 121 / 41 / 138 | 102 / 76 / 122 | 2 / 0 / 10 |
| PACE with equal attempt prices | 41 / 84 / 175 | 48 / 84 / 168 | 0 / 4 / 8 |

Equal attempt prices cost 1.133589 times PACE on recorded
arrivals, averaged over condition-wise ratios, with paired bootstrap 95%
interval [1.120489, 1.150438]. The actual failure bills and budget
reservations remain unchanged. The proposal alone replaces the failure
price by the successful-attempt price. This variant remains within the
implemented 1.25 prefix bound, including its declared floating tolerance.

## Files

- `config.json`: frozen policies, all 612 scenarios, and source hashes.
- `cells/*.json`: all raw repetitions and original-source references.
- `summary.json`: aggregates and every condition, including unfavorable ones.
- `evidence.json`: grouped cells for the paper renderer.
- `verification.json`: independent accounting, source, coverage, and pairing checks.

The interrupted first run in the parent directory is retained. Its only
failure was an output-name collision because both grids reused scenario
keys. This completed version keys output by group and scenario. Scientific
definitions were unchanged, and no result was excluded.
