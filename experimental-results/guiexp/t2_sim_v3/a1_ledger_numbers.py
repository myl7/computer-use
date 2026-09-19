#!/usr/bin/env python3
"""W7b: recompute the refill-draft assertion-ledger numbers from the a1 runs.

Prints every ledger quantity in draft section 7 order, recomputed from
E3_full_v3_a1.json, E4_full_v3_a1.json, E4_full_v3_a1_e4ds.json (and the
narrow a1 companions for the buy-formula contrast sentences), so the draft
can be rewritten number by number.  E11 quantities come from the merged
E11_env_fragility_a1.json when present.
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(name):
    return json.loads((HERE / name).read_text())["cells"]


def rel(cells, ck, row):
    return cells[ck][row]["rel_to_ours"]


def s7x1_e3():
    c = load("E3_full_v3_a1.json")
    print("== 7.1 E3 (a1) ==")
    order = ["always_reactive", "always_compile", "on_second",
             "success_count", "toolpro_port", "breakeven", "oracle",
             "offline_opt"]
    for cs in ("android_glm", "android_ds"):
        for pat in ("poisson", "zipf", "bursty"):
            k = f"{pat}/{cs}"
            print(f"  {k}: " + ", ".join(
                f"{r} {rel(c, k, r):.7f}" for r in order))
    # caption claim: ours vs best fixed rule
    for pat in ("poisson", "zipf", "bursty"):
        for cs in ("android_glm", "android_ds"):
            k = f"{pat}/{cs}"
            bestr = min(rel(c, k, "always_reactive"),
                        rel(c, k, "always_compile"))
            print(f"  ours/bestfixed {k}: {1.0 / bestr:.3f}")
    print(f"  breakeven == breakeven_cap all 12: "
          f"{all(c[k]['breakeven']['mean_tokens'] == c[k]['breakeven_cap']['mean_tokens'] for k in c)}")
    # openapps control sentence
    rng = {}
    for row in ("always_reactive", "offline_opt", "on_second",
                "success_count", "breakeven"):
        vals = [rel(c, f"{p}/{s}", row) for p in ("poisson", "zipf", "bursty")
                for s in ("openapps_glm", "openapps_ds")]
        rng[row] = (min(vals), max(vals))
        print(f"  openapps {row}: {min(vals):.7f} to {max(vals):.7f}")
    # narrow contrast sentence
    n = load("E3_narrow_v3_a1.json")
    for pat in ("poisson", "zipf", "bursty"):
        k = f"{pat}/android_ds"
        f_ = c[k]["ours"]
        n_ = n[k]["ours"]
        print(f"  full-vs-narrow DS {k}: compiles {f_['mean_n_compiles']:.2f}"
              f" vs {n_['mean_n_compiles']:.2f}, tokens "
              f"{f_['mean_tokens'] / n_['mean_tokens'] - 1:+.4f}")


def s7x2_e4():
    c = load("E4_full_v3_a1.json")
    print("\n== 7.2 E4 GLM (a1) ==")
    order = ["always_reactive", "always_compile", "on_second",
             "success_count", "toolpro_port", "breakeven", "oracle",
             "offline_opt"]
    for s in ("sepsis", "bpi2019", "wiki_A", "wiki_B"):
        for p in ("native", "5M"):
            k = f"{s}/price={p}"
            print(f"  {k}: " + ", ".join(
                f"{r} {rel(c, k, r):.4f}" for r in order))
    print("  success_count bpi all prices: " + ", ".join(
        f"{p} {rel(c, f'bpi2019/price={p}', 'success_count'):.4f}"
        for p in ("native", "autorpa_233k", "1M", "5M")))
    k = "bpi2019/price=native"
    print(f"  bpi native oracle compiles {c[k]['oracle']['mean_n_compiles']}"
          f" ours compiles {c[k]['ours']['mean_n_compiles']}")
    print(f"  breakeven native rel: " + ", ".join(
        f"{s} {rel(c, f'{s}/price=native', 'breakeven'):.4f}"
        for s in ("sepsis", "bpi2019", "wiki_A", "wiki_B")))
    print(f"  breakeven 5M wiki_A/bpi: "
          f"{rel(c, 'wiki_A/price=5M', 'breakeven'):.4f} "
          f"{rel(c, 'bpi2019/price=5M', 'breakeven'):.4f}")
    oo = [rel(c, f"{s}/price={p}", "offline_opt")
          for s in ("sepsis", "bpi2019", "wiki_A", "wiki_B")
          for p in ("native", "5M")]
    print(f"  offline_opt range {min(oo):.4f} to {max(oo):.4f}")
    # reactive closeness on singleton tails
    for s in ("sepsis", "wiki_B"):
        print(f"  ours/reactive {s} native: "
              f"{1.0 / rel(c, f'{s}/price=native', 'always_reactive'):.3f}")
    print(f"  oracle 5M bpi/wiki_B: "
          f"{rel(c, 'bpi2019/price=5M', 'oracle'):.4f} "
          f"{rel(c, 'wiki_B/price=5M', 'oracle'):.4f}")

    d = load("E4_full_v3_a1_e4ds.json")
    print("\n== 7.2 E4 DS (a1) ==")
    for k in d:
        print(f"  {k}: " + ", ".join(
            f"{r} {rel(d, k, r):.4f}" for r in
            ("always_reactive", "success_count", "on_second")))
    print("  ours/reactive DS: " + ", ".join(
        f"{k.split('/')[0]} "
        f"{1.0 / rel(d, k, 'always_reactive'):.3f}" for k in d))
    sc = [rel(d, k, "success_count") for k in d]
    os_ = [rel(d, k, "on_second") for k in d]
    print(f"  count rules DS range {min(os_ + sc):.4f} to "
          f"{max(os_ + sc):.4f}")
    n = load("E4_narrow_v3_a1_e4ds.json")
    for k in ("sepsis/price=native", "bpi2019/price=native"):
        f_, n_ = d[k]["ours"], n[k]["ours"]
        print(f"  full-vs-narrow DS {k}: tokens ratio "
              f"{f_['mean_tokens'] / n_['mean_tokens']:.4f} "
              f"({f_['mean_tokens'] / n_['mean_tokens'] - 1:+.3%}), compiles "
              f"{f_['mean_n_compiles']:.1f} vs {n_['mean_n_compiles']:.1f}")


def s7x3_e11(stem="E11_env_fragility_a1"):
    p = HERE / f"{stem}.json"
    if not p.exists():
        print(f"\n== 7.3 E11: {stem}.json MISSING, skipping ==")
        return
    d = json.loads(p.read_text())["cells"]
    print(f"\n== 7.3 E11 ({stem}) ==")

    def cell_ratio(v, row):
        best = min(v["always_reactive"]["mean_tokens"],
                   v["always_compile_evict"]["mean_tokens"])
        return v[row]["mean_tokens"] / best

    def key_of(k):
        return k.split("|", 1)[1].rsplit("/", 1)[0], k.rsplit("/", 1)[1]

    per = {}
    for k, v in d.items():
        if k.startswith("_"):
            continue
        cs = k.split("|", 1)[0]
        cfg, spprice = key_of(k)
        per.setdefault(cs, []).append((cfg, spprice, k, v))
    for cs, cells in per.items():
        print(f"  {cs}: {len(cells)} cells")
        for row in ("always_reactive", "always_compile_evict", "breakeven",
                    "ours_noinflate", "ours_spend_cap", "ours_cap_epoch",
                    "ours_cap_realized", "ours_pi_prior"):
            vals = sorted(((cell_ratio(v, row), cfg, sp) for
                           cfg, sp, k, v in cells), reverse=True)
            ratios = [x[0] for x in vals]
            ratios_sorted = sorted(ratios)
            med = (ratios_sorted[len(ratios_sorted) // 2]
                   if len(ratios_sorted) % 2 else
                   (ratios_sorted[len(ratios_sorted) // 2 - 1]
                    + ratios_sorted[len(ratios_sorted) // 2]) / 2)
            print(f"    {row}: worst {vals[0][0]:.4f} at {vals[0][1]} "
                  f"{vals[0][2]}, median {med:.4f}, mean "
                  f"{sum(ratios) / len(ratios):.4f}")
        # p=0 cells at high cf
        p0hi = [(cfg, sp, k, v) for cfg, sp, k, v in cells
                if "/p=0/" in cfg and ("cf=10.5" in cfg or "cf=10" in cfg)]
        if p0hi:
            navg = sum(v["always_compile_evict"]["mean_failed_attempts"]
                       for _, _, _, v in p0hi) / len(p0hi)
            ntok = sum(v["always_compile_evict"]["mean_failed_tokens"]
                       for _, _, _, v in p0hi) / len(p0hi)
            o = p0hi[0][3]["ours_noinflate"]
            print(f"    p=0 high-cf ({len(p0hi)} cells): naive attempts/cell "
                  f"{navg:,.1f}, naive failed tokens/cell {ntok/1e9:.1f}G, "
                  f"ours_noinflate attempts "
                  f"{sum(v['ours_noinflate']['mean_failed_attempts'] for _,_,_,v in p0hi)/len(p0hi):.1f}")
            worst = max(p0hi, key=lambda t: t[3]
                        ["always_compile_evict"]["mean_failed_attempts"])
            print(f"    worst naive cell {worst[0]} {worst[1]}: "
                  f"{worst[3]['always_compile_evict']['mean_failed_attempts']:,.0f} attempts, ours "
                  f"{worst[3]['ours_noinflate']['mean_failed_attempts']:,.0f}")
        p0lo = [(cfg, sp, k, v) for cfg, sp, k, v in cells
                if "/p=0/" in cfg and "cf=1" in cfg]
        if p0lo:
            ntok = sum(v["always_compile_evict"]["mean_failed_tokens"]
                       for _, _, _, v in p0lo) / len(p0lo)
            print(f"    p=0 cf=1 ({len(p0lo)} cells): naive failed tokens/cell "
                  f"{ntok/1e9:.1f}G")
        # paired p<1 movement
        for row in ("ours_cap_realized", "ours_noinflate"):
            for cf in ("cf=1", "cf=10.5", "cf=10.509"):
                sel = [(cell_ratio(v, row), cfg) for cfg, sp, k, v in cells
                       if "/p=0." in cfg or "/p=0.3" in cfg or "/p=0.5" in cfg]
            lo, hi = [], []
            for cfg, sp, k, v in cells:
                if "/p=1" in cfg:
                    continue
                if "cf=10" in cfg:
                    hi.append(cell_ratio(v, row))
                else:
                    lo.append(cell_ratio(v, row))
            if lo and len(lo) == len(hi):
                print(f"    {row} paired p<1 cells ({len(lo)}): mean lo "
                      f"{sum(lo)/len(lo):.4f} hi {sum(hi)/len(hi):.4f}")


if __name__ == "__main__":
    s7x1_e3()
    s7x2_e4()
    s7x3_e11()
