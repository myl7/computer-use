"""Build constants.measured.v3.qwen.json: v3 plus a new cost_sets.android_qw.

Deterministic, local-only: reads two JSON files, writes one. No network, no
API, no hand-typed numbers -- every value in the generated block is read or
derived from the qwen rows of paper/measurement_update_20260918.json.

What changes vs constants.measured.v3.json (and nothing else):

  cost_sets.android_qw (NEW)
      the third model qwen/qwen3.8-flash, measured 2026-09-20: 6 families
      (4 AndroidWorld + 2 OSWorld; the WebArena CommentPost cell
      provider-refused before producing stage data, so it has no layout).
      `layouts` and `per_model` mirror the android_glm/android_ds
      conventions EXACTLY (build_constants_v3.build_block): admitted rows
      p=1.0 with their own measurements; rejected/terminated rows p=0.0
      with C/d/q0 = the model median over the ADMITTED rows and the cell's
      own failure price kept under measured.C_with_repair; C_fail_mult is
      the block-level scalar median(C of the failed side) / median(C of the
      admitted rows), here over OsmAndMarker (own failed-build price) and
      CalcTableSave (PARTIAL translator+builder spend -- the wedge.md cell
      was terminated before verification, so its C is a lower bound).

  every other key -- schema / notes / openapps_* / android_glm / android_ds
      / price_ladder / epsilon_cliff / streams / trigger / e3 / e4 / e5 /
      e8 / extras -- is copied verbatim from v3 (the e12 builder's rule).

Known conventions carried over from the measurement layer (block `note`):

  * the Android no-task floor for qwen (t12_grid, 18 no-task runs) is
    subtracted at the DOCUMENTED price weights (r_c 0.107, r_o 3.13, the
    build cells' price_weights): floor = mean pw(per_run) AS RECORDED =
    1031.76 pw, far below the 4432.28 raw-token mean of the same runs
    because 16/18 runs hit the repeated 4352-token cached prefix (one
    partial 512, one cold).  The glm/ds floors (5090 / 2290) are
    raw-uncached, so absolute floor comparisons across models are NOT
    like-for-like; `floor_raw_tokens` carries the pw value actually
    subtracted.  Per-run numbers: measurement_update_20260918.json
    `qwen_android_floor`;
  * the L_doc median spans the 5 rows that have a doc arm (CalcTableSave
    terminated before its doc stage).

Usage:
    python3 computer-use/t2sim/build_constants_qwen.py [--check-only] [--check]
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
V3_PATH = T2SIM / "constants.measured.v3.json"
OUT_PATH = T2SIM / "constants.measured.v3.qwen.json"
MEAS_PATH = REPO_ROOT / "paper" / "measurement_update_20260918.json"

BLOCK = "android_qw"
MODEL = "qwen/qwen3.8-flash"

# The six measured qwen (platform, family) cells of the 2026-09-20 wave.
EXPECTED_CELLS = {
    ("Android", "ContactsAddContact"),
    ("Android", "MarkorDeleteNote"),
    ("Android", "SimpleCalendarAddOneEvent"),
    ("Android", "OsmAndMarker"),      # rejected: unautomatable at verification
    ("Desktop", "CalcTableSave"),     # terminated: artifact wedge (wedge.md)
    ("Desktop", "WriterMemoSave"),    # admitted
}

QWEN_PRICES = {"p_in": 1.5e-07, "p_c": 1.6e-08, "p_o": 4.7e-07}

TERMINATED_REASON = ("artifact wedge: exception-swallowing retry loops defeat "
                     "the replay deadline (wedge.md)")

RHO_NEG_NOTE = ("rho < 0: the doc arm costs MORE than a reactive episode "
                "(L_doc > c), so the schema's [0,1] bound on rho does not "
                "hold. rho and L are documentation only; the engine reads "
                "neither.")

BLOCK_NOTE = ("Third model, measured 2026-09-20 (Android no-task floor "
              "calibrated 2026-09-21 in t12_grid); body/table integration "
              "pending (rows live under measurement_update_20260918.json "
              "`qwen_rows`). Floor convention (2026-09-21 ruling): floor = "
              "mean pw(per_run) AS RECORDED over the 18 t12_grid no-task "
              "runs, cached tokens charged AT the documented cache ratio "
              "(r_c 0.107, r_o 3.13, the build cells' price_weights) = "
              "1031.76 pw, which `floor_raw_tokens` carries; the RAW-token "
              "mean of the same runs is 4432.28 and the two diverge because "
              "16/18 runs hit the repeated 4352-token cached prefix (one "
              "partial 512, one cold). The GLM 5090 / DeepSeek 2290 floors "
              "are raw-uncached, so absolute floor comparisons across "
              "models are not like-for-like. The two OSWorld floors are "
              "per-cell (18 runs each) and already subtracted. The L_doc "
              "median spans the 5 rows with a doc arm. OsmAndMarker was "
              "rejected at verification after repair rounds under the "
              "image-413-guard completion (no deploy stage); its "
              "engine-facing C/d/q0 are the model median over the admitted "
              "rows and its own failed-build price is under "
              "measured.C_with_repair. CalcTableSave was TERMINATED by "
              "operator ruling inside the gate stage (" + TERMINATED_REASON +
              "); verification/deploy/doc_arm never ran, so its C is the "
              "PARTIAL translator+builder spend (the gate stage makes no "
              "model calls) and is a LOWER BOUND on a failed build. "
              "WebArena CommentPost is absent: provider content-filter "
              "refusal (400 data_inspection_failed), deterministic, floor "
              "18/18 passed, single Alibaba endpoint.")


def fmt(x: float) -> str:
    """Compact deterministic number for note prose (v3 style)."""
    return f"{x:.6g}"


def median(vals: list[float]) -> float | None:
    return statistics.median(vals) if vals else None


def build_layout(row: dict, c_fail_mult: float, fill: dict) -> dict:
    """One layout entry, mirroring build_constants_v3.build_layout. `fill`
    carries the model-level medians a rejected/terminated row substitutes
    for its own (unusable) C / d / q0."""
    fam = row["family"]
    pi = row["agent_successes"] / row["n_exploration_episodes"]
    lay: dict = {
        "status": "measured",
        "c": row["c"],
        "L": row["L_doc"] if row["L_doc"] is not None else fill["L"],
        "rho": (row["doc_share"] if row["doc_share"] is not None
                else fill["rho"]),
        "C": row["C"] if row["admitted"] else fill["C"],
        "p": 1.0 if row["admitted"] else 0.0,
        "d": row["d"] if row["admitted"] else fill["d"],
        "q0": row["q"] if row["admitted"] else fill["q0"],
        "pi": pi,
        "C_fail_mult": c_fail_mult,
    }
    own_fail_price = row.get("measured", {}).get("C_with_repair")
    if row["admitted"]:
        q, c, d = row["q"], row["c"], row["d"]
        lay["measured"] = {
            "admitted": True,
            "C_with_repair": row["C"],
            "C_eff_table": row["C"],
            # floor is None for the AndroidWorld qwen rows (no floor
            # measured): their c is already unsubtracted.
            "c_unsubtracted": c + row["floor"] if row["floor"] is not None else c,
            "s_arrival": (1.0 - q) * c - d,
            "nstar_marginal": row["nstar"],
        }
        if row["doc_share"] < 0:
            lay["note"] = RHO_NEG_NOTE
    else:
        lay["measured"] = {"admitted": False,
                           "C_with_repair": own_fail_price}
        if row.get("C_partial"):
            lay["measured"]["partial"] = True
            lay["measured"]["terminated_reason"] = row["terminated_reason"]
        note = (
            f"never admitted ({fam}: " +
            (TERMINATED_REASON + "; verification/deploy/doc_arm never ran, "
             "so q0, d, L and rho have no measurement here -- q0/d are the "
             "model median over its ADMITTED cells and L/rho carry the same "
             "admitted-median fill"
             if row.get("C_partial") else
             "rejected at verification after repair rounds under the "
             "image-413-guard completion; no deploy stage ran") +
            f"): q0 and d are the model median over its ADMITTED cells "
            f"(q0={fmt(fill['q0'])}, d={fmt(fill['d'])}); C is the model "
            f"median over admitted cells ({fmt(fill['C'])}) because this "
            f"family has no measured price for an attempt that PASSES. Its "
            f"own failure price ({fmt(own_fail_price)}"
            + (", a PARTIAL translator+builder spend and a lower bound"
               if row.get("C_partial") else "") +
            ") is kept under `measured`.")
        if row["doc_share"] is not None and row["doc_share"] < 0:
            note += " " + RHO_NEG_NOTE
        lay["note"] = note
    return lay


def build_block(v3_android_block: dict, rows: list[dict],
                sha_prefix: str) -> dict:
    """The android_qw cost-set block: v3 android metadata (unit/tax carried
    over), fresh layouts + per_model from the qwen rows."""
    admitted = [r for r in rows if r["admitted"]]
    failed = [r for r in rows if not r["admitted"]]
    if not admitted:
        raise ValueError("qwen has no admitted row; medians undefined")

    C_median_admitted = median([r["C"] for r in admitted])
    # The failed side uses each cell's MEASURED failure price (the rejected
    # rows' engine-facing C is the fill median, not a measurement): the
    # OsmAndMarker own price and the CalcTableSave partial spend.
    fail_prices = {f"{r['platform']}/{r['family']}":
                   r.get("measured", {}).get("C_with_repair", r["C"])
                   for r in failed}
    C_fail_median = median(list(fail_prices.values()))
    c_fail_mult = C_fail_median / C_median_admitted
    fill = {"C": C_median_admitted,
            "d": median([r["d"] for r in admitted if r["d"] is not None]),
            "q0": median([r["q"] for r in admitted if r["q"] is not None]),
            # doc-arm constants for a family with no doc arm (CalcTableSave
            # terminated before its doc stage): same admitted-median fill.
            "L": median([r["L_doc"] for r in admitted
                         if r["L_doc"] is not None]),
            "rho": median([r["doc_share"] for r in admitted
                           if r["doc_share"] is not None])}

    layouts = {r["family"]: build_layout(r, c_fail_mult, fill)
               for r in sorted(rows, key=lambda r: r["family"])}

    # The AndroidWorld rows share the t12_grid no-task floor (subtracted at
    # the documented price weights); the OSWorld floors are per-cell.
    android_floors = {r["floor"] for r in rows if r["platform"] == "Android"}
    assert len(android_floors) == 1, android_floors
    qwen_android_floor = android_floors.pop()

    # Rows with a REAL deployment stage (the rejected row's d is the fill
    # median, so `d is not None` would over-count).
    deploy_rows = [r for r in rows if r.get("deploy_n") is not None]
    ldoc_rows = [r for r in rows if r["L_doc"] is not None]
    per_model = {
        "model": MODEL,
        "n_cells": len(rows),
        "cells_admitted": len(admitted),
        "cells_not_admitted": len(failed),
        "cells_with_measured_deploy": len(deploy_rows),
        "p_model_admitted_rate": len(admitted) / len(rows),
        "c_median_admitted": median([r["c"] for r in admitted]),
        "c_median_all": median([r["c"] for r in rows]),
        "d_median_measured_deploy": median([r["d"] for r in deploy_rows]),
        "q_median_measured_deploy": median([r["q"] for r in deploy_rows]),
        "L_doc_median_all": median([r["L_doc"] for r in ldoc_rows]),
        "C_median_admitted": C_median_admitted,
        "C_fail_median_not_admitted": C_fail_median,
        "C_fail_over_C_ratio": C_fail_median / C_median_admitted,
        "C_fail_side": fail_prices,
    }

    return {
        "label": ("Three-dataset build 2026-09-20, 6 measured families "
                  "(4 AndroidWorld + 2 OSWorld; WebArena CommentPost "
                  f"provider-refused, no cell), {MODEL} "
                  "(price-weighted tokens)"),
        "status": v3_android_block["status"],
        "source": "paper/measurement_update_20260918.json qwen_rows "
                  "(2026-09-20)",
        "source_sha256": sha_prefix,
        "source_unit": v3_android_block["source_unit"],
        "unit": v3_android_block["unit"],
        "r_cache": QWEN_PRICES["p_c"] / QWEN_PRICES["p_in"],
        "m": v3_android_block["m"],
        "tau0": v3_android_block["tau0"],
        # the pw floor actually subtracted (NOT a raw-token count; the raw
        # mean of the same runs is 4432.28 -- see the block note)
        "floor_raw_tokens": qwen_android_floor,
        "price_sheet": dict(QWEN_PRICES),
        "note": BLOCK_NOTE,
        "per_model": per_model,
        "layouts": layouts,
    }


def build_constants(v3: dict, meas: dict) -> dict:
    """v3 verbatim + the android_qw block. Raises on any inconsistency."""
    qwen_rows = meas.get("qwen_rows")
    if not qwen_rows:
        raise AssertionError(
            "measurement_update_20260918.json has no qwen_rows; run "
            "paper/measurement_update_20260918.py first")
    cells = {(r["platform"], r["family"]) for r in qwen_rows}
    if cells != EXPECTED_CELLS:
        raise AssertionError(f"unexpected qwen cell set: {cells}")
    if any(r["model"] != MODEL for r in qwen_rows):
        raise AssertionError("qwen_rows carries a foreign model")
    for r in qwen_rows:
        if r["admitted"]:
            for k in ("d", "q", "nstar"):
                if r[k] is None:
                    raise AssertionError(
                        f"{r['family']}: admitted row lacks {k}")
        if (r["family"] == "CalcTableSave") != r.get("C_partial", False):
            raise AssertionError("C_partial must be set exactly on the "
                                 "terminated CalcTableSave row")
    floor_rec = meas.get("qwen_android_floor")
    android_rows = [r for r in qwen_rows if r["platform"] == "Android"]
    if not floor_rec or any(r["floor"] is None for r in android_rows):
        raise AssertionError("qwen_rows missing the t12_grid Android floor; "
                             "rerun paper/measurement_update_20260918.py")
    if any(abs(r["floor"] - floor_rec["floor_pw"]) > 1e-9
           for r in android_rows):
        raise AssertionError("Android rows' floor disagrees with "
                             "qwen_android_floor.floor_pw")

    sha_prefix = hashlib.sha256(MEAS_PATH.read_bytes()).hexdigest()[:16]
    out = json.loads(json.dumps(v3))            # deep copy, verbatim default
    out["cost_sets"][BLOCK] = build_block(
        v3["cost_sets"]["android_glm"], qwen_rows, sha_prefix)

    # The measurement row already carries the block fill for the rejected
    # row (same median over the same admitted set): pin that equivalence.
    osm = next(r for r in qwen_rows if r["family"] == "OsmAndMarker")
    block = out["cost_sets"][BLOCK]
    if abs(osm["C"] - block["per_model"]["C_median_admitted"]) > 1e-9:
        raise AssertionError("OsmAndMarker fill C drifted from the admitted "
                             "median -- the measurement layer and the "
                             "constants block disagree")
    return out


def frozen_checks(out: dict) -> None:
    """Pin the generated block against literal expectations."""
    blk = out["cost_sets"][BLOCK]
    lay = blk["layouts"]
    assert set(lay) == {f for _, f in sorted(EXPECTED_CELLS,
                                             key=lambda t: t[1])}
    assert blk["per_model"]["cells_admitted"] == 4
    assert blk["per_model"]["n_cells"] == 6
    assert abs(blk["per_model"]["C_median_admitted"]
               - 346595.7266666667) < 1e-6
    assert abs(blk["floor_raw_tokens"] - 1031.7576666666666) < 1e-9
    mult = lay["ContactsAddContact"]["C_fail_mult"]
    assert abs(mult - 3.5099146923507942) < 1e-9
    for fam in ("ContactsAddContact", "MarkorDeleteNote",
                "SimpleCalendarAddOneEvent", "WriterMemoSave"):
        assert lay[fam]["p"] == 1.0, fam
    assert lay["OsmAndMarker"]["p"] == 0.0
    assert abs(lay["OsmAndMarker"]["measured"]["C_with_repair"]
               - 1585498.9733333334) < 1e-6
    assert lay["CalcTableSave"]["p"] == 0.0
    assert lay["CalcTableSave"]["measured"]["partial"] is True
    assert abs(lay["CalcTableSave"]["measured"]["C_with_repair"]
               - 847543.8933333333) < 1e-6
    assert abs(lay["WriterMemoSave"]["c"] - 36984.96888888889) < 1e-6
    # AndroidWorld rows carry the floored c and the matching c_unsubtracted
    assert abs(lay["ContactsAddContact"]["c"] - 39168.48233333334) < 1e-6
    for fam in ("ContactsAddContact", "MarkorDeleteNote",
                "SimpleCalendarAddOneEvent"):
        l = lay[fam]
        assert abs(l["measured"]["c_unsubtracted"]
                   - (l["c"] + 1031.7576666666666)) < 1e-6, fam
    # verbatim propagation: nothing outside cost_sets.android_qw moved
    v3 = json.loads(V3_PATH.read_text())
    for k, v in v3.items():
        if k == "cost_sets":
            for name, blk3 in v.items():
                assert out["cost_sets"][name] == blk3, name
        else:
            assert out[k] == v, k


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check-only", action="store_true",
                    help="validate the measurement rows and print the "
                         "derived statistics without writing the file")
    ap.add_argument("--check", action="store_true",
                    help="rebuild, run the engine's constants check and the "
                         "frozen-literal checks, and require the on-disk "
                         "file to equal the rebuild byte for byte")
    args = ap.parse_args()

    v3 = json.loads(V3_PATH.read_text())
    meas = json.loads(MEAS_PATH.read_text())
    out = build_constants(v3, meas)
    blk = out["cost_sets"][BLOCK]
    pm = blk["per_model"]
    mult = next(iter(blk["layouts"].values()))["C_fail_mult"]
    print(f"{BLOCK}: cells_admitted={pm['cells_admitted']}/{pm['n_cells']}  "
          f"p_model_admitted_rate={pm['p_model_admitted_rate']:.6g}  "
          f"C_fail_mult={mult:.6g}  "
          f"C_median_admitted={pm['C_median_admitted']:.6g}")

    sys.path.insert(0, str(T2SIM))
    import experiments
    issues, placeholders = experiments.check_constants(out)
    if issues:
        raise ValueError("constants problems: " + "; ".join(issues))

    if args.check:
        frozen_checks(out)
        if not OUT_PATH.exists():
            raise SystemExit(f"--check: {OUT_PATH.name} missing; run without "
                             "--check once to write it")
        on_disk = json.loads(OUT_PATH.read_text())
        if on_disk != out:
            raise SystemExit(f"--check: {OUT_PATH.name} differs from the "
                             "rebuild; regenerate it")
        print(f"CHECK PASS: engine constants check clean, frozen literals "
              f"hold, {OUT_PATH.name} matches the rebuild "
              f"(source sha256 prefix {blk['source_sha256']})")
    if args.check_only:
        return 0
    OUT_PATH.write_text(json.dumps(out, indent=1) + "\n")
    print(f"wrote {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
