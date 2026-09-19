"""Build constants.measured.v3.a1k3dsprices.json: the exact constants dict
the authoritative E4_full_v3_a1k3_e4dsprices.json run loaded, reconstructed
from the current scratch config plus the run's recorded CLI patches.

Deterministic, local-only: reads one JSON file, writes one.  No hand-typed
numbers -- every value is either copied verbatim from
constants.measured.v3.e4dsprices.json or is one of the three documented
patches below.  The output's constants_fingerprint (experiments.
constants_fingerprint, the same hash run.py records in every result meta)
MUST equal the fingerprint in the E4 result file's meta
(16ddf469bfb42b98); the script hard-asserts that, because E12's shared-cell
identity guarantee is exactly this byte-level match.

The three patches, each mirroring how E4_full_v3_a1k3_e4dsprices.json was
run (reps 20 / seed 7 / add_one / k3, 2026-09-20 03:10):

  1. trigger.gate_prior = "add_one"          (run.py --gate-prior add_one)
  2. deployment.k_min_global = 3             (run.py --kmin-global 3)
  3. cost_sets.android_{glm,ds}.source path reverted to
     "computer-use-paper/measurement_update_20260918.json (2026-09-18)".
     The run predates the 2026-09-20 repo reorganization, which moved
     paper/measurement_update_20260918.json and rewrote the generated
     `source` provenance strings with the new path.  The string is inert
     prose (the engine reads no path from it), but it is part of the
     fingerprint, so a byte-level match with the E4 run needs the old
     string back.

Patches 1 and 2 are baked into the file rather than passed on the command
line so the run.py invocation cannot forget one of them; the recorded
fingerprint is the same either way, because it hashes the patched dict.

Usage:
    python3 computer-use/t2sim/build_constants_e12.py [--check-only]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

T2SIM = Path(__file__).resolve().parent
sys.path.insert(0, str(T2SIM))

import experiments  # noqa: E402  (for the fingerprint definition)

BASE_PATH = T2SIM / "constants.measured.v3.e4dsprices.json"
OUT_PATH = T2SIM / "constants.measured.v3.a1k3dsprices.json"

# The authoritative E4 real-stream run this config must reproduce byte for
# byte (see its meta.constants_fingerprint).
E4_RUN = "E4_full_v3_a1k3_e4dsprices.json"
E4_FINGERPRINT = "16ddf469bfb42b98"

_OLD_SOURCE_DIR = "computer-use-paper"


def build(base: dict) -> dict:
    out = json.loads(json.dumps(base))          # deep copy
    # Patch 1: the add-one gate prior (the "a1" of the E4 run's tag).
    out["trigger"]["gate_prior"] = "add_one"
    # Patch 2: Algorithm 1's three-demonstration compiler ("k3").
    out.setdefault("deployment", {})["k_min_global"] = 3
    # Patch 3: pre-reorganization provenance paths in the two android blocks
    # (the cost sets the E4 run actually read).
    for blk in ("android_glm", "android_ds"):
        src = out["cost_sets"][blk].get("source")
        if src and src.startswith("paper/"):
            out["cost_sets"][blk]["source"] = \
                _OLD_SOURCE_DIR + src[len("paper"):]
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check-only", action="store_true",
                    help="verify the fingerprint without writing the file")
    args = ap.parse_args()

    base = json.loads(BASE_PATH.read_text())
    out = build(base)
    fp = experiments.constants_fingerprint(out)
    if fp != E4_FINGERPRINT:
        raise SystemExit(
            f"fingerprint mismatch: rebuilt config hashes to {fp}, but {E4_RUN} "
            f"records {E4_FINGERPRINT}; the reconstruction is wrong, do not "
            f"run E12 against it")
    print(f"fingerprint {fp} == {E4_RUN}'s recorded fingerprint: OK")
    if args.check_only:
        return 0
    OUT_PATH.write_text(json.dumps(out, indent=1) + "\n")
    print(f"wrote {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
