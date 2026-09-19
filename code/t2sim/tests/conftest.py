"""Test bootstrap: the t2sim package directory is not an importable Python
package (its parent is named "computer-use"), so put it on sys.path the same
way the entry modules do.  openapps-exp goes on the path too, for the
port-equivalence test against the old engine (the 2026-09-20 reorganization
moved it under misc/; keep the historical code/ location as first choice)."""
import sys
from pathlib import Path

T2SIM_DIR = Path(__file__).resolve().parents[1]
_OLD_EXP_CANDIDATES = (T2SIM_DIR.parent / "openapps-exp",
                       T2SIM_DIR.parents[1] / "misc" / "openapps-exp")
OLD_EXP_DIR = next(
    (p for p in _OLD_EXP_CANDIDATES if (p / "policy_sim.py").exists()),
    _OLD_EXP_CANDIDATES[0])

for p in (str(T2SIM_DIR), str(OLD_EXP_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)
