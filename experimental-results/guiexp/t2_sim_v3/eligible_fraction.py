#!/usr/bin/env python
"""Eligible-fraction analysis for E4 (paper section 5.4).

A family that arrives fewer than three times never reaches the compile
decision (compiling consumes three agent attempts on the family), so the
paper reports, per real stream, the ELIGIBLE FRACTION -- the share of
arrivals belonging to families that ever reach three arrivals -- and scores
the E4 rule set on the ELIGIBLE SUBSTREAM as well as on the full stream
(full-stream scores are E4_full_v3.json; this script adds the substream
side).

    .venv-gui/bin/python experimental-results/guiexp/t2_sim_v3/eligible_fraction.py \
        --constants computer-use/t2sim/constants.measured.v3.json \
        --reps 20 --seed 7 --prices native,5M \
        --out experimental-results/guiexp/t2_sim_v3/eligible_fraction_v3.json

Pure local CPU; reads the constants' streams block, no network.  The
eligible-substream cells are built exactly like _e4_cells (same policies,
same variants, same offline optimum, same seeding), so the numbers are
directly comparable to the E4 tables.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import sys
import time
from pathlib import Path

T2SIM = (Path(__file__).resolve().parents[3] / "computer-use" / "t2sim")
sys.path.insert(0, str(T2SIM))

import experiments  # noqa: E402
import sim          # noqa: E402
import streams as streams_mod  # noqa: E402

STREAMS = ["wiki_A", "wiki_B", "sepsis", "bpi2019"]
MIN_ARRIVALS = 3      # "ever reach three arrivals"


def eligible_substream(stream: list[str]):
    counts: dict[str, int] = {}
    for name in stream:
        counts[name] = counts.get(name, 0) + 1
    keep = {n for n, k in counts.items() if k >= MIN_ARRIVALS}
    sub = [n for n in stream if n in keep]
    frac_arrivals = len(sub) / max(1, len(stream))
    frac_families = len(keep) / max(1, len(counts))
    return sub, {
        "n_arrivals": len(stream),
        "n_arrivals_eligible": len(sub),
        "eligible_fraction_arrivals": frac_arrivals,
        "families": len(counts),
        "families_eligible": len(keep),
        "eligible_fraction_families": frac_families,
        "definition": (f"arrival belongs to a family with >= {MIN_ARRIVALS} "
                       f"arrivals in the full stream"),
    }


def build_cells(constants: dict, prices: list[str], reps: int, seed: int):
    e4 = constants.get("e4", {})
    cs_name = e4.get("cost_set", "android_glm")
    layouts, tau_cfg, _ = experiments.cost_set_profiles(constants, cs_name)
    eps = experiments.eps_cfg(constants)
    horizon = constants["trigger"]["horizon_fixed"]
    mech = experiments.trigger_mech(constants)
    cells = []
    for sname in e4.get("streams", STREAMS[:3]) + (
            ["bpi2019"] if e4.get("bpi_heldout", True) else []):
        stream = streams_mod.load_stream(constants["streams"][sname])
        sub, summary = eligible_substream(stream)
        for price_name in prices:
            cells.append({
                "kind": "real",
                "key": f"{sname}/eligible/price={price_name}",
                "stream_spec": {"format": "inline", "stream": sub},
                "layouts": layouts, "tau": tau_cfg, "epsilon": eps,
                "horizon": horizon,
                "price_C": experiments.price_C(constants, price_name),
                "price_name": price_name,
                "reps": reps, "seed": seed,
                "policies": list(experiments.E4_POLICIES),
                "variants": experiments._e4_variants(mech),
                "variant_policies": dict(experiments.E4_VARIANT_POLICIES),
                "mech": mech, "offline": True,
            })
    # k_min_global (W10 D1, 2026-09-19): same three-trace eligibility as
    # run.py --kmin-global, so the substream scores stay comparable to the
    # rerun E4 tables.  real cells consume spec["deployment"].
    kg = (constants.get("deployment") or {}).get("k_min_global")
    if kg:
        for cell in cells:
            cell["deployment"] = {"k_min": int(kg)}
    return cs_name, cells, {s: eligible_substream(
        streams_mod.load_stream(constants["streams"][s]))[1]
        for s in (e4.get("streams", STREAMS[:3])
                  + (["bpi2019"] if e4.get("bpi_heldout", True) else []))}


def _patch_load_stream():
    """Teach streams.load_stream an 'inline' format (sublists built here).

    Only safe with --jobs 1: spawn workers re-import the module fresh and
    would not see the patch, so main() forces serial execution.
    """
    orig = streams_mod.load_stream

    def load(spec):
        if spec.get("format") == "inline":
            return spec["stream"]
        return orig(spec)

    streams_mod.load_stream = load


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--constants", required=True)
    ap.add_argument("--reps", type=int, default=20)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--prices", default="native,5M")
    ap.add_argument("--cost-set", default=None,
                    help="override e4.cost_set (default: the constants' own)")
    ap.add_argument("--jobs", type=int, default=1,
                    help="must stay 1: the inline stream format lives in "
                         "this process only")
    ap.add_argument("--bootstrap", type=int, default=2000, dest="B")
    ap.add_argument("--tag", default="")
    ap.add_argument("--kmin-global", type=int, default=None,
                    dest="kmin_global",
                    help="raise the compiler's demonstration requirement "
                         "for every cell (3 = Algorithm 1's three-trace "
                         "eligibility)")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    constants = experiments.load_constants(args.constants)
    if args.cost_set:
        constants = json.loads(json.dumps(constants))
        constants.setdefault("e4", {})["cost_set"] = args.cost_set
    if args.kmin_global is not None:
        constants = json.loads(json.dumps(constants))
        constants.setdefault("deployment", {})["k_min_global"] = \
            args.kmin_global

    t0 = time.time()
    _patch_load_stream()
    cs_name, cells, stream_info = build_cells(constants,
                                              args.prices.split(","),
                                              args.reps, args.seed)
    results: dict = {}
    if args.jobs == 1:
        experiments._init_worker(constants)
        for spec in cells:
            k, r = experiments._run_cell(spec)
            results[k] = r
    else:
        ctx = mp.get_context("spawn")
        with ctx.Pool(processes=args.jobs or None,
                      initializer=experiments._init_worker,
                      initargs=(constants,) ) as p:
            for k, r in p.imap_unordered(experiments._run_cell, cells,
                                         chunksize=1):
                results[k] = r

    assembled = {}
    row_order = [p for p in experiments.E4_ROW_ORDER if p != "offline_opt"]
    for key, res in sorted(results.items()):
        table = experiments.assemble_table(key, res["rows"], row_order,
                                           "ours", res["offline"], args.B,
                                           res["extra"])
        table["_n_star"] = res["n_star"]
        table["_stream"] = res["stream_summary"]
        assembled[key] = table
        experiments._print_cell(key, table, experiments.E4_ROW_ORDER)

    out = {
        "meta": {
            "analysis": "eligible_fraction_v3",
            "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "constants_fingerprint":
                experiments.constants_fingerprint(constants),
            "cost_set": cs_name,
            "buy_formula": sim.buy_formula_mode(
                experiments.trigger_mech(constants)),
            "reps": args.reps, "seed": args.seed,
            "prices": args.prices.split(","),
            "min_arrivals_for_eligible": MIN_ARRIVALS,
            "wall_s": round(time.time() - t0, 1),
            "note": ("full-stream scores are in E4_full_v3.json (or "
                     "E4_full_v3_e4ds.json for android_ds); this file holds "
                     "the eligible-fraction summary and the "
                     "eligible-substream scores"),
        },
        "streams": stream_info,
        "cells": assembled,
    }
    Path(args.out).write_text(json.dumps(experiments.json_safe(out),
                                         indent=1))
    print(f"\nwrote {args.out}  ({len(cells)} cells, "
          f"wall {time.time() - t0:.1f}s)")
    for s, info in stream_info.items():
        print(f"  {s:8s} eligible fraction (arrivals) "
              f"{info['eligible_fraction_arrivals']:.4f}"
              f"  ({info['n_arrivals_eligible']:,}/{info['n_arrivals']:,}),"
              f" families {info['eligible_fraction_families']:.4f}"
              f" ({info['families_eligible']:,}/{info['families']:,})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
