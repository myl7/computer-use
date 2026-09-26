"""Offline paired-replay analysis (t21): shares, pairing, noise.

Reads the replay summaries (`use_*/summary.json` under
`t21_paired_replay/<model_slug>/<Family>/`), the deploy records they are
paired against (`t16_build/<model_slug>/<Family>/deploy.json`), and the
exploration records behind the table's `c` (`t16_build/.../explore/
exploration.json`), and writes `analysis.json` + `analysis.md`:

  * per cell: the paired program-use price (d + q*c, per binding and
    summed) against the re-run agent price, the paired saving share
    1 - sum(b)/sum(c), and the table's share on the same axis;
  * the within-binding paired difference distribution (median, IQR,
    how often the program route is cheaper than the agent re-run);
  * the agent re-run noise: dispersion of the per-binding replay cost
    around its cell mean (std, CV, MAD/median), which is the quantity a
    single audit run cannot see;
  * a recomputation of the table share from exploration + deploy, as a
    cross-check that the paired and table axes are the same unit.

Axis conventions, copied exactly from `constants_table.build_cell_record`
so the paired share and the table share live on one axis:

  * c (agent cost) is price-weighted tokens with the per-episode harness
    floor subtracted (`FLOOR_RAW_TOKENS`, charged as uncached prompt);
  * d (program per-use cost) is the deploy bill over the fresh-input price
    (`cost_usd / p_in`) and is NOT floor-subtracted -- the floor is a
    per-episode harness constant of the agent arms, not of the deploy
    stage's one-shot extraction call (`read_deploy` never floors);
  * a replay that never reached the model (reset/harness error, zero
    model calls) is excluded from the cost statistics the same way the
    table's exploration skips zero-token attempts; genuine task failures
    (model called, reward 0) stay in, because the arrival argument's
    q*c term pays the agent on exactly those bindings.

All network-free, device-free. Runs on the Mac or the server:

    .venv-android/bin/python -m guiexp_android.pairreplay.analyze \
        --replay-root ../experimental-results/guiexp_android/t21_paired_replay \
        --t16-root ../experimental-results/guiexp_android/t16_build
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path

from ..constants_table import PRICE_SHEET, FLOOR_RAW_TOKENS, Meter
from . import CELLS, TABLE_SHARE


def pw(meter: Meter, usage: dict) -> float:
    """Price-weighted tokens of one usage dict (missing cached -> 0)."""
    return meter.counts(usage) or 0.0


def floored(meter: Meter, value: float) -> float:
    """Floor subtraction, applied to agent-episode costs only (see module doc)."""
    return value - FLOOR_RAW_TOKENS.get(meter.model or "", 0.0)


def median_ci(values: list[float], z: float = 1.96) -> tuple[float, float]:
    """Median with an asymptotic CI (binomial sign-test inversion)."""
    if not values:
        return float("nan"), float("nan")
    xs = sorted(values)
    n = len(xs)
    lo = xs[max(0, int(math.floor(0.5 * n - z * 0.5 * math.sqrt(n))) - 1)]
    hi = xs[min(n - 1, int(math.ceil(0.5 * n + z * 0.5 * math.sqrt(n))) - 1)]
    return lo, hi


def load_cell(replay_root: Path, t16_root: Path, model: str, family: str):
    slug = model.replace("/", "_")
    deploy = json.loads((t16_root / slug / family / "deploy.json").read_text())
    replays = []
    for use_dir in sorted((replay_root / slug / family).glob("use_*")):
        summary = use_dir / "summary.json"
        if summary.is_file():
            replays.append(json.loads(summary.read_text()))
    exploration = t16_root / slug / family / "explore" / "exploration.json"
    explo = json.loads(exploration.read_text()) if exploration.is_file() else None
    return deploy, replays, explo


def analyse_cell(model: str, family: str, deploy: dict, replays: list, explo):
    meter = Meter(model)
    uses = deploy["uses"]
    q = 1.0 - deploy["success_count"] / deploy["n"]

    # -- the program route: d = the deploy bill over the fresh-input price,
    # per served binding, NOT floor-subtracted (read_deploy parity).
    d_pw = [(u["cost_usd"] or 0.0) / PRICE_SHEET[model]["p_in"] for u in uses]
    d_raw = [u["tokens"] for u in uses]
    d_bar = statistics.mean(d_pw) if d_pw else float("nan")

    # -- the agent re-runs, keyed by use_index (never by directory order:
    # a run whose summary never landed must not shift the pairing).
    by_index = {r.get("use_index"): r for r in replays}
    valid = []           # (use_index, c_pw floored, success)
    harness_errors = 0   # replays that never reached the model
    for i in range(len(uses)):
        r = by_index.get(i)
        if r is None:
            harness_errors += 1
            continue
        usage = r.get("usage") or {}
        if not usage.get("calls"):
            harness_errors += 1  # reset/harness failure; no paid episode
            continue
        valid.append((i, floored(meter, pw(meter, usage)), bool(r["success"])))
    c_pw = [c for (_, c, _) in valid]
    ok = [s for (_, _, s) in valid]
    runs_ok = sum(1 for r in replays if r.get("success"))

    # -- the table's c (exploration episodes, retries included, floored) --
    c_table = None
    if explo:
        attempt_costs = []
        for instance in explo["instances"]:
            for attempt in instance["attempts"]:
                u = attempt["usage"]
                if u.get("total_tokens") == 0 and u.get("cost_usd") == 0:
                    continue
                attempt_costs.append(pw(meter, u))
        if attempt_costs:
            c_table = floored(meter, statistics.mean(attempt_costs))

    # -- pairing over the valid replays --
    paired = [
        {"use_index": i, "c": c, "d": d_pw[i],
         "b": d_pw[i] + q * max(c, 0.0),
         "diff_c_minus_b": c - (d_pw[i] + q * max(c, 0.0)),
         "agent_ok": s, "deploy_ok": uses[i]["success"]}
        for (i, c, s) in valid
    ]
    for p in paired:
        p["share_i"] = p["diff_c_minus_b"] / p["c"] if p["c"] else None
    c_bar = statistics.mean(c_pw) if c_pw else float("nan")
    b_bar = d_bar + q * c_bar
    share_paired = 1 - b_bar / c_bar if c_pw and c_bar else None

    # table share recomputed exactly as build_cell_record does:
    # s_arrival = (1-q)*c - d over the floored exploration c.
    share_table_recomputed = None
    if c_table:
        share_table_recomputed = ((1 - q) * c_table - d_bar) / c_table

    # -- noise of the agent re-run (the dispersion one audit run cannot see) --
    noise = {}
    if len(c_pw) >= 2:
        mean = statistics.mean(c_pw)
        stdev = statistics.stdev(c_pw)
        median = statistics.median(c_pw)
        noise = {
            "n": len(c_pw),
            "mean": mean,
            "stdev": stdev,
            "cv": stdev / mean if mean else None,
            "median": median,
            "mad": statistics.median([abs(x - median) for x in c_pw]),
            "p10": sorted(c_pw)[max(0, int(0.10 * len(c_pw)) - 1)],
            "p90": sorted(c_pw)[min(len(c_pw) - 1, int(0.90 * len(c_pw)))],
            "max_over_mean": max(c_pw) / mean if mean else None,
        }
        ok_costs = [c for (_, c, s) in valid if s]
        if len(ok_costs) >= 2:
            om, osd = statistics.mean(ok_costs), statistics.stdev(ok_costs)
            noise["success_only"] = {
                "n": len(ok_costs), "mean": om, "stdev": osd,
                "cv": osd / om if om else None,
            }

    diffs = [p["diff_c_minus_b"] for p in paired]
    cheaper = sum(1 for x in diffs if x > 0)
    lo, hi = median_ci(diffs)
    return {
        "model": model,
        "family": family,
        "n_replays": len(replays),
        "n_valid": len(valid),
        "harness_errors": harness_errors,
        "agent_success_runs": runs_ok,
        "deploy_q": q,
        "c_replay_mean_floored_pw": c_bar,
        "c_table_exploration_floored_pw": c_table,
        "d_mean_pw_bill_over_p_in": d_bar,
        "d_mean_raw_tokens": statistics.mean(d_raw) if d_raw else None,
        "b_mean_pw": b_bar,
        "share_paired": share_paired,
        "share_table_reported": TABLE_SHARE[(model, family)],
        "share_table_recomputed": share_table_recomputed,
        "paired_diff": {
            "median": statistics.median(diffs) if diffs else None,
            "ci_lo": lo, "ci_hi": hi,
            "iqr": (statistics.quantiles(diffs, n=4)[2]
                    - statistics.quantiles(diffs, n=4)[0]) if len(diffs) >= 4 else None,
            "program_cheaper_fraction": cheaper / len(diffs) if diffs else None,
        },
        "noise": noise,
        "paired": paired,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--replay-root", type=Path, required=True)
    parser.add_argument("--t16-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=None,
                        help="write analysis.json/md next to replay-root by default")
    args = parser.parse_args()

    cells = []
    for model, family in CELLS:
        deploy, replays, explo = load_cell(
            args.replay_root, args.t16_root, model, family)
        if not replays:
            print(f"skip {model}/{family}: no replays found")
            continue
        cells.append(analyse_cell(model, family, deploy, replays, explo))

    out_json = args.out or (args.replay_root / "analysis.json")
    out_md = out_json.with_suffix(".md")
    summary_cells = [{k: v for k, v in c.items() if k != "paired"}
                     for c in cells]
    out_json.write_text(json.dumps(
        {"cells": cells, "summary": summary_cells}, indent=1))

    lines = [
        "# t21 paired replay analysis", "",
        "c = replay discover cost, pw tokens, harness floor subtracted; "
        "d = deploy bill / p_in per served use (not floored, table parity); "
        "b = d + q*c.", "",
        "| cell | n | valid | err | agent ok | q | c replay | c table | d | b=d+qc | paired share | table share | recomputed |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for c in cells:
        name = f"{c['model'].split('/')[0]}/{c['family']}"
        fmt = lambda x: "--" if x is None else f"{x:,.0f}"  # noqa: E731
        sh = lambda x: "--" if x is None else f"{x:.3f}"  # noqa: E731
        lines.append(
            f"| {name} | {c['n_replays']} | {c['n_valid']} | {c['harness_errors']} "
            f"| {c['agent_success_runs']} | {c['deploy_q']:.2f} "
            f"| {fmt(c['c_replay_mean_floored_pw'])} "
            f"| {fmt(c['c_table_exploration_floored_pw'])} "
            f"| {fmt(c['d_mean_pw_bill_over_p_in'])} | {fmt(c['b_mean_pw'])} "
            f"| {sh(c['share_paired'])} | {c['share_table_reported']:.2f} "
            f"| {sh(c['share_table_recomputed'])} |")
    lines += ["", "## agent re-run noise (per cell, floored pw)", "",
              "| cell | n | mean | stdev | CV | MAD | p10 | p90 | max/mean | ok-only CV |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for c in cells:
        n = c["noise"]
        if not n:
            continue
        name = f"{c['model'].split('/')[0]}/{c['family']}"
        okcv = n.get("success_only", {}).get("cv")
        lines.append(
            f"| {name} | {n['n']} | {n['mean']:,.0f} | {n['stdev']:,.0f} "
            f"| {n['cv']:.2f} | {n['mad']:,.0f} | {n['p10']:,.0f} "
            f"| {n['p90']:,.0f} | {n['max_over_mean']:.2f} "
            f"| {'--' if okcv is None else format(okcv, '.2f')} |")
    lines += ["", "## within-binding paired difference c - b (pw, floored c, raw d)", "",
              "| cell | median | CI lo | CI hi | IQR | program cheaper |",
              "|---|---|---|---|---|---|"]
    for c in cells:
        p = c["paired_diff"]
        name = f"{c['model'].split('/')[0]}/{c['family']}"
        fmtd = lambda x: "--" if x is None else f"{x:,.0f}"  # noqa: E731
        frac = p["program_cheaper_fraction"]
        lines.append(
            f"| {name} | {fmtd(p['median'])} | {fmtd(p['ci_lo'])} "
            f"| {fmtd(p['ci_hi'])} | {fmtd(p['iqr'])} "
            f"| {'--' if frac is None else format(frac, '.2f')} |")
    out_md.write_text("\n".join(lines) + "\n")
    print(out_md.read_text())
    print(f"wrote {out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
