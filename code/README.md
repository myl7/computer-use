# Project code

This directory contains project-owned implementation and experiment code.
Experimental outputs and datasets are stored outside the code tree.

| Directory | Role |
|---|---|
| `guiexp/` | Shared agent, compilation, verification, and deployment code |
| `guiexp_android/` | Android experiments and charge-reservation implementation |
| `guiexp_osworld/` | Desktop measurement port |
| `guiexp_webarena/` | Web measurement port |
| `t2sim/` | Offline arrival simulation, PACE, baselines, component removals, and sensitivity studies |

The root README lists the tests and table-recomputation commands for the current
paper. The live component runners and offline ledger analyzer are retained under
`analysis/pace_live_20260923/`. Repeating GUI runs requires the corresponding
benchmark environment; reading the frozen results and running the listed tests
does not require model API credentials.
