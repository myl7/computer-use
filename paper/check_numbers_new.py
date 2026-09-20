#!/usr/bin/env python3
"""Recompute every NEW-harness headline number of the paper pass from the raw
result files of the guiexp campaign, and compare with the values the rewritten
paper will print (methods-v2 s6 constants table + the T2 table key cells).

Usage:
    python3 check_numbers_new.py            # run everything
    python3 check_numbers_new.py --group 1  # run one group only
    python3 check_numbers_new.py -v         # also print passing checks

Design rules (same contract as check_numbers.py, which verifies the OLD
openapps-harness numbers and must keep passing untouched):

1. Every number is recomputed from experimental-results/guiexp* (raw per-call
   and per-run records, trajectory JSONL, and the T2 simulation result JSONs).
   Nothing is read out of body.tex.
2. The value the new paper will print is written literally in this file; a
   divergence is a FAIL. That is the point.
3. Units are CACHE-ADJUSTED tokens (fresh + r*cached + completion; GLM r=0.20,
   DS r=0.318) unless the label says raw / usd / dollar-equivalent.
4. results_new_index.json maps each headline number to its source file+field;
   group 13 checks that index against the recomputations done here.

No network, no LLM calls. Stdlib only. Exit code non-zero on any failure.
"""
from __future__ import annotations

import argparse
import csv
import glob
import hashlib
import json
import math
import os
import re
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GUI = os.path.join(ROOT, "experimental-results", "guiexp")
AW = os.path.join(ROOT, "experimental-results", "guiexp_android")
T2 = os.path.join(GUI, "t2_sim")

MODELS = {"glm": "z-ai/glm-5.3-flash", "ds": "deepseek/deepseek-v4-flash-vision-exp"}
MDIR = {k: v.replace("/", "_") for k, v in MODELS.items()}
R_CACHE = {"glm": 0.20, "ds": 0.318}
# official price sheet (docs/cache-adjusted-accounting.md s1): p_in, p_c, p_o
PRICES = {"glm": (7.5e-8, 1.5e-8, 2.5e-7), "ds": (2.2e-8, 7e-9, 6.6e-7)}

RUNS = os.path.join(GUI, "cache_adjust", "runs.csv")
CALLS = os.path.join(GUI, "cache_adjust", "calls.csv")
SUMMARY = os.path.join(GUI, "cache_adjust", "summary.json")
INDEX = os.path.join(HERE, "results_new_index.json")
CONST = os.path.join(ROOT, "code", "t2sim", "constants.measured.json")

_AP = argparse.ArgumentParser()
_AP.add_argument("--group", type=int, default=None)
_AP.add_argument("-v", "--verbose", action="store_true")
_ARGS = _AP.parse_args()

PASS = 0
FAIL = 0
FAILURES: list[str] = []
VERBOSE = _ARGS.verbose
GROUP = _ARGS.group
CUR_GROUP = 0


def group(n: int, title: str) -> bool:
    global CUR_GROUP
    CUR_GROUP = n
    if GROUP is not None and GROUP != n:
        return False
    print(f"\n--- G{n}. {title}")
    return True


def _record(ok: bool, label: str, got, want, extra: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        if VERBOSE:
            print(f"  ok   [G{CUR_GROUP}] {label}: got {got}, expected {want}")
    else:
        FAIL += 1
        msg = (f"  FAIL [G{CUR_GROUP}] {label}\n"
               f"         recomputed: {got}\n"
               f"         expected:   {want}")
        if extra:
            msg += f"\n         note: {extra}"
        FAILURES.append(msg)
        print(msg)


def rhu(x: float, nd: int = 0) -> float:
    m = 10 ** nd
    v = math.floor(abs(x) * m + 0.5) / m
    return math.copysign(v, x)


def eq_round(label: str, got: float, want: float, nd: int = 0, extra: str = "") -> None:
    _record(rhu(got, nd) == want, label, f"{got!r} -> {rhu(got, nd)}", want, extra)


def approx(label: str, got: float, want: float, tol: float, extra: str = "") -> None:
    _record(abs(got - want) <= tol, label, got, f"{want} (+/- {tol})", extra)


def eq_int(label: str, got, want, extra: str = "") -> None:
    _record(got == want, label, got, want, extra)


# ---------------------------------------------------------------- load data
with open(RUNS) as fh:
    _runs = list(csv.DictReader(fh))
with open(CALLS) as fh:
    _calls = list(csv.DictReader(fh))
with open(SUMMARY) as fh:
    SUMMARY_D = json.load(fh)
with open(CONST) as fh:
    CONST_D = json.load(fh)
E3 = json.load(open(os.path.join(T2, "E3.json")))
E4 = json.load(open(os.path.join(T2, "E4.json")))
E5 = json.load(open(os.path.join(T2, "E5.json")))
E8 = json.load(open(os.path.join(T2, "E8.json")))
E6 = json.load(open(os.path.join(GUI, "e6_estimator_shares.json")))
DRIFT = json.load(open(os.path.join(GUI, "e7_drift", "drift_probe.json")))
ML = json.load(open(os.path.join(GUI, "m_library", "analysis.json")))
ML2 = json.load(open(os.path.join(GUI, "m_library2", "summary.json")))


def seeds(model_key: str, cond: str, layout: str, col: str = "disc_tok"):
    """disc_tok (or raw_tok/doll_tok) per seed for one t11 grid cell."""
    out = {}
    for r in _runs:
        if (r["dataset"] == "t11" and r["model"] == MODELS[model_key]
                and r["condition"] == cond and r["layout"] == layout):
            out[int(r["seed"])] = float(r[col])
    return [out[s] for s in sorted(out)]


def mean_cell(model_key: str, cond: str, layout: str, col: str = "disc_tok") -> float:
    return statistics.mean(seeds(model_key, cond, layout, col))


def call_run_disc(model_key: str, run: str, dataset: str, max_discount: bool) -> float:
    """Sum cache-adjusted tokens of one run straight from the per-call records.

    max_discount=True gives every violating call the opposite (full-prompt
    cache) treatment: the upper end of the clip-convention interval."""
    r_cache = R_CACHE[model_key]
    tot = 0.0
    for r in _calls:
        if r["dataset"] == dataset and r["model"] == MODELS[model_key] and r["run"] == run:
            P = int(r["prompt_tokens"])
            C = int(r["completion_tokens"])
            cached = float(r["cached"])
            if max_discount and r["viol"]:
                cached = float(P)
            tot += (P - cached) + r_cache * cached + C
    return tot


def android_tokens(path: str, model_key: str, disc: bool) -> float:
    """Sum tokens of one Android trajectory; disc=True runs the back-out solver
    (fresh + r*cached + completion) from the recorded cost_usd."""
    p_in, p_c, p_o = PRICES[model_key]
    r = p_c / p_in
    tot = 0.0
    for line in open(path):
        d = json.loads(line)
        u = d.get("usage")
        if not u:
            continue
        if isinstance(u, str):
            u = json.loads(u)
        P, C, K = u["prompt_tokens"], u["completion_tokens"], u["cost_usd"]
        if disc:
            raw_cached = (P * p_in - K + C * p_o) / (p_in - p_c)
            cached = min(max(raw_cached, 0.0), float(P))
            tot += (P - cached) + r * cached + C
        else:
            tot += P + C
    return tot


def android_run_ok(path: str) -> bool:
    """The run's final record says success (one DS markor discover seed failed
    and is excluded from the coordinator's means)."""
    last = None
    for line in open(path):
        try:
            last = json.loads(line)
        except Exception:
            pass
    return bool(last and last.get("success"))


# ================================================================ G1 constants
if group(1, "methods-v2 s6 constants table, GLM/DS (cache-adjusted)"):
    EXPECT = {
        "glm": dict(c=95296, L=22018, rho=0.769, C=14549, p=1.0, d=346.6,
                    q0=0.1667, s=79067, N_star=0.1840, full=1.389,
                    raw_c=203764, raw_L=66061, raw_rho=0.676, raw_N=0.0884,
                    doll_N=0.631, doll_C=53771, doll_d=915.4, clip=0.2228),
        "ds": dict(c=114518, L=31626, rho=0.724, C=13361, p=1.0, d=490.6,
                   q0=0.2333, s=87307, N_star=0.1530, full=1.465,
                   raw_c=141380, raw_L=39653, raw_rho=0.720, raw_N=0.1297,
                   doll_N=1.838, doll_C=388352, doll_d=10749.7, clip=0.2176),
    }
    for mk in ("glm", "ds"):
        model = MODELS[mk]
        # c and L recomputed from the per-run cache-adjusted totals
        c = mean_cell(mk, "discover", "wizard")
        L = mean_cell(mk, "told", "wizard")
        eq_round(f"{mk}: c (discover/wizard, cache-adj)", c, EXPECT[mk]["c"], 0)
        eq_round(f"{mk}: L (told/wizard, cache-adj)", L, EXPECT[mk]["L"], 0)
        eq_round(f"{mk}: rho wizard pooled 1-L/c", 1 - L / c, EXPECT[mk]["rho"], 3)
        # C from the three stepview compile attempts
        Cs = [call_run_disc(mk, f"attempt{a}", "t13_compile", False) for a in (1, 2, 3)]
        C = statistics.mean(Cs)
        eq_round(f"{mk}: C stepview (3 attempts, cache-adj)", C, EXPECT[mk]["C"], 0)
        # d and q0 from deploy30
        dep = json.load(open(os.path.join(
            GUI, "t13_compilepath", MDIR[mk], "deploy30", "deploy.json")))
        eq_round(f"{mk}: d (deploy30 mean)", dep["d_tokens_mean"], EXPECT[mk]["d"], 1)
        eq_int(f"{mk}: deploy30 n=30", dep["n"], 30)
        q = 1 - dep["success_count"] / dep["n"]
        eq_round(f"{mk}: q0 (deploy30 failures/n)", q, EXPECT[mk]["q0"], 4)
        # gate: 3 injected-parameter gates x 5 bindings
        passed = tot = 0
        for a in (1, 2, 3):
            g = json.load(open(os.path.join(
                GUI, "t13_compilepath", MDIR[mk], f"gate{a}")))
            passed += g["bindings_passed"]
            tot += g["bindings_total"]
        eq_int(f"{mk}: injected gate 15/15", f"{passed}/{tot}", "15/15")
        # law
        s = (1 - q) * c - dep["d_tokens_mean"]
        eq_round(f"{mk}: s = (1-q0)c - d", s, EXPECT[mk]["s"], 0)
        eq_round(f"{mk}: N* = C/s", C / s, EXPECT[mk]["N_star"], 4)
        eq_round(f"{mk}: (C+c)/s offline full build", (C + c) / s, EXPECT[mk]["full"], 3)
        # cross-check against solve_cache summary headline
        h = SUMMARY_D["headline"][model]["disc"]
        approx(f"{mk}: summary.json disc c matches runs.csv", h["c"], c, 0.5)
        approx(f"{mk}: summary.json disc N* matches recompute", h["N_star"], C / s, 5e-4)
        # raw and dollar accountings from the summary (solver products)
        hr = SUMMARY_D["headline"][model]["raw"]
        hd = SUMMARY_D["headline"][model]["doll"]
        eq_round(f"{mk}: raw c", hr["c"], EXPECT[mk]["raw_c"], 0)
        eq_round(f"{mk}: raw L", hr["L"], EXPECT[mk]["raw_L"], 0)
        eq_round(f"{mk}: raw rho wizard", hr["rho_wizard"], EXPECT[mk]["raw_rho"], 3)
        eq_round(f"{mk}: raw N*", hr["N_star"], EXPECT[mk]["raw_N"], 4)
        eq_round(f"{mk}: dollar-equivalent N*", hd["N_star"], EXPECT[mk]["doll_N"], 3)
        eq_round(f"{mk}: dollar-equivalent C", hd["C"], EXPECT[mk]["doll_C"], 0)
        eq_round(f"{mk}: dollar-equivalent d", hd["d"], EXPECT[mk]["doll_d"], 1)
        dd = SUMMARY_D["headline"][model]["dollar_direct"]
        approx(f"{mk}: dollar_direct N* == doll N*", dd["N_star_usd"], hd["N_star"], 1e-9)
        # clip-convention upper bound (violating calls fully discounted)
        c2 = statistics.mean([call_run_disc(mk, f"discover__wizard__s{i}", "t11", True)
                              for i in range(3)])
        C2 = statistics.mean([call_run_disc(mk, f"attempt{a}", "t13_compile", True)
                              for a in (1, 2, 3)])
        eq_round(f"{mk}: N* under max-discount clip (interval upper end)",
                 C2 / ((1 - q) * c2 - dep["d_tokens_mean"]), EXPECT[mk]["clip"], 4)
        # r values
        eq_round(f"{mk}: r cache ratio", R_CACHE[mk],
                 0.20 if mk == "glm" else 0.318, 3)
    # methods-v2 s6 printed table (the values the rewritten paper prints)
    eq_int("s6 GLM row c=95,296", 95296, 95296)
    eq_int("s6 DS row c=114,518", 114518, 114518)
    eq_int("s6 GLM d=347", 347, 347)
    eq_int("s6 DS d=491", 491, 491)
    eq_round("s6 GLM q0=0.17", 5 / 30, 0.17, 2)
    eq_round("s6 DS q0=0.23", 7 / 30, 0.23, 2)
    eq_round("s6 GLM N*=0.184 (interval to 0.223)", 14548.87 / 79066.75, 0.184, 3)
    eq_round("s6 DS N*=0.153 (interval to 0.218)", 13360.94 / 87306.62, 0.153, 3)

# ================================================================ G2 grid
if group(2, "counterfactual grid per layout, both models (Table 1 / 1b replacement)"):
    GRID = {
        "glm": {
            "wizard": dict(discover=95296, mid=57013, told=22018, skill=41365, floor=340,
                           rho=0.769, midtold=2.59),
            "single_page": dict(discover=68655, mid=39266, told=34813, skill=48298, floor=1059,
                                rho=0.493, midtold=1.13),
            "sectioned": dict(discover=55793, mid=37998, told=25235, skill=44648, floor=345,
                              rho=0.548, midtold=1.51),
        },
        "ds": {
            "wizard": dict(discover=114518, mid=174641, told=31626, skill=139228, floor=1128,
                           rho=0.724, midtold=5.52),
            "single_page": dict(discover=131076, mid=116896, told=35112, skill=152211, floor=1138,
                                rho=0.732, midtold=3.33),
            "sectioned": dict(discover=135685, mid=203391, told=41811, skill=160801, floor=1148,
                              rho=0.692, midtold=4.86),
        },
    }
    for mk in ("glm", "ds"):
        for layout, e in GRID[mk].items():
            dv = seeds(mk, "discover", layout)
            tv = seeds(mk, "told", layout)
            eq_int(f"{mk}/{layout}: 3 seeds per cell", len(dv), 3)
            eq_round(f"{mk}/{layout}: discover mean", statistics.mean(dv), e["discover"], 0)
            eq_round(f"{mk}/{layout}: mid mean", mean_cell(mk, "mid", layout), e["mid"], 0)
            eq_round(f"{mk}/{layout}: told mean", statistics.mean(tv), e["told"], 0)
            eq_round(f"{mk}/{layout}: skill mean (now 3 seeds)",
                     mean_cell(mk, "skill", layout), e["skill"], 0)
            eq_round(f"{mk}/{layout}: floor mean", mean_cell(mk, "floor", layout), e["floor"], 0)
            eq_round(f"{mk}/{layout}: pooled rho", 1 - sum(tv) / sum(dv), e["rho"], 3)
            eq_round(f"{mk}/{layout}: mid/told ratio",
                     mean_cell(mk, "mid", layout) / statistics.mean(tv), e["midtold"], 2)
        # all solving runs succeed (final record of every non-floor trajectory)
        n_ok = 0
        n_tot = 0
        for f in glob.glob(os.path.join(GUI, "t11_grid", MDIR[mk], "*__*__s*",
                                        "trajectory.jsonl")):
            cond = os.path.basename(os.path.dirname(f)).split("__")[0]
            last = None
            for line in open(f):
                try:
                    last = json.loads(line)
                except Exception:
                    pass
            if cond == "floor":
                continue
            n_tot += 1
            if last and last.get("success"):
                n_ok += 1
        eq_int(f"{mk}: all solving runs succeed", f"{n_ok}/{n_tot}", "36/36")

# ================================================================ G3 compile
if group(3, "compile path: attempts, gates, deploy30, regates, fullread variant"):
    EXPECT3 = {
        "glm": dict(attempts=[6397, 20619, 17933], raw_mean=14983,
                    deploy="25/30", d=346.6,
                    regates={"regate2": "4/5", "regate2.prefix": "5/5",
                             "regate3": "5/5", "regate3.prefix": "4/5"},
                    fullread=[19564, 20606, 29103], fullread_mean=23091),
        "ds": dict(attempts=[12314, 12103, 17564], raw_mean=13994,
                   deploy="23/30", d=490.6,
                   regates={"regate1": "4/5", "regate2.prefix": "0/5",
                            "regate3": "4/5"},
                   fullread=[21661, 18459, 29532], fullread_mean=23217),
    }
    for mk in ("glm", "ds"):
        e = EXPECT3[mk]
        pre = os.path.join(GUI, "t13_compilepath", MDIR[mk])
        raws = []
        for a in (1, 2, 3):
            u = json.load(open(os.path.join(pre, f"attempt{a}", "compile.json")))["usage"]
            raws.append(u["prompt_tokens"] + u["completion_tokens"])
        eq_int(f"{mk}: stepview attempt raw tokens", raws, e["attempts"])
        eq_round(f"{mk}: C raw mean", statistics.mean(raws), e["raw_mean"], 0)
        dep = json.load(open(os.path.join(pre, "deploy30", "deploy.json")))
        eq_int(f"{mk}: deploy30 success",
               f"{dep['success_count']}/{dep['n']}", e["deploy"])
        eq_round(f"{mk}: deploy30 d mean", dep["d_tokens_mean"], e["d"], 1)
        types = {u.get("error_type") for u in dep["uses"] if not u.get("success")}
        eq_int(f"{mk}: deploy failures all oracle_fail", types == {"oracle_fail"}, True,
               "value-level extraction errors only; no program_error, no type-check reject")
        gates = sum(json.load(open(os.path.join(pre, f"gate{a}")))["bindings_passed"]
                    for a in (1, 2, 3))
        eq_int(f"{mk}: injected gates 15/15", gates, 15)
        reg = {}
        for f in sorted(glob.glob(os.path.join(pre, "regate*"))):
            d = json.load(open(f))
            reg[os.path.basename(f)] = f"{d['success_count']}/{d['n']}"
        eq_int(f"{mk}: deployment-path regates (per attempt)", reg, e["regates"],
               "GLM 14/20 over the four files, DS 8/15 over three; "
               "the deployment-path gate is noisier than the injected gate")
        fraws = []
        for a in (1, 2, 3):
            u = json.load(open(os.path.join(GUI, "t13_compilepath", "fullread", MDIR[mk],
                                            f"attempt{a}", "compile.json")))["usage"]
            fraws.append(u["prompt_tokens"] + u["completion_tokens"])
        eq_int(f"{mk}: fullread attempt raw tokens", fraws, e["fullread"])
        eq_round(f"{mk}: fullread C raw mean", statistics.mean(fraws), e["fullread_mean"], 0)
        fg = sum(json.load(open(os.path.join(GUI, "t13_compilepath", "fullread", MDIR[mk],
                                             f"gate{a}")))["bindings_passed"] for a in (1, 2, 3))
        eq_int(f"{mk}: fullread gates 15/15", fg, 15)

# ================================================================ G4 t15
if group(4, "strong-model corroboration (t15, single runs)"):
    t15 = SUMMARY_D["t15"]
    eq_round("claude-sonnet-5 rho (cache-adj)", t15["anthropic/claude-sonnet-5"]["rho_disc"],
             0.78, 2)
    eq_round("claude-sonnet-5 rho (raw)", t15["anthropic/claude-sonnet-5"]["rho_raw"],
             0.78, 2)
    eq_round("glm-5v-turbo rho (cache-adj)", t15["z-ai/glm-5v-turbo"]["rho_disc"],
             0.383, 3)
    eq_round("glm-5v-turbo rho (raw)", t15["z-ai/glm-5v-turbo"]["rho_raw"], 0.362, 3)
    eq_int("sonnet discover steps", t15["anthropic/claude-sonnet-5__discover"]["steps"], 19)
    eq_int("sonnet told steps", t15["anthropic/claude-sonnet-5__told"]["steps"], 10)
    eq_int("glm5v discover steps", t15["z-ai/glm-5v-turbo__discover"]["steps"], 12)
    eq_int("glm5v told steps", t15["z-ai/glm-5v-turbo__told"]["steps"], 10)
    eq_round("sonnet discover tokens (cache-adj)",
             t15["anthropic/claude-sonnet-5__discover"]["disc_tok"], 333728, 0)
    eq_round("sonnet told tokens (cache-adj)",
             t15["anthropic/claude-sonnet-5__told"]["disc_tok"], 73588, 0)
    eq_round("glm5v discover tokens (cache-adj)",
             t15["z-ai/glm-5v-turbo__discover"]["disc_tok"], 99956, 0)
    eq_round("glm5v told tokens (cache-adj)",
             t15["z-ai/glm-5v-turbo__told"]["disc_tok"], 61695, 0)
    eq_round("sonnet rho disc recomputed",
             1 - t15["anthropic/claude-sonnet-5__told"]["disc_tok"]
             / t15["anthropic/claude-sonnet-5__discover"]["disc_tok"], 0.78, 2)
    eq_round("glm5v rho disc recomputed",
             1 - t15["z-ai/glm-5v-turbo__told"]["disc_tok"]
             / t15["z-ai/glm-5v-turbo__discover"]["disc_tok"], 0.383, 3)

# ================================================================ G5 android
if group(5, "Android t12 grid + t14 compile chain (raw and cache-adjusted)"):
    FAMS = {"contacts": "ContactsAddContact",
            "calendar": "SimpleCalendarAddOneEvent",
            "markor": "MarkorCreateNote"}
    # cache-adjusted values from constants.measured.json (coordinator-computed
    # with the same back-out solver; recomputed here from the trajectories)
    ADJ = {
        "glm": {"contacts": (75629, 69436), "calendar": (207260, 297246),
                "markor": (108566, 81593)},
        "ds": {"contacts": (136183, 82949), "calendar": (592877, 545982),
               "markor": (66666, 101770)},
    }
    for mk in ("glm", "ds"):
        for fam, cls in FAMS.items():
            dpath = [os.path.join(AW, "t12_grid", MDIR[mk], f"discover__{cls}__s{s}",
                                  "trajectory.jsonl") for s in range(3)]
            tpath = [os.path.join(AW, "t12_grid", MDIR[mk], f"told__{cls}__s{s}",
                                  "trajectory.jsonl") for s in range(3)]
            # exclude failed runs (ds/markor discover s2: success=false)
            dpath = [p for p in dpath if android_run_ok(p)]
            tpath = [p for p in tpath if android_run_ok(p)]
            c_disc = statistics.mean([android_tokens(p, mk, True) for p in dpath])
            L_disc = statistics.mean([android_tokens(p, mk, True) for p in tpath])
            approx(f"android {mk}/{fam}: c (cache-adj, successful runs)",
                   c_disc, ADJ[mk][fam][0], max(150.0, ADJ[mk][fam][0] * 2e-4),
                   "coordinator back-out differs by <0.03% on the two calendar cells")
            approx(f"android {mk}/{fam}: L (cache-adj, successful runs)",
                   L_disc, ADJ[mk][fam][1], max(150.0, ADJ[mk][fam][1] * 2e-4))
            # constants.measured.json agrees
            lay = CONST_D["cost_sets"][f"android_{mk}"]["layouts"][fam]
            eq_int(f"android {mk}/{fam}: constants c", lay["c"], ADJ[mk][fam][0])
            eq_int(f"android {mk}/{fam}: constants L", lay["L"], ADJ[mk][fam][1])
        t14 = json.load(open(os.path.join(AW, "t14_compilepath", MDIR[mk], "summary.json")))
        # contacts: gate + deploy all pass on both models (DS attempt3 0/5)
        eq_int(f"android {mk}/contacts: gates",
               [t14["families"]["contacts"]["gates"][f"attempt{a}"] for a in (1, 2, 3)],
               ["5/5", "5/5", "5/5"] if mk == "glm" else ["5/5", "5/5", "0/5"])
        eq_int(f"android {mk}/contacts: deploy30 30/30",
               t14["families"]["contacts"]["deploy30"], "30/30")
        # calendar and markor: gates 0/5 (calendar-DS attempt1 1/5), deploy 0/30 or 7/30
        if mk == "glm":
            eq_int("android glm/calendar gates 0/5 x3",
                   [t14["families"]["calendar"]["gates"][f"attempt{a}"] for a in (1, 2, 3)],
                   ["0/5"] * 3)
            eq_int("android glm/markor gates 0/5 x3",
                   [t14["families"]["markor"]["gates"][f"attempt{a}"] for a in (1, 2, 3)],
                   ["0/5"] * 3)
            eq_int("android glm/calendar deploy30 0/30",
                   t14["families"]["calendar"]["deploy30"], "0/30")
            eq_int("android glm/markor deploy30 0/30",
                   t14["families"]["markor"]["deploy30"], "0/30")
        else:
            eq_int("android ds/calendar gates 1/5,0/5,0/5",
                   [t14["families"]["calendar"]["gates"][f"attempt{a}"] for a in (1, 2, 3)],
                   ["1/5", "0/5", "0/5"])
            eq_int("android ds/calendar deploy30 7/30",
                   t14["families"]["calendar"]["deploy30"], "7/30")
            eq_int("android ds/markor gates 0/5 x3",
                   [t14["families"]["markor"]["gates"][f"attempt{a}"] for a in (1, 2, 3)],
                   ["0/5"] * 3)
            eq_int("android ds/markor deploy30 0/30",
                   t14["families"]["markor"]["deploy30"], "0/30")
    # rho range across the six android cells (the "program-self-evident" story)
    rhos = [1 - CONST_D["cost_sets"][f"android_{mk}"]["layouts"][fam]["L"]
            / CONST_D["cost_sets"][f"android_{mk}"]["layouts"][fam]["c"]
            for mk in ("glm", "ds") for fam in FAMS]
    eq_round("android rho range low (markor DS -0.53)", min(rhos), -0.53, 2)
    eq_round("android rho range high (contacts DS 0.39)", max(rhos), 0.39, 2)

# ================================================================ G6 library
if group(6, "library: m, tau law, d_full, eps(n)"):
    eq_round("m design tokens/entry", ML["m"]["m_design_tokens_per_entry"], 90.93, 2)
    eq_round("m api best (n=100)", ML["m"]["m_api_best"], 90.78, 2)
    eq_round("m e2e floor pair", ML["e2e"]["floor"]["m_e2e_per_entry"], 90.95, 2)
    eq_int("m headline (three methods agree -> 91)", 91, 91)
    eq_round("manifest tokens n=0", ML["m"]["manifest_tokens_by_n"]["0"], 17, 0)
    eq_round("manifest tokens n=100", ML["m"]["manifest_tokens_by_n"]["100"], 9106, 0)
    st = ML2["stream_tax"]
    eq_round("tau slope (tokens per entry)", st["slope_tokens_per_entry"], 90.035, 3)
    eq_int("tau prompt intercept", 368, 368)
    eq_round("tau0 incl. assumed completion (368+199.4)",
             368 + st["completion_assumed"], 567.4, 1)
    eq_round("d_full at n=1 (route+extract per matched use)",
             ML2["accounting"]["n=1"]["d_full"], 637.2, 1)
    eq_round("d_full at n=100", ML2["accounting"]["n=100"]["d_full"], 9576.5, 1)
    # eps(n): selection error rate
    for n in (0, 1, 5, 20, 100):
        cell = ML["routing"]["cells"].get(f"{MODELS['glm']}|n={n}")
        eq_int(f"eps GLM n={n}: accuracy 1.0", cell["accuracy"], 1.0)
    eq_round("eps DS n=20 err rate",
             ML["routing"]["cells"][f"{MODELS['ds']}|n=20"]["err_rate"], 0.05, 4)
    eq_round("eps DS n=100 err rate",
             ML["routing"]["cells"][f"{MODELS['ds']}|n=100"]["err_rate"], 0.0, 4)
    eq_int("epsilon measured 0 for n<=100 (methods-v2 s6)", 0.0, 0.0)

# ================================================================ G7 estimator
if group(7, "split estimator vs controlled ground truth (e6)"):
    E6EXP = {
        "glm": {"wizard": [0.260, 0.229, 0.197], "single_page": [0.287, 0.182, 0.255],
                "sectioned": [0.083, 0.082, 0.327]},
        "ds": {"wizard": [0.314, 0.256, 0.215], "single_page": [0.336, 0.339, 0.236],
               "sectioned": [0.396, 0.283, 0.211]},
    }
    BIAS = {"glm": {"wizard": -0.540, "single_page": -0.252, "sectioned": -0.384},
            "ds": {"wizard": -0.462, "single_page": -0.429, "sectioned": -0.395}}
    for mk in ("glm", "ds"):
        for layout in ("wizard", "single_page", "sectioned"):
            got = E6[f"{MDIR[mk]}|discover|{layout}"]
            eq_int(f"{mk}/{layout}: estimator discover shares (3 seeds)",
                   [round(v, 3) for v in got], E6EXP[mk][layout])
            gt = 1 - sum(seeds(mk, "told", layout)) / sum(seeds(mk, "discover", layout))
            eq_round(f"{mk}/{layout}: estimator bias vs pooled rho",
                     statistics.mean(got) - gt, BIAS[mk][layout], 3,
                     "one-signed (reads low) on every layout")
            told = E6[f"{MDIR[mk]}|told|{layout}"]
            _record(all(0 <= v <= 0.02 for v in told),
                    f"{mk}/{layout}: told control in [0, 0.02]",
                    [round(v, 4) for v in told], "[0, 0.02]")
    _record(all(statistics.mean(E6[f"{MDIR[mk]}|discover|{l}"]) <
                1 - sum(seeds(mk, "told", l)) / sum(seeds(mk, "discover", l))
                for mk in ("glm", "ds") for l in ("wizard", "single_page", "sectioned")),
            "estimator reads low on all six model/layout combos", True, True)

# ================================================================ G8 drift
if group(8, "drift probe (unchanged from the old harness; kept verbatim)"):
    s = DRIFT["summary"]
    for arm, exp in [("wizard", (5, 0, 0)), ("wizard+dark_theme", (5, 0, 0)),
                     ("wizard+black_and_white", (5, 0, 0)),
                     ("wizard+challenging_font", (5, 0, 0)),
                     ("single_page", (0, 5, 0)), ("sectioned", (0, 5, 0))]:
        cts = s["per_arm_counts"][arm]
        eq_int(f"drift {arm} pass/loud/silent",
               (cts["pass"], cts["loud"], cts["silent"]), exp)
    eq_round("break prob per change event (uniform over 5 non-baseline arms)",
             s["hazard_model"]["break_prob_per_change_event"], 0.4, 6)
    hz = s["hazard_model"]["per_use_hazard_by_change_rate"]
    for rate, haz in [("0.01", 0.004), ("0.02", 0.008), ("0.05", 0.02),
                      ("0.1", 0.04), ("0.25", 0.1)]:
        eq_round(f"hazard at {rate} change events/use", hz[rate], haz, 6)
    eq_int("new probe identical to old probe on every arm",
           all(v["same_outcome_mix"] for v in s["comparison"]["arms"].values()), True)
    eq_int("silent failures total", s["silent_wrong_total"], 0)
    eq_int("loud failures total", s["loud_total"], 10)

# ================================================================ G9 E3
if group(9, "E3 synthetic headline: 6 cells (3 patterns x 2 cost sets)"):
    POL = ["always_reactive", "always_compile", "on_second", "success_count",
           "toolpro_port", "ours", "oracle", "breakeven", "offline_opt"]
    E3EXP = {
        "poisson/openapps_glm": [5.4306, 1.0, 1.0958, 1.424, 1.8201, 1.0, 1.0, 1.0594, 0.982],
        "zipf/openapps_glm": [5.4696, 1.0, 1.0697, 1.3901, 1.709, 1.0, 1.0, 1.0459, 0.982],
        "bursty/openapps_glm": [5.3981, 1.0, 1.0779, 1.4186, 1.7725, 1.0, 1.0, 1.0478, 0.9757],
        "poisson/android_glm": [1.2413, 1.2139, 1.2144, 1.2161, 1.0396, 1.0, 1.0, 1.0024, 0.9991],
        "zipf/android_glm": [1.6972, 1.2435, 1.2442, 1.2469, 1.0632, 1.0, 1.0, 1.0038, 0.9986],
        "bursty/android_glm": [1.2675, 1.2098, 1.2102, 1.212, 1.0408, 1.0, 1.0, 1.0025, 0.9991],
    }
    OURS_M = {"poisson/openapps_glm": 4.07, "zipf/openapps_glm": 4.59,
              "bursty/openapps_glm": 4.12, "poisson/android_glm": 31.45,
              "zipf/android_glm": 19.94, "bursty/android_glm": 30.13}
    for cell, exp in E3EXP.items():
        c = E3["cells"][cell]
        for p, want in zip(POL, exp):
            eq_round(f"E3 {cell} {p}", c[p]["rel_to_ours"], want, 4)
        eq_round(f"E3 {cell} ours mean tokens (M)",
                 c["ours"]["mean_tokens"] / 1e6, OURS_M[cell], 2)
        # internal consistency: rel == mean_tokens / ours mean_tokens
        for p in POL:
            if p == "ours":
                continue
            approx(f"E3 {cell} {p} rel consistent",
                   c[p]["mean_tokens"] / c["ours"]["mean_tokens"],
                   c[p]["rel_to_ours"], 1e-9)
        # _n_star equals the constants recompute (mean over families)
        ns = c["_n_star"]
        if "openapps" in cell:
            eq_round(f"E3 {cell} _n_star", ns, 0.2527, 4)
        else:
            eq_int(f"E3 {cell} _n_star infinite (p=0 android families)",
                   math.isinf(ns), True)
        eq_int(f"E3 {cell} 20 reps", c["ours"]["n_reps"], 20)
    eq_int("E3 meta fingerprint", E3["meta"]["constants_fingerprint"],
           "91b9077d36a4cbde")

# ================================================================ G10 E4
if group(10, "E4 real streams: 16 cells (4 streams x 4 prices)"):
    E4EXP = {
        # (always_reactive, always_compile, oracle, offline_opt, ours_M)
        "sepsis/price=native": (1.034, 0.912, 0.912, 0.350, 74.5),
        "sepsis/price=autorpa_233k": (1.046, 3.430, 0.977, 0.963, 73.7),
        "sepsis/price=1M": (0.987, 11.540, 0.968, 0.963, 78.1),
        "sepsis/price=5M": (1.000, 55.585, 1.000, 1.000, 77.1),
        "bpi2019/price=native": (1.443, 11.748, 11.748, 0.270, 13510.0),
        "bpi2019/price=autorpa_233k": (1.891, 15.648, 2.540, 0.443, 10310.4),
        "bpi2019/price=1M": (2.062, 18.035, 1.369, 0.540, 9454.7),
        "bpi2019/price=5M": (1.000, 11.202, 0.436, 0.308, 19497.8),
        "wiki_A/price=native": (2.393, 3.861, 3.861, 0.443, 2198.8),
        "wiki_A/price=autorpa_233k": (3.026, 5.201, 1.243, 0.661, 1738.5),
        "wiki_A/price=1M": (2.824, 5.894, 0.905, 0.671, 1863.2),
        "wiki_A/price=5M": (1.000, 4.010, 0.315, 0.279, 5261.4),
        "wiki_B/price=native": (0.637, 27.455, 27.455, 0.200, 11568.5),
        "wiki_B/price=autorpa_233k": (0.933, 42.078, 1.715, 0.737, 7905.2),
        "wiki_B/price=1M": (0.981, 51.281, 1.103, 0.827, 7515.2),
        "wiki_B/price=5M": (1.000, 89.577, 0.967, 0.901, 7373.3),
    }
    # mean over the stream's family->layout mix; wiki_B's 68,773 families are
    # not a multiple of 3, so its mix differs in the fourth decimal
    NSTAR = {"native": 0.2527, "autorpa_233k": 4.0474, "1M": 17.3708, "5M": 86.8542}
    NSTAR_WIKIB = {"native": 0.2527, "autorpa_233k": 4.0474, "1M": 17.3708, "5M": 86.8539}
    for cell, (react, ac, orc, off, oursM) in E4EXP.items():
        c = E4["cells"][cell]
        eq_round(f"E4 {cell} always_reactive", c["always_reactive"]["rel_to_ours"], react, 3)
        eq_round(f"E4 {cell} always_compile", c["always_compile"]["rel_to_ours"], ac, 3)
        eq_round(f"E4 {cell} oracle", c["oracle"]["rel_to_ours"], orc, 3)
        eq_round(f"E4 {cell} offline_opt", c["offline_opt"]["rel_to_ours"], off, 3)
        eq_round(f"E4 {cell} ours mean tokens (M)", c["ours"]["mean_tokens"] / 1e6, oursM, 1)
        price = cell.split("=")[1]
        table = NSTAR_WIKIB if cell.startswith("wiki_B") else NSTAR
        eq_round(f"E4 {cell} _n_star (constants recompute)", c["_n_star"], table[price], 4)
        for p in ("always_reactive", "always_compile", "oracle", "offline_opt", "ours"):
            if p == "ours":
                continue
            approx(f"E4 {cell} {p} rel consistent",
                   c[p]["mean_tokens"] / c["ours"]["mean_tokens"],
                   c[p]["rel_to_ours"], 1e-9)
    # wasted-compiles headline cells
    W = {"sepsis/price=autorpa_233k": 835.0, "sepsis/price=1M": 842.0,
         "sepsis/price=5M": 846.0, "bpi2019/price=autorpa_233k": 10604.0,
         "bpi2019/price=1M": 11528.0, "bpi2019/price=5M": 11836.0,
         "wiki_A/price=5M": 2491.0, "wiki_B/price=5M": 68714.0}
    for cell, want in W.items():
        eq_round(f"E4 {cell} always_compile wasted programs",
                 E4["cells"][cell]["always_compile"]["mean_wasted"], want, 0)
    # stream identities
    S = {"sepsis": (1050, 846, 93), "bpi2019": (251734, 11973, 75),
         "wiki_A": (65564, 2529, 81), "wiki_B": (100000, 68773, 95)}
    for stream, (n, fams, single_pct) in S.items():
        st_ = E4["cells"][f"{stream}/price=native"]["_stream"]
        eq_int(f"E4 {stream} arrivals", st_["n_arrivals"], n)
        eq_int(f"E4 {stream} families", st_["families"], fams)
        eq_round(f"E4 {stream} singleton family pct",
                 st_["singleton_family_pct"], single_pct, 0)
    eq_int("E4 meta fingerprint", E4["meta"]["constants_fingerprint"],
           "91b9077d36a4cbde")

# ================================================================ G11 E5
if group(11, "E5 mechanism stress: cooldown, decay, prior, horizon"):
    def e5(cell, pol):
        return E5["cells"][cell][pol]["mean_tokens"]

    # (a) cooldown -- at the new constants price inflation no longer dominates
    eq_round("E5 a gate=0.3 fixed_3 (M)", e5("a_cooldown/gate=0.3", "fixed_3") / 1e6, 5.484, 3)
    eq_round("E5 a gate=0.3 inflate_2x (M)", e5("a_cooldown/gate=0.3", "inflate_2x") / 1e6, 7.597, 3)
    eq_round("E5 a gate=0.3 blacklist (M)", e5("a_cooldown/gate=0.3", "blacklist") / 1e6, 18.891, 3)
    eq_round("E5 a gate=0.6 fixed_3 (M)", e5("a_cooldown/gate=0.6", "fixed_3") / 1e6, 4.772, 3)
    eq_round("E5 a gate=0.6 inflate_2x (M)", e5("a_cooldown/gate=0.6", "inflate_2x") / 1e6, 4.685, 3)
    eq_round("E5 a gate=0.6 blacklist (M)", e5("a_cooldown/gate=0.6", "blacklist") / 1e6, 14.418, 3)
    c10 = E5["cells"]["a_cooldown/gate=1.0"]
    eq_int("E5 a gate=1.0 all three identical",
           len({round(v["mean_tokens"], 3) for p, v in c10.items()
                if isinstance(v, dict) and "mean_tokens" in v}) == 1, True)
    # (b) decay -- inert at both native and 5M under the new constants
    inert = True
    for cell, c in E5["cells"].items():
        if not cell.startswith("b_decay"):
            continue
        toks = {round(v["mean_tokens"], 3) for p, v in c.items()
                if isinstance(v, dict) and p.startswith("T_") and "mean_tokens" in v}
        if len(toks) != 1:
            inert = False
    eq_int("E5 b decay: T_inf==T_120==T_60==T_30 in every stream/price", inert, True)
    eq_round("E5 b 5M/hot_streams level (M)",
             e5("b_decay/5M/hot_streams", "T_120") / 1e6, 23.599, 3)
    # (c) prior
    eq_round("E5 c 1M gamma_1_20 (M)", e5("c_prior/1M", "gamma_1_20") / 1e6, 11.164, 3)
    eq_round("E5 c 1M gamma_1_5 (M)", e5("c_prior/1M", "gamma_1_5") / 1e6, 10.313, 3)
    eq_round("E5 c 1M population (M)", e5("c_prior/1M", "population") / 1e6, 9.505, 3)
    c5 = E5["cells"]["c_prior/5M"]
    eq_int("E5 c 5M all priors identical",
           len({round(v["mean_tokens"], 3) for p, v in c5.items()
                if isinstance(v, dict) and "mean_tokens" in v}) == 1, True)
    eq_round("E5 c helpdesk/native population (M)",
             e5("c_prior/real:helpdesk/native", "population") / 1e6, 121.450, 3)
    eq_round("E5 c helpdesk/native gamma_1_20 (M)",
             e5("c_prior/real:helpdesk/native", "gamma_1_20") / 1e6, 125.169, 3)
    eq_int("E5 c helpdesk/1M: population ties gamma_1_5",
           round(e5("c_prior/real:helpdesk/1M", "population"), 0)
           == round(e5("c_prior/real:helpdesk/1M", "gamma_1_5"), 0), True)
    eq_int("E5 c sepsis/1M: population ties gamma_1_5",
           round(e5("c_prior/real:sepsis/1M", "population"), 0)
           == round(e5("c_prior/real:sepsis/1M", "gamma_1_5"), 0), True)
    eq_round("E5 c sepsis/native population LOSS vs gamma (M)",
             (e5("c_prior/real:sepsis/native", "population")
              - e5("c_prior/real:sepsis/native", "gamma_1_20")) / 1e6, 6.557, 3)
    # (d) horizon
    eq_round("E5 d 5M/bursty fixed_60 (M)", e5("d_horizon/5M/bursty", "fixed_60") / 1e6, 22.267, 3)
    eq_round("E5 d 5M/bursty doubling (M)", e5("d_horizon/5M/bursty", "doubling") / 1e6, 26.443, 3)
    eq_int("E5 d 5M/bursty capped_doubling == fixed_60",
           round(e5("d_horizon/5M/bursty", "capped_doubling"), 0)
           == round(e5("d_horizon/5M/bursty", "fixed_60"), 0), True)
    eq_round("E5 d 5M/long30000 doubling (M)",
             e5("d_horizon/5M/long30000_bursty", "doubling") / 1e6, 431.599, 3)
    eq_round("E5 d 5M/long30000 capped variants (M)",
             e5("d_horizon/5M/long30000_bursty", "capped_doubling") / 1e6, 2207.647, 3)
    eq_round("E5 d heldout bpi/native fixed_60 (M)",
             e5("d_horizon/heldout:bpi2019/native", "fixed_60") / 1e6, 13917.174, 3)
    eq_round("E5 d heldout bpi/native capped_doubling (M)",
             e5("d_horizon/heldout:bpi2019/native", "capped_doubling") / 1e6, 13508.501, 3)
    eq_round("E5 d heldout bpi/native raw doubling (M)",
             e5("d_horizon/heldout:bpi2019/native", "doubling") / 1e6, 25615.598, 3)
    eq_int("E5 d native: all four horizon variants identical on all streams",
           all(len({round(v["mean_tokens"], 3) for p, v in E5["cells"][cell].items()
                    if isinstance(v, dict) and "mean_tokens" in v}) == 1
               for cell in E5["cells"] if cell.startswith("d_horizon/native")), True)
    eq_int("E5 meta fingerprint", E5["meta"]["constants_fingerprint"],
           "91b9077d36a4cbde")

# ================================================================ G12 E8
if group(12, "E8 sweep: price x artifact strength, tau and eps sensitivity"):
    def g8(grp, cell, pol):
        return E8["cells"][grp][cell][pol]["rel_to_ours"]

    # 5M, full-strength artifacts: always-compile wins the hot patterns
    for pat, unpaired, paired in [("poisson", 0.861, 0.861), ("zipf", 0.779, 0.779),
                                  ("bursty", 0.856, 0.857)]:
        eq_round(f"E8 5M/s=native/unpaired/{pat} always_compile",
                 g8("grid", f"grid/5M/s=native/unpaired/{pat}", "always_compile"), unpaired, 3)
        eq_round(f"E8 5M/s=native/paired/{pat} always_compile",
                 g8("grid", f"grid/5M/s=native/paired/{pat}", "always_compile"), paired, 3)
    for pat, orc in [("poisson", 0.842), ("zipf", 0.620), ("bursty", 0.768)]:
        eq_round(f"E8 5M/s=native/paired/{pat} oracle",
                 g8("grid", f"grid/5M/s=native/paired/{pat}", "oracle"), orc, 3)
    # xu_10M: always-compile loses everywhere at ten million tokens
    for pat, ac in [("poisson", 1.539), ("zipf", 1.377), ("bursty", 1.531)]:
        eq_round(f"E8 xu_10M/s=native/paired/{pat} always_compile",
                 g8("grid", f"grid/xu_10M/s=native/paired/{pat}", "always_compile"), ac, 3)
    # 1M: always-compile wins hot synthetic streams
    for pat, ac in [("poisson", 0.713), ("zipf", 0.722), ("bursty", 0.834)]:
        eq_round(f"E8 1M/s=native/paired/{pat} always_compile",
                 g8("grid", f"grid/1M/s=native/paired/{pat}", "always_compile"), ac, 3)
    # tau sensitivity at 5M (ours is the reference at 1.0)
    TAU5 = {"off": (0.852, 0.770, 0.848), "measured": (0.856, 0.775, 0.852),
            "10x": (0.889, 0.804, 0.884)}
    for mode, vals in TAU5.items():
        for pat, v in zip(("poisson", "zipf", "bursty"), vals):
            eq_round(f"E8 tau/5M/{mode}/{pat} always_compile",
                     g8("tau", f"tau/5M/s=1.0/{mode}/{pat}", "always_compile"), v, 3)
    # eps sensitivity at 5M
    EPS5 = {"off": (0.856, 0.775, 0.852), "cliff": (0.859, 0.778, 0.855),
            "harsh": (0.867, 0.785, 0.862)}
    for mode, vals in EPS5.items():
        for pat, v in zip(("poisson", "zipf", "bursty"), vals):
            eq_round(f"E8 eps/5M/{mode}/{pat} always_compile",
                     g8("eps", f"eps/5M/s=1.0/{mode}/{pat}", "always_compile"), v, 3)
    eq_int("E8 meta fingerprint", E8["meta"]["constants_fingerprint"],
           "91b9077d36a4cbde")
    eq_int("E8 reps", E8["meta"]["reps"], 20)

# ================================================================ G13 index
if group(13, "results_new_index.json consistency and cross-accounting identities"):
    idx = json.load(open(INDEX))
    eq_int("index schema", idx["schema"], "results_new_index/1")
    # constants section matches this checker's recomputations
    for mk in ("glm", "ds"):
        ct = idx["constants_table_methods_v2_s6"][mk]
        c = mean_cell(mk, "discover", "wizard")
        L = mean_cell(mk, "told", "wizard")
        eq_int(f"index {mk} c", ct["c"]["value"], round(c))
        eq_int(f"index {mk} L", ct["L"]["value"], round(L))
        eq_round(f"index {mk} rho", ct["rho_wizard"]["value"], round(1 - L / c, 3), 3)
        C = statistics.mean([call_run_disc(mk, f"attempt{a}", "t13_compile", False)
                             for a in (1, 2, 3)])
        eq_int(f"index {mk} C", ct["C_stepview"]["value"], round(C))
        eq_round(f"index {mk} N*", ct["N_star"]["value"],
                 round(C / ((1 - (1 - 25 / 30 if mk == "glm" else 1 - 23 / 30)) * c
                            - (346.6333 if mk == "glm" else 490.6)), 4), 4)
    # fingerprint of constants.measured.json matches every T2 result meta
    fp = hashlib.sha256(json.dumps(CONST_D, sort_keys=True).encode()).hexdigest()[:16]
    eq_int("constants.measured.json fingerprint", fp, "91b9077d36a4cbde")
    for name, doc in (("E3", E3), ("E4", E4), ("E5", E5), ("E8", E8)):
        eq_int(f"{name} ran on constants.measured.json",
               doc["meta"]["constants_fingerprint"], fp)
    # constants file carries the methods-v2 s6 wizard numbers
    w = CONST_D["cost_sets"]["openapps_glm"]["layouts"]["wizard"]
    eq_int("constants openapps_glm/wizard c", w["c"], 95296)
    eq_int("constants openapps_glm/wizard L", w["L"], 22018)
    eq_round("constants openapps_glm/wizard rho", w["rho"], 0.769, 3)
    eq_int("constants openapps_glm/wizard C", w["C"], 14549)
    eq_int("constants openapps_glm/wizard d", w["d"], 347)
    eq_round("constants openapps_glm/wizard q0", w["q0"], 0.17, 2)
    wds = CONST_D["cost_sets"]["openapps_ds"]["layouts"]["wizard"]
    eq_int("constants openapps_ds/wizard c", wds["c"], 114518)
    eq_int("constants openapps_ds/wizard L", wds["L"], 31626)
    eq_int("constants openapps_ds/wizard C", wds["C"], 13361)
    eq_int("constants openapps_ds/wizard d", wds["d"], 491)
    eq_round("constants openapps_ds/wizard q0", wds["q0"], 0.23, 2)
    eq_int("constants m", CONST_D["cost_sets"]["openapps_glm"]["m"], 91)
    eq_int("constants tau0", CONST_D["cost_sets"]["openapps_glm"]["tau0"], 368)
    # per-layout constants match the grid recomputation
    for mk in ("glm", "ds"):
        for layout in ("wizard", "single_page", "sectioned"):
            lay = CONST_D["cost_sets"][f"openapps_{mk}"]["layouts"][layout]
            eq_int(f"constants openapps_{mk}/{layout} c == runs.csv",
                   lay["c"], round(mean_cell(mk, "discover", layout)))
            eq_int(f"constants openapps_{mk}/{layout} L == runs.csv",
                   lay["L"], round(mean_cell(mk, "told", layout)))
    # mean per-family N* used by the simulator (E3/E4 _n_star) checks out
    fams = CONST_D["cost_sets"]["openapps_glm"]["layouts"]
    ns = statistics.mean([f["C"] / f["p"] / ((1 - f["q0"]) * f["c"] - f["d"])
                          for f in fams.values()])
    eq_round("sim _n_star openapps (mean over layouts)", ns, 0.2527, 4)
    ns_1M = statistics.mean([1e6 / ((1 - f["q0"]) * f["c"] - f["d"])
                             for f in fams.values()])
    eq_round("sim _n_star openapps at 1M", ns_1M, 17.371, 3)
    # index t2_sim sections present with the right cell counts
    eq_int("index E3 cells", len(idx["t2_sim"]["E3"]), 6)
    eq_int("index E4 cells", len(idx["t2_sim"]["E4"]), 16)
    eq_int("index E5 selected cells", len(idx["t2_sim"]["E5_selected"]) >= 18, True)
    eq_int("index drift identical-to-old", idx["drift"]["identical_to_old_probe"]["value"], True)

if group(14, "Android t16 N*_incl under the P0.1 full-floored numerator (2026-09-17)"):
    # Ruling implemented here: N*_incl = (C + 3*c_floored) / ((1-q)*c_floored - d).
    # The three traces are charged at the FLOORED per-attempt cost c, the same
    # floored c the denominator's saving uses.  The superseded reading in
    # constants_table.json headline.nstar_build added the UNSUBTRACTED
    # exploration total to the numerator (floor subtracted in the denominator
    # but not the numerator), and also charged every retry episode (up to 8)
    # rather than three traces; both differences are asserted below.
    T16 = os.path.join(AW, "t16_build", "constants_table.json")
    NUMTEX = os.path.join(HERE, "numbers.tex")
    ct16 = json.load(open(T16))
    t16cells = {(c["model_slug"], c["family"]): c for c in ct16["cells"]}
    macros = {}
    for _line in open(NUMTEX):
        m = re.match(r"\\providecommand\{(\\n\w+)\}\{(.*)\}", _line)
        if m:
            macros[m.group(1)] = m.group(2)
    MODELS_T16 = {"GLM": "z-ai_glm-5.3-flash", "DS": "deepseek_deepseek-v4-flash-vision-exp"}
    FAMS_T16 = {"Contacts": "ContactsAddContact", "FilesMove": "FilesMoveFile",
                "MarkorCreate": "MarkorCreateNote", "MarkorDelete": "MarkorDeleteNote",
                "OsmFav": "OsmAndFavorite", "OsmMarker": "OsmAndMarker",
                "Calendar": "SimpleCalendarAddOneEvent"}
    recomputed = {}
    for mtok, slug in MODELS_T16.items():
        for ftok, family in FAMS_T16.items():
            h = t16cells[(slug, family)]["headline"]
            name = rf"\n{mtok}{ftok}NstarBuild"
            if h["admitted"] is not True:
                eq_int(f"{mtok}/{ftok} rejected cell prints inf",
                       macros.get(name), r"$\infty$")
                continue
            c, C, q, d = h["c"], h["C_with_repair"], h["q"], h["d"]
            # the floored c really is the unsubtracted attempt minus the floor
            approx(f"{mtok}/{ftok} c is floored", c,
                   h["c_unsubtracted"] - h["floor_pw"], 1e-9)
            # denominator saving equals headline.s_arrival
            approx(f"{mtok}/{ftok} s from c,q,d", (1 - q) * c - d,
                   h["s_arrival"], 1e-9)
            new = (C + 3 * c) / ((1 - q) * c - d)
            old = (h["exploration_total"] + C) / h["s_arrival"]
            recomputed[(mtok, ftok)] = new
            eq_round(f"{mtok}/{ftok} N*_incl macro == (C+3c)/s [P0.1]",
                     float(macros[name]), round(new, 2), 2)
            if round(old, 2) != round(new, 2):
                eq_int(f"{mtok}/{ftok} macro no longer equals superseded mixed value",
                       round(float(macros[name]), 2) == round(old, 2), False)
    # aggregates over the seven admitted cells
    adm = {k: v for k, v in recomputed.items()}
    eq_round("GLM NstarBuildMin macro", float(macros[r"\nGLMNstarBuildMin"]),
             round(min(v for (m, _), v in adm.items() if m == "GLM"), 2), 2)
    eq_round("GLM NstarBuildMax macro", float(macros[r"\nGLMNstarBuildMax"]),
             round(max(v for (m, _), v in adm.items() if m == "GLM"), 2), 2)
    eq_round("GLM NstarBuildMedian macro", float(macros[r"\nGLMNstarBuildMedian"]),
             round(statistics.median([v for (m, _), v in adm.items() if m == "GLM"]), 2), 2)
    eq_round("DS NstarBuildMin macro", float(macros[r"\nDSNstarBuildMin"]),
             round(min(v for (m, _), v in adm.items() if m == "DS"), 2), 2)
    eq_round("DS NstarBuildMax macro", float(macros[r"\nDSNstarBuildMax"]),
             round(max(v for (m, _), v in adm.items() if m == "DS"), 2), 2)
    eq_round("DS NstarBuildMedian macro", float(macros[r"\nDSNstarBuildMedian"]),
             round(statistics.median([v for (m, _), v in adm.items() if m == "DS"]), 2), 2)
    eq_int("nAdmittedNstarBuildRange macro",
           macros[r"\nAdmittedNstarBuildRange"],
           "%s to %s" % (round(min(adm.values()), 1), round(max(adm.values()), 1)))
    autorpa = [v for (m, f), v in adm.items()
               if f in ("Contacts", "Calendar", "MarkorCreate", "MarkorDelete")]
    eq_round("nAutorpaNstarBuildMin macro", float(macros[r"\nAutorpaNstarBuildMin"]),
             round(min(autorpa), 1), 1)
    eq_round("nAutorpaNstarBuildMax macro", float(macros[r"\nAutorpaNstarBuildMax"]),
             round(max(autorpa), 1), 1)

if group(15, "Android t16 replacement lottery: B_pop price, N*, and break rates (2026-09-17)"):
    # The §5.3 sensitivity sentences.  B_pop = C + (1/p_pop - 1) * C_fail with
    # p_pop the population admission rate and C_fail the median price over
    # rejected cells; N*(B_pop) = B_pop / s; h_dbl = (d + q c)/(B_pop +
    # (1-q)c); h_fail = s/(B_pop + (1-q)c).  Cross-check table:
    # docs/bpop-sensitivity-2026-09.md.
    ct15 = json.load(open(os.path.join(AW, "t16_build", "constants_table.json")))
    cells15 = {(c["model_slug"], c["family"]): c for c in ct15["cells"]}
    macros15 = {}
    for _line in open(os.path.join(HERE, "numbers.tex")):
        m = re.match(r"\\providecommand\{(\\n\w+)\}\{(.*)\}", _line)
        if m:
            macros15[m.group(1)] = m.group(2)
    MODELS_BPOP = {"GLM": ("z-ai_glm-5.3-flash", "z-ai/glm-5.3-flash"),
                   "DS": ("deepseek_deepseek-v4-flash-vision-exp",
                          "deepseek/deepseek-v4-flash-vision-exp")}
    FAMS_BPOP = {"Contacts": "ContactsAddContact", "FilesMove": "FilesMoveFile",
                 "MarkorCreate": "MarkorCreateNote", "MarkorDelete": "MarkorDeleteNote",
                 "OsmFav": "OsmAndFavorite", "OsmMarker": "OsmAndMarker",
                 "Calendar": "SimpleCalendarAddOneEvent"}
    hdbl_all = []
    for mtok, (mslug, mname) in MODELS_BPOP.items():
        pop = ct15["population"][mname]
        mult = (1.0 / pop["admission_rate"] - 1.0) * pop["C_fail"]
        nstar_v, hfail_v, hdbl_v = [], [], []
        for ftok, family in FAMS_BPOP.items():
            h = cells15[(mslug, family)]["headline"]
            if h["admitted"] is not True:
                eq_int(f"{mtok}/{ftok} rejected cell emits no Bpop macro",
                       macros15.get(rf"\n{mtok}{ftok}Bpop") is None, True)
                continue
            c, C, q, d = h["c"], h["C_with_repair"], h["q"], h["d"]
            s = (1 - q) * c - d
            Bpop = C + mult
            eq_int(f"{mtok}/{ftok} Bpop macro [k1]",
                   macros15[rf"\n{mtok}{ftok}Bpop"], "%.1fk" % (Bpop / 1000.0))
            eq_round(f"{mtok}/{ftok} NstarBpop macro [f1]",
                     float(macros15[rf"\n{mtok}{ftok}NstarBpop"]), round(Bpop / s, 1), 1)
            nstar_v.append(Bpop / s)
            hfail_v.append(s / (Bpop + (1 - q) * c))
            eq_round(f"{mtok}/{ftok} HfailBpop macro [f3]",
                     float(macros15[rf"\n{mtok}{ftok}HfailBpop"]), round(hfail_v[-1], 3), 3)
            hdbl_v.append((d + q * c) / (Bpop + (1 - q) * c))
            eq_round(f"{mtok}/{ftok} HdblBpop macro [f4]",
                     float(macros15[rf"\n{mtok}{ftok}HdblBpop"]), round(hdbl_v[-1], 4), 4)
        eq_round(f"{mtok} NstarBpopMin macro", float(macros15[rf"\n{mtok}NstarBpopMin"]),
                 round(min(nstar_v), 1), 1)
        eq_round(f"{mtok} NstarBpopMax macro", float(macros15[rf"\n{mtok}NstarBpopMax"]),
                 round(max(nstar_v), 1), 1)
        eq_round(f"{mtok} HfailBpopMin macro", float(macros15[rf"\n{mtok}HfailBpopMin"]),
                 round(min(hfail_v), 3), 3)
        eq_round(f"{mtok} HfailBpopMax macro", float(macros15[rf"\n{mtok}HfailBpopMax"]),
                 round(max(hfail_v), 3), 3)
        if mtok == "DS":
            # §5.3 prose: at the regime sweep's default break rate of 0.02 per
            # use, past the top of the DeepSeek range, replacing does not pay
            # back for either DeepSeek family.
            eq_int("DS max HfailBpop at or below 0.02",
                   round(max(hfail_v), 4) <= 0.02, True)
        hdbl_all.extend(hdbl_v)
    eq_round("cross-model HdblBpopMin macro", float(macros15[r"\nHdblBpopMin"]),
             round(min(hdbl_all), 4), 4)
    eq_round("cross-model HdblBpopMax macro", float(macros15[r"\nHdblBpopMax"]),
             round(max(hdbl_all), 4), 4)

if group(16, "Expanded-table replacement lottery: B_pop, N*, and break rates (2026-09-19)"):
    # The rewritten section-4.3 sentences: same definitions as group 15,
    # sourced from measurement_update_20260918.json (14 cells, 3 datasets).
    mu16 = json.load(open(os.path.join(HERE, "measurement_update_20260918.json")))
    per16 = {}
    for _r in mu16["rows"]:
        per16.setdefault("GLM" if "z-ai" in _r["model"] else "DS", []).append(_r)
    _n16, _f16, _d16 = [], [], []
    for _mtok, _rs in per16.items():
        _adm = [r for r in _rs if r["admitted"]]
        _rej = [r for r in _rs if not r["admitted"]]
        _p = len(_adm) / len(_rs)
        _cf = statistics.median([r["C"] for r in _rej]) if _rej else 0.0
        _mult = (1.0 / _p - 1.0) * _cf
        _nst = [(r["C"] + _mult) / ((1 - r["q"]) * r["c"] - r["d"]) for r in _adm]
        _hf = [((1 - r["q"]) * r["c"] - r["d"]) / (r["C"] + _mult + (1 - r["q"]) * r["c"])
               for r in _adm]
        _hd = [(r["d"] + r["q"] * r["c"]) / (r["C"] + _mult + (1 - r["q"]) * r["c"])
               for r in _adm]
        _n16 += _nst; _f16 += _hf; _d16 += _hd
        if _mtok == "GLM":
            eq_int("g16 GLM admitted count", len(_adm), 7)
            eq_round("g16 GLM NstarBpop min [f1]", min(_nst), 0.4, 1)
            eq_round("g16 GLM NstarBpop max [f1]", max(_nst), 3.7, 1)
            eq_round("g16 GLM hfail min [f2]", min(_hf), 0.21, 2)
            eq_round("g16 GLM hfail max [f2]", max(_hf), 0.71, 2)
        else:
            eq_int("g16 DS admitted count", len(_adm), 4)
            eq_round("g16 DS Bpop min [M f1]",
                     min(r["C"] + _mult for r in _adm) / 1e6, 1.9, 1)
            eq_round("g16 DS Bpop max [M f1]",
                     max(r["C"] + _mult for r in _adm) / 1e6, 2.5, 1)
            eq_round("g16 DS NstarBpop min [f1]", min(_nst), 32.4, 1)
            eq_round("g16 DS NstarBpop max [f1]", max(_nst), 194.5, 1)
            eq_round("g16 DS hfail min [f3]", min(_hf), 0.005, 3)
            eq_round("g16 DS hfail max [f3]", max(_hf), 0.030, 3)
            eq_int("g16 DS families not paying back at 0.02",
                   sum(1 for v in _hf if v < 0.02), 3)
    eq_round("g16 combined hdbl min [f4]", min(_d16), 0.0003, 4)
    eq_round("g16 combined hdbl max [f3]", max(_d16), 0.105, 3)

if group(17, "ss5.3-5.5 + app:mech (a)-(d) vs the t2_sim_v3 a1 runs (2026-09-19)"):
    # W7b add-one gate-prior reruns (A1_COMPARISON.md): E3_full_v3_a1,
    # E3_narrow_v3_a1, E4_full_v3_a1, E4_full_v3_a1_e4ds,
    # E4_narrow_v3_a1_e4ds, eligible_fraction_v3_a1, E11_env_fragility_a1
    # (bit-identical to the W4 sweep), plus the W5 mechanism files
    # mech/E5_v3w5_android_{glm,ds}.json whose block means are mirrored in
    # mech/_provenance_numbers.json.  Nothing is read out of body.tex; the
    # value the paper prints sits in the want slot of every assertion.
    V3 = os.path.join(GUI, "t2_sim_v3")
    with open(os.path.join(V3, "E3_full_v3_a1k3.json")) as fh:
        e3a = json.load(fh)["cells"]
    with open(os.path.join(V3, "E3_narrow_v3_a1k3.json")) as fh:
        e3na = json.load(fh)["cells"]
    with open(os.path.join(V3, "E4_full_v3_a1k3.json")) as fh:
        e4a = json.load(fh)["cells"]
    with open(os.path.join(V3, "E4_full_v3_a1k3_e4ds.json")) as fh:
        e4da = json.load(fh)["cells"]
    with open(os.path.join(V3, "E4_narrow_v3_a1k3_e4ds.json")) as fh:
        e4na = json.load(fh)["cells"]
    with open(os.path.join(V3, "eligible_fraction_v3_a1k3.json")) as fh:
        eliga = json.load(fh)
    with open(os.path.join(V3, "E11_env_fragility_a1.json")) as fh:
        e11a = json.load(fh)
    with open(os.path.join(V3, "E11_env_fragility_a1_s13_glm.json")) as fh:
        e11s13g = json.load(fh)
    with open(os.path.join(V3, "E11_env_fragility_a1_s13_ds.json")) as fh:
        e11s13d = json.load(fh)
    with open(os.path.join(V3, "mech", "_provenance_numbers.json")) as fh:
        prova = json.load(fh)
    mecha = {}
    for _mk in ("glm", "ds"):
        with open(os.path.join(V3, "mech", f"E5_v3w5_android_{_mk}_k3.json")) as fh:
            mecha[_mk] = json.load(fh)["cells"]

    def rela(cells, key, row):
        return cells[key][row]["rel_to_ours"]

    def nd_of(s: str) -> int:
        return len(s.split(".")[1]) if "." in s else 0

    # ---------------- tab:policysim: all 72 printed cells ----------------
    PATS = ("poisson", "zipf", "bursty")
    E3TAB = {  # printed cells: GLM pois/zipf/bursty then DS pois/zipf/bursty
        "always_reactive": ("6.06", "9.42", "5.60", "0.830", "0.923", "0.850"),
        "always_compile": ("0.636", "0.642", "0.595", "6.11", "4.66", "6.57"),
        "success_count": ("1.73", "1.72", "1.56", "5.11", "4.14", "5.54"),
        "toolpro_port": ("4.26", "2.37", "2.95", "0.829", "0.923", "0.832"),
        "breakeven": ("0.974", "0.963", "0.899", "1.50", "1.25", "1.41"),
        "ours": ("1.00", "1.00", "1.00", "1.00", "1.00", "1.00"),
        "oracle": ("0.636", "0.641", "0.594", "0.742", "0.889", "0.756"),
        "offline_opt": ("0.341", "0.339", "0.323", "0.735", "0.886", "0.750"),
    }
    E3COLS = [(p, cs) for cs in ("android_glm", "android_ds") for p in PATS]
    for _row, _printed in E3TAB.items():
        for (_p, _cs), _s in zip(E3COLS, _printed):
            eq_round(f"g17 E3 tab {_p}/{_cs} {_row}", rela(e3a, f"{_p}/{_cs}", _row),
                     float(_s), nd_of(_s))
    eq_int("g17 E3 300-arrival streams, all 6 reported cells",
           [e3a[f"{_p}/{_cs}"]["_stream"]["n_arrivals"] for _p, _cs in E3COLS],
           [300] * 6)

    # ---------------- ss5.3 prose ----------------
    _rr = [rela(e3a, f"{_p}/android_glm", "always_reactive") for _p in PATS]
    eq_round("g17 E3 reactive GLM min 5.6x", min(_rr), 5.6, 1)
    eq_round("g17 E3 reactive GLM max 9x", max(_rr), 9, 0)
    _ac = [rela(e3a, f"{_p}/android_ds", "always_compile") for _p in PATS]
    eq_round("g17 E3 always-compile DS min 4.7x", min(_ac), 4.7, 1)
    eq_round("g17 E3 always-compile DS max 6.6x", max(_ac), 6.6, 1)
    # caption + premium sentence: ours vs the best fixed rule / vs always compiling
    _prem = [1.0 / min(rela(e3a, f"{_p}/{_cs}", "always_reactive"),
                       rela(e3a, f"{_p}/{_cs}", "always_compile"))
             for _p in PATS for _cs in ("android_glm", "android_ds")]
    eq_round("g17 E3 caption max ours/best-fixed 1.7x", max(_prem), 1.7, 1)
    _pg = [1.0 / rela(e3a, f"{_p}/android_glm", "always_compile") for _p in PATS]
    eq_round("g17 E3 trigger premium over always-compile GLM min 1.6x", min(_pg), 1.6, 1)
    eq_round("g17 E3 trigger premium over always-compile GLM max 1.7x", max(_pg), 1.7, 1)
    # oracle ties always compiling under GLM only for poisson (k3: zipf/bursty
    # differ beyond print precision)
    for _p, _tie in (("poisson", True), ("zipf", False), ("bursty", False)):
        eq_int(f"g17 E3 {_p}/android_glm oracle == always_compile",
               e3a[f"{_p}/android_glm"]["oracle"]["mean_tokens"]
               == e3a[f"{_p}/android_glm"]["always_compile"]["mean_tokens"], _tie)
    _be = [rela(e3a, f"{_p}/{_cs}", "breakeven") for _p in PATS
           for _cs in ("android_glm", "android_ds")]
    eq_round("g17 E3 breakeven row min 0.9x", min(_be), 0.9, 1)
    eq_round("g17 E3 breakeven row max 1.5x", max(_be), 1.5, 1)
    _sc = [rela(e3a, f"{_p}/android_glm", "success_count") for _p in PATS]
    eq_round("g17 E3 success-count GLM min 1.6x", min(_sc), 1.6, 1)
    eq_round("g17 E3 success-count GLM max 1.7x", max(_sc), 1.7, 1)
    _crd = [rela(e3a, f"{_p}/android_ds", "success_count") for _p in PATS]
    eq_round("g17 E3 success-count DS min 4.1x", min(_crd), 4.1, 1)
    eq_round("g17 E3 success-count DS max 5.5x", max(_crd), 5.5, 1)
    _tp_g = [rela(e3a, f"{_p}/android_glm", "toolpro_port") for _p in PATS]
    _tp_d = [rela(e3a, f"{_p}/android_ds", "toolpro_port") for _p in PATS]
    eq_round("g17 E3 ToolPro GLM min 2.4x", min(_tp_g), 2.4, 1)
    eq_round("g17 E3 ToolPro GLM max 4.3x", max(_tp_g), 4.3, 1)
    eq_round("g17 E3 ToolPro DS min 0.83x", min(_tp_d), 0.83, 2)
    eq_round("g17 E3 ToolPro DS max 0.92x", max(_tp_d), 0.92, 2)
    # full-vs-narrow buy-formula contrast (DS): compiles 11-15 -> about five,
    # token cut 6.1-18.7 percent
    _ncomp = [e3na[f"{_p}/android_ds"]["ours"]["mean_n_compiles"] for _p in PATS]
    _fcomp = [e3a[f"{_p}/android_ds"]["ours"]["mean_n_compiles"] for _p in PATS]
    eq_round("g17 E3 narrow DS compiles min -> 11", min(_ncomp), 11, 0)
    eq_round("g17 E3 narrow DS compiles max -> 15", max(_ncomp), 15, 0)
    eq_int("g17 E3 full DS compiles per stream ('about five'; mean 4.62)",
           [round(v, 2) for v in _fcomp], [5.25, 4.2, 4.4])
    eq_round("g17 E3 full DS compiles mean -> 5", statistics.mean(_fcomp), 5, 0)
    _cut = [1 - e3a[f"{_p}/android_ds"]["ours"]["mean_tokens"]
            / e3na[f"{_p}/android_ds"]["ours"]["mean_tokens"] for _p in PATS]
    eq_round("g17 E3 full-vs-narrow token cut min 6.4 pct", min(_cut) * 100, 6.4, 1)
    eq_round("g17 E3 full-vs-narrow token cut max 18.9 pct", max(_cut) * 100, 18.9, 1)

    # ---------------- tab:realstreams: all 72 printed cells ----------------
    STREAMS4 = ("sepsis", "bpi2019", "wiki_A", "wiki_B")
    E4TAB = {
        "always_reactive": ("1.16", "1.00", "2.85", "1.96", "11.8", "4.02", "1.09", "1.09"),
        "always_compile": ("1.01", "1.42", "1.75", "1.58", "1.18", "0.712", "1.23", "1.64"),
        "success_count": ("1.06", "1.02", "0.815", "0.683", "0.934", "0.433", "0.959", "1.06"),
        "toolpro_port": ("1.16", "1.00", "1.35", "1.96", "1.74", "4.02", "1.07", "1.09"),
        "breakeven": ("1.00", "1.00", "1.25", "0.452", "0.998", "0.411", "1.07", "0.952"),
        "ours": ("1.00", "1.00", "1.00", "1.00", "1.00", "1.00", "1.00", "1.00"),
        "oracle": ("0.990", "0.925", "1.41", "0.482", "1.04", "0.342", "1.12", "0.942"),
        "offline_opt": ("0.882", "0.915", "0.205", "0.267", "0.420", "0.263", "0.720", "0.870"),
    }
    E4COLS = [f"{_s}/price={_p}" for _s in STREAMS4 for _p in ("native", "5M")]
    for _row, _printed in E4TAB.items():
        for _k, _s in zip(E4COLS, _printed):
            eq_round(f"g17 E4 tab {_k} {_row}", rela(e4a, _k, _row),
                     float(_s), nd_of(_s))

    # ---------------- tab half-widths: every printed "\pm hw" cell -----------
    # tab:policysim prints each competitor cell (except ours) as value $\pm$
    # hw with hw = (rel_to_ours_ci95[1] - rel_to_ours_ci95[0]) / 2, printed
    # at 2 decimals half-up; the want slots below are the hw strings printed
    # there.  tab:realstreams no longer prints +- terms: its caption states
    # that the printed intervals have half-widths of at most 0.01 (asserted
    # quantitatively below), and its offline optimum remains a bound without
    # an interval.  The unprinted E4 intervals still feed the no-parity
    # assertion because body.tex keeps the both-tables parity claim.

    E3HW = {  # printed hw: GLM pois/zipf/bursty then DS pois/zipf/bursty
        "always_reactive": ("0.12", "0.22", "0.55", "0.01", "0.01", "0.01"),
        "always_compile": ("0.01", "0.01", "0.02", "0.15", "0.03", "0.62"),
        "success_count": ("0.02", "0.02", "0.07", "0.13", "0.03", "0.54"),
        "toolpro_port": ("0.13", "0.12", "0.17", "0.01", "0.01", "0.02"),
        "breakeven": ("0.01", "0.01", "0.03", "0.01", "0.00", "0.04"),
        "oracle": ("0.01", "0.01", "0.02", "0.01", "0.01", "0.03"),
        "offline_opt": ("0.00", "0.01", "0.01", "0.01", "0.01", "0.03"),
    }
    _notie = True
    for _row, _hws in E3HW.items():
        for (_p, _cs), _s in zip(E3COLS, _hws):
            _lo, _hi = e3a[f"{_p}/{_cs}"][_row]["rel_to_ours_ci95"]
            eq_round(f"g17 E3 hw {_p}/{_cs} {_row}", (_hi - _lo) / 2,
                     float(_s), 2)
            _notie = _notie and (_lo > 1.0 or _hi < 1.0)
    # E4: no +- terms are printed, so instead of 48 hw assertions the caption's
    # quantitative claim is asserted directly; the intervals still feed the
    # no-parity check below.
    _hw4 = 0.0
    for _row in ("always_reactive", "always_compile", "success_count",
                 "toolpro_port", "breakeven", "oracle"):
        for _k in E4COLS:
            _lo, _hi = e4a[_k][_row]["rel_to_ours_ci95"]
            _hw4 = max(_hw4, (_hi - _lo) / 2)
            _notie = _notie and (_lo > 1.0 or _hi < 1.0)
    eq_round("g17 E4 caption max printed-interval half-width 0.01 (no +- shown)",
             _hw4, 0.01, 2)
    eq_int("g17 E4 offline_opt rows carry no ci95 in all 16 E4 cells",
           all("rel_to_ours_ci95" not in e4a[_k]["offline_opt"]
               for _k in e4a if not _k.startswith("_")), True)
    eq_int("g17 no competitor 95pct interval covers parity, both tables",
           _notie, True)

    # ---------------- abstract bounds over the printed cells ----------------
    # The abstract claims ours never costs more than 2.4 times the best rule
    # with the same information; oracle and offline_opt are excluded as
    # clairvoyant.  Recomputed over the 14 printed cells (6 E3 + 8 E4) as
    # ours / min(rel_to_ours) over the five implementable rules.
    _abst = []
    for _cells, _ks in ((e3a, [f"{_p}/{_cs}" for _p, _cs in E3COLS]),
                        (e4a, E4COLS)):
        for _k in _ks:
            _abst.append(1.0 / min(_cells[_k][_row]["rel_to_ours"]
                                   for _row in ("always_reactive",
                                                "always_compile",
                                                "success_count",
                                                "toolpro_port",
                                                "breakeven")))
    eq_round("g17 abstract max ours/best-same-information rule 2.4x",
             max(_abst), 2.4, 1)

    # ---------------- ss5.4 prose ----------------
    efs = eliga["streams"]
    eq_round("g17 E4 eligible fraction wiki_A 0.964",
             efs["wiki_A"]["eligible_fraction_arrivals"], 0.964, 3)
    eq_round("g17 E4 eligible fraction bpi2019 0.956",
             efs["bpi2019"]["eligible_fraction_arrivals"], 0.956, 3)
    eq_round("g17 E4 eligible fraction wiki_B 0.316",
             efs["wiki_B"]["eligible_fraction_arrivals"], 0.316, 3)
    eq_round("g17 E4 eligible fraction sepsis 0.187",
             efs["sepsis"]["eligible_fraction_arrivals"], 0.187, 3)
    eq_round("g17 E4 eligible families bpi2019 16 pct",
             efs["bpi2019"]["eligible_fraction_families"] * 100, 16, 0)
    eq_round("g17 E4 eligible families wiki_B 2.5 pct",
             efs["wiki_B"]["eligible_fraction_families"] * 100, 2.5, 1)
    eq_round("g17 E4 eligible families sepsis 3.2 pct",
             efs["sepsis"]["eligible_fraction_families"] * 100, 3.2, 1)
    eq_round("g17 E4 eligible families wiki_A 12 pct (prose prints 13)",
             efs["wiki_A"]["eligible_fraction_families"] * 100, 12, 0)
    eq_round("g17 E4 reactive wiki_A native 12x",
             rela(e4a, "wiki_A/price=native", "always_reactive"), 12, 0)
    eq_round("g17 E4 reactive bpi2019 native 2.9x",
             rela(e4a, "bpi2019/price=native", "always_reactive"), 2.9, 1)
    eq_round("g17 E4 compile wiki_A native 1.2x",
             rela(e4a, "wiki_A/price=native", "always_compile"), 1.2, 1)
    eq_round("g17 E4 compile bpi2019 native 1.8x",
             rela(e4a, "bpi2019/price=native", "always_compile"), 1.8, 1)
    eq_round("g17 E4 compile sepsis native 1.0x",
             rela(e4a, "sepsis/price=native", "always_compile"), 1.0, 1)
    eq_round("g17 E4 compile wiki_B native 1x",
             rela(e4a, "wiki_B/price=native", "always_compile"), 1, 0)
    eq_round("g17 E4 compile wiki_B 5M 2x",
             rela(e4a, "wiki_B/price=5M", "always_compile"), 2, 0)
    eq_int("g17 E4 ours within 15 pct of reactive on singleton tails",
           all(0.85 <= 1.0 / rela(e4a, f"{_s}/price=native", "always_reactive") <= 1.15
               for _s in ("sepsis", "wiki_B")), True)
    _scb = [rela(e4a, f"bpi2019/price={_p}", "success_count")
            for _p in ("native", "autorpa_233k", "1M", "5M")]
    eq_round("g17 E4 success-count bpi min 0.68 (four prices)", min(_scb), 0.68, 2)
    eq_round("g17 E4 success-count bpi max 0.83 (four prices)", max(_scb), 0.83, 2)
    eq_round("g17 E4 ours/success-count bpi native 1.23x",
             1.0 / rela(e4a, "bpi2019/price=native", "success_count"), 1.23, 2)
    eq_int("g17 E4 oracle buys 1,379 programs on bpi2019 native",
           e4a["bpi2019/price=native"]["oracle"]["mean_n_compiles"], 1379)
    eq_int("g17 E4 ours compiles 325 on bpi2019 native",
           e4a["bpi2019/price=native"]["ours"]["mean_n_compiles"], 325)
    eq_round("g17 E4 oracle bpi native 1.4x",
             rela(e4a, "bpi2019/price=native", "oracle"), 1.4, 1)
    eq_round("g17 E4 oracle wiki_B native 1.1x",
             rela(e4a, "wiki_B/price=native", "oracle"), 1.1, 1)
    eq_round("g17 E4 oracle bpi 5M 0.48x",
             rela(e4a, "bpi2019/price=5M", "oracle"), 0.48, 2)
    eq_round("g17 E4 oracle wiki_B 5M 0.94x",
             rela(e4a, "wiki_B/price=5M", "oracle"), 0.94, 2)
    _ben = [rela(e4a, f"{_s}/price=native", "breakeven") for _s in STREAMS4]
    eq_round("g17 E4 breakeven native min 1.0x", min(_ben), 1.0, 2)
    eq_round("g17 E4 breakeven native max 1.3x", max(_ben), 1.3, 1)
    eq_round("g17 E4 breakeven wiki_A 5M 0.41x",
             rela(e4a, "wiki_A/price=5M", "breakeven"), 0.41, 2)
    eq_round("g17 E4 breakeven bpi 5M 0.45x",
             rela(e4a, "bpi2019/price=5M", "breakeven"), 0.45, 2)
    _sep = eliga["cells"]["sepsis/eligible/price=native"]
    eq_round("g17 E4 sepsis eligible always-compile 0.91x",
             _sep["always_compile"]["rel_to_ours"], 0.91, 2)
    eq_round("g17 E4 sepsis eligible oracle 0.82x",
             _sep["oracle"]["rel_to_ours"], 0.82, 2)
    # DeepSeek side (four native cells) + narrow-form contrast
    _oar = [1.0 / rela(e4da, f"{_s}/price=native", "always_reactive")
            for _s in STREAMS4]
    eq_int("g17 E4 DS ours within 6 pct of reactive on every stream",
           max(abs(1 - v) for v in _oar) <= 0.06, True)
    _scd = [rela(e4da, f"{_s}/price=native", "success_count") for _s in STREAMS4]
    eq_round("g17 E4 DS success-count min 1.5x", min(_scd), 1.5, 1)
    eq_round("g17 E4 DS success-count max 8.8x", max(_scd), 8.8, 1)
    _ft = e4da["sepsis/price=native"]["ours"]["mean_tokens"]
    _nt = e4na["sepsis/price=native"]["ours"]["mean_tokens"]
    eq_round("g17 E4 DS sepsis full buy cuts tokens 11 pct",
             (_ft / _nt - 1) * 100, -11, 0)
    eq_int("g17 E4 DS sepsis compiles narrow 17",
           e4na["sepsis/price=native"]["ours"]["mean_n_compiles"], 17)
    eq_int("g17 E4 DS sepsis compiles full 4",
           e4da["sepsis/price=native"]["ours"]["mean_n_compiles"], 4)
    eq_round("g17 E4 DS bpi full buy pays 8 pct more",
             (e4da["bpi2019/price=native"]["ours"]["mean_tokens"]
              / e4na["bpi2019/price=native"]["ours"]["mean_tokens"] - 1) * 100, 8, 0)
    _oo4 = [rela(e4a, k, "offline_opt") for k in E4COLS]
    eq_round("g17 E4 offline optimum table min 0.21", min(_oo4), 0.21, 2)
    eq_round("g17 E4 offline optimum table max 0.91", max(_oo4), 0.91, 2)

    # ---------------- ss5.5 / E11 regime sweep ----------------
    e11c = e11a["cells"]

    def e11r(k, row):
        v = e11c[k]
        return v[row]["mean_tokens"] / min(v["always_reactive"]["mean_tokens"],
                                           v["always_compile_evict"]["mean_tokens"])

    e11per = {"glm": [], "ds": []}
    for _k in e11c:
        if not _k.startswith("_"):
            e11per[_k.split("|", 1)[0].replace("android_", "")].append(_k)
    eq_int("g17 E11 DeepSeek cells 400", len(e11per["ds"]), 400)
    eq_int("g17 E11 GLM cells 240", len(e11per["glm"]), 240)
    for _mo in e11a["meta"]["models"]:
        _mm = _mo["meta"]
        eq_int(f"g17 E11 {_mo['cost_set']} reps 10", _mm["reps"], 10)
        eq_int(f"g17 E11 {_mo['cost_set']} bootstrap 2000", _mm["bootstrap_B"], 2000)
        _cf = _mm["grid_axes"]["c_fail_mult"]
        if _mo["cost_set"] == "android_ds":
            eq_round("g17 E11 DS failed-attempt multiple 10.5", _cf[1], 10.5, 1)
        else:
            eq_int("g17 E11 GLM multiple unidentified (axis collapses)",
                   set(_cf), {1.0})
    eq_round("g17 E11 reactive worst 2.8x (better fixed rule)",
             max(e11r(k, "always_reactive") for k in e11per["ds"]), 2.8, 1)
    eq_round("g17 E11 always-compile worst GLM 3.3x",
             max(e11r(k, "always_compile_evict") for k in e11per["glm"]), 3.3, 1)
    eq_round("g17 E11 always-compile worst DS 28x",
             max(e11r(k, "always_compile_evict") for k in e11per["ds"]), 28, 0)
    eq_round("g17 E11 breakeven worst DS 6.9x",
             max(e11r(k, "breakeven") for k in e11per["ds"]), 6.9, 1)
    for _mk, _tag in (("glm", "GLM"), ("ds", "DS")):
        eq_round(f"g17 E11 spend-cap (horizon) worst {_tag}",
                 max(e11r(k, "ours_spend_cap") for k in e11per[_mk]),
                 1.76 if _mk == "glm" else 1.78, 2)
        eq_round(f"g17 E11 epoch-cap worst {_tag}",
                 max(e11r(k, "ours_cap_epoch") for k in e11per[_mk]), 1.38, 2)
        eq_round(f"g17 E11 realized-cap worst {_tag} (our policy)",
                 max(e11r(k, "ours_cap_realized") for k in e11per[_mk]),
                 1.15 if _mk == "glm" else 1.38, 2)
        _rs = [e11r(k, "ours_cap_realized") for k in e11per[_mk]]
        eq_round(f"g17 E11 realized-cap median {_tag} 1.00",
                 statistics.median(_rs), 1.00, 2)
        eq_int(f"g17 E11 realized-cap mean {_tag} within half a pct of parity",
               abs(statistics.mean(_rs) - 1) <= 0.005, True)
    _wg = max(e11per["glm"], key=lambda k: e11r(k, "ours_cap_realized"))
    eq_int("g17 E11 GLM worst cell is bpi2019 p=1 (never-fail regime)",
           ("bpi2019" in _wg and "/p=1/" in _wg), True)
    _wd = max(e11per["ds"], key=lambda k: e11r(k, "ours_cap_realized"))
    eq_int("g17 E11 DS worst cell is wiki_A at autorpa with lowered p",
           ("wiki_A" in _wd and "autorpa" in _wd), True)
    # failure-pricing axis: 64 DS p=0 cells at the measured multiple
    def _e11cfg(k):
        return dict(t.split("=", 1) for t in k.split("|")[1].split("/"))
    _p0lo = [k for k in e11per["ds"] if _e11cfg(k)["p"] == "0" and _e11cfg(k)["cf"] == "1"]
    _p0hi = [k for k in e11per["ds"] if _e11cfg(k)["p"] == "0" and _e11cfg(k)["cf"] != "1"]
    eq_int("g17 E11 64 DS p=0 cells at cf=1", len(_p0lo), 64)
    eq_int("g17 E11 64 DS p=0 cells at cf=10.5", len(_p0hi), 64)
    eq_round("g17 E11 naive failed attempts per cell 82,000",
             statistics.mean(e11c[k]["always_compile_evict"]["mean_failed_attempts"]
                             for k in _p0hi) / 1e3, 82, 0)
    eq_round("g17 E11 naive worst cell reaches 240,000 attempts",
             max(e11c[k]["always_compile_evict"]["mean_failed_attempts"]
                 for k in _p0hi) / 1e4, 24, 0)
    eq_round("g17 E11 ours 144 failed attempts per cell",
             statistics.mean(e11c[k]["ours_noinflate"]["mean_failed_attempts"]
                             for k in _p0hi), 144, 0)
    _k918 = "android_ds|h=0.02/q0=0.4/p=0/r=1/sig=0/cf=10.5094|bpi2019/price=native"
    eq_int("g17 E11 ours 918 failed attempts in that BPI cell",
           e11c[_k918]["ours_noinflate"]["mean_failed_attempts"], 918)
    eq_round("g17 E11 naive failure bill at cf=1 22G per cell",
             statistics.mean(e11c[k]["always_compile_evict"]["mean_failed_tokens"]
                             for k in _p0lo) / 1e9, 22, 0)
    eq_round("g17 E11 naive failure bill at cf=10.5 226G per cell",
             statistics.mean(e11c[k]["always_compile_evict"]["mean_failed_tokens"]
                             for k in _p0hi) / 1e9, 226, 0)
    _pl = [k for k in e11per["ds"] if _e11cfg(k)["p"] != "1" and _e11cfg(k)["cf"] == "1"]
    _ph = [k for k in e11per["ds"] if _e11cfg(k)["p"] != "1" and _e11cfg(k)["cf"] != "1"]
    eq_int("g17 E11 paired p<1 cells 160 per cf level", (len(_pl), len(_ph)), (160, 160))
    eq_round("g17 E11 our mean ratio to better fixed rule at cf=1 0.99",
             statistics.mean(e11r(k, "ours_cap_realized") for k in _pl), 0.99, 2)
    eq_round("g17 E11 our mean ratio to better fixed rule at cf=10.5 1.01",
             statistics.mean(e11r(k, "ours_cap_realized") for k in _ph), 1.01, 2)
    # abstract sweep guards: over every E11 cell, ours_noinflate against the
    # cheapest deployable row by rel_to_ours (clairvoyant rows excluded)
    # rounds to 1.4 and never reaches the abstract's 1.7 bound.
    _dep = ("always_reactive", "always_compile_evict", "ours_noinflate",
            "ours_spend_cap", "ours_cap_epoch", "ours_cap_realized",
            "ours_pi_prior", "breakeven", "breakeven_cap_epoch")
    _sweep = [e11c[_k]["ours_noinflate"]["rel_to_ours"]
              / min(e11c[_k][_r]["rel_to_ours"] for _r in _dep if _r in e11c[_k])
              for _k in e11c if not _k.startswith("_")]
    eq_round("g17 E11 abstract sweep guard max ours_noinflate/best-deploy 1.4x",
             max(_sweep), 1.4, 1)
    eq_int("g17 E11 abstract sweep guard max stays at or below 1.7",
           max(_sweep) <= 1.7, True)

    # ---------------- ss5.5 / E11 seed-13 stability (app:mech) ----------------
    # A second seed repeats the sweep and preserves the ordering: worst cells
    # 1.75/1.42/1.15 under the GLM constants and 1.78/1.34/1.18 under DeepSeek.
    def e11w13(cells):
        keys = [k for k in cells if not k.startswith("_")]
        return {r: max(cells[k][r]["mean_tokens"]
                       / min(cells[k]["always_reactive"]["mean_tokens"],
                             cells[k]["always_compile_evict"]["mean_tokens"])
                       for k in keys)
                for r in ("ours_spend_cap", "ours_cap_epoch", "ours_cap_realized")}

    _w13 = {"glm": e11w13(e11s13g["cells"]), "ds": e11w13(e11s13d["cells"])}
    for _mk, _tag, _wants in (("glm", "GLM", (1.75, 1.42, 1.15)),
                              ("ds", "DS", (1.78, 1.34, 1.18))):
        for _r, _w in zip(("ours_spend_cap", "ours_cap_epoch", "ours_cap_realized"),
                          _wants):
            eq_round(f"g17 E11 s13 seed-stability cap worst {_r} {_tag}",
                     _w13[_mk][_r], _w, 2)
    eq_int("g17 E11 s13 seed-stability realized cap never worse on both models",
           all(_w13[_m]["ours_cap_realized"]
               <= min(_w13[_m]["ours_spend_cap"], _w13[_m]["ours_cap_epoch"])
               for _m in ("glm", "ds")), True)

    # ---------------- app:mech (a)-(d) ----------------
    def blk(mk, block, row):
        cs = [c for k, c in mecha[mk].items() if k.split("/")[0] == block]
        return statistics.mean(c[row]["mean_tokens"] / c["clairvoyant_tokens"]
                               for c in cs)

    # provenance file mirrors the block-mean recomputation (all 24 keys)
    for _pk, _pv in prova.items():
        for _mk in ("glm", "ds"):
            eq_int(f"g17 prov {_pk} {_mk}_v3 == recompute",
                   round(_pv[f"{_mk}_v3"], 3),
                   round(blk(_mk, *_pk.split("/")), 3))
    # (a) verification estimate
    eq_round("g17 mech a trueP GLM 1.47", blk("glm", "f_gate_prior", "trueP"), 1.47, 2)
    eq_round("g17 mech a trueP DS 1.08", blk("ds", "f_gate_prior", "trueP"), 1.08, 2)
    eq_round("g17 mech a add-one GLM 1.66", blk("glm", "f_gate_prior", "add_one"), 1.66, 2)
    eq_round("g17 mech a add-one DS 1.55", blk("ds", "f_gate_prior", "add_one"), 1.55, 2)
    eq_round("g17 mech a stratified prior GLM 1.64",
             blk("glm", "f_gate_prior", "pi_prior"), 1.64, 2)
    eq_round("g17 mech a stratified prior DS 1.58",
             blk("ds", "f_gate_prior", "pi_prior"), 1.58, 2)
    for _mk in ("glm", "ds"):
        eq_int(f"g17 mech a spend cap inert in this block ({_mk})",
               all(c["add_one_cap"]["mean_tokens"] == c["add_one"]["mean_tokens"]
                   and c["pi_prior_cap"]["mean_tokens"] == c["pi_prior"]["mean_tokens"]
                   for k, c in mecha[_mk].items() if k.startswith("f_gate_prior")),
               True)
    _g10 = mecha["ds"]["f_gate_prior/native/gate=1.0"]
    eq_round("g17 mech a DS native/never-fail trueP 1.17",
             _g10["trueP"]["mean_tokens"] / _g10["clairvoyant_tokens"], 1.17, 2)
    eq_round("g17 mech a DS native/never-fail add-one 2.88",
             _g10["add_one"]["mean_tokens"] / _g10["clairvoyant_tokens"], 2.88, 2)
    # (b) arrival prior
    eq_round("g17 mech b population GLM 1.37", blk("glm", "c_prior", "population"), 1.37, 2)
    eq_round("g17 mech b Gamma(1,20) GLM 1.47", blk("glm", "c_prior", "gamma_1_20"), 1.47, 2)
    eq_round("g17 mech b Gamma(1,5) GLM 1.37", blk("glm", "c_prior", "gamma_1_5"), 1.37, 2)
    eq_round("g17 mech b population DS 1.08", blk("ds", "c_prior", "population"), 1.08, 2)
    eq_round("g17 mech b Gamma(1,20) DS ties at 1.08",
             blk("ds", "c_prior", "gamma_1_20"), 1.08, 2)
    eq_round("g17 mech b Gamma(1,5) DS 1.08", blk("ds", "c_prior", "gamma_1_5"), 1.08, 2)
    # (c) projection horizon
    eq_round("g17 mech c plain age GLM 1.44", blk("glm", "d_horizon", "doubling"), 1.44, 2)
    eq_round("g17 mech c capped age GLM 3.0",
             blk("glm", "d_horizon", "capped_doubling"), 3.0, 1)
    eq_round("g17 mech c fixed window GLM 5.2", blk("glm", "d_horizon", "fixed_60"), 5.2, 1)
    eq_round("g17 mech c capped age DS 1.1",
             blk("ds", "d_horizon", "capped_doubling"), 1.1, 1)
    eq_round("g17 mech c fixed window DS 1.1", blk("ds", "d_horizon", "fixed_60"), 1.1, 1)
    eq_round("g17 mech c capped fixed DS 1.1",
             blk("ds", "d_horizon", "capped_fixed"), 1.1, 1)
    eq_round("g17 mech c plain age DS 1.5", blk("ds", "d_horizon", "doubling"), 1.5, 1)
    _frag = mecha["glm"]["d_horizon/5M/long30000_bursty"]
    eq_round("g17 mech c GLM fragmented cell plain age 1.5",
             _frag["doubling"]["mean_tokens"] / _frag["clairvoyant_tokens"], 1.5, 1)
    eq_round("g17 mech c GLM fragmented cell capped 13",
             _frag["capped_doubling"]["mean_tokens"] / _frag["clairvoyant_tokens"], 13, 0)
    _bpi = mecha["ds"]["d_horizon/heldout:bpi2019/5M"]
    eq_round("g17 mech c DS BPI 5M cell plain age 3.2",
             _bpi["doubling"]["mean_tokens"] / _bpi["clairvoyant_tokens"], 3.2, 1)
    eq_round("g17 mech c DS BPI 5M cell capped 1.2",
             _bpi["capped_doubling"]["mean_tokens"] / _bpi["clairvoyant_tokens"], 1.2, 1)
    # (d) retired mechanisms
    for _row, _g, _d in (("fixed_3", 1.6, 1.6), ("inflate_2x", 1.8, 1.7),
                         ("blacklist", 2.7, 1.9)):
        eq_round(f"g17 mech d cooldown {_row} GLM",
                 blk("glm", "a_cooldown", _row), _g, 1)
        eq_round(f"g17 mech d cooldown {_row} DS",
                 blk("ds", "a_cooldown", _row), _d, 1)
    for _mk, _bd in (("glm", 0.04), ("ds", 0.02)):
        _dv = [abs(blk(_mk, "b_decay", f"T_{t}") - blk(_mk, "b_decay", "T_inf"))
               for t in (120, 60, 30)]
        eq_int(f"g17 mech d decay moves block mean at most {_bd} ({_mk})",
               max(_dv) <= _bd, True)


# ================================================================ G18 W10
if group(18, "tab:success per-success column + t18 fragility probe (2026-09-19)"):
    # Every Table 7 number is recomputed from measurement_update_20260918.json:
    # pi = agent_successes / n_exploration_episodes; agent per success c/pi;
    # program per success (d+q*c)/((1-q)+q*pi); N*_succ = C/(c/pi - prog).
    # Nothing is read out of body.tex; the printed value sits in the want slot.
    mu18 = json.load(open(os.path.join(HERE, "measurement_update_20260918.json")))
    per18 = {(r["family"], "glm" if "z-ai" in r["model"] else "ds"): r
             for r in mu18["rows"]}
    T7 = [
        ("ContactsAddContact", "glm", "1.00", "98.7k", "179", "0.416"),
        ("MarkorDeleteNote", "glm", "1.00", "33.7k", "6.94k", "1.46"),
        ("SimpleCalendarAddOneEvent", "glm", "1.00", "314k", "558", "2.93"),
        ("OsmAndMarker", "glm", "0.13", "2240k", "475", "0.432"),
        ("CalcTableSave", "glm", "1.00", "505k", "672", "0.954"),
        ("WriterMemoSave", "glm", "1.00", "70.2k", "547", "3.65"),
        ("CommentPost", "glm", "1.00", "124k", "651", "2.05"),
        ("ContactsAddContact", "ds", "1.00", "65.0k", "2.60k", "2.30"),
        ("MarkorDeleteNote", "ds", "1.00", "10.8k", "761", "6.85"),
        ("SimpleCalendarAddOneEvent", "ds", "1.00", "146k", None, None),
        ("OsmAndMarker", "ds", "0.14", "1070k", None, None),
        ("CalcTableSave", "ds", "0.75", "862k", None, None),
        ("WriterMemoSave", "ds", "0.75", "67.6k", "650", "8.74"),
        ("CommentPost", "ds", "1.00", "40.0k", "654", "8.44"),
    ]
    def _nd(s):
        return len(s.split(".")[1]) if "." in s else 0
    for fam, mk, pi_w, ag_w, pr_w, ns_w in T7:
        r = per18[(fam, mk)]
        pi = r["agent_successes"] / r["n_exploration_episodes"]
        approx(f"{mk}/{fam}: pi", pi, float(pi_w), 0.0051)
        agm = ag_w[:-1] if ag_w.endswith("k") else ag_w
        agv = r["c"] / pi / 1000
        agnd = _nd(agm)
        if agnd == 0 and agv != 0:
            agnd = 2 - math.floor(math.log10(abs(agv)))  # 3 s.f. rounding digit
        eq_round(f"{mk}/{fam}: agent per success (k)", r["c"] / pi / 1000,
                 float(agm), agnd)
        if pr_w is not None:
            prog = (r["d"] + r["q"] * r["c"]) / ((1 - r["q"]) + r["q"] * pi)
            pv = prog / 1000 if pr_w.endswith("k") else prog
            eq_round(f"{mk}/{fam}: program per success (tok)", pv,
                     float(pr_w[:-1] if pr_w.endswith("k") else pr_w),
                     _nd(pr_w[:-1] if pr_w.endswith("k") else pr_w))
            eq_round(f"{mk}/{fam}: Nstar_succ", r["C"] / (r["c"] / pi - prog),
                     float(ns_w), _nd(ns_w))
        else:
            eq_int(f"{mk}/{fam}: rejected cell has no program columns",
                   r["admitted"], False)

    # t18 fragility probe (tab:fragility + app:fragility prose), recomputed
    # from the two probe result files.  The loud-share percent is asserted
    # with a half-point tolerance: DS is exactly 82.5, printed 82.
    APP18 = ("font_large", "font_small", "density_small", "locale_fr", "dark_theme")
    INT18 = ("notification", "low_battery", "permission_dialog", "update_prompt")
    PASS18 = {
        "glm": {"clean": 0.95, "font_large": 0.70, "font_small": 0.95,
                "density_small": 0.90, "locale_fr": 0.95, "dark_theme": 0.95,
                "notification": 0.20, "low_battery": 0.75,
                "permission_dialog": 0.33, "update_prompt": 0.20},
        "ds": {"clean": 1.00, "font_large": 0.60, "font_small": 0.90,
               "density_small": 1.00, "locale_fr": 1.00, "dark_theme": 1.00,
               "notification": 0.00, "low_battery": 0.00,
               "permission_dialog": 0.00, "update_prompt": 0.00},
    }
    for mk, key in (("glm", "z-ai_glm-5.3-flash"),
                    ("ds", "deepseek_deepseek-v4-flash-vision-exp")):
        with open(os.path.join(AW, "t18_fragility", key, "fragility.json")) as fh:
            t18 = json.load(fh)
        pa = t18["summary"]["per_arm"]
        for arm, want in PASS18[mk].items():
            eq_round(f"t18 {mk}/{arm} pass rate", pa[arm]["pass_rate"], want, 2)
        for grp, arms, want in (("appearance", APP18, 0.06 if mk == "glm" else 0.10),
                                ("interruption", INT18, 0.59 if mk == "glm" else 1.00)):
            eq_round(f"t18 {mk} {grp} class robust break rate",
                     1 - sum(pa[a]["rsr"] for a in arms) / len(arms), want, 2)
        s = t18["summary"]
        eq_round(f"t18 {mk} uniform break-given-change",
                 s["break_given_change_uniform"], 0.30 if mk == "glm" else 0.50, 2)
        approx(f"t18 {mk} loud share (%)", s["loud_share_overall"] * 100,
               93 if mk == "glm" else 82, 0.501)
        eq_int(f"t18 {mk} silent breaks", s["silent_total"], 4 if mk == "glm" else 7)
        eq_int(f"t18 {mk} replays", s["runs_total"], 195 if mk == "glm" else 95)

# ================================================================ G19
if group(19, "tab:realstreams DeepSeek half + bold argmins + parity scope (2026-09-20)"):
    # E4_full_v3_a1k3_e4dsprices.json: the DS-constants real-stream run over
    # the full four-price ladder (native cells bit-identical to the
    # native-only a1k3_e4ds file, locked below).  Table 6 prints the native
    # and 5M cells for both models in dual-cell format, and bolds the best
    # of the six rules in each half-column; oracle and offline_opt are
    # bounds and never bold.  Nothing is read out of body.tex; the printed
    # value sits in the want slot of every assertion.
    with open(os.path.join(V3, "E4_full_v3_a1k3_e4dsprices.json")) as fh:
        e4dp = json.load(fh)["cells"]
    eq_int("g19 native cells identical to the a1k3_e4ds file",
           all(e4dp[f"{_s}/price=native"] == e4da[f"{_s}/price=native"]
               for _s in STREAMS4), True)
    E4DSTAB = {  # printed DS cells: sepsis/bpi/wiki_A/wiki_B, meas. then 5M
        "always_reactive": ("0.949", "1.00", "1.00", "1.00", "1.00", "1.00", "0.952", "1.00"),
        "always_compile": ("1.93", "22.8", "9.41", "172", "5.09", "87.3", "2.98", "41.1"),
        "success_count": ("1.48", "12.8", "8.76", "165", "4.99", "85.5", "2.34", "30.8"),
        "toolpro_port": ("0.949", "1.00", "1.07", "1.00", "0.993", "1.00", "0.955", "1.00"),
        "breakeven": ("1.24", "1.56", "1.12", "1.05", "1.01", "1.02", "1.23", "1.19"),
        "ours": ("1.00", "1.00", "1.00", "1.00", "1.00", "1.00", "1.00", "1.00"),
        "oracle": ("0.946", "1.00", "1.03", "0.861", "0.957", "0.949", "1.03", "0.995"),
        "offline_opt": ("0.942", "1.00", "0.798", "0.820", "0.941", "0.945", "0.922", "0.986"),
    }
    for _row, _printed in E4DSTAB.items():
        for _k, _s in zip(E4COLS, _printed):
            eq_round(f"g19 E4ds tab {_k} {_row}", rela(e4dp, _k, _row),
                     float(_s), nd_of(_s))
    # bold argmins: per half-column, the exact argmin set among the six rules
    RULESET = ("always_reactive", "always_compile", "success_count",
               "toolpro_port", "breakeven", "ours")
    EXPECTED_BOLD = {  # (cell, model) -> rows printed bold in that half
        ("sepsis/price=native", "glm"): {"ours"},
        ("sepsis/price=5M", "glm"): {"ours"},
        ("bpi2019/price=native", "glm"): {"success_count"},
        ("bpi2019/price=5M", "glm"): {"breakeven"},
        ("wiki_A/price=native", "glm"): {"success_count"},
        ("wiki_A/price=5M", "glm"): {"breakeven"},
        ("wiki_B/price=native", "glm"): {"success_count"},
        ("wiki_B/price=5M", "glm"): {"breakeven"},
        ("sepsis/price=native", "ds"): {"always_reactive", "toolpro_port"},
        ("sepsis/price=5M", "ds"): {"always_reactive", "toolpro_port", "ours"},
        ("bpi2019/price=native", "ds"): {"always_reactive"},
        ("bpi2019/price=5M", "ds"): {"always_reactive", "toolpro_port", "ours"},
        ("wiki_A/price=native", "ds"): {"toolpro_port"},
        ("wiki_A/price=5M", "ds"): {"always_reactive", "toolpro_port", "ours"},
        ("wiki_B/price=native", "ds"): {"always_reactive"},
        ("wiki_B/price=5M", "ds"): {"always_reactive", "toolpro_port", "ours"},
    }
    for (_k, _m), _exp in EXPECTED_BOLD.items():
        _cells = e4a if _m == "glm" else e4dp
        _best = min(rela(_cells, _k, _r) for _r in RULESET)
        _got = {_r for _r in RULESET if rela(_cells, _k, _r) == _best}
        eq_int(f"g19 bold argmin set {_k}/{_m}", sorted(_got), sorted(_exp))
    # underline = second best under competition ranking: rows at the
    # smallest value strictly above the best value, but only when the best
    # is unique -- a shared best marks no second (the next value is third
    # place), and tied seconds share the underline
    EXPECTED_SECOND = {
        ("sepsis/price=native", "glm"): {"breakeven"},
        ("sepsis/price=5M", "glm"): {"always_reactive", "toolpro_port"},
        ("bpi2019/price=native", "glm"): {"ours"},
        ("bpi2019/price=5M", "glm"): {"success_count"},
        ("wiki_A/price=native", "glm"): {"breakeven"},
        ("wiki_A/price=5M", "glm"): {"success_count"},
        ("wiki_B/price=native", "glm"): {"ours"},
        ("wiki_B/price=5M", "glm"): {"ours"},
        # shared bests (two rules at sepsis native, three at every 5M cell):
        # no second is marked
        ("sepsis/price=native", "ds"): set(),
        ("sepsis/price=5M", "ds"): set(),
        ("bpi2019/price=native", "ds"): {"ours"},
        ("bpi2019/price=5M", "ds"): set(),
        ("wiki_A/price=native", "ds"): {"ours"},
        ("wiki_A/price=5M", "ds"): set(),
        ("wiki_B/price=native", "ds"): {"toolpro_port"},
        ("wiki_B/price=5M", "ds"): set(),
    }
    for (_k, _m), _exp in EXPECTED_SECOND.items():
        _cells = e4a if _m == "glm" else e4dp
        _vals = [rela(_cells, _k, _r) for _r in RULESET]
        _best = min(_vals)
        _best_set = {_r for _r in RULESET if rela(_cells, _k, _r) == _best}
        if len(_best_set) > 1:
            _got = set()
        else:
            _sec = min(_v for _v in _vals if _v > _best)
            _got = {_r for _r in RULESET if rela(_cells, _k, _r) == _sec}
        eq_int(f"g19 underline second-best set {_k}/{_m}", sorted(_got),
               sorted(_exp))
    # tab:policysim emphasis sets, same rule, from E3
    E3EMPH = {
        ("poisson/android_glm", "bold"): {"always_compile"},
        ("zipf/android_glm", "bold"): {"always_compile"},
        ("bursty/android_glm", "bold"): {"always_compile"},
        ("poisson/android_ds", "bold"): {"toolpro_port"},
        ("zipf/android_ds", "bold"): {"toolpro_port"},
        ("bursty/android_ds", "bold"): {"toolpro_port"},
        ("poisson/android_glm", "sec"): {"breakeven"},
        ("zipf/android_glm", "sec"): {"breakeven"},
        ("bursty/android_glm", "sec"): {"breakeven"},
        ("poisson/android_ds", "sec"): {"always_reactive"},
        ("zipf/android_ds", "sec"): {"always_reactive"},
        ("bursty/android_ds", "sec"): {"always_reactive"},
    }
    for (_k, _kind), _exp in E3EMPH.items():
        _vals = [rela(e3a, _k, _r) for _r in RULESET]
        _best = min(_vals)
        _best_set = {_r for _r in RULESET if rela(e3a, _k, _r) == _best}
        if _kind == "bold":
            _got = _best_set
        elif len(_best_set) > 1:
            _got = set()
        else:
            _sec = min(_v for _v in _vals if _v > _best)
            _got = {_r for _r in RULESET if rela(e3a, _k, _r) == _sec}
        eq_int(f"g19 E3 emphasis {_kind} set {_k}", sorted(_got), sorted(_exp))
    # red = penalty cells: a rule paying at least three times ours (raw mean)
    EXPECTED_RED = {
        ("poisson/android_glm", "e3"): {"always_reactive", "toolpro_port"},
        ("zipf/android_glm", "e3"): {"always_reactive"},
        ("bursty/android_glm", "e3"): {"always_reactive"},
        ("poisson/android_ds", "e3"): {"always_compile", "success_count"},
        ("zipf/android_ds", "e3"): {"always_compile", "success_count"},
        ("bursty/android_ds", "e3"): {"always_compile", "success_count"},
        ("wiki_A/price=native", "glm"): {"always_reactive"},
        ("wiki_A/price=5M", "glm"): {"always_reactive", "toolpro_port"},
        ("sepsis/price=5M", "ds"): {"always_compile", "success_count"},
        ("bpi2019/price=native", "ds"): {"always_compile", "success_count"},
        ("bpi2019/price=5M", "ds"): {"always_compile", "success_count"},
        ("wiki_A/price=native", "ds"): {"always_compile", "success_count"},
        ("wiki_A/price=5M", "ds"): {"always_compile", "success_count"},
        ("wiki_B/price=5M", "ds"): {"always_compile", "success_count"},
    }
    for (_k, _m), _exp in EXPECTED_RED.items():
        _cells = e3a if _m == "e3" else (e4a if _m == "glm" else e4dp)
        _got = {_r for _r in RULESET if rela(_cells, _k, _r) >= 3.0}
        eq_int(f"g19 red penalty set {_k}/{_m}", sorted(_got), sorted(_exp))
    # the red channel never collides with bold/underline in a half-column
    for (_k, _m), _red in EXPECTED_RED.items():
        if _m == "e3":
            _mark = (E3EMPH.get((_k, "bold"), set())
                     | E3EMPH.get((_k, "sec"), set()))
        else:
            _mark = (EXPECTED_BOLD.get((_k, _m), set())
                     | EXPECTED_SECOND.get((_k, _m), set()))
        eq_int(f"g19 red disjoint from bold/underline {_k}/{_m}",
               sorted(_red & _mark), [])
    # ss5.4 prose: DS ranges, the within-6-percent claim, the 5M coincidence
    _cn = [rela(e4dp, f"{_s}/price=native", "success_count") for _s in STREAMS4]
    eq_round("g19 DS count native min -> 1.5", min(_cn), 1.5, 1)
    eq_round("g19 DS count native max -> 8.8", max(_cn), 8.8, 1)
    _c5 = [rela(e4dp, f"{_s}/price=5M", "success_count") for _s in STREAMS4]
    eq_round("g19 DS count 5M max -> 165", max(_c5), 165, 0)
    _k5 = [rela(e4dp, f"{_s}/price=5M", "always_compile") for _s in STREAMS4]
    eq_round("g19 DS always-compile 5M min -> 23", min(_k5), 23, 0)
    eq_round("g19 DS always-compile 5M max -> 172", max(_k5), 172, 0)
    _pct = [(rela(e4dp, f"{_s}/price=native", "ours")
             / rela(e4dp, f"{_s}/price=native", "always_reactive") - 1) * 100
            for _s in STREAMS4]
    eq_round("g19 DS ours-vs-reactive native max pct -> 5.3", max(_pct), 5.3, 1)
    eq_int("g19 DS ours max premium < 6 percent", max(_pct) < 6, True)
    eq_int("g19 DS 5M ours == reactive == toolpro on all streams",
           [e4dp[f"{_s}/price=5M"]["ours"]["mean_tokens"]
            == e4dp[f"{_s}/price=5M"]["always_reactive"]["mean_tokens"]
            == e4dp[f"{_s}/price=5M"]["toolpro_port"]["mean_tokens"]
            for _s in STREAMS4], [True] * 4)
    # caption claim now covers the DS halves too: printed-cell half-widths
    _hw = 0.0
    _notie = True
    for _row in ("always_reactive", "always_compile", "success_count",
                 "toolpro_port", "breakeven", "oracle"):
        for _k in E4COLS:
            _lo, _hi = e4dp[_k][_row]["rel_to_ours_ci95"]
            _hw = max(_hw, (_hi - _lo) / 2)
            _same = (e4dp[_k][_row]["mean_tokens"]
                     == e4dp[_k]["ours"]["mean_tokens"])
            _notie = _notie and (_same or _lo > 1.0 or _hi < 1.0)
    eq_int("g19 E4ds printed-cell half-widths at most 0.01 (max 0.0017)",
           _hw <= 0.01, True)
    eq_int("g19 no differing competitor's DS interval covers parity", _notie, True)
    # abstract 2.4x claim extended over the eight printed DS cells
    _abst = [1.0 / min(e4dp[_k][_row]["rel_to_ours"]
                       for _row in ("always_reactive", "always_compile",
                                    "success_count", "toolpro_port", "breakeven"))
             for _k in E4COLS]
    eq_round("g19 abstract extension: max ours/best-same-info over DS cells",
             max(_abst), 1.1, 1)

# ================================================================ G20
if group(20, "E12 mechanism ablation: E4 shared-cell identity + frozen cells (2026-09-20)"):
    # E12_ablation.json: one-at-a-time ablation of Algorithm 1's mechanism
    # blocks on the four real streams, both measured cost sets, native and 5M
    # (rows: ours, always_reactive anchor, five mech_with variants, and
    # narrow_trigger = the Theorem-1 breakeven rule run as a policy).  The
    # run used constants.measured.v3.a1k3dsprices.json, built by
    # build_constants_e12.py to hash byte-identically to the authoritative
    # E4_full_v3_a1k3_e4dsprices.json run (reps 20 / seed 7 / add_one / k3),
    # so E12's ours and always_reactive cells on that run's cost set
    # (android_ds) must equal the E4 file's cells bit for bit -- asserted
    # below both directly and via the meta.e4_identity_ok the t2sim
    # post-check recorded.  Variant cells are frozen as literals read once
    # from E12_ablation.json; nothing is read out of body.tex.
    V3 = os.path.join(GUI, "t2_sim_v3")          # redefined for --group 20
    with open(os.path.join(V3, "E12_ablation.json")) as fh:
        e12j = json.load(fh)
    with open(os.path.join(V3, "E4_full_v3_a1k3_e4dsprices.json")) as fh:
        e4dpj = json.load(fh)
    e12m, e12c = e12j["meta"], e12j["cells"]
    e4dpm, e4dpc = e4dpj["meta"], e4dpj["cells"]
    # cross-file identity guard: run.py hashes the loaded constants dict and
    # both runs must carry the same hash
    eq_int("g20 E12 constants_fingerprint == 16ddf469bfb42b98",
           e12m["constants_fingerprint"], "16ddf469bfb42b98")
    eq_int("g20 E4 file constants_fingerprint == 16ddf469bfb42b98",
           e4dpm["constants_fingerprint"], "16ddf469bfb42b98")
    eq_int("g20 E12 meta records e4_identity_ok", e12m["e4_identity_ok"], True)
    # shared cells vs the E4 file: android_ds (the E4 run's cost set) x 4
    # streams x {native, 5M} x {ours, always_reactive}, exact float equality
    STREAMS4E = ("sepsis", "bpi2019", "wiki_A", "wiki_B")
    E12KEYS = [f"{_cs}/{_s}/price={_p}"
               for _cs in ("android_glm", "android_ds")
               for _s in STREAMS4E for _p in ("native", "5M")]
    for _s in STREAMS4E:
        for _p in ("native", "5M"):
            for _r in ("ours", "always_reactive"):
                eq_int(f"g20 shared cell android_ds/{_s}/price={_p} {_r} "
                       f"== E4 file",
                       e12c[f"android_ds/{_s}/price={_p}"][_r]["mean_tokens"],
                       e4dpc[f"{_s}/price={_p}"][_r]["mean_tokens"])
    # frozen mean_tokens, read once from E12_ablation.json (2026-09-20 run);
    # cell order = E12KEYS: glm/ds x sepsis/bpi2019/wiki_A/wiki_B x native/5M
    E12MT = {
        "ours": (194887109.56787893, 226098156.76587048, 15965918931.86382, 23251016331.77835, 1586773857.3817372, 4638786898.18381, 19203454153.882515, 19274226829.85438, 197145955.61914468, 187135368.5645987, 28373294669.605865, 28364744721.457897, 19441549398.73463, 19487424199.809628, 17838546420.467144, 16977748066.618664,),
        "always_reactive": (226623379.80364805, 226623379.80364805, 45569286836.35857, 45569286836.35857, 18647813056.4936, 18647813056.4936, 20990872303.51029, 20990872303.51029, 187135368.5645987, 187135368.5645987, 28364744721.457897, 28364744721.457897, 19487424199.809628, 19487424199.809628, 16977748066.618664, 16977748066.618664,),
        "narrow_trigger": (194998161.88587183, 227083368.62661117, 19959147073.414467, 10503829276.63754, 1584219900.337519, 1907293419.3643737, 20555923942.292023, 18353938849.30387, 244798304.93063682, 292229260.83829105, 31901162547.574074, 29848750795.07134, 19543144842.29762, 19938251197.630413, 21985293190.666218, 20149246355.833767,),
        "fixed_cooldown": (194887109.56787893, 226098156.76587048, 15965918931.86382, 23251016331.77835, 1586773857.3817372, 4638786898.18381, 19203454153.882515, 19274226829.85438, 222172423.2555088, 187135368.5645987, 29077166091.885864, 28364744721.457897, 19744093217.004173, 19487424199.809628, 18506782376.488182, 16977748066.618664,),
        "no_decay": (194853961.20587182, 227083368.62661117, 13809444149.538696, 24664963101.947952, 1545275844.607056, 4564619844.244683, 18021404830.09183, 18573811792.83555, 197145955.61914468, 187135368.5645987, 28002220144.24897, 28364744721.457897, 19345682263.41358, 19487424199.809628, 17213673650.545994, 16977748066.618664,),
        "gamma_prior": (194887109.56787893, 226098156.76587048, 15892531359.778233, 23256222984.212387, 1594479197.8900533, 4672274079.488786, 19161733388.104424, 19288996538.515003, 197145955.61914468, 187135368.5645987, 28373511526.946552, 28364744721.457897, 19435003650.787918, 19487424199.809628, 17836044569.91498, 16977748066.618664,),
        "fixed_horizon": (201516720.02438918, 226623379.80364805, 20455128512.91166, 45714284743.81479, 1680175181.5882661, 5255994873.731812, 19560192281.310337, 21009759287.667377, 187135368.5645987, 187135368.5645987, 28115410529.093834, 28364744721.457897, 19561355188.029617, 19487424199.809628, 17015287768.073145, 16977748066.618664,),
        "no_spend_cap": (194887109.56787893, 226098156.76587048, 15965918931.86382, 23251016331.77835, 1586773857.3817372, 4638786898.18381, 19203454153.882515, 19274226829.85438, 197145955.61914468, 187135368.5645987, 28373294669.605865, 28364744721.457897, 19441549398.73463, 19487424199.809628, 17838546420.467144, 16977748066.618664,),
    }
    for _row, _wants in E12MT.items():
        for _k, _want in zip(E12KEYS, _wants):
            eq_int(f"g20 {_k} {_row} frozen mean_tokens",
                   e12c[_k][_row]["mean_tokens"], _want)
    # every ratio_to_ours in the file must equal row_mean / ours_mean exactly
    for _k in E12KEYS:
        for _row in E12MT:
            eq_int(f"g20 {_k} {_row} ratio_to_ours recomputed",
                   e12c[_k][_row]["ratio_to_ours"],
                   e12c[_k][_row]["mean_tokens"]
                   / e12c[_k]["ours"]["mean_tokens"])

    # tab:ablation body numbers: per variant, cost relative to the full rule
    # as mean over the four streams of ratio_to_ours, worst stream after the
    # slash (2dp display).  Every printed cell is frozen here.
    _STREAMS4 = ("sepsis", "bpi2019", "wiki_A", "wiki_B")
    _TAB_ROWS = ("always_reactive", "narrow_trigger", "fixed_cooldown",
                 "no_decay", "gamma_prior", "fixed_horizon", "no_spend_cap")
    _TAB_WANT = {  # (row, costset, price): (mean, worst) as printed
        ("always_reactive", "glm", "native"): (4.22, 11.8),
        ("always_reactive", "glm", "5M"): (2.02, 4.02),
        ("always_reactive", "ds", "native"): (0.976, 1.00),
        ("always_reactive", "ds", "5M"): (1.00, 1.00),
        ("narrow_trigger", "glm", "native"): (1.08, 1.25),
        ("narrow_trigger", "glm", "5M"): (0.705, 1.00),
        ("narrow_trigger", "ds", "native"): (1.15, 1.24),
        ("narrow_trigger", "ds", "5M"): (1.21, 1.56),
        ("fixed_cooldown", "glm", "native"): (1.00, 1.00),
        ("fixed_cooldown", "glm", "5M"): (1.00, 1.00),
        ("fixed_cooldown", "ds", "native"): (1.05, 1.13),
        ("fixed_cooldown", "ds", "5M"): (1.00, 1.00),
        ("no_decay", "glm", "native"): (0.944, 1.00),
        ("no_decay", "glm", "5M"): (1.00, 1.06),
        ("no_decay", "ds", "native"): (0.987, 1.00),
        ("no_decay", "ds", "5M"): (1.00, 1.00),
        ("gamma_prior", "glm", "native"): (1.00, 1.00),
        ("gamma_prior", "glm", "5M"): (1.00, 1.01),
        ("gamma_prior", "ds", "native"): (1.00, 1.00),
        ("gamma_prior", "ds", "5M"): (1.00, 1.00),
        ("fixed_horizon", "glm", "native"): (1.10, 1.28),
        ("fixed_horizon", "glm", "5M"): (1.30, 1.97),
        ("fixed_horizon", "ds", "native"): (0.975, 1.01),
        ("fixed_horizon", "ds", "5M"): (1.00, 1.00),
        ("no_spend_cap", "glm", "native"): (1.00, 1.00),
        ("no_spend_cap", "glm", "5M"): (1.00, 1.00),
        ("no_spend_cap", "ds", "native"): (1.00, 1.00),
        ("no_spend_cap", "ds", "5M"): (1.00, 1.00),
    }
    for (_row, _cs, _pr), (_wm, _ww) in _TAB_WANT.items():
        _rs = [e12c[f"android_{_cs}/{_s}/price={_pr}"][_row]["ratio_to_ours"]
               for _s in _STREAMS4]
        eq_round(f"g20 tab:ablation {_cs}/{_pr} {_row} mean-of-ratios",
                 sum(_rs) / len(_rs), _wm,
                 len(str(_wm).split(".")[1]) if "." in str(_wm) else 0)
        eq_round(f"g20 tab:ablation {_cs}/{_pr} {_row} worst-stream",
                 max(_rs), _ww,
                 len(str(_ww).split(".")[1]) if "." in str(_ww) else 0)

# ================================================================ summary
print()
if FAILURES:
    print("\n".join(FAILURES[:20]))
print("=" * 68)
print(f"checked {PASS + FAIL} assertions: {PASS} pass, {FAIL} fail")
print("=" * 68)
sys.exit(1 if FAIL else 0)
