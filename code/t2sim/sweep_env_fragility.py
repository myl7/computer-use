"""E11: environment-level fragility, with both failure channels acting.

E9 (docs/t2sim-fragility-sweep.md) asked whether fragility restores the
trigger's lead and got "no", but it could not ask the question properly.  Its
lifetime axis was a trigger constant (mech["horizon_cap"]), not a change to
the world, because the engine's only drift channel REPLACED the q0 channel:
with fam["h"] > 0 the per-use coin was spent on the break test and q0 was
never read, so the two could not act together.

This sweep runs the environment model instead (sim.ENV_DOC):

  * h, the drift hazard, acts in the environment.  On each use of a live
    program a coin from the CoinBook decides whether the artifact survives
    it.  A dead program's use fails loudly, the arrival falls back to a
    reactive episode, the manifest entry goes with the artifact, and the
    family must pay C_eff again before it can serve from a program.
  * q0 acts on every live-program use independently, as it always did.
    Both channels act on the same use; a use fails if either fires.
  * r_fallback multiplies the reactive episode a program failure falls back
    to.  The WAREX pilot (docs/warex-pilot-2026-09-10.md) measured a
    reactive agent surviving its perturbations at 1.0 to 2.2 times its clean
    cost, so the same perturbation that kills the program also makes the
    fallback dearer.
  * sigma is the fraction of program failures that are SILENT: charged d,
    counted as served, plus silent_penalty * c for finding and repairing the
    wrong record later, and the artifact is not marked dead.  The WAREX
    pilot measured zero silent failures in 35 replays and the e7 drift probe
    measured zero in its own, so sigma = 0 is the measured value and this is
    a sensitivity axis, not a claim.

Policy rows.  The reference is `ours_noinflate`: C_eff = C/p_hat, no price
doubling after a missed gate, no abandon rule.  E9 measured that the price
doubling and the three-strike abandon are what lose money, so pricing the
trigger against a version that still carries them would measure the wrong
thing.  `always_compile_evict` (no abandon) is the naive reference, and
`oracle_tax` / `offline_opt_tax` are clairvoyant.

2026-09-11.  Three things the t16_build batch showed this sweep could not
ask about, all added here:

  * p = 0 is now a level of the gate axis.  Eight of the batch's fourteen
    family x model cells never passed the gate; the sweep's lowest rate was
    0.3, so the regime where a hot family is retried forever was never run.
  * a failed attempt is priced separately (C_fail, sim.C_FAIL_DOC).  The
    batch's failures cost 1-3M raw tokens, several times a successful
    compile, and the engine charged them C.  The ratio is an axis, {1, 3},
    with 3 the placeholder until the regenerated constants table lands.
  * the reference row runs the gate-rate ESTIMATOR of Algorithm 1
    (p_hat = (passes + 1)/(attempts + 2)) instead of the family's true p,
    which at p = 0 priced the family out using information no deployment
    has.  Two mechanisms are then layered on it as extra rows: the
    attempt-spend cap (sim.SPEND_CAP_DOC), in each of its three
    variants, and the pi-stratified prior (sim.GATE_PRIOR_DOC).

    python3 computer-use/t2sim/sweep_env_fragility.py \\
        --constants computer-use/t2sim/constants.measured.json \\
        --reps 10 --jobs 2

Writes experimental-results/guiexp/t2_sim/E11_env_fragility.json and .csv.
constants.measured.json is read only; every grid point is a deep copy built
in memory by `env_constants`.
"""
from __future__ import annotations

import argparse
import copy
import csv
import json
import multiprocessing as mp
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import experiments
import sim
import stats  # noqa: F401  (imported for parity with the other sweeps)

PRICES = ("native", "autorpa_233k")
STREAMS = ("sepsis", "bpi2019", "wiki_A", "wiki_B")

# The reference row.  Not `ours_evict`: E9 measured that the inflating
# threshold, not C_eff = C/p, is what costs the trigger money at 0 < p < 1,
# so the question "does environment fragility make the compile decision
# non-trivial" has to be asked of a trigger that is not already handicapped.
REFERENCE = "ours_noinflate"
POLICIES = ["always_reactive", "always_compile_evict", REFERENCE,
            "oracle_tax"]

# The two mechanisms of 2026-09-11, as mechanism blocks layered on the
# reference row rather than as new decision rules (sim.SPEND_CAP_DOC,
# sim.GATE_PRIOR_DOC).  The reference "ours (old)" runs the flat add-one
# gate prior of Algorithm 1 with no attempt-spend cap; the three variants
# turn one or both on and are run under the SAME policy, so the four rows
# differ in exactly the mechanism.
# 2026-09-11 (second pass): Theorem 1's accumulated-excess rule is a row
# too.  It runs the SAME gate-rate estimator as the reference (add-one) and
# the same residency deployment, so the comparison is rule against rule; the
# _cap row adds the attempt-spend cap on top, exactly as ours_spend_cap does.
# 2026-09-11 (third pass): the attempt-spend cap has three variants
# (sim.SPEND_CAP_DOC) and the sweep runs all three.  "horizon" is the cap as
# first written; "epoch" restarts its accumulator at every observed break;
# "realized" widens its budget by the saving the family's programs have
# already delivered.  The earlier `ours_both` and `breakeven_cap` rows are
# dropped to keep the cell width where it was: the question this sweep now
# asks is which cap to deploy, and the pi prior is carried alone as the
# other mechanism.
BREAKEVEN_ROWS = ("breakeven", "breakeven_cap_epoch")
VARIANT_ROWS = ("ours_spend_cap", "ours_cap_epoch", "ours_cap_realized",
                "ours_pi_prior") + BREAKEVEN_ROWS
ROW_ORDER = POLICIES + list(VARIANT_ROWS) + ["offline_taxblind",
                                             "offline_opt_tax"]
DEPLOYABLE = [p for p in POLICIES if p != "oracle_tax"] + list(VARIANT_ROWS)
NAIVE = "always_compile_evict"

# Gate prior of the reference row.  "true" would hand `ours` the family's
# real p, which at p = 0 prices the family out before the first attempt --
# information a deployment does not have, and the reason the p = 0 regime
# looked solved.
BASE_GATE_PRIOR = "add_one"
SPEND_CAP_MULT = 1.0


BREAKEVEN_POLICY = "breakeven_evict"


# The heterogeneous-regime block (sim.MIXED_REGIME_DOC).  Its rows are the
# ones the question needs: the favoured cap alone, the population admission
# prior alone at both strengths, the two together, and Theorem 1's rule.
MIXED_VARIANT_ROWS = ("ours_cap_epoch", "ours_cap_realized",
                      "ours_pop_prior", "ours_pop_prior_s4",
                      "ours_pop_cap_epoch", "ours_pop_cap_realized",
                      "breakeven")
POP_PRIOR_STRENGTH_HIGH = 4.0


def variant_mechs(mech: dict, rows=None) -> dict:
    def cap(mode):
        return sim.mech_with(mech, spend_cap=mode,
                             spend_cap_mult=SPEND_CAP_MULT)
    table = {"ours_spend_cap": cap("horizon"),
             "ours_cap_epoch": cap("epoch"),
             "ours_cap_realized": cap("realized"),
             "ours_pi_prior": sim.mech_with(mech, gate_prior="pi"),
             "ours_pop_prior": sim.mech_with(mech, gate_prior="population"),
             "ours_pop_prior_s4": sim.mech_with(
                 mech, gate_prior="population",
                 gate_prior_strength=POP_PRIOR_STRENGTH_HIGH),
             "ours_pop_cap_epoch": sim.mech_with(
                 cap("epoch"), gate_prior="population"),
             "ours_pop_cap_realized": sim.mech_with(
                 cap("realized"), gate_prior="population"),
             "breakeven": sim.mech_with(mech, spend_cap=False),
             "breakeven_cap_epoch": cap("epoch")}
    return {r: table[r] for r in (rows or VARIANT_ROWS)}


def variant_policies() -> dict:
    return {name: BREAKEVEN_POLICY for name in BREAKEVEN_ROWS}


OUT_NAME = "E11_env_fragility"

# ---------------------------------------------------------------------------
# the grid
# ---------------------------------------------------------------------------
# The full product of the five axes asked for -- h in {0, 0.02, 0.1, 0.3},
# q0 in {0.17, 0.4, 0.6}, r in {1.0, 2.2}, sigma in {0, 0.3}, p in {1.0, 0.5,
# 0.3} -- is 144 grid points, which is 1,152 stream x price cells and about
# three hours at 10 repetitions and --jobs 2.  So the grid is a core factorial
# on the two axes the compile decision turns on, plus a one-axis-at-a-time
# fan out of the other three from two anchors.  Every value of every axis is
# run; what is dropped is their interaction away from the anchors.
CORE_H = (0.0, 0.02, 0.1, 0.3)
# p = 0 added 2026-09-11.  Eight of the fourteen family x model cells of the
# t16_build batch never passed the gate, and the sweep had no level where
# they live: its lowest gate rate was 0.3, so a family that can never be
# compiled was never simulated and the retry loop this regime creates was
# never priced.
CORE_P = (1.0, 0.5, 0.3, 0.0)
CORE_Q0 = 0.4
ANCHOR_H = 0.1               # a real hazard: 1/h = 10 uses of program life
ANCHOR_P = (1.0, 0.3, 0.0)   # a gate that never misses, one that mostly
#                              misses, one that never passes (block A sweeps
#                              p in full)
SILENT_PENALTY = 3.0         # Pi, charged only where sigma > 0

# C_fail / C.  1 is the historical pricing (a failed attempt costs what a
# successful one costs); C_FAIL_HIGH is the batch's placeholder ratio -- a
# failed attempt runs translator, builder and all three repair rounds to
# exhaustion at 1-3M raw tokens, several times the price of a compile that
# passed.  REPLACE C_FAIL_HIGH with the measured ratio when the regenerated
# constants table lands; --c-fail-ratio overrides it from the CLI.
C_FAIL_LOW = 1.0
C_FAIL_HIGH = 3.0


def _grid_points(c_fail_high: float = C_FAIL_HIGH) -> list[dict]:
    """Grid points, each tagged with the block it belongs to.

    The C_fail axis is applied only where p < 1: with a gate that never
    misses there are no failed attempts, so a C_fail = 3 copy of a p = 1
    point would be the same simulation run twice.
    """
    pts: list[dict] = []
    seen: set[tuple] = set()

    def add(block, h, q0, p, r=1.0, sg=0.0):
        pen = SILENT_PENALTY if sg > 0 else 1.0
        cfs = (C_FAIL_LOW,) if p >= 1.0 else (C_FAIL_LOW, c_fail_high)
        for cf in cfs:
            key = (h, q0, p, r, sg, pen, cf)
            if key in seen:
                continue
            seen.add(key)
            pts.append({"block": block, "h": h, "q0": q0, "p": p,
                        "r_fallback": r, "sigma": sg, "silent_penalty": pen,
                        "c_fail": cf})

    for h in CORE_H:                       # A: the (h, p) map at q0 = 0.4
        for p in CORE_P:
            add("A_core", h, CORE_Q0, p)
    for q0 in (0.17, 0.6):                 # B: the q0 axis
        for p in ANCHOR_P:
            add("B_q0", ANCHOR_H, q0, p)
    for h in (0.0, ANCHOR_H):              # C: the perturbed-fallback axis
        for p in ANCHOR_P:
            if h == 0.0 and p != 1.0:
                continue
            add("C_rfb", h, CORE_Q0, p, r=2.2)
    for h in (0.0, ANCHOR_H):              # D: the silent-failure axis
        for p in ANCHOR_P:
            if h == 0.0 and p != 1.0:
                continue
            add("D_sigma", h, CORE_Q0, p, sg=0.3)
    return pts


# ---------------------------------------------------------------------------
# constants variants (built in memory; the file on disk is never touched)
# ---------------------------------------------------------------------------

def _grid_points_mixed() -> list[dict]:
    """The H_mixed grid: the core hazard axis plus one step on each of the
    other three environment axes.  There is no p axis -- the regime draw
    replaces it -- and no C_fail axis, because the failed-attempt price is
    the model's own measured ratio.  `p` is carried for the row label only
    and names the admission RATE, which env_constants writes to the layouts
    and real_params then overrides per family.
    """
    pts = []

    def add(block, h, q0, r=1.0, sg=0.0):
        pts.append({"block": block, "h": h, "q0": q0, "p": -1.0,
                    "r_fallback": r, "sigma": sg,
                    "silent_penalty": SILENT_PENALTY if sg > 0 else 1.0,
                    "c_fail": C_FAIL_LOW, "mixed": True})

    for h in CORE_H:
        add("M_core", h, CORE_Q0)
    for q0 in (0.17, 0.6):
        add("M_q0", ANCHOR_H, q0)
    for h in (0.0, ANCHOR_H):
        add("M_rfb", h, CORE_Q0, r=2.2)
    for h in (0.0, ANCHOR_H):
        add("M_sigma", h, CORE_Q0, sg=0.3)
    return pts


def mixed_regime(base: dict, cost_set: str, seed: int) -> dict:
    """The regime draw's parameters, read from the cost set's own measured
    per-model block (sim.MIXED_REGIME_DOC).  Nothing is hard-coded here: the
    admission rate, the admitted-median price and the C_fail / C ratio are
    the three numbers the t16_build batch reports for that model.
    """
    pm = base["cost_sets"][cost_set]["per_model"]
    return {"mode": "mixed",
            "admission_rate": float(pm["p_model_admitted_rate"]),
            "C": float(pm["C_median_admitted"]),
            "C_fail_mult": float(pm["C_fail_over_C_ratio"]),
            "seed": int(seed),
            "model": pm["model"],
            "cells_admitted": pm["cells_admitted"],
            "n_cells": pm["n_cells"]}


def env_constants(base: dict, point: dict, cost_set: str = "openapps_glm",
                  prices=PRICES, streams=STREAMS) -> dict:
    """A deep copy of `base` at one grid point.

    Sets p, q0 and the three environment channels on every layout of
    `cost_set` and restricts the E4 grid to this sweep's streams and prices.
    The trigger's horizon cap is left alone: with h > 0 the engine reads the
    program's expected service count 1/h from the environment instead, which
    is the point of moving the hazard out of the trigger.
    """
    c = copy.deepcopy(base)
    for lay in c["cost_sets"][cost_set]["layouts"].values():
        if not point.get("mixed"):
            lay["p"] = float(point["p"])
        lay["q0"] = float(point["q0"])
        lay["h_env"] = float(point["h"])
        lay["r_fallback"] = float(point["r_fallback"])
        lay["sigma"] = float(point["sigma"])
        lay["silent_penalty"] = float(point["silent_penalty"])
        # A failed attempt is priced as its own constant (sim.C_FAIL_DOC).
        # A multiplier, not an absolute: the price ladder overwrites C.
        lay["C_fail_mult"] = float(point.get("c_fail", C_FAIL_LOW))
    # The reference row runs the gate-rate estimator Algorithm 1 is written
    # with, not the family's true p.  Every row of the cell reads this block,
    # and the three variants layer their own mechanism on top of it.  The
    # attempt-spend cap is pinned OFF here for the same reason: it is one of
    # the variants, so a constants file that deploys it (as
    # constants.measured.v2.json does) would otherwise hand it to the
    # reference row too and the ablation would compare nothing.
    c.setdefault("trigger", {})["gate_prior"] = BASE_GATE_PRIOR
    c["trigger"]["spend_cap"] = False
    e4 = c.setdefault("e4", {})
    e4["cost_set"] = cost_set
    e4["prices"] = list(prices)
    e4["streams"] = [s for s in streams if s != "bpi2019"]
    e4["bpi_heldout"] = "bpi2019" in streams
    e4["bpi_reps"] = None
    return c


def config_key(point: dict) -> str:
    if point.get("mixed"):
        return (f"h={point['h']:g}/q0={point['q0']:g}/p=mixed"
                f"/r={point['r_fallback']:g}/sig={point['sigma']:g}")
    return (f"h={point['h']:g}/q0={point['q0']:g}/p={point['p']:g}"
            f"/r={point['r_fallback']:g}/sig={point['sigma']:g}"
            f"/cf={point.get('c_fail', C_FAIL_LOW):g}")


def build_cells(base: dict, reps: int, seed: int, grid=None,
                streams=STREAMS, cost_set: str = "openapps_glm",
                rows=None) -> list[dict]:
    """Every grid point's E4_KMIN3 cells, keyed by config and cell."""
    cells: list[dict] = []
    for point in (grid if grid is not None else _grid_points()):
        cst = env_constants(base, point, cost_set=cost_set, streams=streams)
        ck = config_key(point)
        mech = experiments.trigger_mech(cst)
        for cell in experiments._e4_deploy_cells(cst, reps, seed, "kmin3",
                                                 POLICIES):
            cell["key"] = f"{ck}|{cell['key']}"
            cell["env_metrics"] = True
            # The mechanism rows, run under the reference policy so the four
            # ours rows differ in exactly the mechanism, on the same coins.
            cell["variants"] = variant_mechs(mech, rows)
            cell["variant_policy"] = REFERENCE
            if point.get("mixed"):
                # sim.MIXED_REGIME_DOC: each family's admission regime is
                # drawn from the model's measured population, addressed by
                # the family name so every policy sees the same world.
                cell["regime"] = mixed_regime(base, cost_set, seed)
            cell["variant_policies"] = variant_policies()
            # Per-family pi, the demonstration success rate the pi prior
            # reads.  Drawn from the batch population and addressed by the
            # family name, so it is the same family for every policy, every
            # repetition and every grid point.
            cell["pi_mode"] = "population"
            cell["pi_seed"] = seed
            cell["_config"] = dict(point, config_key=ck)
            cells.append(cell)
    return cells


# ---------------------------------------------------------------------------
# assembly
# ---------------------------------------------------------------------------

def _split_key(key: str) -> tuple[str, str, str]:
    cfg, cell = key.split("|", 1)
    stream, price = cell.split("/price=", 1)
    return cfg, stream, price


def assemble(results: dict, cells: list[dict], B: int) -> dict:
    cfg_of = {c["key"]: c["_config"] for c in cells}
    out: dict = {}
    violations: list[dict] = []
    for key in sorted(results):
        res = results[key]
        table = experiments.assemble_table(
            key, res["rows"], ROW_ORDER, REFERENCE, res["offline"], B,
            res["extra"], offline_label="offline_taxblind",
            offline_tax=res.get("offline_tax"))
        cfg, stream, price = _split_key(key)
        table["_config"] = cfg_of[key]
        table["_stream_name"] = stream
        table["_price"] = price
        table["_n_star"] = res["n_star"]
        table["_stream"] = res["stream_summary"]
        real = {p: table[p]["mean_tokens"] for p in DEPLOYABLE if p in table}
        best = min(real, key=lambda p: real[p])
        naive = real[NAIVE]
        table["_cheapest_policy"] = best
        table["_reactive_is_cheapest"] = (best == "always_reactive")
        # "the compile decision is non-trivial here": some deployable rule is
        # more than 5% cheaper than buying a program for every family that
        # has k_min demonstrations.
        table["_best_vs_naive"] = real[best] / naive
        table["_beats_naive_5pct"] = real[best] < 0.95 * naive
        out[key] = table
        violations.extend(experiments._check_lower_bound(
            key, table, res["rows"], res.get("offline_tax")))
    out["_lower_bound_violations"] = violations
    return out


CSV_FIELDS = ("mean_final_library", "mean_max_library", "mean_n_compiles",
              "mean_program_deaths", "mean_silent_failures",
              "mean_use_failures", "mean_wasted_tax", "mean_tax_share",
              "mean_failed_attempts", "mean_failed_tokens")


def csv_rows(cells_out: dict) -> list[dict]:
    rows = []
    for key, table in cells_out.items():
        if key.startswith("_"):
            continue
        cfg = table["_config"]
        for pol in ROW_ORDER:
            rec = table.get(pol)
            if rec is None:
                continue
            row = {"config": cfg["config_key"], "block": cfg["block"],
                   "h": cfg["h"], "q0": cfg["q0"], "p": cfg["p"],
                   "r_fallback": cfg["r_fallback"], "sigma": cfg["sigma"],
                   "silent_penalty": cfg["silent_penalty"],
                   "c_fail_mult": cfg.get("c_fail", C_FAIL_LOW),
                   "stream": table["_stream_name"], "price": table["_price"],
                   "policy": pol,
                   "mean_tokens": round(rec["mean_tokens"], 3),
                   "rel_to_ours_noinflate": round(rec["rel_to_ours"], 6)}
            for f in CSV_FIELDS:
                row[f] = (round(rec[f], 3) if f in rec else "")
            row["cheapest_policy"] = table["_cheapest_policy"]
            row["best_vs_naive"] = round(table["_best_vs_naive"], 6)
            rows.append(row)
    return rows


MD_ROWS = ("always_reactive", NAIVE, "ours_spend_cap", "ours_cap_epoch",
           "ours_cap_realized", "ours_pi_prior", "breakeven",
           "breakeven_cap_epoch")


def markdown_tables(cells_out: dict, streams=STREAMS, grid=None) -> str:
    """One row per grid point; every number is relative to ours (old).

    The reference column is 1.000 by construction and omitted, so the table
    reads as "what the mechanism does to the deployed trigger".
    """
    lines: list[str] = []
    by_stream: dict[str, dict] = {}
    for key, table in cells_out.items():
        if key.startswith("_"):
            continue
        by_stream.setdefault(table["_stream_name"], {})[
            (table["_config"]["config_key"], table["_price"])] = table
    order = [config_key(pt) for pt in (grid if grid is not None
                                       else _grid_points())]
    head = [r.replace("always_", "").replace("ours_", "+")
            for r in MD_ROWS] + ["best/naive"]
    for stream in streams:
        tabs = by_stream.get(stream)
        if not tabs:
            continue
        lines.append(f"\n### {stream}  (x ours-old; ours-old = 1.000)\n")
        cols = [f"{c} {tag}" for tag in ("nat", "233k") for c in head]
        lines.append("| h | q0 | p | r | sig | cf | " + " | ".join(cols)
                     + " |")
        lines.append("|" + "---|" * (6 + len(cols)))
        for ck in order:
            cells = [tabs.get((ck, price)) for price in PRICES]
            if not any(cells):
                continue
            axes = [seg.split("=", 1)[1] for seg in ck.split("/")]
            axes += ["-"] * (6 - len(axes))   # H_mixed keys carry no cf
            body: list[str] = []
            for t in cells:
                if t is None:
                    body += ["-"] * len(head)
                    continue
                for pol in MD_ROWS:
                    rec = t.get(pol)
                    body.append("-" if rec is None
                                else f"{rec['rel_to_ours']:.3f}")
                mark = "**" if t["_beats_naive_5pct"] else ""
                body.append(f"{mark}{t['_best_vs_naive']:.3f}{mark}")
            lines.append("| " + " | ".join(axes + body) + " |")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--constants", default=str(
        Path(__file__).resolve().parent / "constants.measured.json"))
    ap.add_argument("--reps", type=int, default=10)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--bootstrap", type=int, default=2000, dest="B")
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--tag", default="",
                    help="suffix for the output file name (probe runs)")
    ap.add_argument("--only", default=None,
                    help="comma list of h:q0:p:r:sigma[:c_fail] points, "
                         "for probes")
    ap.add_argument("--streams", default=None,
                    help="comma list, overrides the four real streams")
    ap.add_argument("--cost-set", default="openapps_glm", dest="cost_set",
                    help="cost set the grid overwrites (default "
                         "openapps_glm, the published sweep's set)")
    ap.add_argument("--c-fail-ratio", type=float, default=C_FAIL_HIGH,
                    dest="c_fail_ratio",
                    help="C_fail / C at the high level of the failed-attempt "
                         f"price axis (default {C_FAIL_HIGH:g}, the batch "
                         "placeholder)")
    ap.add_argument("--mixed", action="store_true",
                    help="run the H_mixed block (sim.MIXED_REGIME_DOC): "
                         "each family draws its admission regime and its "
                         "failed-attempt price from the model's measured "
                         "population, instead of one p per cell")
    ap.add_argument("--quick", action="store_true",
                    help="four grid points, short streams, few reps: a smoke "
                         "run that touches every new code path")
    args = ap.parse_args()

    base = experiments.load_constants(args.constants)
    issues, _ = experiments.check_constants(base)
    if issues:
        raise ValueError("constants problems: " + "; ".join(issues))

    grid = None
    if args.only:
        grid = []
        for spec in args.only.split(","):
            vals = [float(x) for x in spec.split(":")]
            h, q0, p, r, sg = vals[:5]
            cf = vals[5] if len(vals) > 5 else C_FAIL_LOW
            grid.append({"block": "probe", "h": h, "q0": q0, "p": p,
                         "r_fallback": r, "sigma": sg, "c_fail": cf,
                         "silent_penalty": SILENT_PENALTY if sg > 0 else 1.0})
    streams = tuple(args.streams.split(",")) if args.streams else STREAMS
    rows = None
    if args.mixed:
        global VARIANT_ROWS, ROW_ORDER, DEPLOYABLE, MD_ROWS
        rows = list(MIXED_VARIANT_ROWS)
        VARIANT_ROWS = tuple(rows)
        ROW_ORDER = POLICIES + rows + ["offline_taxblind", "offline_opt_tax"]
        DEPLOYABLE = [p for p in POLICIES if p != "oracle_tax"] + rows
        MD_ROWS = ("always_reactive", NAIVE) + tuple(rows)
        if grid is None:
            grid = _grid_points_mixed()
    if args.quick:
        # The p = 0 and C_fail axes are the point of the smoke run, so the
        # quick grid is the (p, C_fail) corners at the anchor hazard.
        if grid is None:
            grid = [{"block": "quick", "h": ANCHOR_H, "q0": CORE_Q0, "p": p,
                     "r_fallback": 1.0, "sigma": 0.0, "silent_penalty": 1.0,
                     "c_fail": cf}
                    for p in (0.0, 0.3)
                    for cf in (C_FAIL_LOW, args.c_fail_ratio)]
        base = copy.deepcopy(base)
        for k in ("wiki_A", "wiki_B"):
            base["streams"][k]["window"] = 600
        base["streams"]["bpi2019"]["window"] = 1500
        streams = tuple(s for s in streams if s in ("wiki_A", "sepsis"))
    elif grid is None and args.c_fail_ratio != C_FAIL_HIGH:
        grid = _grid_points(args.c_fail_ratio)

    cells = build_cells(base, args.reps, args.seed, grid, streams,
                        args.cost_set, rows)
    n_points = len(grid if grid is not None else _grid_points())
    print(f"{len(cells)} cells ({n_points} grid points x {len(streams)} "
          f"streams x {len(PRICES)} prices), {args.reps} reps, "
          f"jobs={args.jobs}")

    t0 = time.time()
    results: dict = {}
    if args.jobs == 1:
        for spec in cells:
            k, r = experiments._run_cell(spec)
            results[k] = r
            print(f"  done {k} ({r['wall_s']:.1f}s)", flush=True)
    else:
        ctx = mp.get_context("spawn")
        with ctx.Pool(processes=args.jobs) as pool:
            for i, (k, r) in enumerate(
                    pool.imap_unordered(experiments._run_cell, cells,
                                        chunksize=1)):
                results[k] = r
                print(f"  [{i + 1}/{len(cells)}] {k} ({r['wall_s']:.1f}s) "
                      f"elapsed {time.time() - t0:.0f}s", flush=True)
    wall = time.time() - t0

    assembled = assemble(results, cells, args.B)
    viol = assembled["_lower_bound_violations"]
    hard = [v for v in viol if v["significant"]]
    print(f"\noffline_opt_tax check: {len(viol)} row(s) below the bound, "
          f"{len(hard)} beyond 2 paired SE")

    out_dir = Path(args.out_dir) if args.out_dir \
        else experiments.DEFAULT_OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "experiment": "E11_env_fragility",
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "constants_fingerprint": experiments.constants_fingerprint(base),
        "constants_path": str(args.constants),
        "reps": args.reps, "seed": args.seed, "jobs": args.jobs,
        "bootstrap_B": args.B,
        "n_cells": len(cells),
        "n_grid_points": n_points,
        "grid": [dict(pt) for pt in (grid if grid is not None
                                     else _grid_points())],
        "grid_axes": {"h": list(CORE_H), "q0": [0.17, 0.4, 0.6],
                      "p": list(CORE_P), "r_fallback": [1.0, 2.2],
                      "sigma": [0.0, 0.3],
                      "c_fail_mult": [C_FAIL_LOW, args.c_fail_ratio],
                      "silent_penalty_when_sigma_positive": SILENT_PENALTY,
                      "prices": list(PRICES), "streams": list(streams)},
        "cost_set": args.cost_set,
        "block": ("H_mixed" if args.mixed else "core"),
        "regime": (mixed_regime(base, args.cost_set, args.seed)
                   if args.mixed else None),
        "reference_policy": REFERENCE,
        "reference_label": "ours (old): C_eff = C/p_hat with the flat "
                           "add-one gate prior, no attempt-spend cap",
        "policies": list(POLICIES),
        "variant_rows": list(VARIANT_ROWS),
        "breakeven_rows": list(BREAKEVEN_ROWS),
        "breakeven_policy": BREAKEVEN_POLICY,
        "mechanisms": {"gate_prior_base": BASE_GATE_PRIOR,
                       "spend_cap_mult": SPEND_CAP_MULT,
                       "spend_cap_variants": {
                           "ours_spend_cap": "horizon",
                           "ours_cap_epoch": "epoch",
                           "ours_cap_realized": "realized",
                           "breakeven_cap_epoch": "epoch"},
                       "pi_prior_table": [list(r)
                                          for r in sim.PI_PRIOR_TABLE],
                       "pi_prior_strength": sim.PI_PRIOR_STRENGTH,
                       "pi_population": [list(r)
                                         for r in sim.PI_POPULATION],
                       "c_fail_ratio_high": args.c_fail_ratio},
        "deployment": {"crn": True, "router": sim.ROUTER_LISTING,
                       "eviction": sim.EVICTION_DEFAULT, "k_min": 3},
        "fragility_model": "environment: h kills the artifact on a use "
                           "(loud, manifest entry removed, family must "
                           "recompile); q0 acts on every live-program use "
                           "independently; both channels act together",
        "wall_s": round(wall, 3),
        "cells_wall_s_sum": round(sum(r.get("wall_s", 0.0)
                                      for r in results.values()), 3),
        "units": "cache-adjusted tokens (fresh + r*cached)",
    }
    stem = OUT_NAME + (f"_{args.tag}" if args.tag else "")
    jpath = out_dir / f"{stem}.json"
    cpath = out_dir / f"{stem}.csv"
    mpath = out_dir / f"{stem}.md"
    for path in (jpath, cpath, mpath):
        if path.exists():
            raise SystemExit(f"refusing to overwrite {path}")
    # json_safe: at p = 0 the cell's N* = C_eff / s is infinite, which
    # Python writes as the non-standard token `Infinity`.
    jpath.write_text(json.dumps(
        experiments.json_safe({"meta": meta, "cells": assembled}), indent=1))
    rows = csv_rows(assembled)
    with cpath.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    md = markdown_tables(assembled, streams, grid)
    mpath.write_text(f"# {stem}\n\nconstants {args.constants} "
                     f"(fingerprint {meta['constants_fingerprint']}), cost "
                     f"set {args.cost_set}, {args.reps} reps, seed "
                     f"{args.seed}, C_fail/C {args.c_fail_ratio:g}.\n"
                     f"Reference column: {REFERENCE} "
                     f"({meta['reference_label']}).\n" + md + "\n")
    print(f"wrote {jpath}\nwrote {cpath}\nwrote {mpath}\nwall {wall:.1f}s")
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
