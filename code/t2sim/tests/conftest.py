"""Test bootstrap: the t2sim package directory is not an importable Python
package (its parent is named "computer-use"), so put it on sys.path the same
way the entry modules do.  openapps-exp goes on the path too, for the
port-equivalence test against the old engine."""
import sys
from pathlib import Path

T2SIM_DIR = Path(__file__).resolve().parents[1]
OLD_EXP_DIR = T2SIM_DIR.parent / "openapps-exp"

for p in (str(T2SIM_DIR), str(OLD_EXP_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)
