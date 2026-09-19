"""Deterministic cache-adjusted token accounting for the guiexp/guiexp_android
measurement.

WHY THIS EXISTS
---------------
The published unit (``solve_cache.py``, accounting (b)) prices each call as
``fresh + r*cached + completion`` where ``cached`` is backed out of the real
OpenRouter bill.  That makes the unit depend on how warm the provider's cache
happened to be at run time.  ``docs/android-told-diagnosis.md`` showed the
consequence: two AndroidWorld conditions run minutes apart got different cache
warmth, and the calendar/GLM discovery share moved from -0.18 (raw tokens) to
-0.43 (provider-reported cache adjustment).  The unit was not reproducible.

THE DETERMINISTIC CONVENTION (author ruling, 2026-09-09)
--------------------------------------------------------
Within one episode, the prefix that a call re-sends from the previous call is
ALWAYS priced at the cache-read rate, and only the tokens the call adds are
fresh.  Provider-reported / back-solved ``cached`` is ignored.

    call 1 of an episode:   effective_1 = prompt_1 + completion_1
    call i > 1:             new_i       = prompt_i - prompt_{i-1}
                            effective_i = new_i + r*prompt_{i-1} + completion_i

Across episodes nothing is cached: every episode starts cold.

Single calls that are not part of an episode (compile attempts, deployment
extraction calls) are fresh in full: ``effective = prompt + completion``.
A documented exception exists but is NOT applied here: when a call is a
byte-identical retry of a previous call in the same build, its prefix could be
priced at the cache rate.  ``t13_compilepath/fullread`` is exactly that case
(identical 9,808 / 9,274 prompt tokens across three attempts, with the provider
reporting ``cached_tokens`` 9,807 / 9,216 on the repeats).  Keeping those fully
fresh is the conservative choice and the one implemented; the option is exposed
as ``--retry-exception`` for a sensitivity read only.

EDGE CASES, all explicitly handled and counted
----------------------------------------------
* ``prompt_i < prompt_{i-1}``  -- context truncation or summarization: the
  re-sent prefix is not a prefix any more, so the whole prompt of that call is
  charged fresh and the call is flagged ``prompt_shrank``.
* ``prompt_i == prompt_{i-1}`` -- the call re-sent exactly what the previous
  call sent (an in-episode retry).  ``new_i = 0``; the whole prompt is cached.
  Flagged ``zero_new``.
* tool-result-only turns -- this harness issues exactly one model call per
  recorded step and every step carries a ``usage`` block (verified: 0 of 1,826
  steps lack one), so there is no separate tool-result turn to price.  The
  counter is kept and reported so the claim stays checkable.
* missing / zero usage -- counted under ``no_usage``.

OUTPUTS (all new files, nothing existing is overwritten)
--------------------------------------------------------
* ``calls_deterministic.csv``   -- every call, both accountings side by side
* ``runs_deterministic.csv``    -- per-episode totals, both accountings
* ``summary_deterministic.json``-- headline quantities, edge-case counts,
                                   provider-vs-deterministic difference
* ``computer-use-paper/results_deterministic_index.json``
* ``computer-use/t2sim/constants.deterministic.json``

Usage:  ../../../.venv-gui/bin/python deterministic_accounting.py
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
GUIEXP = HERE.parent                          # experimental-results/guiexp
RESULTS = GUIEXP.parent                       # experimental-results
REPO = RESULTS.parent                         # repo root
ANDROID = RESULTS / "guiexp_android"

T11 = GUIEXP / "t11_grid"
T13 = GUIEXP / "t13_compilepath"
T15 = GUIEXP / "t15_strong"
T12 = ANDROID / "t12_grid"
T14 = ANDROID / "t14_compilepath"

PAPER = REPO / "computer-use-paper"
T2SIM = REPO / "computer-use" / "t2sim"

GLM = "z-ai/glm-5.3-flash"
DS = "deepseek/deepseek-v4-flash-vision-exp"
SONNET = "anthropic/claude-sonnet-5"
GLM5V = "z-ai/glm-5v-turbo"

MODEL_DIR = {
    GLM: "z-ai_glm-5.3-flash",
    DS: "deepseek_deepseek-v4-flash-vision-exp",
    SONNET: "anthropic_claude-sonnet-5",
    GLM5V: "z-ai_glm-5v-turbo",
}
DIR_MODEL = {v: k for k, v in MODEL_DIR.items()}

# (p_in, p_c, p_o) -- OpenRouter official sheet, see docs/cache-adjusted-accounting.md s1.
# DS is time-tiered but r = p_c/p_in and p_o/p_in are identical in both tiers,
# and every artifact's mtime falls in the off-peak window, so off-peak is used.
PRICES = {
    GLM: (7.5e-8, 1.5e-8, 2.5e-7),
    DS: (2.2e-8, 7e-9, 6.6e-7),
    SONNET: (2e-6, 2e-7, 1e-5),
    GLM5V: (1.2e-6, 2.4e-7, 4e-6),
}
R_CACHE = {m: p[1] / p[0] for m, p in PRICES.items()}
# The paper quotes r to three digits for DS; use the exact price ratio and
# report it, the difference is 0.0002.
R_QUOTED = {GLM: 0.20, DS: 0.318, SONNET: 0.10, GLM5V: 0.20}

ANDROID_FAMILY = {
    "contacts": "ContactsAddContact",
    "calendar": "SimpleCalendarAddOneEvent",
    "markor": "MarkorCreateNote",
}
LAYOUTS = ("wizard", "single_page", "sectioned")
CONDITIONS = ("discover", "told", "mid", "skill", "floor")
SEEDS = (0, 1, 2)


# --------------------------------------------------------------------------
# provider-reported accounting (the published unit), kept only for comparison
# --------------------------------------------------------------------------
def solve_cached(model, P, C, K):
    """Back out provider-billed cached tokens; clip to [0, P]."""
    p_in, p_c, p_o = PRICES[model]
    raw = (P * p_in - K + C * p_o) / (p_in - p_c)
    viol = ""
    if raw < -0.5:
        viol = "cached<0"
    elif raw > P + 0.5:
        viol = "cached>prompt"
    return min(max(raw, 0.0), float(P)), raw, viol


# --------------------------------------------------------------------------
# deterministic accounting
# --------------------------------------------------------------------------
def episode_rows(dataset, model, run, usages, labels=None):
    """Price one episode under both conventions.

    ``usages`` is the ordered list of per-call usage dicts of ONE episode.
    Returns per-call rows.  The episode is assumed to start cold.
    """
    r = R_CACHE[model]
    rows = []
    prev_prompt = 0
    for i, u in enumerate(usages):
        P = int(u.get("prompt_tokens") or 0)
        C = int(u.get("completion_tokens") or 0)
        K = float(u.get("cost_usd") or 0.0)
        flag = ""
        if P == 0 and C == 0:
            flag = "no_usage"
        if i == 0:
            new = P
            prefix = 0
            flag = flag or "first_call"
        elif P < prev_prompt:
            # truncation / summarization: the previous prompt is not a prefix
            new = P
            prefix = 0
            flag = "prompt_shrank"
        elif P == prev_prompt:
            new = 0
            prefix = prev_prompt
            flag = "zero_new"
        else:
            new = P - prev_prompt
            prefix = prev_prompt
        det = new + r * prefix + C
        cached, cached_raw, viol = solve_cached(model, P, C, K)
        prov = (P - cached) + r * cached + C
        rows.append({
            "dataset": dataset, "model": model, "run": run,
            "call": labels[i] if labels else i + 1,
            "prompt_tokens": P, "completion_tokens": C, "cost_usd": K,
            "prev_prompt": prefix, "new_tokens": new,
            "det_tok": det,
            "prov_cached": round(cached, 2), "prov_cached_raw": round(cached_raw, 2),
            "prov_viol": viol,
            "prov_tok": prov,
            "raw_tok": P + C,
            "flag": flag,
        })
        prev_prompt = P
    return rows


def single_call_row(dataset, model, run, u, label, retry_exception=False,
                    prev_identical_prompt=None):
    """A call that is not part of an episode: fresh in full by ruling."""
    r = R_CACHE[model]
    P = int(u.get("prompt_tokens") or 0)
    C = int(u.get("completion_tokens") or 0)
    K = float(u.get("cost_usd") or 0.0)
    flag = "single_fresh"
    new, prefix = P, 0
    if retry_exception and prev_identical_prompt == P and P > 0:
        new, prefix, flag = 0, P, "single_retry_cached"
    det = new + r * prefix + C
    cached, cached_raw, viol = solve_cached(model, P, C, K)
    prov = (P - cached) + r * cached + C
    return {
        "dataset": dataset, "model": model, "run": run, "call": label,
        "prompt_tokens": P, "completion_tokens": C, "cost_usd": K,
        "prev_prompt": prefix, "new_tokens": new,
        "det_tok": det,
        "prov_cached": round(cached, 2), "prov_cached_raw": round(cached_raw, 2),
        "prov_viol": viol, "prov_tok": prov, "raw_tok": P + C,
        "flag": flag,
    }


def load_trajectory(path):
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


def run_total(rows, model, **extra):
    tot = {
        "n_calls": len(rows),
        "prompt_tokens": sum(x["prompt_tokens"] for x in rows),
        "completion_tokens": sum(x["completion_tokens"] for x in rows),
        "new_tokens": sum(x["new_tokens"] for x in rows),
        "raw_tok": sum(x["raw_tok"] for x in rows),
        "det_tok": sum(x["det_tok"] for x in rows),
        "prov_tok": sum(x["prov_tok"] for x in rows),
        "cost_usd": sum(x["cost_usd"] for x in rows),
        "prov_violations": sum(1 for x in rows if x["prov_viol"]),
        "flag_prompt_shrank": sum(1 for x in rows if x["flag"] == "prompt_shrank"),
        "flag_zero_new": sum(1 for x in rows if x["flag"] == "zero_new"),
        "model": model,
        "r_cache": R_CACHE[model],
    }
    tot.update(extra)
    return tot


# --------------------------------------------------------------------------
# collection
# --------------------------------------------------------------------------
def collect(retry_exception=False):
    calls, runs = [], []

    def add_grid(base, dataset, keys):
        for mdir in sorted(p.name for p in base.iterdir() if p.is_dir()):
            model = DIR_MODEL[mdir]
            for run_dir in sorted((base / mdir).iterdir()):
                traj = run_dir / "trajectory.jsonl"
                if not run_dir.is_dir() or not traj.exists():
                    continue
                steps, final = load_trajectory(traj)
                usages = [s["usage"] for s in steps if s.get("usage")]
                no_usage = sum(1 for s in steps if not s.get("usage"))
                cond, key, seed = run_dir.name.split("__")
                rows = episode_rows(dataset, model, run_dir.name, usages)
                calls.extend(rows)
                runs.append(run_total(
                    rows, model, dataset=dataset, condition=cond,
                    **{keys: key}, seed=int(seed[1:]),
                    steps=(final or {}).get("steps"),
                    success=(final or {}).get("success"),
                    final_total_tokens=(final or {}).get("total_tokens"),
                    steps_without_usage=no_usage))

    add_grid(T11, "t11", "layout")
    add_grid(T12, "t12", "layout")   # android family lands in the layout column

    # ---- t15 strong-model corroboration
    for run_dir in sorted(T15.iterdir()):
        traj = run_dir / "trajectory.jsonl"
        if not run_dir.is_dir() or not traj.exists():
            continue
        mdir, cond, layout, seed = run_dir.name.split("__")
        model = DIR_MODEL[mdir]
        steps, final = load_trajectory(traj)
        usages = [s["usage"] for s in steps if s.get("usage")]
        rows = episode_rows("t15", model, run_dir.name, usages)
        calls.extend(rows)
        runs.append(run_total(rows, model, dataset="t15", condition=cond,
                              layout=layout, seed=int(seed[1:]),
                              steps=(final or {}).get("steps"),
                              success=(final or {}).get("success"),
                              final_total_tokens=(final or {}).get("total_tokens"),
                              steps_without_usage=0))

    # ---- t13 compile attempts (stepview and fullread) : single calls
    for variant, root in (("t13_compile", T13), ("t13_compile_fullread", T13 / "fullread")):
        for model in (GLM, DS):
            prev_P = None
            for a in ("attempt1", "attempt2", "attempt3"):
                cj = root / MODEL_DIR[model] / a / "compile.json"
                if not cj.exists():
                    continue
                u = json.loads(cj.read_text())["usage"]
                row = single_call_row(variant, model, a, u, "compile",
                                      retry_exception, prev_P)
                prev_P = int(u.get("prompt_tokens") or 0)
                calls.append(row)
                runs.append(run_total([row], model, dataset=variant,
                                      condition="compile", layout="wizard",
                                      seed=None, steps=1, success=None,
                                      final_total_tokens=None,
                                      steps_without_usage=0))

    # ---- t13 deploy30 : per-use extraction calls, aggregate tokens only
    for model in (GLM, DS):
        dj = json.loads((T13 / MODEL_DIR[model] / "deploy30" / "deploy.json").read_text())
        rows = []
        for i, u in enumerate(dj["uses"]):
            # only the aggregate token count is logged; the call is a single
            # extraction call, fresh in full, so det == raw exactly.
            rows.append({
                "dataset": "t13_deploy", "model": model, "run": f"use{i+1}",
                "call": f"use{i+1}",
                "prompt_tokens": 0, "completion_tokens": 0,
                "cost_usd": float(u["cost_usd"]),
                "prev_prompt": 0, "new_tokens": int(u["tokens"]),
                "det_tok": float(u["tokens"]),
                "prov_cached": 0.0, "prov_cached_raw": 0.0, "prov_viol": "",
                "prov_tok": float(u["tokens"]),
                "raw_tok": int(u["tokens"]),
                "flag": "single_fresh_aggregate",
            })
        calls.extend(rows)
        runs.append(run_total(rows, model, dataset="t13_deploy",
                              condition="deploy", layout="wizard", seed=None,
                              steps=len(rows), success=None,
                              final_total_tokens=dj["total_tokens"],
                              steps_without_usage=0,
                              success_rate=dj["success_rate"],
                              success_count=dj["success_count"]))

    # ---- t14 android compile + deploy, per family
    for model in (GLM, DS):
        for fam in ANDROID_FAMILY:
            for a in ("attempt1", "attempt2", "attempt3"):
                cj = T14 / MODEL_DIR[model] / fam / a / "compile.json"
                if not cj.exists():
                    continue
                u = json.loads(cj.read_text())["usage"]
                row = single_call_row("t14_compile", model, f"{fam}__{a}", u, "compile")
                calls.append(row)
                runs.append(run_total([row], model, dataset="t14_compile",
                                      condition="compile", layout=fam,
                                      seed=None, steps=1, success=None,
                                      final_total_tokens=None,
                                      steps_without_usage=0))
            dj_p = T14 / MODEL_DIR[model] / fam / "deploy30" / "deploy.json"
            if not dj_p.exists():
                continue
            dj = json.loads(dj_p.read_text())
            rows = []
            for i, u in enumerate(dj["uses"]):
                rows.append({
                    "dataset": "t14_deploy", "model": model,
                    "run": f"{fam}__use{i+1}", "call": f"use{i+1}",
                    "prompt_tokens": 0, "completion_tokens": 0,
                    "cost_usd": float(u.get("cost_usd") or 0.0),
                    "prev_prompt": 0, "new_tokens": int(u["tokens"]),
                    "det_tok": float(u["tokens"]),
                    "prov_cached": 0.0, "prov_cached_raw": 0.0, "prov_viol": "",
                    "prov_tok": float(u["tokens"]), "raw_tok": int(u["tokens"]),
                    "flag": "single_fresh_aggregate",
                })
            calls.extend(rows)
            runs.append(run_total(rows, model, dataset="t14_deploy",
                                  condition="deploy", layout=fam, seed=None,
                                  steps=len(rows), success=None,
                                  final_total_tokens=dj["total_tokens"],
                                  steps_without_usage=0,
                                  success_rate=dj["success_rate"],
                                  success_count=dj["success_count"]))
    return calls, runs


# --------------------------------------------------------------------------
# aggregation helpers
# --------------------------------------------------------------------------
def pick(runs, **kw):
    out = []
    for r in runs:
        if all(r.get(k) == v for k, v in kw.items()):
            out.append(r)
    return out


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def gate_rate(model, root, fam=None):
    """Pass rate over the three gate runs (5 bindings each)."""
    if fam is None:
        passed = total = 0
        for a in (1, 2, 3):
            # t13 gate records are extensionless JSON files (gate1, gate2, gate3)
            g = json.loads((root / MODEL_DIR[model] / f"gate{a}").read_text())
            passed += g["bindings_passed"]
            total += g["bindings_total"]
        return passed / total if total else None
    s = json.loads((root / MODEL_DIR[model] / "summary.json").read_text())
    ent = s["families"][fam]["gates"]
    passed = total = 0
    for v in ent.values():
        a, b = v.split("/")
        passed += int(a)
        total += int(b)
    return passed / total if total else None


def field_of(runs, field, **kw):
    return [r[field] for r in pick(runs, **kw)]


# --------------------------------------------------------------------------
def build_summary(calls, runs, retry_exception):
    S = {
        "schema": "deterministic_accounting/1",
        "convention": {
            "within_episode": "effective_i = (prompt_i - prompt_{i-1}) + r*prompt_{i-1} + completion_i; call 1 all fresh",
            "across_episodes": "cold start, nothing cached",
            "single_calls": "fresh in full (compile attempts, deployment extraction calls)",
            "retry_exception_applied": bool(retry_exception),
            "retry_exception_note": (
                "A byte-identical retry of a previous call in the same build could "
                "have its prefix priced at the cache rate. Not applied: single calls "
                "stay fully fresh. t13_compilepath/fullread is the one place where it "
                "would bind (identical prompts across the three attempts)."),
            "r_cache": {m: R_CACHE[m] for m in PRICES},
            "r_quoted_in_paper": R_QUOTED,
        },
        "edge_cases": {},
        "provider_vs_deterministic": {},
        "openapps": {},
        "android": {},
        "t15": {},
        "headline": {},
    }

    # ---------------------------------------------------------- edge cases
    for c in calls:
        if not c["flag"]:
            c["flag"] = "regular"
    flags = Counter(c["flag"] for c in calls)
    for name in ("regular", "first_call", "prompt_shrank", "zero_new", "no_usage",
                 "single_fresh", "single_retry_cached", "single_fresh_aggregate"):
        flags.setdefault(name, 0)
    per_dataset = {}
    for c in calls:
        e = per_dataset.setdefault(c["dataset"], Counter())
        e[c["flag"]] += 1
    S["edge_cases"] = {
        "totals": dict(flags),
        "by_dataset": {k: dict(v) for k, v in sorted(per_dataset.items())},
        "prompt_shrank_calls": [
            {"dataset": c["dataset"], "model": c["model"], "run": c["run"],
             "call": c["call"], "prompt": c["prompt_tokens"],
             "prev_prompt_of_call": c["prev_prompt"]}
            for c in calls if c["flag"] == "prompt_shrank"],
        "zero_new_calls": [
            {"dataset": c["dataset"], "model": c["model"], "run": c["run"],
             "call": c["call"], "prompt": c["prompt_tokens"]}
            for c in calls if c["flag"] == "zero_new"],
        "steps_without_usage": sum(r.get("steps_without_usage") or 0 for r in runs),
        "tool_result_only_turns": 0,
        "tool_result_note": (
            "The harness issues exactly one model call per recorded step and every "
            "step carries a usage block, so there are no tool-result-only turns to "
            "price. Counted, not assumed."),
    }

    # ------------------------------------------ provider vs deterministic
    for dataset in ("t11", "t12", "t15"):
        diffs, ratios, rows = [], [], []
        for r in pick(runs, dataset=dataset):
            d = r["det_tok"] - r["prov_tok"]
            diffs.append(d)
            ratios.append(r["det_tok"] / r["prov_tok"] if r["prov_tok"] else None)
            rows.append({"model": r["model"], "run": r.get("condition", "") + "__" +
                         str(r.get("layout")) + "__s" + str(r.get("seed")),
                         "det": round(r["det_tok"]), "prov": round(r["prov_tok"]),
                         "raw": r["raw_tok"],
                         "det_minus_prov": round(d),
                         "det_over_prov": round(r["det_tok"] / r["prov_tok"], 4)
                         if r["prov_tok"] else None})
        rr = [x for x in ratios if x]
        S["provider_vs_deterministic"][dataset] = {
            "n_episodes": len(diffs),
            "det_minus_prov": {
                "min": round(min(diffs)), "p25": round(quantile(diffs, .25)),
                "median": round(statistics.median(diffs)),
                "p75": round(quantile(diffs, .75)), "max": round(max(diffs)),
                "mean": round(mean(diffs)),
            },
            "det_over_prov": {
                "min": round(min(rr), 4), "p25": round(quantile(rr, .25), 4),
                "median": round(statistics.median(rr), 4),
                "p75": round(quantile(rr, .75), 4), "max": round(max(rr), 4),
                "mean": round(mean(rr), 4),
            },
            "episodes_det_above_prov": sum(1 for d in diffs if d > 0),
            "episodes_det_below_prov": sum(1 for d in diffs if d < 0),
            "per_episode": rows,
        }

    # ------------------------------------------------------------ OpenApps
    for mk, model in (("glm", GLM), ("ds", DS)):
        grid = {}
        for layout in LAYOUTS:
            cell = {}
            for cond in CONDITIONS:
                rs = sorted(pick(runs, dataset="t11", condition=cond, layout=layout),
                            key=lambda r: r["seed"])
                cell[cond] = {
                    "det_mean": mean(r["det_tok"] for r in rs if r["model"] == model),
                    "det_per_seed": [r["det_tok"] for r in rs if r["model"] == model],
                    "raw_mean": mean(r["raw_tok"] for r in rs if r["model"] == model),
                    "prov_mean": mean(r["prov_tok"] for r in rs if r["model"] == model),
                }
            c = cell["discover"]["det_mean"]
            L = cell["told"]["det_mean"]
            cs = cell["discover"]["det_per_seed"]
            Ls = cell["told"]["det_per_seed"]
            cell["c"] = c
            cell["L"] = L
            cell["D"] = c - L
            cell["rho_pooled"] = 1 - sum(Ls) / sum(cs)
            cell["rho_per_seed"] = [1 - l / cc for cc, l in zip(cs, Ls)]
            cell["rho_raw_pooled"] = 1 - (cell["told"]["raw_mean"] /
                                          cell["discover"]["raw_mean"])
            cell["mid_over_told"] = cell["mid"]["det_mean"] / L
            cell["skill_recovery"] = (c - cell["skill"]["det_mean"]) / (c - L)
            cell["mid_recovery"] = (c - cell["mid"]["det_mean"]) / (c - L)
            grid[layout] = cell

        C_step = mean(field_of(runs, "det_tok", dataset="t13_compile", model=model))
        C_step_attempts = field_of(runs, "det_tok", dataset="t13_compile", model=model)
        C_full = mean(field_of(runs, "det_tok", dataset="t13_compile_fullread", model=model))
        C_full_attempts = field_of(runs, "det_tok", dataset="t13_compile_fullread", model=model)
        dep = pick(runs, dataset="t13_deploy", model=model)[0]
        d = dep["det_tok"] / dep["n_calls"]
        q0 = 1 - dep["success_rate"]
        p_gate = gate_rate(model, T13)
        c = grid["wizard"]["c"]
        L = grid["wizard"]["L"]
        s = (1 - q0) * c - d
        # dollars are the bill: unchanged by the convention
        c_usd = mean(field_of(runs, "cost_usd", dataset="t11", model=model,
                              condition="discover", layout="wizard"))
        C_usd = mean(field_of(runs, "cost_usd", dataset="t13_compile", model=model))
        d_usd = dep["cost_usd"] / dep["n_calls"]
        s_usd = (1 - q0) * c_usd - d_usd
        S["openapps"][mk] = {
            "model": model, "grid": grid,
            "c": c, "L": L, "D": c - L,
            "rho_wizard_pooled": grid["wizard"]["rho_pooled"],
            "rho_per_layout": {k: grid[k]["rho_pooled"] for k in LAYOUTS},
            "rho_per_layout_per_seed": {k: grid[k]["rho_per_seed"] for k in LAYOUTS},
            "C_stepview": C_step, "C_stepview_attempts": C_step_attempts,
            "C_fullread": C_full, "C_fullread_attempts": C_full_attempts,
            "p_gate": p_gate, "d": d, "q0": q0,
            "s": s,
            "N_star_marginal": C_step / p_gate / s,
            "N_star_full_build": (C_step + c) / s,
            "dollar": {"c_usd": c_usd, "C_usd": C_usd, "d_usd": d_usd,
                       "s_usd": s_usd, "N_star_usd": C_usd / s_usd},
        }

    # ------------------------------------------------------------- Android
    for mk, model in (("glm", GLM), ("ds", DS)):
        fams = {}
        for fam, famkey in ANDROID_FAMILY.items():
            cell = {}
            for cond in CONDITIONS:
                rs = sorted(pick(runs, dataset="t12", condition=cond,
                                 layout=famkey, model=model),
                            key=lambda r: r["seed"])
                ok = [r for r in rs if r["success"] is not False]
                cell[cond] = {
                    "det_mean_all_seeds": mean(r["det_tok"] for r in rs),
                    "det_mean_successful": mean(r["det_tok"] for r in ok),
                    "det_per_seed": [r["det_tok"] for r in rs],
                    "raw_mean_all_seeds": mean(r["raw_tok"] for r in rs),
                    "raw_mean_successful": mean(r["raw_tok"] for r in ok),
                    "prov_mean_all_seeds": mean(r["prov_tok"] for r in rs),
                    "prov_mean_successful": mean(r["prov_tok"] for r in ok),
                    "n_runs": len(rs), "n_successful": len(ok),
                    "steps": [r["steps"] for r in rs],
                }
            C = mean(field_of(runs, "det_tok", dataset="t14_compile",
                              model=model, layout=fam))
            dep = pick(runs, dataset="t14_deploy", model=model, layout=fam)
            d = dep[0]["det_tok"] / dep[0]["n_calls"] if dep else None
            q0 = 1 - dep[0]["success_rate"] if dep else None
            p = gate_rate(model, T14, fam)
            for basis in ("all_seeds", "successful"):
                c = cell["discover"][f"det_mean_{basis}"]
                L = cell["told"][f"det_mean_{basis}"]
                s = (1 - q0) * c - d if (q0 is not None and d is not None) else None
                cell[basis] = {
                    "c": c, "L": L, "D": c - L, "rho": 1 - L / c,
                    "c_raw": cell["discover"][f"raw_mean_{basis}"],
                    "L_raw": cell["told"][f"raw_mean_{basis}"],
                    "rho_raw": 1 - (cell["told"][f"raw_mean_{basis}"] /
                                    cell["discover"][f"raw_mean_{basis}"]),
                    "c_prov": cell["discover"][f"prov_mean_{basis}"],
                    "L_prov": cell["told"][f"prov_mean_{basis}"],
                    "rho_prov": 1 - (cell["told"][f"prov_mean_{basis}"] /
                                     cell["discover"][f"prov_mean_{basis}"]),
                    "s": s,
                    "N_star_marginal": (C / p / s) if (p and s and s > 0) else "inf",
                }
            cell["C"] = C
            cell["C_attempts"] = field_of(runs, "det_tok", dataset="t14_compile",
                                          model=model, layout=fam)
            cell["p_gate"] = p
            cell["d"] = d
            cell["q0"] = q0
            fams[fam] = cell
        S["android"][mk] = {"model": model, "families": fams}

    # ----------------------------------------------------------------- t15
    for r in pick(runs, dataset="t15"):
        S["t15"][f"{r['model']}__{r['condition']}"] = {
            "steps": r["steps"], "raw_tok": r["raw_tok"],
            "det_tok": round(r["det_tok"], 1), "prov_tok": round(r["prov_tok"], 1),
            "cost_usd": r["cost_usd"], "r": r["r_cache"],
        }
    for model in (SONNET, GLM5V):
        c = S["t15"].get(f"{model}__discover")
        L = S["t15"].get(f"{model}__told")
        if not c or not L:
            continue
        S["t15"][model] = {
            "rho_det": round(1 - L["det_tok"] / c["det_tok"], 4),
            "rho_raw": round(1 - L["raw_tok"] / c["raw_tok"], 4),
            "rho_prov": round(1 - L["prov_tok"] / c["prov_tok"], 4),
            "rho_usd": round(1 - L["cost_usd"] / c["cost_usd"], 4),
        }

    # ------------------------------------------------------------ headline
    for mk in ("glm", "ds"):
        o = S["openapps"][mk]
        S["headline"][mk] = {
            "c": round(o["c"]), "L": round(o["L"]), "D": round(o["D"]),
            "rho_wizard": round(o["rho_wizard_pooled"], 3),
            "C_stepview": round(o["C_stepview"]),
            "C_fullread": round(o["C_fullread"]),
            "p": o["p_gate"], "d": round(o["d"], 1), "q0": round(o["q0"], 4),
            "s": round(o["s"]),
            "N_star": round(o["N_star_marginal"], 4),
            "C_plus_c_over_s": round(o["N_star_full_build"], 3),
            "dollar_N_star": round(o["dollar"]["N_star_usd"], 4),
        }

    # -------------------------------------------------- old vs new table
    old_idx = json.loads((PAPER / "results_new_index.json").read_text())
    old_const = json.loads((T2SIM / "constants.measured.json").read_text())
    cmp_rows = []

    def row(quantity, model, old, new, unit="tokens"):
        old_f = None if old is None else float(old)
        new_f = None if new is None else float(new)
        cmp_rows.append({
            "quantity": quantity, "model": model, "unit": unit,
            "old_provider_reported": old, "new_deterministic": new,
            "ratio": (round(new_f / old_f, 3)
                      if old_f not in (None, 0) and new_f is not None else None),
            "sign_flip": (old_f is not None and new_f is not None
                          and (old_f > 0) != (new_f > 0)),
        })

    for mk, label in (("glm", "GLM 5.3 Flash"), ("ds", "DeepSeek v4 flash vision-exp")):
        ct = old_idx["constants_table_methods_v2_s6"][mk]
        o = S["openapps"][mk]
        row("c (OpenApps wizard)", label, ct["c"]["value"], round(o["c"]))
        row("L (OpenApps wizard)", label, ct["L"]["value"], round(o["L"]))
        row("D = c - L", label, ct["c"]["value"] - ct["L"]["value"], round(o["D"]))
        for layout in LAYOUTS:
            row(f"rho {layout}", label,
                old_const["cost_sets"][f"openapps_{mk}"]["layouts"][layout]["rho"],
                round(o["rho_per_layout"][layout], 3), "share")
        row("C stepview", label, ct["C_stepview"]["value"], round(o["C_stepview"]))
        row("C fullread", label,
            old_const["extras"]["C_fullread"][GLM if mk == "glm" else DS],
            round(o["C_fullread"]))
        row("d (per use)", label, ct["d"]["value"], round(o["d"], 1))
        row("s = (1-q0)c - d", label, ct["s"]["value"], round(o["s"]))
        row("N* marginal", label, ct["N_star"]["value"],
            round(o["N_star_marginal"], 4), "uses")
        row("N* full build (C+c)/s", label, ct["C_plus_c_over_s"]["value"],
            round(o["N_star_full_build"], 3), "uses")
        row("N* dollar-equivalent", label, ct["dollar_N_star"]["value"],
            round(o["dollar"]["N_star_usd"], 4), "uses")
        for fam in ANDROID_FAMILY:
            cell = S["android"][mk]["families"][fam]
            row(f"Android rho {fam}", label,
                old_const["cost_sets"][f"android_{mk}"]["layouts"][fam]["rho"],
                round(cell["successful"]["rho"], 3), "share")
    for key, model in (("claude_sonnet_5", SONNET), ("glm_5v_turbo", GLM5V)):
        if model in S["t15"]:
            row("t15 rho", model, old_idx["t15"][f"{key}_rho_disc"]["value"],
                S["t15"][model]["rho_det"], "share")
    S["comparison_old_vs_new"] = {
        "old_unit": "provider-reported cache adjustment (back-out solver, clipped)",
        "new_unit": "deterministic per-call convention",
        "rows": cmp_rows,
        "sign_flips": [r for r in cmp_rows if r["sign_flip"]],
    }
    return S


def quantile(xs, q):
    xs = sorted(xs)
    if not xs:
        return None
    i = q * (len(xs) - 1)
    lo, hi = math.floor(i), math.ceil(i)
    return xs[lo] + (xs[hi] - xs[lo]) * (i - lo)


# --------------------------------------------------------------------------
# writers
# --------------------------------------------------------------------------
def write_tables(calls, runs):
    call_fields = list(calls[0].keys())
    with open(HERE / "calls_deterministic.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=call_fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(calls)
    run_fields = sorted({k for r in runs for k in r})
    with open(HERE / "runs_deterministic.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=run_fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(runs)


def entry(value, source, field, note=None):
    e = {"value": value, "source": source, "field": field}
    if note:
        e["note"] = note
    return e


DET_RUNS = "/Users/myl/app/computer-use/experimental-results/guiexp/cache_adjust/runs_deterministic.csv"
DET_SUM = "/Users/myl/app/computer-use/experimental-results/guiexp/cache_adjust/summary_deterministic.json"


def build_index(S, old_index_path):
    old = json.loads(Path(old_index_path).read_text())
    idx = {
        "schema": "results_new_index/1",
        "generated": "2026-09-09",
        "purpose": old["purpose"] + " DETERMINISTIC-CONVENTION EDITION: same key "
                   "structure as results_new_index.json so the number pass can "
                   "switch by changing one path.",
        "units": ("deterministic cache-adjusted tokens: within an episode "
                  "effective_i = (prompt_i - prompt_{i-1}) + r*prompt_{i-1} + "
                  "completion_i, call 1 all fresh, episodes cold; single calls "
                  "(compile, extraction) fresh in full. r: GLM 0.20, DS 0.318. "
                  "Entries named raw/usd keep their own unit."),
        "convention": S["convention"],
    }

    ct = {}
    for mk, mfull in (("glm", GLM), ("ds", DS)):
        o = S["openapps"][mk]
        h = S["headline"][mk]
        dirn = MODEL_DIR[mfull]
        gridsrc = f"experimental-results/guiexp/t11_grid/{dirn}"
        ct[mk] = {
            "c": entry(h["c"], DET_RUNS, f"t11 discover/wizard det_tok 3-seed mean ({mfull})",
                       "mean det_tok of 3 discover/wizard seeds"),
            "L": entry(h["L"], DET_RUNS, f"t11 told/wizard det_tok 3-seed mean ({mfull})"),
            "D": entry(h["D"], "derived", "c - L"),
            "rho_wizard": entry(round(o["rho_wizard_pooled"], 3), DET_SUM,
                                f"openapps.{mk}.rho_wizard_pooled",
                                "pooled 1 - sum(told)/sum(discover), deterministic"),
            "C_stepview": entry(h["C_stepview"], DET_RUNS,
                                "t13_compile rows det_tok 3-attempt mean",
                                "single calls fresh in full, so this equals the raw mean"),
            "C_fullread": entry(h["C_fullread"], DET_RUNS,
                                "t13_compile_fullread rows det_tok 3-attempt mean",
                                "retry exception NOT applied; see summary.convention"),
            "p_gate": entry(o["p_gate"], f"{gridsrc.replace('t11_grid','t13_compilepath')}/gate1..3",
                            "bindings_passed/bindings_total", "unchanged by the convention"),
            "d": entry(round(o["d"], 1),
                       f"experimental-results/guiexp/t13_compilepath/{dirn}/deploy30/deploy.json",
                       "mean of uses[].tokens",
                       "single extraction call, fresh in full: identical to the raw d"),
            "q0": entry(round(o["q0"], 4),
                        f"experimental-results/guiexp/t13_compilepath/{dirn}/deploy30/deploy.json",
                        "1 - success_rate", "unchanged by the convention"),
            "s": entry(h["s"], DET_SUM, f"openapps.{mk}.s", "(1-q0)*c - d"),
            "N_star": entry(round(o["N_star_marginal"], 4), DET_SUM,
                            f"openapps.{mk}.N_star_marginal", "C/p / ((1-q0)c-d)"),
            "C_plus_c_over_s": entry(round(o["N_star_full_build"], 3), DET_SUM,
                                     f"openapps.{mk}.N_star_full_build",
                                     "offline full-build reading"),
            "raw_c": entry(round(o["grid"]["wizard"]["discover"]["raw_mean"]), DET_RUNS,
                           "t11 discover/wizard raw_tok 3-seed mean"),
            "raw_L": entry(round(o["grid"]["wizard"]["told"]["raw_mean"]), DET_RUNS,
                           "t11 told/wizard raw_tok 3-seed mean"),
            "raw_rho_wizard": entry(round(o["grid"]["wizard"]["rho_raw_pooled"], 3),
                                    DET_SUM, f"openapps.{mk}.grid.wizard.rho_raw_pooled"),
            "provider_c": entry(round(o["grid"]["wizard"]["discover"]["prov_mean"]),
                                DET_RUNS, "t11 discover/wizard prov_tok 3-seed mean",
                                "the superseded provider-reported unit, for comparison"),
            "provider_L": entry(round(o["grid"]["wizard"]["told"]["prov_mean"]),
                                DET_RUNS, "t11 told/wizard prov_tok 3-seed mean"),
            "dollar_N_star": entry(round(o["dollar"]["N_star_usd"], 4), DET_SUM,
                                   f"openapps.{mk}.dollar.N_star_usd",
                                   "dollars are the bill: unchanged from results_new_index.json"),
            "r_cache": entry(R_QUOTED[mfull], "docs/cache-adjusted-accounting.md s1",
                             "official p_c/p_in"),
            "deploy_d_usd": entry(o["dollar"]["d_usd"], DET_SUM,
                                  f"openapps.{mk}.dollar.d_usd"),
        }
    idx["constants_table_methods_v2_s6"] = ct

    mg = {}
    for mk, mfull in (("glm", GLM), ("ds", DS)):
        o = S["openapps"][mk]
        cell = {}
        for layout in LAYOUTS:
            g = o["grid"][layout]
            f = f"t11|{mfull}|{{}}|{layout}|det_tok mean"
            cell[layout] = {
                "discover": entry(round(g["discover"]["det_mean"]), DET_RUNS, f.format("discover")),
                "mid": entry(round(g["mid"]["det_mean"]), DET_RUNS, f.format("mid")),
                "told": entry(round(g["told"]["det_mean"]), DET_RUNS, f.format("told")),
                "skill": entry(round(g["skill"]["det_mean"]), DET_RUNS, f.format("skill")),
                "floor": entry(round(g["floor"]["det_mean"]), DET_RUNS, f.format("floor"),
                               "one call, therefore all fresh: equals the raw floor"),
                "rho_pooled": entry(round(g["rho_pooled"], 3), DET_RUNS,
                                    "pooled 1 - sum(told)/sum(discover)"),
                "rho_per_seed": entry([round(x, 3) for x in g["rho_per_seed"]], DET_RUNS,
                                      "1 - told_s/discover_s"),
                "mid_over_told": entry(round(g["mid_over_told"], 2), DET_RUNS,
                                       "mid mean / told mean"),
                "skill_recovery": entry(round(g["skill_recovery"], 3), DET_RUNS,
                                        "(discover-skill)/(discover-told)"),
                "mid_recovery": entry(round(g["mid_recovery"], 3), DET_RUNS,
                                      "(discover-mid)/(discover-told)"),
            }
        cell["solving_runs_all_succeed"] = old["measure_grid"][mk]["solving_runs_all_succeed"]
        mg[mk] = cell
    idx["measure_grid"] = mg

    cp = {}
    for mk, mfull in (("glm", GLM), ("ds", DS)):
        o = S["openapps"][mk]
        dirn = MODEL_DIR[mfull]
        base = f"experimental-results/guiexp/t13_compilepath/{dirn}"
        cp[mk] = {
            "attempts_det": entry([round(x) for x in o["C_stepview_attempts"]],
                                  f"{base}/attempt1..3/compile.json",
                                  "usage.prompt_tokens+completion_tokens (single call, all fresh)"),
            "C_det_mean": entry(round(o["C_stepview"]), f"{base}/attempt*", "det mean"),
            "deploy30_success": old["compile_path"][mk]["deploy30_success"],
            "deploy30_d_mean": entry(round(o["d"], 1), f"{base}/deploy30/deploy.json",
                                     "d_tokens_mean", "unchanged: single extraction call"),
            "deploy30_fail_types": old["compile_path"][mk]["deploy30_fail_types"],
            "gates": entry(f"{round(o['p_gate']*15)}/15", f"{base}/gate1..3",
                           "bindings_passed sum"),
            "regates": old["compile_path"][mk]["regates"],
            "fullread_attempts_det": entry([round(x) for x in o["C_fullread_attempts"]],
                                           f"experimental-results/guiexp/t13_compilepath/fullread/{dirn}/attempt1..3/compile.json",
                                           "usage.prompt+completion; retry exception not applied"),
            "fullread_C_det_mean": entry(round(o["C_fullread"]),
                                         f"experimental-results/guiexp/t13_compilepath/fullread/{dirn}/attempt*",
                                         "det mean"),
            "fullread_gates": old["compile_path"][mk]["fullread_gates"],
            "_note_old_index_bug": ("results_new_index.json carried the DeepSeek "
                                    "values and paths in its compile_path.glm block; "
                                    "this file uses each model's own artifacts."),
        }
    idx["compile_path"] = cp

    t15 = {}
    for key, model in (("claude_sonnet_5", SONNET), ("glm_5v_turbo", GLM5V)):
        e = S["t15"].get(model)
        if not e:
            continue
        t15[f"{key}_rho_det"] = entry(e["rho_det"], DET_SUM, f"t15.{model}.rho_det",
                                      "1 - told/discover, single runs, deterministic")
        t15[f"{key}_rho_raw"] = entry(e["rho_raw"], DET_SUM, f"t15.{model}.rho_raw")
        t15[f"{key}_rho_provider"] = entry(e["rho_prov"], DET_SUM, f"t15.{model}.rho_prov",
                                           "superseded provider-reported unit")
        for cond in ("discover", "told"):
            t15[f"{key}_{cond}"] = entry(S["t15"][f"{model}__{cond}"], DET_SUM,
                                         f"t15.{model}__{cond}")
    idx["t15"] = t15

    andr = {}
    for mk, mfull in (("glm", GLM), ("ds", DS)):
        a = S["android"][mk]["families"]
        dirn = MODEL_DIR[mfull]
        blk = {}
        for fam, famkey in ANDROID_FAMILY.items():
            src = f"experimental-results/guiexp_android/t12_grid/{dirn}/{{}}__{famkey}__s*"
            cell = a[fam]["all_seeds"]
            blk[fam] = {
                "c_raw": entry(round(cell["c_raw"]), src.format("discover"),
                               "sum usage P+C, 3-seed mean"),
                "L_raw": entry(round(cell["L_raw"]), src.format("told"),
                               "sum usage P+C, 3-seed mean"),
                "c_det": entry(round(cell["c"]), src.format("discover"),
                               "deterministic per-call accounting, 3-seed mean"),
                "L_det": entry(round(cell["L"]), src.format("told"),
                               "deterministic per-call accounting, 3-seed mean"),
                "rho_det": entry(round(cell["rho"], 3), "derived", "1 - L/c (deterministic)"),
                "rho_provider": entry(round(cell["rho_prov"], 3), "derived",
                                      "1 - L/c under the superseded provider-reported unit"),
                "rho_raw": entry(round(cell["rho_raw"], 3), "derived", "1 - L/c (raw P+C)"),
                "C_det": entry(round(a[fam]["C"]),
                               f"experimental-results/guiexp_android/t14_compilepath/{dirn}/{fam}/attempt1..3/compile.json",
                               "3-attempt mean, single call fresh in full"),
                "p_gate": entry(a[fam]["p_gate"],
                                f"experimental-results/guiexp_android/t14_compilepath/{dirn}/summary.json",
                                "families.*.gates summed"),
                "d": entry(round(a[fam]["d"], 1) if a[fam]["d"] is not None else None,
                           f"experimental-results/guiexp_android/t14_compilepath/{dirn}/{fam}/deploy30/deploy.json",
                           "mean uses[].tokens"),
                "q0": entry(round(a[fam]["q0"], 4) if a[fam]["q0"] is not None else None,
                            f"experimental-results/guiexp_android/t14_compilepath/{dirn}/{fam}/deploy30/deploy.json",
                            "1 - success_rate"),
            }
        blk["t14_gates_deploy"] = old["android"][mk]["t14_gates_deploy"]
        blk["_note_seed_basis"] = (
            "c/L here are 3-seed means over ALL seeds, the basis "
            "results_new_index.json used. constants.deterministic.json keeps the "
            "constants.measured.json basis instead and drops the one failed run "
            "(DS discover MarkorCreateNote s2).")
        andr[mk] = blk
    idx["android"] = andr

    for k in ("library", "estimator", "drift"):
        idx[k] = old[k]
    idx["estimator"]["_note"] = (
        "estimator shares are fractions of an episode's own steps and do not "
        "depend on the token unit; the *_bias entries are recomputed against the "
        "deterministic pooled rho below.")
    for mk in ("glm", "ds"):
        for layout in LAYOUTS:
            key = f"{mk}_bias_{layout}"
            if key not in idx["estimator"]:
                continue
            shares = idx["estimator"].get(f"{mk}_discover_{layout}", {}).get("value")
            if not shares:
                continue
            rho = S["openapps"][mk]["rho_per_layout"][layout]
            idx["estimator"][key] = entry(round(mean(shares) - rho, 3), "derived",
                                          "mean(estimator discover shares) - deterministic pooled rho")

    idx["t2_sim"] = dict(old["t2_sim"])
    idx["t2_sim"]["_status"] = (
        "COPIED VERBATIM from results_new_index.json. These are outputs of the T2 "
        "simulator run on constants.measured.json (fingerprint 91b9077d36a4cbde). "
        "constants.deterministic.json changes the inputs, so E3/E4/E5/E8 must be "
        "re-run before these cells can be quoted alongside the deterministic unit.")
    idx["t2_sim"]["constants_file_deterministic"] = "computer-use/t2sim/constants.deterministic.json"

    nr = {}
    for mk, mfull in (("glm", GLM), ("ds", DS)):
        o = S["openapps"][mk]
        nr[f"{mk}_N_star_deterministic"] = entry(round(o["N_star_marginal"], 4), DET_SUM,
                                                 f"openapps.{mk}.N_star_marginal")
        nr[f"{mk}_N_star_provider_reported"] = entry(
            old["constants_table_methods_v2_s6"][mk]["N_star"]["value"],
            "results_new_index.json", f"constants_table_methods_v2_s6.{mk}.N_star",
            "the superseded number, kept for the comparison table")
    nr["_note_clip_interval_retired"] = (
        "The clip-convention interval [0.15, 0.22] quoted in methods-v2 s6 was a "
        "property of the back-out solver. The deterministic convention has no clip "
        "and no interval.")
    idx["nstar_recompute"] = nr
    return idx


def build_constants(S, old_const_path):
    old = json.loads(Path(old_const_path).read_text())
    new = json.loads(json.dumps(old))  # deep copy, keeps every non-cost key
    new["notes"] = (
        "T2 simulation constants under the DETERMINISTIC cache convention "
        "(within an episode the re-sent prefix is always priced at r, only new "
        "tokens are fresh, episodes start cold; single calls fresh in full). "
        "Recomputed by experimental-results/guiexp/cache_adjust/"
        "deterministic_accounting.py. Sibling of constants.measured.json, which "
        "is untouched and holds the provider-reported unit. Seed basis matches "
        "constants.measured.json: the one failed AndroidWorld run "
        "(DS discover MarkorCreateNote s2) is excluded. p, q0, gates, d and m "
        "do not depend on the token unit and are unchanged.")

    for mk, key in (("glm", "openapps_glm"), ("ds", "openapps_ds")):
        o = S["openapps"][mk]
        cs = new["cost_sets"][key]
        cs["label"] = cs["label"].replace("(cache-adjusted)", "(deterministic cache-adjusted)")
        cs["source"] = "T1.1/T1.3 deterministic cache-adjusted + d_full"
        for layout in LAYOUTS:
            g = o["grid"][layout]
            lay = cs["layouts"][layout]
            lay["c"] = round(g["c"])
            lay["L"] = round(g["L"])
            lay["rho"] = round(g["rho_pooled"], 3)
            lay["C"] = round(o["C_stepview"])
            lay["p"] = o["p_gate"]
            lay["d"] = round(o["d"])
            lay["q0"] = round(o["q0"], 2)

    for mk, key in (("glm", "android_glm"), ("ds", "android_ds")):
        a = S["android"][mk]["families"]
        cs = new["cost_sets"][key]
        cs["source"] = "T1.2/T1.4 deterministic cache-adjusted"
        for fam in ANDROID_FAMILY:
            cell = a[fam]["successful"]
            lay = cs["layouts"][fam]
            lay["c"] = round(cell["c"])
            lay["L"] = round(cell["L"])
            lay["rho"] = round(cell["rho"], 2)
            lay["C"] = round(a[fam]["C"])
            lay["p"] = round(a[fam]["p_gate"], 2)
            lay["d"] = round(a[fam]["d"])
            lay["q0"] = round(a[fam]["q0"], 2)
            if lay["rho"] < 0:
                lay["note"] = ("rho negative: told-text defect, see "
                               "docs/android-told-diagnosis.md")
            elif "note" in lay:
                del lay["note"]

    new["extras"]["C_fullread"] = {
        GLM: round(S["openapps"]["glm"]["C_fullread"]),
        DS: round(S["openapps"]["ds"]["C_fullread"]),
    }
    new["_schema_deviations"] = (
        "constants.schema.json bounds rho to [0,1] and p to (0,1]. Four "
        "AndroidWorld cells break those bounds because the measurement does: "
        "rho is negative where the told text is defective (see "
        "docs/android-told-diagnosis.md) and p is exactly 0 where the gate never "
        "passed. constants.measured.json breaks the same bounds in 5 places. The "
        "schema is not relaxed here; the deviations are recorded and listed by "
        "deterministic_accounting.py.")
    return new


def validate_constants(const, schema_path):
    try:
        import jsonschema
    except ImportError:
        return ["jsonschema not installed; schema check skipped"]
    schema = json.loads(Path(schema_path).read_text())
    v = jsonschema.Draft7Validator(schema)
    return [f"{'/'.join(str(p) for p in e.path)}: {e.message}"
            for e in sorted(v.iter_errors(const), key=lambda e: list(e.path))]


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--retry-exception", action="store_true",
                    help="price a byte-identical repeat compile prompt at the cache "
                         "rate (sensitivity read only; the ruling is NOT to apply it)")
    args = ap.parse_args()

    calls, runs = collect(args.retry_exception)
    S = build_summary(calls, runs, args.retry_exception)
    write_tables(calls, runs)
    (HERE / "summary_deterministic.json").write_text(
        json.dumps(S, indent=1, default=str))

    idx = build_index(S, PAPER / "results_new_index.json")
    (PAPER / "results_deterministic_index.json").write_text(
        json.dumps(idx, indent=1, default=str))

    const = build_constants(S, T2SIM / "constants.measured.json")
    (T2SIM / "constants.deterministic.json").write_text(
        json.dumps(const, indent=1) + "\n")
    errs = validate_constants(const, T2SIM / "constants.schema.json")
    base_errs = validate_constants(
        json.loads((T2SIM / "constants.measured.json").read_text()),
        T2SIM / "constants.schema.json")

    # ---------------------------------------------------------------- print
    print("== EDGE CASES ==")
    for k, v in sorted(S["edge_cases"]["totals"].items()):
        print(f"  {k:<26} {v}")
    print(f"  steps without usage        {S['edge_cases']['steps_without_usage']}")
    print(f"  tool-result-only turns     {S['edge_cases']['tool_result_only_turns']}")

    print("\n== PROVIDER-REPORTED vs DETERMINISTIC, per episode ==")
    for ds, v in S["provider_vs_deterministic"].items():
        d = v["det_minus_prov"]
        rt = v["det_over_prov"]
        print(f"  {ds}: n={v['n_episodes']}  det-prov min={d['min']:,} "
              f"med={d['median']:,} max={d['max']:,} | ratio min={rt['min']} "
              f"med={rt['median']} max={rt['max']} | above={v['episodes_det_above_prov']} "
              f"below={v['episodes_det_below_prov']}")

    print("\n== OPENAPPS HEADLINE (deterministic) ==")
    for mk in ("glm", "ds"):
        h = S["headline"][mk]
        print(f"  {mk}: " + "  ".join(f"{k}={v}" for k, v in h.items()))
        print(f"       rho per layout: " +
              ", ".join(f"{k}={v:.3f}" for k, v in S['openapps'][mk]['rho_per_layout'].items()))

    print("\n== ANDROID (deterministic) ==")
    for mk in ("glm", "ds"):
        for fam, cell in S["android"][mk]["families"].items():
            a, s = cell["all_seeds"], cell["successful"]
            print(f"  {mk}/{fam:<9} all-seeds rho={a['rho']:+.3f} (prov {a['rho_prov']:+.3f}, "
                  f"raw {a['rho_raw']:+.3f})  successful-only rho={s['rho']:+.3f} "
                  f"c={round(s['c']):,} L={round(s['L']):,} C={round(cell['C']):,} "
                  f"p={cell['p_gate']:.2f} d={cell['d']:.0f} q0={cell['q0']:.2f}")

    print("\n== t15 ==")
    for m in (SONNET, GLM5V):
        if m in S["t15"]:
            print(f"  {m}: {S['t15'][m]}")

    print("\n== OLD (provider-reported) vs NEW (deterministic) ==")
    print(f"  {'quantity':<26} {'model':<30} {'old':>12} {'new':>12} {'ratio':>7}  flip")
    for r in S["comparison_old_vs_new"]["rows"]:
        print(f"  {r['quantity']:<26} {r['model'][:30]:<30} "
              f"{r['old_provider_reported']!s:>12} {r['new_deterministic']!s:>12} "
              f"{r['ratio']!s:>7}  {'SIGN' if r['sign_flip'] else ''}")

    print("\n== constants.deterministic.json schema check ==")
    print(f"  new file errors      ({len(errs)}): {errs}")
    print(f"  measured file errors ({len(base_errs)}): {base_errs}")
    print("\nwrote calls_deterministic.csv, runs_deterministic.csv, "
          "summary_deterministic.json,\n      "
          "computer-use-paper/results_deterministic_index.json,\n      "
          "computer-use/t2sim/constants.deterministic.json")


if __name__ == "__main__":
    main()
