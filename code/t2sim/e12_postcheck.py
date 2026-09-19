"""Post-run verification + CSV export for E12 (mechanism ablation, real
streams).

E12's shared-cell contract: the ours and always_reactive rows of the cost
set the authoritative E4 run used (constants e4.cost_set) must be
numerically IDENTICAL to the same cells of
experimental-results/guiexp/t2_sim_v3/E4_full_v3_a1k3_e4dsprices.json --
same constants fingerprint (build_constants_e12.py guarantees the byte-level
match), same cell construction, same paired seeds, no common random numbers.
This script checks every shared cell, records the verdict in the E12 meta
(e4_identity_ok, plus a provenance note naming the E4 file), and writes the
flat CSV next to the JSON.

    python3 computer-use/t2sim/e12_postcheck.py \
        [--e12 PATH] [--e4 PATH] [--csv PATH] [--constants PATH]

Exits non-zero if any shared cell differs (the spec diverged from E4 -- fix
the spec, do not ship) or if meta.constants_fingerprint does not equal the
E4 file's.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import experiments  # noqa: E402

T2SIM = Path(__file__).resolve().parent
DEFAULT_OUT = Path("/Users/myl/app/computer-use/experimental-results"
                   "/guiexp/t2_sim_v3")
E12_PATH = DEFAULT_OUT / "E12_ablation.json"
E4_PATH = DEFAULT_OUT / "E4_full_v3_a1k3_e4dsprices.json"
CSV_PATH = DEFAULT_OUT / "E12_ablation.csv"
CONSTANTS_PATH = T2SIM / "constants.measured.v3.a1k3dsprices.json"

# Rows the ablation table reports, in print order (experiments.E12_ROW_ORDER).
ROWS = ("ours", "always_reactive", "narrow_trigger", "fixed_cooldown",
        "no_decay", "gamma_prior", "fixed_horizon", "no_spend_cap")

PROVENANCE = (
    "Shared-cell anchor: E4_full_v3_a1k3_e4dsprices.json (the authoritative "
    "DS-constants real-stream E4 run: reps=20, seed=7, add_one gate prior, "
    "deployment k_min=3, B=2000).  E12 was run on "
    "constants.measured.v3.a1k3dsprices.json (build_constants_e12.py), whose "
    "constants_fingerprint equals that run's; the ours and always_reactive "
    "native-price cells of E4's cost set are therefore bit-identical to its "
    "cells, verified by e12_postcheck.py and recorded in e4_identity_ok."
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--e12", default=str(E12_PATH))
    ap.add_argument("--e4", default=str(E4_PATH))
    ap.add_argument("--csv", default=str(CSV_PATH))
    ap.add_argument("--constants", default=str(CONSTANTS_PATH))
    args = ap.parse_args()

    e12 = json.loads(Path(args.e12).read_text())
    e4 = json.loads(Path(args.e4).read_text())
    constants = json.loads(Path(args.constants).read_text())

    cells = e12["cells"]
    e4_cells = e4["cells"]
    e4_cs = constants["e4"]["cost_set"]
    streams = list(constants["e4"].get("streams",
                                       ["wiki_A", "wiki_B", "sepsis"]))
    if constants["e4"].get("bpi_heldout", True):
        streams.append("bpi2019")

    # Cross-file identity guard first: a fingerprint mismatch means E12 did
    # not run on the E4 constants at all, whatever the cells say.
    fp_ok = (e12["meta"]["constants_fingerprint"]
             == e4["meta"]["constants_fingerprint"])
    print(f"constants_fingerprint {e12['meta']['constants_fingerprint']} "
          f"== E4 file's {e4['meta']['constants_fingerprint']}: {fp_ok}")

    checks = []
    for pname in ("native", "5M"):
        for sname in streams:
            for row in ("ours", "always_reactive"):
                got = cells[f"{e4_cs}/{sname}/price={pname}"][row]["mean_tokens"]
                want = e4_cells[f"{sname}/price={pname}"][row]["mean_tokens"]
                ok = got == want
                checks.append(ok)
                if not ok:
                    print(f"  MISMATCH {e4_cs}/{sname}/price={pname} {row}: "
                          f"{got!r} != {want!r}")
    identity_ok = fp_ok and all(checks)
    n_shared = len(checks)
    print(f"shared cells vs E4 ({e4_cs}, native + 5M, ours/always_reactive): "
          f"{sum(checks)}/{n_shared} bit-identical -> "
          f"e4_identity_ok={identity_ok}")

    meta = e12["meta"]
    meta["e4_identity_ok"] = identity_ok
    meta["provenance"] = PROVENANCE
    Path(args.e12).write_text(json.dumps(e12, indent=1))

    with open(args.csv, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["cost_set", "stream", "price", "row", "mean_tokens",
                    "ratio_to_ours"])
        for key in sorted(cells):
            cs, rest = key.split("/", 1)
            sname, pname = rest.split("/price=")
            ours = cells[key]["ours"]["mean_tokens"]
            for row in ROWS:
                rec = cells[key][row]
                w.writerow([cs, sname, pname, row,
                            repr(rec["mean_tokens"]),
                            repr(rec["ratio_to_ours"])])
    print(f"wrote {args.csv} and updated {args.e12}")

    return 0 if identity_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
