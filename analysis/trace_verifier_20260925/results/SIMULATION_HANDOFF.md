# Revised simulation handoff

## Completion update, 2026-09-25

All four revised offline studies are complete. The source profile is
`analysis/trace_verifier_20260925/results/profiles.json` with SHA-256
`5bcdf481b78f4048278d0dbd2aa883345aafe615e577579aa58ef6cc4a253fc4`.
The validator has 20/20 main and 22/22 repeat identities with
`coverage_ready=true`. `profile_ready=false` is preserved because DeepSeek Web
has incomplete billing and uses the declared modeled `C_fail` profile.

| Study | Validated cells | Config SHA-256 | Result SHA-256 |
| --- | ---: | --- | --- |
| Main | 616/616 | `872eb5845349317e241e79d5cb1823ca719866dd12e1f5ad03219dd852e7f58c` | `688ec61cef9f287af5112b01d47dcd20efee4983429b0a9001082e1345b32be7` |
| Comparisons | 612/612 | `61f52727319baed1f7bc9c6745beab7f395f674943d5f900f31a41811f444ff4` | `490db25403f06cd5656679affa567abc4459c17e257c9a0a0476e721cdfda171` |
| Ablations | 612/612 | `a017bdbf220d61099835239b4855292034d00ea5ae25b1c299716f4152a2f18e` | `43d484f0d5365b283254b4aafe1802c293902d035a074a88851f1540ee38f55f` |
| Sensitivity | 72/72 | `7a83b74049554d0f7032246209c312d73061c161132aef05a38ce703a9cc8ab9` | `a60f88019b65400111c7d7bbc0ddc385eb2fc2c2d06e73c46d2a099da1d01e54` |

Each study directory has `rerun-complete.json`, `cell-validation.json`,
`profile_mapping.json`, and `run.log`. The main and downstream result paths are
`main/summary.json`, `comparisons/summary.json`, `ablations/summary.json`, and
`sensitivity/evidence.json` under
`experimental-results/trace_verifier_20260925/simulations/`.

The safe-resume gate checks unique destinations and exact frozen job/result
agreement before reusing a cell. Main recovery reused 603 core cells, then ran
9 missing core cells and 4 isolated missing-cost cases. The reuse proof is
`main/reuse-audit.json`. The four extra cases have
`_source_group=additional_missing_cost` and do not enter the 612-cell core
suite averages.

`main/core-preservation-audit.json` checks all 612 core keys, seeds,
repetitions, streams, assigned scenario probabilities, and non-measurement
profile fields against the frozen source config. The cross-study schema and
hash index is `simulations/final-audit.json` with SHA-256
`5b3ff510ea67ecac660238fd4a2e6db30e7ba0c30573f2bc5401b3fd1cb122a7`.

Frozen invalid outputs, including
`simulations/main-invalid-frozen-20260925-duplicate-missing-cost/` and
`simulations/sensitivity-invalid-frozen-20260925-modeled-partial-price/`,
stay outside this release. They are retained for audit and are not inputs to
the completed summaries.

The staged commands below are now reproduction and safe-resume commands. A
zero-pending run revalidates all cells and refreshes the completion manifest.

## Evidence gate

Do not freeze profiles until the selected main outcomes needed for `C`,
`C_fail`, `d`, and `q` pass validation. Repeated-compilation results can finish
later because they do not define serving profiles.

Current checkpoint after producer implementation: 11/20 main identities and
4/22 repeat identities selected, with no invalid selected records. The latest
machine-readable count is always `coverage-report.json`; this sentence is only
a handoff checkpoint.

## Runtime estimate

Historical elapsed-time sums for the same condition sets are:

| Stage | Cells | Recorded serial cell time |
|---|---:|---:|
| Active main source policies | 612-paper subset from 879 recorded cells | 616 s across recorded source cells |
| Comparisons | 612 | 248 s |
| Ablations | 612 | 362 s |
| Sensitivity | 72 | 870 s |

The combined recorded CPU time is about 35 minutes before the extra old-policy
recomputation in revised main. Budget 45-70 serial CPU-minutes for all four
studies. With 12 workers on the 144-CPU host, allow 10-20 wall-clock minutes
for simulation, including long-tail cells and process/file overhead. With six
workers, allow 15-30 minutes.

Uncertainty recomputation, aggregation validation, and table rendering should
take another 5-15 minutes. The practical post-profile estimate is therefore
15-35 minutes with 12 workers, or 20-45 minutes with six workers. A resumed
run only processes missing cells.

The one-repetition scratch runs were deliberately conservative real-arrival
cells: main 13.8 s for 22 policies, comparisons 2.2 s for four policies,
ablations 3.3 s for twelve policies, and sensitivity 1.8 s for three policies.
They prove data flow but are not multiplied directly into the ETA because the
612-cell grids contain shorter and differently structured streams.

## Staged commands

```sh
python3 analysis/trace_verifier_20260925/results/validate_results.py
python3 analysis/trace_verifier_20260925/results/build_profiles.py
python3 analysis/trace_verifier_20260925/results/rerun_simulations.py --prepare main
python3 analysis/trace_verifier_20260925/results/rerun_simulations.py --run main --workers 12
python3 analysis/trace_verifier_20260925/results/rerun_simulations.py --prepare comparisons
python3 analysis/trace_verifier_20260925/results/rerun_simulations.py --prepare ablations
python3 analysis/trace_verifier_20260925/results/rerun_simulations.py --prepare sensitivity
python3 analysis/trace_verifier_20260925/results/rerun_simulations.py --run comparisons --workers 12
python3 analysis/trace_verifier_20260925/results/rerun_simulations.py --run ablations --workers 12
python3 analysis/trace_verifier_20260925/results/rerun_simulations.py --run sensitivity --workers 12
```

Main must finish before downstream preparation because comparisons, ablations,
and sensitivity pin revised main-cell hashes.
