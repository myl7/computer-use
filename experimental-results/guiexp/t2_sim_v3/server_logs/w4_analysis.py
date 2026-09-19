#!/usr/bin/env python3
"""W4: old-vs-new E11 comparison analysis.

Old = the archived constants-v2 sweeps (pre-W1.5 narrow buy price):
  E11_env_fragility_v3_android_{glm,ds}.json  (2026-09-12, 400 cells each)
New = the W4 rerun with constants.measured.v3.json + buy_formula "full".
Prints the tables that feed E11_COMPARISON.md.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

OLD_DIR = Path("/Users/myl/app/computer-use/experimental-results/guiexp/t2_sim")
NEW_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
    "/Users/myl/app/computer-use/experimental-results/guiexp/t2_sim_v3")

CAPS = ("ours_spend_cap", "ours_cap_epoch", "ours_cap_realized")
FIXED = ("always_reactive", "always_compile_evict")
TRIG = ("ours_noinflate",) + CAPS


def load(path):
    d = json.loads(Path(path).read_text())
    cells = {k: v for k, v in d["cells"].items()
             if not k.startswith("_")}
    return d["meta"], cells


def parse_key(key):
    cfg, cell = key.split("|", 1)
    stream, price = cell.split("/price=", 1)
    ax = {}
    for seg in cfg.split("/"):
        k, v = seg.split("=", 1)
        ax[k] = v
    return ax, stream, price


def cf_level(cf_str):
    return "high" if float(cf_str) > 1.0001 else "low"


def idx(cells):
    out = {}
    for key, t in cells.items():
        ax, stream, price = parse_key(key)
        out[(ax["h"], ax["q0"], ax["p"], ax["r"], ax["sig"],
             cf_level(ax["cf"]), stream, price)] = (key, t)
    return out


def worst_cells(cells):
    """worst variant/best-fixed ratio per cell, max over cells."""
    res = {}
    for key, t in cells.items():
        best_fixed = min(t[p]["mean_tokens"] for p in FIXED if p in t)
        for pol in CAPS + ("ours_noinflate",):
            if pol in t:
                r = t[pol]["mean_tokens"] / best_fixed
                cur = res.get(pol, (0.0, ""))
                if r > cur[0]:
                    res[pol] = (r, key)
    return res


def main():
    summary = {}
    for model, old_tag in (("android_glm", "E11_env_fragility_v3_android_glm"),
                           ("android_ds", "E11_env_fragility_v3_android_ds")):
        mo, co = load(OLD_DIR / f"{old_tag}.json")
        mn, cn = load(NEW_DIR / f"E11_env_fragility_{model}.json")
        io, inew = idx(co), idx(cn)
        print(f"\n================ {model} ================")
        print(f"old: {mo['n_cells']} cells, consts {mo['constants_fingerprint']},"
              f" cf_high {mo['mechanisms']['c_fail_ratio_high']:.4f}")
        print(f"new: {mn['n_cells']} cells, consts {mn['constants_fingerprint']},"
              f" cf_high {mn['mechanisms']['c_fail_ratio_high']:.4f},"
              f" buy_formula full")

        print("\n--- worst cell vs best fixed rule (old -> new) ---")
        wo, wn = worst_cells(co), worst_cells(cn)
        for pol in CAPS:
            ro, ko = wo.get(pol, (float("nan"), ""))
            rn, kn = wn.get(pol, (float("nan"), ""))
            print(f"  {pol:20s} {ro:6.3f} -> {rn:6.3f}")
            print(f"    old at {ko[:80]}")
            print(f"    new at {kn[:80]}")

        # shared configs: (h,q0,p,r,sig,cf_level) x stream x price present in both
        shared = sorted(set(io) & set(inew))
        print(f"\n--- shared configs: {len(shared)} of old {len(io)} / new"
              f" {len(inew)} ---")

        # distribution of trigger-vs-fixed ratio over shared cells
        import statistics as st
        for pol in TRIG:
            ro = [io[k][1][pol]["mean_tokens"] /
                  min(io[k][1][f]["mean_tokens"] for f in FIXED)
                  for k in shared if pol in io[k][1]]
            rn = [inew[k][1][pol]["mean_tokens"] /
                  min(inew[k][1][f]["mean_tokens"] for f in FIXED)
                  for k in shared if pol in inew[k][1]]
            print(f"  {pol:20s} mean {st.mean(ro):5.3f} -> {st.mean(rn):5.3f}  "
                  f"median {st.median(ro):5.3f} -> {st.median(rn):5.3f}  "
                  f"max {max(ro):6.3f} -> {max(rn):6.3f}")

        # p=0, cf high: repeated-pay blocking (DS story)
        print("\n--- p=0 cells, C_fail HIGH level: trigger repeated pay"
              " (mean over cells) ---")
        for lvl in ("high", "low"):
            sel = [k for k in shared if k[2] == "0" and k[5] == lvl]
            if not sel:
                continue
            for pol in ("always_compile_evict", "ours_noinflate",
                        "ours_cap_realized"):
                def m(src, k, f):
                    v = src[k][1].get(pol, {}).get(f)
                    return float(v) if v is not None else float("nan")
                fo = st.mean([m(io, k, "mean_failed_attempts") for k in sel])
                fn = st.mean([m(inew, k, "mean_failed_attempts") for k in sel])
                to = st.mean([m(io, k, "mean_failed_tokens") for k in sel])
                tn = st.mean([m(inew, k, "mean_failed_tokens") for k in sel])
                print(f"  cf={lvl:4s} {pol:22s} failed_attempts {fo:9.1f} ->"
                      f" {fn:9.1f}   failed_tokens {to:12.0f} -> {tn:12.0f}"
                      f"   (n={len(sel)})")

        # cf-axis effect within the new run (high vs low at same regime)
        print("\n--- new run: cf high vs low, trigger ratio to best fixed"
              " (p<1 cells) ---")
        for pol in TRIG + ("always_compile_evict",):
            hi, lo = [], []
            for k in shared:
                if float(k[2]) >= 1.0 or k[5] == "low":
                    continue
                if (k[0], k[1], k[2], k[3], k[4], "high", k[6], k[7]) in inew:
                    kh = (k[0], k[1], k[2], k[3], k[4], "high", k[6], k[7])
                    for store, kk in ((hi, kh), (lo, k)):
                        t = inew[kk][1]
                        store.append(t[pol]["mean_tokens"] /
                                     min(t[f]["mean_tokens"] for f in FIXED))
            if hi:
                import statistics as st
                print(f"  {pol:22s} low {st.mean(lo):6.3f} -> high"
                      f" {st.mean(hi):6.3f}  (n={len(hi)})")

        # best/naive flags
        bo = sum(1 for k in shared if io[k][1]["_beats_naive_5pct"])
        bn = sum(1 for k in shared if inew[k][1]["_beats_naive_5pct"])
        print(f"\n--- beats-naive-by-5% cells (shared): {bo} -> {bn} of"
              f" {len(shared)} ---")
        summary[model] = {"worst_old": {p: wo[p][0] for p in CAPS},
                          "worst_new": {p: wn[p][0] for p in CAPS},
                          "shared": len(shared)}
    print("\nsummary:", json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
