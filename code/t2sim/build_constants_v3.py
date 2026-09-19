"""Build constants.measured.v3.json from the 2026-09-18 three-dataset update.

Deterministic, local-only: reads two JSON files, writes one. No network, no
API, no hand-typed numbers -- every value in the regenerated blocks is read
or derived from paper/measurement_update_20260918.json.

What changes vs constants.measured.v2.json (and nothing else):

  cost_sets.android_glm / cost_sets.android_ds
      label / source / source_sha256 point at the 2026-09-18 measurement
      update; `layouts` and `per_model` are recomputed from the update's 14
      rows (7 families x 2 models; 4 AndroidWorld + 2 OSWorld desktop +
      1 WebArena web per model).  The old seven-family Android-only table
      (FilesMoveFile, MarkorCreateNote, OsmAndFavorite, ...) is superseded.

  every other key -- openapps_glm / openapps_ds / price_ladder /
      epsilon_cliff / streams / trigger / e3 / e4 / e5 / e8 / extras and
      the top-level schema / notes -- is copied verbatim from v2.

Conventions (inherited from v2; see CONSTANTS_V2.md):

  admitted row   p=1.0, C/d/q0 are the row's own measurements, and the
                 `measured` block keeps C_with_repair, C_eff_table,
                 c_unsubtracted = c + floor, s_arrival = (1-q)c - d and
                 nstar_marginal for documentation.
  rejected row   p=0.0; C, d, q0 are the model median over its ADMITTED
                 rows (the engine needs numbers, and a family that never
                 passed the gate has no price for an attempt that passes);
                 c / L / rho / pi keep the row's own measurements.  Its own
                 failure price is kept under measured.C_with_repair.
  C_fail_mult    model-level scalar shared by every layout in the block:
                 median(C of rejected rows) / median(C of admitted rows).
                 GLM has no rejected row in the initial table, so the
                 multiple is unidentified and set to 1 (block `note`).

Usage:
    python3 computer-use/t2sim/build_constants_v3.py [--check-only]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
from pathlib import Path

T2SIM = Path(__file__).resolve().parent
REPO_ROOT = T2SIM.parent.parent
V2_PATH = T2SIM / "constants.measured.v2.json"
V3_PATH = T2SIM / "constants.measured.v3.json"
MEAS_PATH = (REPO_ROOT / "paper"
             / "measurement_update_20260918.json")

# Which model feeds which regenerated block (structural mapping, not data).
BLOCK_MODEL = {
    "android_glm": "z-ai/glm-5.3-flash",
    "android_ds": "deepseek/deepseek-v4-flash-vision-exp",
}

# The seven families of the 2026-09-18 update (structural invariant).
EXPECTED_FAMILIES = {
    "ContactsAddContact", "MarkorDeleteNote", "SimpleCalendarAddOneEvent",
    "OsmAndMarker",                      # AndroidWorld
    "CalcTableSave", "WriterMemoSave",   # OSWorld desktop
    "CommentPost",                       # WebArena web
}

PLATFORM_NAMES = {"Android": "AndroidWorld", "Desktop": "OSWorld",
                  "Web": "WebArena"}

GLM_C_FAIL_NOTE = ("no rejected GLM compilation in the initial table; the "
                   "failed-price multiple is unidentified and set to 1; GLM "
                   "failed-attempt prices appear only in the "
                   "repeated-compilation records (t19), reported in the "
                   "verification-distribution section")

RHO_NEG_NOTE = ("rho < 0: the doc arm costs MORE than a reactive episode "
                "(L_doc > c), so the schema's [0,1] bound on rho does not "
                "hold. rho and L are documentation only; the engine reads "
                "neither.")


def fmt(x: float) -> str:
    """Compact deterministic number for note prose (v2 style: '916335')."""
    return f"{x:.6g}"


def median(vals: list[float]) -> float | None:
    return statistics.median(vals) if vals else None


def check_rho(row: dict) -> None:
    """doc_share must equal (c - L_doc)/c up to float noise."""
    c, L = row["c"], row["L_doc"]
    expect = (c - L) / c
    got = row["doc_share"]
    if abs(got - expect) > 1e-9 * max(1.0, abs(expect)):
        raise AssertionError(
            f"{row['family']}: doc_share {got} != (c-L)/c {expect}")


def build_layout(row: dict, c_fail_mult: float, fill: dict) -> dict:
    """One layout entry; `fill` carries the model-level medians a rejected
    row substitutes for its own (unmeasured) C / d / q0."""
    fam = row["family"]
    pi = row["agent_successes"] / row["n_exploration_episodes"]
    lay: dict = {
        "status": "measured",
        "c": row["c"],
        "L": row["L_doc"],
        "rho": row["doc_share"],
        "C": row["C"] if row["admitted"] else fill["C"],
        "p": 1.0 if row["admitted"] else 0.0,
        "d": row["d"] if row["admitted"] else fill["d"],
        "q0": row["q"] if row["admitted"] else fill["q0"],
        "pi": pi,
        "C_fail_mult": c_fail_mult,
    }
    if row["admitted"]:
        q, c, d = row["q"], row["c"], row["d"]
        lay["measured"] = {
            "admitted": True,
            "C_with_repair": row["C"],
            "C_eff_table": row["C"],
            "c_unsubtracted": c + row["floor"],
            "s_arrival": (1.0 - q) * c - d,
            "nstar_marginal": row["nstar"],
        }
        if row["doc_share"] < 0:
            lay["note"] = RHO_NEG_NOTE
    else:
        lay["measured"] = {"admitted": False, "C_with_repair": row["C"]}
        note = (
            f"never admitted (the 2026-09-18 three-dataset build never "
            f"passed the gate for {fam}): no deploy stage ran, so q0 and d "
            f"are the model median over its admitted cells "
            f"(q0={fmt(fill['q0'])}, d={fmt(fill['d'])}); C is the model "
            f"median over admitted cells ({fmt(fill['C'])}) because this "
            f"family has no measured price for an attempt that PASSES. Its "
            f"own C_with_repair ({fmt(row['C'])}) is the price of an "
            f"attempt that failed and is kept under `measured`.")
        if row["doc_share"] < 0:
            note += " " + RHO_NEG_NOTE
        lay["note"] = note
    return lay


def build_block(v2_block: dict, rows: list[dict], sha_prefix: str) -> dict:
    """One regenerated cost-set block: v2 metadata (label/source/sha
    updated), fresh layouts + per_model."""
    platforms = sorted({r["platform"] for r in rows},
                       key=lambda p: -sum(1 for r in rows
                                          if r["platform"] == p))
    label_counts = " + ".join(
        f"{sum(1 for r in rows if r['platform'] == p)} {PLATFORM_NAMES[p]}"
        for p in platforms)
    n_families = len(rows)

    admitted = [r for r in rows if r["admitted"]]
    rejected = [r for r in rows if not r["admitted"]]
    if not admitted:
        raise ValueError("model has no admitted row; medians undefined")

    C_median_admitted = median([r["C"] for r in admitted])
    C_fail_median = median([r["C"] for r in rejected])
    c_fail_mult = (C_fail_median / C_median_admitted
                   if C_fail_median is not None else 1.0)
    fill = {"C": C_median_admitted,
            "d": median([r["d"] for r in admitted]),
            "q0": median([r["q"] for r in admitted])}

    layouts = {r["family"]: build_layout(r, c_fail_mult, fill)
               for r in sorted(rows, key=lambda r: r["family"])}

    deploy_rows = [r for r in rows if r["d"] is not None]
    q_rows = [r for r in rows if r["q"] is not None]
    per_model = {
        "model": rows[0]["model"],
        "n_cells": len(rows),
        "cells_admitted": len(admitted),
        "cells_not_admitted": len(rejected),
        "cells_with_measured_deploy": len(deploy_rows),
        "p_model_admitted_rate": len(admitted) / len(rows),
        "c_median_admitted": median([r["c"] for r in admitted]),
        "c_median_all": median([r["c"] for r in rows]),
        "d_median_measured_deploy": median([r["d"] for r in deploy_rows]),
        "q_median_measured_deploy": median([r["q"] for r in q_rows]),
        "L_doc_median_all": median([r["L_doc"] for r in rows]),
        "C_median_admitted": C_median_admitted,
        "C_fail_median_not_admitted": C_fail_median,
        "C_fail_over_C_ratio": (C_fail_median / C_median_admitted
                                if C_fail_median is not None else None),
    }

    block = {
        "label": (f"Three-dataset build 2026-09-18, {n_families} families "
                  f"({label_counts}), {rows[0]['model']} "
                  f"(price-weighted tokens)"),
        "status": v2_block["status"],
        "source": ("paper/measurement_update_20260918.json "
                   "(2026-09-18)"),
        "source_sha256": sha_prefix,
        "source_unit": v2_block["source_unit"],
        "unit": v2_block["unit"],
        "r_cache": v2_block["r_cache"],
        "m": v2_block["m"],
        "tau0": v2_block["tau0"],
        "floor_raw_tokens": v2_block["floor_raw_tokens"],
        "price_sheet": v2_block["price_sheet"],
    }
    if not rejected:
        block["note"] = GLM_C_FAIL_NOTE
    block["per_model"] = per_model
    block["layouts"] = layouts
    return block


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check-only", action="store_true",
                    help="validate the measurement rows and print the "
                         "derived statistics without writing the file")
    args = ap.parse_args()

    v2 = json.loads(V2_PATH.read_text())
    meas = json.loads(MEAS_PATH.read_text())
    sha_prefix = hashlib.sha256(MEAS_PATH.read_bytes()).hexdigest()[:16]

    families = {r["family"] for r in meas["rows"]}
    if families != EXPECTED_FAMILIES:
        raise AssertionError(
            f"unexpected family set in the measurement update: {families}")
    if len(meas["rows"]) != 2 * len(EXPECTED_FAMILIES):
        raise AssertionError("expected one row per family per model")

    v3 = json.loads(json.dumps(v2))          # deep copy, verbatim default
    for block_name, model in BLOCK_MODEL.items():
        rows = [r for r in meas["rows"] if r["model"] == model]
        if len(rows) != len(EXPECTED_FAMILIES):
            raise AssertionError(f"{model}: expected "
                                 f"{len(EXPECTED_FAMILIES)} rows, got "
                                 f"{len(rows)}")
        for r in rows:
            if r["admitted"]:
                for k in ("d", "q", "nstar"):
                    if r[k] is None:
                        raise AssertionError(f"{model}/{r['family']}: "
                                             f"admitted row lacks {k}")
            check_rho(r)
        v3["cost_sets"][block_name] = build_block(
            v2["cost_sets"][block_name], rows, sha_prefix)
        pm = v3["cost_sets"][block_name]["per_model"]
        mult = next(iter(v3["cost_sets"][block_name]["layouts"]
                         .values()))["C_fail_mult"]
        print(f"{block_name}: cells_admitted={pm['cells_admitted']}/"
              f"{pm['n_cells']}  p_model_admitted_rate="
              f"{pm['p_model_admitted_rate']:.6g}  C_fail_mult={mult:.6g}  "
              f"C_median_admitted={pm['C_median_admitted']:.6g}")

    if args.check_only:
        return 0

    # The engine's own gate: every layout must carry numeric c/L/rho/C/p/d/q0.
    sys.path.insert(0, str(T2SIM))
    import experiments
    issues, _ = experiments.check_constants(v3)
    if issues:
        raise ValueError("constants problems: " + "; ".join(issues))

    V3_PATH.write_text(json.dumps(v3, indent=1) + "\n")
    print(f"wrote {V3_PATH} (source sha256 prefix {sha_prefix})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
