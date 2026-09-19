"""Cache-adjusted token accounting for the guiexp headline table.

Ruling being implemented: cached input tokens must NOT be counted as regular
tokens; they are discounted by the official cache-read/input price ratio r =
p_c / p_in, per model. Trajectories record per-call usage
{prompt_tokens, completion_tokens, cost_usd}; cost_usd is OpenRouter's real
bill (already cache-priced), so cached vs fresh can be backed out exactly
from the linear billing equation

    cost = fresh * p_in + cached * p_c + completion * p_o
    prompt_tokens = fresh + cached
=>  cached = (prompt_tokens * p_in - cost + completion_tokens * p_o)
            / (p_in - p_c)

Three accountings per run:
  (a) RAW        : prompt_tokens + completion_tokens              (old paper unit)
  (b) CACHE-DISC : fresh + r*cached + completion_tokens           (primary new)
  (c) DOLLAR-EQ  : cost_usd / p_in_ref  =  fresh + r*cached
                                     + (p_o/p_in)*completion      (secondary)

Prices (OpenRouter, verified live 2026-09-05 against
https://openrouter.ai/api/v1/models, see report):
  z-ai/glm-5.3-flash              p_in 7.5e-8  p_c 1.5e-8  p_o 2.5e-7    r=0.20
  deepseek/...-vision-exp off-peak p_in 2.2e-8  p_c 7e-9    p_o 6.6e-7   r=0.31818
                          peak     p_in 4.4e-8  p_c 1.4e-8  p_o 1.32e-6  r=0.31818
  anthropic/claude-sonnet-5        p_in 2e-6    p_c 2e-7    p_o 1e-5     r=0.10
                                   (cache_write 2.5e-6 -> write premium
                                    not modeled; biases cached down)
  z-ai/glm-5v-turbo                p_in 1.2e-6  p_c 2.4e-7  p_o 4e-6     r=0.20

Outputs (this directory): calls.csv, runs.csv, summary.json, and a printed
report. READ-ONLY on t11_grid / t13_compilepath / t15_strong.

Usage: ../../.venv-gui/bin/python solve_cache.py   (from this directory)
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
GUIEXP = HERE.parent                      # experimental-results/guiexp
REPO = (GUIEXP / ".." / "..").resolve()   # repo root

T11 = GUIEXP / "t11_grid"
T13 = GUIEXP / "t13_compilepath"
T15 = GUIEXP / "t15_strong"

MODELS = {
    "z-ai/glm-5.3-flash": "z-ai_glm-5.3-flash",
    "deepseek/deepseek-v4-flash-vision-exp": "deepseek_deepseek-v4-flash-vision-exp",
    "anthropic/claude-sonnet-5": "anthropic_claude-sonnet-5",
    "z-ai/glm-5v-turbo": "z-ai_glm-5v-turbo",
}

TIERS = {
    # model -> {tier: (p_in, p_c, p_o)}
    "z-ai/glm-5.3-flash": {"base": (7.5e-8, 1.5e-8, 2.5e-7)},
    "deepseek/deepseek-v4-flash-vision-exp": {
        "offpeak": (2.2e-8, 7e-9, 6.6e-7),
        "peak": (4.4e-8, 1.4e-8, 1.32e-6),
    },
    "anthropic/claude-sonnet-5": {"base": (2e-6, 2e-7, 1e-5)},
    "z-ai/glm-5v-turbo": {"base": (1.2e-6, 2.4e-7, 4e-6)},
}
# dollar-equivalent reference input price (N* is invariant to this rescale)
P_IN_REF = {
    "z-ai/glm-5.3-flash": 7.5e-8,
    "deepseek/deepseek-v4-flash-vision-exp": 2.2e-8,  # off-peak reference
    "anthropic/claude-sonnet-5": 2e-6,
    "z-ai/glm-5v-turbo": 1.2e-6,
}


def solve_cached(p_in: float, p_c: float, p_o: float, P: int, C: int, K: float):
    """Back out cached tokens; clip to [0, P]; return (cached, raw, violation)."""
    raw = (P * p_in - K + C * p_o) / (p_in - p_c)
    viol = None
    if raw < -0.5:
        viol = "cached<0"
    elif raw > P + 0.5:
        viol = "cached>prompt"
    cached = min(max(raw, 0.0), float(P))
    return cached, raw, viol


def tier_feasibility(model: str, P: int, C: int, K: float):
    """For tiered models: which price tiers can reproduce the bill at all."""
    out = {}
    for tier, (p_in, p_c, p_o) in TIERS[model].items():
        _, _, viol = solve_cached(p_in, p_c, p_o, P, C, K)
        out[tier] = viol is None
    return out


def load_trajectory(path: Path):
    steps, final = [], None
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("record_type") == "final":
                final = rec
            else:
                steps.append(rec)
    return steps, final


def call_rows(dataset: str, model: str, run: str, calls: list[dict], call_labels=None):
    """Solve every call of one run; per-call rows in the three accountings."""
    rows = []
    for i, u in enumerate(calls):
        P = int(u.get("prompt_tokens") or 0)
        C = int(u.get("completion_tokens") or 0)
        K = float(u.get("cost_usd") or 0.0)
        feas = tier_feasibility(model, P, C, K)
        tiers = TIERS[model]
        # Tier used for the reported split. DS vision-exp is time-tiered, but
        # every t11/t13 DS artifact's mtime falls on Sunday 2026-09-06 UTC
        # (02:30-05:20) -> off-peak window (weekend override); peak prices are
        # kept only as a recorded bound (r and p_o/p_in are tier-invariant).
        chosen = "offpeak" if "offpeak" in tiers else next(iter(tiers))
        p_in, p_c, p_o = tiers[chosen]
        cached, raw, viol = solve_cached(p_in, p_c, p_o, P, C, K)
        r = p_c / p_in
        fresh = P - cached
        rows.append({
            "dataset": dataset, "model": model, "run": run,
            "call": call_labels[i] if call_labels else i + 1,
            "prompt_tokens": P, "completion_tokens": C, "cost_usd": K,
            "tier_feasible": "+".join(t for t, ok in feas.items() if ok) or "none",
            "tier_used": chosen,
            "cached_raw": round(raw, 2), "cached": round(cached, 2),
            "fresh": round(fresh, 2),
            "viol": viol or "",
            "raw_tok": P + C,
            "disc_tok": fresh + r * cached + C,
            "doll_tok": K / P_IN_REF[model],
        })
    return rows


def run_totals(rows: list[dict], model: str):
    r = next(iter(TIERS[model].values()))
    r_cache = r[1] / r[0]
    tot_P = sum(x["prompt_tokens"] or 0 for x in rows)
    tot_C = sum(x["completion_tokens"] or 0 for x in rows)
    tot_cached = sum(x["cached"] for x in rows)
    tot_K = sum(x["cost_usd"] for x in rows)
    n_viol = sum(1 for x in rows if x["viol"])
    return {
        "n_calls": len(rows),
        "prompt_tokens": tot_P,
        "completion_tokens": tot_C,
        "cached": tot_cached,
        "cache_share_prompt": (tot_cached / tot_P) if tot_P else None,
        "violations": n_viol,
        "raw_tok": sum(x["raw_tok"] for x in rows),
        "disc_tok": sum(x["disc_tok"] for x in rows),
        "doll_tok": sum(x["doll_tok"] for x in rows),
        "cost_usd": tot_K,
        "r": r_cache,
    }


def main() -> None:
    all_calls: list[dict] = []
    runs: list[dict] = []

    # ---------------------------------------------------------------- t11_grid
    for model, mdir in MODELS.items():
        if model.startswith("anthropic") or "5v-turbo" in model:
            continue  # t11 has only the two flash models
        base = T11 / mdir
        for run_dir in sorted(base.iterdir()):
            if not run_dir.is_dir():
                continue
            steps, final = load_trajectory(run_dir / "trajectory.jsonl")
            calls = [s["usage"] for s in steps if s.get("usage")]
            rows = call_rows("t11", model, run_dir.name, calls)
            all_calls.extend(rows)
            tot = run_totals(rows, model)
            cond, layout, seed = run_dir.name.split("__")
            seed = int(seed[1:])
            tot.update(model=model, dataset="t11", condition=cond, layout=layout, seed=seed,
                       final_total_tokens=final.get("total_tokens"),
                       final_total_cost_usd=final.get("total_cost_usd"),
                       steps=final.get("steps"))
            runs.append(tot)

    # ---------------------------------------------------------- t13_compilepath
    for model in ("z-ai/glm-5.3-flash", "deepseek/deepseek-v4-flash-vision-exp"):
        mdir = MODELS[model]
        for attempt in ("attempt1", "attempt2", "attempt3"):
            cj = T13 / mdir / attempt / "compile.json"
            d = json.loads(cj.read_text())
            rows = call_rows("t13_compile", model, attempt, [d["usage"]],
                             call_labels=["compile"])
            all_calls.extend(rows)
            tot = run_totals(rows, model)
            tot.update(model=model, dataset="t13_compile", condition="compile", layout="wizard",
                       seed=None, steps=1)
            runs.append(tot)
        # deploy30: per use only aggregate tokens+cost are logged (one
        # extraction call, cached not separable). Accounting per use:
        #   (a) tokens; (c) cost/p_in_ref; (b) = (a) under the documented
        #   cached=0 assumption (bounded in the report).
        dj = json.loads((T13 / mdir / "deploy30" / "deploy.json").read_text())
        uses = dj["uses"]
        for i, u in enumerate(uses):
            P_C = u["tokens"]
            K = u["cost_usd"]
            # feasibility of the aggregate under each tier with cached=0
            feas = {}
            for tier, (p_in, p_c, p_o) in TIERS[model].items():
                lo = P_C * p_in            # all prompt, fresh
                hi = P_C * p_o             # all completion
                feas[tier] = lo - 1e-12 <= K <= hi + 1e-12
            chosen = "offpeak" if "offpeak" in TIERS[model] else next(iter(TIERS[model]))
            all_calls.append({
                "dataset": "t13_deploy", "model": model, "run": f"use{i+1}",
                "call": f"use{i+1}",
                "prompt_tokens": None, "completion_tokens": None,
                "cost_usd": K, "tier_feasible": "+".join(t for t, v in feas.items() if v) or "none",
                "tier_used": chosen, "cached_raw": 0.0, "cached": 0.0, "fresh": 0.0,
                "viol": "" if chosen in feas and feas[chosen] else "agg-cost>max(all-completion)",
                "raw_tok": P_C, "disc_tok": float(P_C),  # cached=0 assumption
                "doll_tok": K / P_IN_REF[model],
            })
        d_rows = [c for c in all_calls if c["dataset"] == "t13_deploy" and c["model"] == model]
        tot = run_totals(d_rows, model)
        tot.update(model=model, dataset="t13_deploy", condition="deploy", layout="wizard",
                   seed=None, steps=len(uses), n_calls=len(uses))
        tot["q"] = 1 - dj["success_rate"]
        tot["success_count"] = dj["success_count"]
        runs.append(tot)

    # ----------------------------------------------------------------- t15_strong
    for run_dir in sorted(T15.iterdir()):
        name = run_dir.name  # <model-dir>__<cond>__<layout>__s0
        mdir, cond, layout, seed = name.split("__")
        seed = int(seed[1:])
        traj = run_dir / "trajectory.jsonl"
        if not traj.exists():
            continue  # the two z-ai_glm-5.3 (non-flash) dirs never produced runs
        model = next(m for m, d in MODELS.items() if d == mdir)
        steps, final = load_trajectory(traj)
        calls = [s["usage"] for s in steps if s.get("usage")]
        rows = call_rows("t15", model, name, calls)
        all_calls.extend(rows)
        tot = run_totals(rows, model)
        tot.update(model=model, dataset="t15", condition=cond, layout=layout, seed=seed,
                   final_total_tokens=final.get("total_tokens"),
                   final_total_cost_usd=final.get("total_cost_usd"),
                   steps=final.get("steps"))
        runs.append(tot)

    # -------------------------------------------------------------- aggregation
    def mean(xs):
        xs = list(xs)
        return sum(xs) / len(xs) if xs else None

    def series(model, dataset, condition, layout, field, seeds=(0, 1, 2)):
        vals = [r[field] for r in runs
                if r["model"] == model and r["dataset"] == dataset
                and r["condition"] == condition and r["layout"] == layout
                and (r["seed"] in seeds if seeds is not None else True)]
        return mean(vals)

    def pooled(model, dataset, condition, layout, field, seeds=(0, 1, 2)):
        vals = [r[field] for r in runs
                if r["model"] == model and r["dataset"] == dataset
                and r["condition"] == condition and r["layout"] == layout
                and r["seed"] in seeds]
        return sum(vals)

    summary = {"headline": {}, "validation": {}, "f3": {}, "t15": {},
               "cache_share": {}, "deploy": {}, "clip_counts": {}}

    for model in ("z-ai/glm-5.3-flash", "deepseek/deepseek-v4-flash-vision-exp"):
        h = {}
        q = [r["q"] for r in runs if r["model"] == model and r["dataset"] == "t13_deploy"][0]
        for acc, field in (("raw", "raw_tok"), ("disc", "disc_tok"), ("doll", "doll_tok")):
            c = series(model, "t11", "discover", "wizard", field)
            L = series(model, "t11", "told", "wizard", field)
            rho = {}
            for layout in ("wizard", "single_page", "sectioned"):
                cp = pooled(model, "t11", "discover", layout, field)
                lp = pooled(model, "t11", "told", layout, field)
                rho[layout] = 1 - lp / cp if cp else None
            comp = series(model, "t13_compile", "compile", "wizard", field, seeds=None)
            dep_use_rows = [c for c in all_calls
                            if c["model"] == model and c["dataset"] == "t13_deploy"]
            d = mean([x[field] for x in dep_use_rows]) if dep_use_rows else None
            s = c - (d + q * c)
            h[acc] = {"c": c, "L": L, "rho_wizard": rho["wizard"],
                      "rho": rho, "C": comp, "d": d, "q": q, "s": s,
                      "N_star": comp / s if s else None,
                      "C_plus_c_over_s": (comp + c) / s if s else None}
        # dollar cross-check straight from cost_usd
        c_usd = series(model, "t11", "discover", "wizard", "cost_usd")
        comp_usd = series(model, "t13_compile", "compile", "wizard", "cost_usd", seeds=None)
        dep_usd = mean([r["cost_usd"] / r["n_calls"] for r in runs
                        if r["model"] == model and r["dataset"] == "t13_deploy"])
        q = [r["q"] for r in runs if r["model"] == model and r["dataset"] == "t13_deploy"][0]
        s_usd = c_usd - (dep_usd + q * c_usd)
        h["dollar_direct"] = {"c_usd": c_usd, "C_usd": comp_usd, "d_usd": dep_usd,
                              "s_usd": s_usd, "N_star_usd": comp_usd / s_usd,
                              "doll_N_star_matches": abs(comp_usd / s_usd - h["doll"]["N_star"]) < 1e-9}
        summary["headline"][model] = h

    # ------------------------------------------------------------- validation
    val = {"floor_runs": [], "violations": {}, "tier_resolution": {}}
    for r in runs:
        if r["dataset"] == "t11" and r["condition"] == "floor":
            val["floor_runs"].append({
                "model": r["model"], "run": f"{r['condition']}__{r['layout']}__s{r['seed']}",
                "prompt_tokens": r["prompt_tokens"],
                "cached_solved": round(r["cached"], 1),
                "cache_share": round(r["cache_share_prompt"], 4),
                "violations": r["violations"],
            })
    for key in ("t11", "t13_compile", "t13_deploy", "t15"):
        sel = [c for c in all_calls if c["dataset"] == key]
        val["violations"][key] = {
            "n_calls": len(sel),
            "cached_lt0": sum(1 for c in sel if c["viol"] == "cached<0"),
            "cached_gt_prompt": sum(1 for c in sel if c["viol"] == "cached>prompt"),
            "agg_overmax": sum(1 for c in sel if c["viol"].startswith("agg-cost")),
        }
    for model in MODELS:
        sel = [c for c in all_calls if c["model"] == model and c["prompt_tokens"]]
        from collections import Counter
        cnt = Counter(c["tier_feasible"] for c in sel)
        val["tier_resolution"][model] = dict(cnt)
    t11_by_cond = {}
    for r in runs:
        if r["dataset"] != "t11":
            continue
        key = (r["model"], r["condition"])
        e = t11_by_cond.setdefault(key, {"runs": 0, "calls": 0, "viol_calls": 0,
                                         "prompt_tokens": 0, "cached": 0})
        e["runs"] += 1
        e["calls"] += r["n_calls"]
        e["viol_calls"] += r["violations"]
        e["prompt_tokens"] += r["prompt_tokens"]
        e["cached"] += r["cached"]
    val["t11_by_condition"] = {
        f"{m}|{c}": {"runs": e["runs"], "calls": e["calls"],
                     "viol_calls": e["viol_calls"],
                     "cache_share": round(e["cached"] / e["prompt_tokens"], 4)}
        for (m, c), e in sorted(t11_by_cond.items())
    }
    summary["validation"] = val

    # ----------------------------------------------------- cache share by cond
    for model in ("z-ai/glm-5.3-flash", "deepseek/deepseek-v4-flash-vision-exp"):
        cs = {}
        for cond in ("discover", "told", "floor"):
            per_layout = {}
            for layout in ("wizard", "single_page", "sectioned"):
                P = pooled(model, "t11", cond, layout, "prompt_tokens")
                cached = pooled(model, "t11", cond, layout, "cached")
                per_layout[layout] = round(cached / P, 4) if P else None
            Pw = pooled(model, "t11", cond, "wizard", "prompt_tokens")
            cw = pooled(model, "t11", cond, "wizard", "cached")
            cs[cond] = {"wizard": round(cw / Pw, 4), "by_layout": per_layout}
        cs["compile"] = round(
            sum(c["cached"] for c in all_calls if c["dataset"] == "t13_compile" and c["model"] == model)
            / sum(c["prompt_tokens"] for c in all_calls if c["dataset"] == "t13_compile" and c["model"] == model), 4)
        summary["cache_share"][model] = cs

    # ---------------------------------------------------------------- F3
    # observation re-send amplification, raw and cache-discounted, per seed of
    # wizard discover; see nstar-smallness-analysis.md section 2.3.
    for model in ("z-ai/glm-5.3-flash", "deepseek/deepseek-v4-flash-vision-exp"):
        per_seed = []
        for seed in (0, 1, 2):
            sel = [c for c in all_calls if c["dataset"] == "t11" and c["model"] == model
                   and c["run"] == f"discover__wizard__s{seed}"]
            sel.sort(key=lambda c: c["call"])
            n = len(sel)
            Ps = [c["prompt_tokens"] for c in sel]
            base = Ps[0]
            sum_P = sum(Ps)
            sum_fresh = sum(c["fresh"] for c in sel)
            r = next(iter(TIERS[model].values()))
            r = r[1] / r[0]
            disc_input = sum(c["fresh"] + r * c["cached"] for c in sel)
            obs_once = Ps[-1] - base                      # total new observation content
            resend_raw = sum_P - n * base                 # old F3 numerator
            amp_raw = resend_raw / obs_once if obs_once else None
            # instruction prefix is paid once per call; it is cache-priced on
            # call k only when that call's solve shows a cache hit (f_k>=0.5);
            # on violation calls (f_k~0) the prefix bills fresh, otherwise the
            # observation residual silently absorbs the instruction cost.
            instr_units = sum((r if (c["cached"] / max(c["prompt_tokens"], 1)) >= 0.5 else 1.0) for c in sel)
            instr_disc = base * instr_units
            obs_paid_disc = disc_input - instr_disc
            amp_disc = obs_paid_disc / obs_once if obs_once else None
            per_seed.append({
                "seed": seed, "steps": n, "base_prompt": base,
                "obs_once_sum_delta": obs_once,
                "resend_raw": round(resend_raw, 1), "amp_raw": round(amp_raw, 3),
                "obs_paid_disc": round(obs_paid_disc, 1), "amp_disc": round(amp_disc, 3),
                "eff_input_mult_vs_fresh": round(disc_input / sum_fresh, 3) if sum_fresh else None,
            })
        summary["f3"][model] = {
            "per_seed": per_seed,
            "amp_raw_mean": mean(x["amp_raw"] for x in per_seed),
            "amp_disc_mean": mean(x["amp_disc"] for x in per_seed),
        }

    # ---------------------------------------------------------------- t15
    for r in runs:
        if r["dataset"] == "t15":
            summary["t15"][f"{r['model']}__{r['condition']}"] = {
                "steps": r["steps"], "raw_tok": r["raw_tok"],
                "disc_tok": round(r["disc_tok"], 1),
                "doll_tok": round(r["doll_tok"], 1),
                "cost_usd": r["cost_usd"],
                "cache_share_prompt": round(r["cache_share_prompt"], 4),
                "r": r["r"], "violations": r["violations"],
            }
    # t15 rho per model (single seed)
    for model in ("anthropic/claude-sonnet-5", "z-ai/glm-5v-turbo"):
        ent = summary["t15"]
        disc = [v["disc_tok"] for k, v in ent.items() if k.startswith(model.split("/")[-1]) or k.startswith(model)]
        summary["t15"][model + "__rho_disc"] = None  # filled below
        c_d = next((v["disc_tok"] for k, v in ent.items() if k == f"{model}__discover"), None)
        L_d = next((v["disc_tok"] for k, v in ent.items() if k == f"{model}__told"), None)
        c_r = next((v["raw_tok"] for k, v in ent.items() if k == f"{model}__discover"), None)
        L_r = next((v["raw_tok"] for k, v in ent.items() if k == f"{model}__told"), None)
        c_u = next((v["cost_usd"] for k, v in ent.items() if k == f"{model}__discover"), None)
        L_u = next((v["cost_usd"] for k, v in ent.items() if k == f"{model}__told"), None)
        summary["t15"][model] = {
            "rho_raw": round(1 - L_r / c_r, 4), "rho_disc": round(1 - L_d / c_d, 4),
            "rho_usd": round(1 - L_u / c_u, 6),
        }
        del summary["t15"][model + "__rho_disc"]

    # deploy detail
    for model in ("z-ai/glm-5.3-flash", "deepseek/deepseek-v4-flash-vision-exp"):
        sel = [c for c in all_calls if c["dataset"] == "t13_deploy" and c["model"] == model]
        summary["deploy"][model] = {
            "d_raw": mean(c["raw_tok"] for c in sel),
            "d_disc_assumed_nocache": mean(c["disc_tok"] for c in sel),
            "d_doll": mean(c["doll_tok"] for c in sel),
            "d_usd": mean(c["cost_usd"] for c in sel),
            "overmax_uses": sum(1 for c in sel if c["viol"].startswith("agg-cost")),
            "tier_counts": dict(Counter(c["tier_feasible"] for c in sel)),
        }

    # ------------------------------------------------------------- write out
    with open(HERE / "calls.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(all_calls[0].keys()))
        w.writeheader()
        w.writerows(all_calls)
    run_fields = sorted({k for r in runs for k in r})
    with open(HERE / "runs.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=run_fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(runs)
    with open(HERE / "summary.json", "w") as fh:
        json.dump(summary, fh, indent=1, default=str)

    # ------------------------------------------------------------- printout
    print("== VALIDATION: floor runs (1 call; naive expectation cached~0) ==")
    for f in val["floor_runs"]:
        print(f"  {f['model'].split('/')[-1]:<28} {f['run']:<24} "
              f"P={f['prompt_tokens']:>6} cached={f['cached_solved']:>8} share={f['cache_share']:.3f} viol={f['violations']}")
    print("\n== Violations (clipped) ==")
    for k, v in val["violations"].items():
        print(f"  {k:<12} calls={v['n_calls']:>4} cached<0={v['cached_lt0']:>3} "
              f"cached>P={v['cached_gt_prompt']:>3} agg-overmax={v['agg_overmax']:>3}")
    print("\n== Tier resolution (feasible tiers per call) ==")
    for m, cnt in val["tier_resolution"].items():
        print(f"  {m:<42} {cnt}")
    print("\n== HEADLINE (per accounting) ==")
    for model, h in summary["headline"].items():
        print(f"  {model}")
        for acc in ("raw", "disc", "doll"):
            e = h[acc]
            print(f"    {acc:<5} c={e['c']:>10,.0f} L={e['L']:>9,.0f} rho={e['rho_wizard']:.3f} "
                  f"C={e['C']:>8,.0f} d={e['d']:>8,.1f} s={e['s']:>10,.0f} "
                  f"N*={e['N_star']:.4f} (C+c)/s={e['C_plus_c_over_s']:.3f}")
        dd = h["dollar_direct"]
        print(f"    dollar-direct N*={dd['N_star_usd']:.4f} matches doll-N*: {dd['doll_N_star_matches']}")
    print("\n== CACHE SHARE of prompt tokens (wizard; discover/told/floor/compile) ==")
    for model, cs in summary["cache_share"].items():
        print(f"  {model}: " + " ".join(f"{k}={v['wizard'] if isinstance(v, dict) else v}" for k, v in cs.items()))
    print("\n== F3 observation re-send amplification ==")
    for model, f3 in summary["f3"].items():
        print(f"  {model}: raw={f3['amp_raw_mean']:.2f}x  cache-disc={f3['amp_disc_mean']:.2f}x")
        for x in f3["per_seed"]:
            print(f"    s{x['seed']}: n={x['steps']} amp_raw={x['amp_raw']} amp_disc={x['amp_disc']} "
                  f"eff_mult_vs_fresh={x['eff_input_mult_vs_fresh']}")
    print("\n== t15_strong (4 runs) ==")
    for k, v in summary["t15"].items():
        print(f"  {k}: {v}")
    print("\n== deploy ==")
    for model, d in summary["deploy"].items():
        print(f"  {model}: {d}")
    print("\nwrote calls.csv, runs.csv, summary.json")


if __name__ == "__main__":
    from collections import Counter
    main()
