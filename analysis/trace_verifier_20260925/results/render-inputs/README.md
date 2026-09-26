# Revised table input contract

Final rendering is gated until this directory contains:

- `manifest.json`: protocol, completion state, selected aggregation, coverage,
  validated profiles, selected-program diagnostics, complete claim deltas,
  simulation completion records, and SHA-256 hashes for every source below.
- `measurement-data.json`: revised measurement rows for price, serving share,
  paired replay, and repeat tables. Lower-bound costs remain marked as such.
- `table-data.json`: revised measurement and online point/interval records in
  the renderer schema.
- `online-uncertainty.json`: revised 612-condition comparisons, ablations,
  sensitivities, and paired bootstrap records.
- `provenance.json`: source hashes and independent accounting, coverage,
  formula, and point-estimate check counts.

`render_revised_tables.py --final` refuses partial coverage or stale hashes.
It writes the existing generated paper filenames only after all gates pass.
`--smoke` uses historical data solely as a routing fixture and writes under
`experimental-results/trace_verifier_20260925/render-smoke/`, never `paper/`.
