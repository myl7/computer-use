"""E9: does `ours` regain the lead when compiled programs are fragile?

The router/eviction study (docs/t2sim-router-eviction-2026-09-09.md) found
that at the measured constants the naive rule "compile every family once it
has k_min demonstrations, then evict idle programs" beats the trigger in most
cells.  Every one of those cells is run at p = 1 (the gate never misses),
q0 = 0.17 (the program works five times out of six) and a 50-use program
lifetime, which is the friendliest corner of the space for a rule that buys
first and asks later.

This sweep walks away from that corner along the three axes the trigger
prices and the naive rule does not:

  * p, the gate pass rate.  `ours` compares the saving against C_eff = C/p,
    so a low p raises its threshold; `always_compile*` pays C per attempt and
    keeps attempting.
  * q0, the per-use failure rate.  It lowers the saving s = (1-q0)c - d that
    both rules earn per program-served arrival, so it raises the break-even
    use count N* = C_eff/s for everyone, but only `ours` reads N*.
  * the program lifetime in uses, which enters as the trigger's horizon cap
    (mech["horizon_cap"], "1/h" in the old engine's Theorem 2).  A short
    lifetime truncates the projection `ours` is allowed to make.

WHAT THE LIFETIME AXIS IS, EXACTLY.  It is a trigger constant, not an
environment change.  The engine does have a drift channel (fam["h"] > 0,
programs really break), but that channel REPLACES the q0 channel in
FamilyState.serve -- with h > 0 the per-use coin is spent on the break test
and q0 is never read -- so turning it on would collapse the q0 axis of this
grid, and it also forces offline_optimum_tax to fall back to the tax-blind
DP.  So the lifetime axis here answers "how much does capping the horizon by
the program's expected service count buy the trigger", which is the third
clause of the hypothesis, and it does not make programs die.  Stated in the
report rather than buried here.

Deployment: the k_min = 3 configuration of E4_KMIN3, i.e. the measured
full-listing router, the residency rule at T*, common random numbers on, and
a compiler that needs three demonstrations.  Two prices (native and the
AutoRPA 233k build price) and the four real streams.  At (p=1.0, q0=0.17,
lifetime=50) the cells are exactly E4_KMIN3's native and 233k columns, which
is the reproduction check.

    python3 computer-use/t2sim/sweep_fragility.py \
        --constants computer-use/t2sim/constants.measured.json \
        --reps 10 --jobs 2

Writes experimental-results/guiexp/t2_sim/E9_fragility.json and .csv.
constants.measured.json is read only; every grid point is a deep copy built
in memory by `fragility_constants`.
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
import stats

# The grid.  p and q0 are family constants of every layout in the cost set;
# the lifetime is the trigger's horizon cap.
P_GRID = (1.0, 0.5, 0.3, 0.1)
Q0_GRID = (0.17, 0.4)
LIFETIME_GRID = (50, 10, 3)
PRICES = ("native", "autorpa_233k")
STREAMS = ("sepsis", "bpi2019", "wiki_A", "wiki_B")

# ours_evict is the reference row: it is the policy that carries BOTH the
# trigger and the residency rule, so it is the one the naive evicting rules
# are a fair comparison for.  Plain `ours` (monotone library) is not run.
REFERENCE = "ours_evict"
POLICIES = ["always_reactive", "always_compile_evict",
            "always_compile_evict_abandon", "on_second", REFERENCE,
            "oracle_tax"]
ROW_ORDER = POLICIES + ["offline_taxblind", "offline_opt_tax"]
# rules a deployment could actually run; oracle_tax and the offline rows are
# clairvoyant references and never count as "the cheapest policy".
DEPLOYABLE = [p for p in POLICIES if p != "oracle_tax"]

OUT_NAME = "E9_fragility"


# ---------------------------------------------------------------------------
# constants variants (built in memory; the file on disk is never touched)
# ---------------------------------------------------------------------------

def fragility_constants(base: dict, p: float, q0: float, lifetime: float,
                        cost_set: str = "openapps_glm",
                        prices=PRICES, streams=STREAMS) -> dict:
    """A deep copy of `base` at one grid point.

    Sets p and q0 on every layout of `cost_set`, the trigger's horizon cap to
    `lifetime`, and restricts the E4 grid to the streams and prices this
    sweep runs.  `base` is not mutated and no other cost set is touched, so
    the same loaded constants object can build the whole grid.
    """
    c = copy.deepcopy(base)
    layouts = c["cost_sets"][cost_set]["layouts"]
    for lay in layouts.values():
        lay["p"] = float(p)
        lay["q0"] = float(q0)
    c["trigger"]["horizon_cap"] = float(lifetime)
    e4 = c.setdefault("e4", {})
    e4["cost_set"] = cost_set
    e4["prices"] = [x for x in prices]
    e4["streams"] = [s for s in streams if s != "bpi2019"]
    e4["bpi_heldout"] = "bpi2019" in streams
    e4["bpi_reps"] = None
    return c


def config_key(p: float, q0: float, lifetime: float) -> str:
    return f"p={p:g}/q0={q0:g}/life={lifetime:g}"


def build_cells(base: dict, reps: int, seed: int, grid=None,
                streams=STREAMS) -> list[dict]:
    """Every grid point's E4_KMIN3 cells, keyed by config and cell."""
    cells: list[dict] = []
    for p, q0, life in (grid or _full_grid()):
        cst = fragility_constants(base, p, q0, life, streams=streams)
        ck = config_key(p, q0, life)
        for cell in experiments._e4_deploy_cells(cst, reps, seed, "kmin3",
                                                 POLICIES):
            cell["key"] = f"{ck}|{cell['key']}"
            cell["_config"] = {"p": p, "q0": q0, "lifetime": life,
                               "config_key": ck}
            cells.append(cell)
    return cells


def _full_grid():
    return [(p, q0, life) for p in P_GRID for q0 in Q0_GRID
            for life in LIFETIME_GRID]


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
        # the cheapest row that is not a clairvoyant reference (oracle_tax
        # and the two offline rows are references, not deployable rules)
        real = {p: table[p]["mean_tokens"] for p in DEPLOYABLE if p in table}
        best = min(real, key=lambda p: real[p])
        table["_cheapest_policy"] = best
        table["_ours_is_cheapest"] = (best == REFERENCE)
        out[key] = table
        violations.extend(experiments._check_lower_bound(
            key, table, res["rows"], res.get("offline_tax")))
    out["_lower_bound_violations"] = violations
    return out


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
            rows.append({
                "config": cfg["config_key"],
                "p": cfg["p"], "q0": cfg["q0"], "lifetime": cfg["lifetime"],
                "stream": table["_stream_name"], "price": table["_price"],
                "policy": pol,
                "mean_tokens": round(rec["mean_tokens"], 3),
                "rel_to_ours_evict": round(rec["rel_to_ours"], 6),
                "mean_final_library": round(rec.get("mean_final_library",
                                                    float("nan")), 3)
                if "mean_final_library" in rec else "",
                "mean_max_library": round(rec.get("mean_max_library",
                                                  float("nan")), 3)
                if "mean_max_library" in rec else "",
                "mean_n_compiles": round(rec.get("mean_n_compiles",
                                                 float("nan")), 3)
                if "mean_n_compiles" in rec else "",
                "mean_wasted_compiles": round(rec.get("mean_wasted",
                                                      float("nan")), 3)
                if "mean_wasted" in rec else "",
                "mean_wasted_compiles_tax": round(
                    rec.get("mean_wasted_tax", float("nan")), 3)
                if "mean_wasted_tax" in rec else "",
                "mean_wasted_tax_tokens": round(
                    rec.get("mean_wasted_tax_tokens", float("nan")), 3)
                if "mean_wasted_tax_tokens" in rec else "",
                "mean_tax_share": round(rec.get("mean_tax_share",
                                                float("nan")), 6)
                if "mean_tax_share" in rec else "",
                "cheapest_policy": table["_cheapest_policy"],
            })
    return rows


def markdown_tables(cells_out: dict, streams=STREAMS) -> str:
    """Per stream: the two ratios of interest at both prices, per grid cell."""
    lines: list[str] = []
    by_stream: dict[str, dict] = {}
    for key, table in cells_out.items():
        if key.startswith("_"):
            continue
        by_stream.setdefault(table["_stream_name"], {})[
            (table["_config"]["config_key"], table["_price"])] = table
    for stream in streams:
        tabs = by_stream.get(stream)
        if not tabs:
            continue
        lines.append(f"\n### {stream}\n")
        lines.append("| p | q0 | lifetime | naive/ours native | "
                     "ours/opt native | naive/ours 233k | ours/opt 233k | "
                     "ours cheapest |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for p in P_GRID:
            for q0 in Q0_GRID:
                for life in LIFETIME_GRID:
                    ck = config_key(p, q0, life)
                    cols = []
                    winner = []
                    for price in PRICES:
                        t = tabs.get((ck, price))
                        if t is None:
                            cols += ["-", "-"]
                            continue
                        ab = t.get("always_compile_evict_abandon")
                        ours = t[REFERENCE]["mean_tokens"]
                        opt = t.get("offline_opt_tax")
                        cols.append(f"{ab['rel_to_ours']:.3f}"
                                    if ab else "-")
                        cols.append(f"{ours / opt['mean_tokens']:.3f}"
                                    if opt else "-")
                        if t["_ours_is_cheapest"]:
                            winner.append(price.replace("autorpa_", ""))
                    mark = ("**" + ",".join(winner) + "**") if winner else ""
                    lines.append(
                        f"| {p:g} | {q0:g} | {life:g} | {cols[0]} | "
                        f"{cols[1]} | {cols[2]} | {cols[3]} | {mark} |")
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
                    help="comma list of p:q0:life points, for timing probes")
    ap.add_argument("--streams", default=None,
                    help="comma list, overrides the four real streams")
    args = ap.parse_args()

    base = experiments.load_constants(args.constants)
    issues, _ = experiments.check_constants(base)
    if issues:
        raise ValueError("constants problems: " + "; ".join(issues))

    grid = None
    if args.only:
        grid = []
        for spec in args.only.split(","):
            a, b, c = spec.split(":")
            grid.append((float(a), float(b), float(c)))
    streams = tuple(args.streams.split(",")) if args.streams else STREAMS

    cells = build_cells(base, args.reps, args.seed, grid, streams)
    print(f"{len(cells)} cells "
          f"({len(grid or _full_grid())} grid points x {len(streams)} "
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
        "experiment": "E9_fragility",
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "constants_fingerprint": experiments.constants_fingerprint(base),
        "constants_path": str(args.constants),
        "reps": args.reps, "seed": args.seed, "jobs": args.jobs,
        "bootstrap_B": args.B,
        "n_cells": len(cells),
        "grid": {"p": list(P_GRID), "q0": list(Q0_GRID),
                 "lifetime": list(LIFETIME_GRID),
                 "prices": list(PRICES), "streams": list(streams)},
        "reference_policy": REFERENCE,
        "policies": list(POLICIES),
        "abandon_after": sim.ABANDON_AFTER,
        "deployment": {"crn": True, "router": sim.ROUTER_LISTING,
                       "eviction": sim.EVICTION_DEFAULT, "k_min": 3},
        "lifetime_axis": "trigger horizon_cap (mech), not an environment "
                         "drift hazard; fam h stays 0 so the q0 channel "
                         "stays live and offline_optimum_tax stays exact",
        "wall_s": round(wall, 3),
        "cells_wall_s_sum": round(sum(r.get("wall_s", 0.0)
                                      for r in results.values()), 3),
        "units": "cache-adjusted tokens (fresh + r*cached)",
    }
    stem = OUT_NAME + (f"_{args.tag}" if args.tag else "")
    jpath = out_dir / f"{stem}.json"
    cpath = out_dir / f"{stem}.csv"
    for path in (jpath, cpath):
        if path.exists():
            raise SystemExit(f"refusing to overwrite {path}")
    jpath.write_text(json.dumps({"meta": meta, "cells": assembled}, indent=1))
    rows = csv_rows(assembled)
    with cpath.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {jpath}\nwrote {cpath}\nwall {wall:.1f}s")
    print(markdown_tables(assembled, streams))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
