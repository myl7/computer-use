# Three-building verifier result aggregation

`validate_results.py` validates revision attempt records and writes
`coverage-report.json`. It accepts an empty or partially populated result tree so
that coverage can be audited while experiments are running. A strict run exits
nonzero until every expected cell is present and scientifically usable.

Canonical attempt records live at:

```
experimental-results/trace_verifier_20260925/<platform>/attempts/<attempt-id>/result.json
```

Run:

```
python analysis/trace_verifier_20260925/results/validate_results.py
python analysis/trace_verifier_20260925/results/validate_results.py --strict
```

After all 20 main records validate, build profiles and freeze the four revised
offline studies:

```
python3 analysis/trace_verifier_20260925/results/build_profiles.py
python3 analysis/trace_verifier_20260925/results/rerun_simulations.py --prepare main
python3 analysis/trace_verifier_20260925/results/rerun_simulations.py --run main --workers 6
python3 analysis/trace_verifier_20260925/results/rerun_simulations.py --prepare comparisons
python3 analysis/trace_verifier_20260925/results/rerun_simulations.py --prepare ablations
python3 analysis/trace_verifier_20260925/results/rerun_simulations.py --prepare sensitivity
python3 analysis/trace_verifier_20260925/results/rerun_simulations.py --run comparisons --workers 6
python3 analysis/trace_verifier_20260925/results/rerun_simulations.py --run ablations --workers 6
python3 analysis/trace_verifier_20260925/results/rerun_simulations.py --run sensitivity --workers 6
```

The studies write only below
`experimental-results/trace_verifier_20260925/simulations/`. Preparation
preserves every saved scenario probability, stream, seed, repetition count,
and policy setting. It changes the validated `C`, `C_fail`, `d`, and `q`
profile fields and preserves preassigned sensitivity multipliers.

The validator distinguishes coverage from profile eligibility. Valid refusal,
provider interruption, insufficient-credit, whole-attempt timeout, and
operator-abort records can close planned coverage while remaining ineligible
for profile estimation. An enforced replay deadline is recorded inside a
`complete` attempt as a failed task with `failure_kind: replay_timeout`; it is
not a whole-attempt timeout.

Paper accounting uses the frozen price-weighted token (`pw`) unit. Every result
must identify its frozen price sheet. New extraction and repair charges must
reconcile to recorded model calls; reused charges must point to the hashed
historical source records.
