# When to Compile a Computer-Use Agent?

Code, retained results, and anonymous paper sources for PACE (Price-Aware
Compilation from Experience). The review copy is available through
[the anonymous repository](https://anonymous.4open.science/r/computer-use).

## Build the paper

Both LaTeX entry points use anonymous ICLR 2027 review mode. The official style
and bibliography files are included. A TeX distribution with `latexmk` is required.

```bash
cd paper
latexmk -pdf -interaction=nonstopmode -halt-on-error -outdir=build main.tex
```

The main text ends on page 9. AI use, ethics, and reproducibility statements
precede the references and are excluded from that limit. The Appendix follows
the references.

## Recompute the reported tables

Run from the repository root with Python 3.11 or newer. These commands use
retained data and make no model API calls.

```bash
python3 analysis/pace_edit_20260923/uncertainty/recompute.py
python3 paper/rewrite-review/render_pace_tables.py
python3 paper/rewrite-review/render_live_tables.py
```

The uncertainty script verifies recorded input hashes and recomputes the paired
bootstrap estimates. The renderers regenerate the current measurement,
simulation, ablation, sensitivity, and live-study tables. Their verification
records are written beside the retained analysis data.
Release integrity manifests allow the anonymous mirror to redact user-directory
names in the specifically identified path metadata. All other content remains
covered by the recorded hashes, including every measured cost and outcome.

To reconstruct the live-study summaries from the retained billing ledgers and
episode JSON records:

```bash
python3 analysis/pace_live_20260923/analyze_joint_v1.py
```

This writes to `analysis/pace_live_20260923/reproduced/` and preserves the frozen
summaries used by the paper. The two main SQLite ledgers are included; transient
database sidecars and server state are excluded.

## Offline checks

```bash
python3 -m unittest discover -s code/t2sim/protocol_explore -p 'test_*.py' -q
python3 -m unittest discover -s code/t2sim/pace_baselines -p 'test_*.py' -q
python3 -m unittest discover -s code/t2sim/pace_review_controls -p 'test_*.py' -q
python3 -m unittest discover -s code/t2sim/pace_price_sensitivity -p 'test_*.py' -q
python3 -m unittest discover -s code/t2sim/tests -p 'test_paper_revision.py' -q
python3 -m unittest discover -s code/guiexp_android/tests -p 'test_recovery_validation_budget_v4.py' -q
```

## Artifact contents

| Path | Contents |
|---|---|
| `paper/` | Anonymous manuscript, figures, current table renderers, and measurement summaries |
| `code/guiexp*/` | Agent, compilation, verification, deployment, and benchmark-specific implementations |
| `code/t2sim/` | Offline cost model, policies, frozen study drivers, and tests |
| `experimental-results/` | Retained measurement, paired simulation, and live-study records |
| `analysis/pace_edit_20260923/uncertainty/` | Paired bootstrap calculation, table data, and input provenance |
| `analysis/pace_live_20260923/` | Live-study runners, offline analyzer, and frozen summaries |
| `datasets/` | Public process logs and Wikipedia arrival streams |

Selected inputs retain their historical paths so their recorded hashes remain
valid, including `misc/results-dead/openapps/real-streams/real_streams.json`.
Historical literature snapshots and an internal review document are provenance
references, not inputs to offline table recomputation; they are not distributed.
Full GUI execution additionally requires the benchmark environments and a model
provider. The paper distinguishes measured GUI costs from simulations using
those costs, and the live component study from execution of the complete online
policy.
