# Storage correction

The first run used the original scenario key as its output identifier.
The two inherited parameter grids use identical keys in separate original
directories. The no-overwrite guard stopped the run at the first collision.
The original manifest, interrupted log, source code, and partial result
files remain unchanged in the parent output directory.

`study_v2.py` uses `(group, key)` as the output identifier, checks uniqueness
for all 612 conditions before running, and writes to the `paired_v2/`
subdirectory. Algorithms, parameters, pairing, sample sizes, and summaries
are unchanged. Every condition is evaluated, and no results were selected
based on the interrupted run's outcomes.

```
python3 -m unittest discover -s code/t2sim/pace_baselines -p 'test_*.py' -q
python3 code/t2sim/pace_baselines/study_v2.py --freeze
python3 code/t2sim/pace_baselines/study_v2.py --run --workers 6
```

Final raw repetitions are in `paired_v2/cells/*.json`. `summary.json`
contains all condition summaries and paired bootstrap intervals on the
twelve recorded-arrival conditions. `evidence.json` contains grouped cells
with `key`, `model`, `pattern`, raw source paths and hashes, and per-policy
metrics, matching the structure used by the manuscript table renderer.
