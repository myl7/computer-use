"""t2sim: the methods-v2 policy-simulation layer (T2 / E3 E4 E5 E8).

Zero API cost.  Everything here is a replay of measured constants over
arrival streams, per docs/methods-v2.md.  Entry points:

    python3 computer-use/t2sim/run.py --constants <constants.json> --exp E3 --reps 20 --jobs 8
    python3 computer-use/t2sim/validate.py            # faithfulness vs the old paper Table 2

Outputs land in experimental-results/guiexp/t2_sim/.

The directory name "computer-use" is not an importable Python identifier, so
sibling modules import each other by name after inserting this directory on
sys.path (same convention as openapps-exp/).  Every entry module does that
bootstrap itself; nothing here depends on the caller.
"""

__version__ = "1.0.0"
