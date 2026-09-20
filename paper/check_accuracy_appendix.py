#!/usr/bin/env python3
"""Standalone checker for the accuracy-impact appendix numbers (t21 paired
replay of compiled programs vs reactive agent replay).

Recomputes every number frozen in accuracy_paired_summary.json straight from
the raw experiment records, and compares against literals written in the want
slots below.  No network, no LLM calls.  Stdlib only.

Usage:
    python3 check_accuracy_appendix.py            # run everything
    python3 check_accuracy_appendix.py --group 1  # run one group only
    python3 check_accuracy_appendix.py -v         # also print passing checks

Design rules (same contract as check_numbers_new.py):

1. Every number is recomputed from experimental-results/guiexp_android/
   t21_paired_replay (per-use paired summary.json), t16_build
   (constants_table.json + per-family build.json), and guiexp_osworld /
   guiexp_webarena (build.json + deploy.json).  Nothing is read out of
   body.tex.
2. The frozen value sits literally in the want slot of each assertion; a
   divergence is a FAIL.  That is the point.
3. Group G3 additionally loads paper/accuracy_paired_summary.json and asserts
   every one of its numbers equals the recompute, so the JSON and this file
   cannot drift apart.  That summary stays glm/ds-only; the third model's
   families (qwen_qwen3.8-flash, added 2026-09-21) carry their frozen
   literals directly in G1/G2 -- the paper's appendix sentence cites them
   from there, not from the JSON.

Semantics of the t21 paired summary.json (verified against
t21_paired_replay/analysis.json, whose agent_ok/deploy_ok fields are built
from the same two fields):
    top-level  "success"          -> reactive agent replay arm (no program)
    "deploy_use"."success"      -> compiled-program arm, same binding
Program use-time pass rate per family = program successes / replayed pairs.

Exit code non-zero on any failure.
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
AW = os.path.join(ROOT, "experimental-results", "guiexp_android")
T21 = os.path.join(AW, "t21_paired_replay")
T16_CONST = os.path.join(AW, "t16_build", "constants_table.json")
T16_BUILD = os.path.join(AW, "t16_build")
OSW = os.path.join(ROOT, "experimental-results", "guiexp_osworld")
WEB = os.path.join(ROOT, "experimental-results", "guiexp_webarena")
SUMJSON = os.path.join(HERE, "accuracy_paired_summary.json")

MODELS = ("z-ai_glm-5.3-flash", "deepseek_deepseek-v4-flash-vision-exp")
QWEN = "qwen_qwen3.8-flash"          # third model: literals only, no JSON row
FAMS = ("ContactsAddContact", "MarkorDeleteNote", "OsmAndMarker",
        "SimpleCalendarAddOneEvent")

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


def eq_int(label: str, got, want, extra: str = "") -> None:
    _record(got == want, label, got, want, extra)


def eq_exact(label: str, got, want, extra: str = "") -> None:
    """Exact equality (same integer arithmetic on both sides)."""
    _record(got == want, label, got, want, extra)


# ---------------------------------------------------------------- load data
def binom_two_sided(b: int, c: int):
    """Two-sided exact binomial McNemar p on the discordant pairs.

    X ~ Bin(b + c, 0.5); p = min(1, 2 * P(X <= min(b, c))).  None when there
    are no discordant pairs."""
    n = b + c
    if n == 0:
        return None
    tail = sum(math.comb(n, i) for i in range(min(b, c) + 1)) / 2 ** n
    return min(1.0, 2.0 * tail)


# t21: per (model_slug, family) -> {use_index: summary dict}
T21_RAW: dict = {}
for _f in sorted(glob.glob(os.path.join(T21, "*", "*", "use_*", "summary.json"))):
    _d = json.load(open(_f))
    _slug = _d["cell"].split("/")[0]
    T21_RAW.setdefault((_slug, _d["family"]), {})[_d["use_index"]] = _d

# recompute per-family paired stats
PER: dict = {}
for (_slug, _fam), _runs in sorted(T21_RAW.items()):
    _n = len(_runs)
    _both = _only_a = _only_p = _bothf = 0
    for _r in _runs.values():
        _a = bool(_r["success"])
        _p = bool(_r["deploy_use"]["success"])
        if _a and _p:
            _both += 1
        elif _a:
            _only_a += 1
        elif _p:
            _only_p += 1
        else:
            _bothf += 1
    _na = _both + _only_a
    _np = _both + _only_p
    PER[(_slug, _fam)] = {
        "n": _n, "agent": _na, "prog": _np,
        "agent_rate": _na / _n, "prog_rate": _np / _n,
        "both": _both, "only_agent": _only_a, "only_program": _only_p,
        "both_fail": _bothf, "p": binom_two_sided(_only_a, _only_p),
    }

# recompute pooled-per-model stats
POOL: dict = {}
for _slug in MODELS + (QWEN,):
    _all = [r for (s, _), runs in T21_RAW.items() if s == _slug
            for r in runs.values()]
    _n = len(_all)
    _na = sum(1 for r in _all if r["success"])
    _np = sum(1 for r in _all if r["deploy_use"]["success"])
    _only_a = sum(1 for r in _all
                  if r["success"] and not r["deploy_use"]["success"])
    _only_p = sum(1 for r in _all
                  if r["deploy_use"]["success"] and not r["success"])
    POOL[_slug] = {
        "n": _n, "agent": _na, "prog": _np,
        "agent_rate": _na / _n, "prog_rate": _np / _n,
        "only_agent": _only_a, "only_program": _only_p,
        "disc_total": _only_a + _only_p,
        "p": binom_two_sided(_only_a, _only_p),
    }

# t16 constants table (frozen headline q + admission)
T16 = json.load(open(T16_CONST))
T16_H = {(c["model_slug"], c["family"]): c["headline"] for c in T16["cells"]}

# osworld / webarena deploy recomputes: (bench, slug, fam) -> dict
BENCH_DEPLOY: dict = {}
for _bench, _root in (("osworld", OSW), ("webarena", WEB)):
    for _mf in sorted(glob.glob(os.path.join(_root, "*", "*", ""))):
        _parts = _mf.rstrip(os.sep).split(os.sep)
        _slug, _fam = _parts[-2], _parts[-1]
        if _slug not in MODELS:
            continue
        _b = os.path.join(_mf, "build.json")
        _d = os.path.join(_mf, "deploy.json")
        _adm = False
        if os.path.exists(_b):
            _vd = json.load(open(_b)).get("verification", {})
            _adm = bool(_vd.get("admitted", False))
        if not os.path.exists(_d):
            BENCH_DEPLOY[(_bench, _slug, _fam)] = {
                "admitted": _adm, "n": 0, "succ": None, "q": None}
        else:
            _dd = json.load(open(_d))
            _succ = sum(1 for u in _dd["uses"] if u.get("success"))
            BENCH_DEPLOY[(_bench, _slug, _fam)] = {
                "admitted": _adm, "n": _dd["n"], "succ": _succ,
                "q": _succ / _dd["n"],
                "field_succ": _dd["success_count"], "field_rate": _dd["success_rate"]}

SUM_D = json.load(open(SUMJSON))

SHORT = {"z-ai_glm-5.3-flash": "glm",
         "deepseek_deepseek-v4-flash-vision-exp": "ds",
         "qwen_qwen3.8-flash": "qw"}

# ================================================================ G1 t21 cells
if group(1, "t21 per-family paired replays vs literals (agent vs program arm)"):
    # literals: n, agent successes, program successes, the four paired cells
    # both_success / only_agent / only_program / both_fail, then the two pass
    # rates at 4 decimals
    WANT = {
        ("z-ai_glm-5.3-flash", "ContactsAddContact"):
            (30, 30, 30, 30, 0, 0, 0, 1.0, 1.0),
        ("z-ai_glm-5.3-flash", "MarkorDeleteNote"):
            (30, 30, 24, 24, 6, 0, 0, 1.0, 0.8),
        ("z-ai_glm-5.3-flash", "OsmAndMarker"):
            (30, 12, 30, 12, 0, 18, 0, 0.4, 1.0),
        ("z-ai_glm-5.3-flash", "SimpleCalendarAddOneEvent"):
            (30, 14, 30, 14, 0, 16, 0, 0.4667, 1.0),
        ("deepseek_deepseek-v4-flash-vision-exp", "ContactsAddContact"):
            (30, 27, 29, 26, 1, 3, 0, 0.9, 0.9667),
        ("deepseek_deepseek-v4-flash-vision-exp", "MarkorDeleteNote"):
            (30, 23, 29, 23, 0, 6, 1, 0.7667, 0.9667),
        # third model (2026-09-21): three qwen families; Calendar's agent
        # arm succeeds on only 15 of 30 replays while the program holds 30/30
        ("qwen_qwen3.8-flash", "ContactsAddContact"):
            (30, 30, 29, 29, 1, 0, 0, 1.0, 0.9667),
        ("qwen_qwen3.8-flash", "MarkorDeleteNote"):
            (30, 30, 30, 30, 0, 0, 0, 1.0, 1.0),
        ("qwen_qwen3.8-flash", "SimpleCalendarAddOneEvent"):
            (30, 15, 30, 15, 0, 15, 0, 0.5, 1.0),
    }
    eq_int("t21: 270 paired summary.json files parsed (180 glm/ds + 90 qw)",
           sum(len(v) for v in T21_RAW.values()), 270)
    eq_int("t21: 90 qw paired files parsed",
           sum(len(v) for (s, _), v in T21_RAW.items() if s == QWEN), 90)
    eq_int("t21: qw families with replays (OsmAndMarker rejected -> none)",
           sorted(f for (s, f) in T21_RAW if s == QWEN),
           ["ContactsAddContact", "MarkorDeleteNote",
            "SimpleCalendarAddOneEvent"])
    for key, (n, na, np_, both, oa, op, bf, ra, rp) in WANT.items():
        mk, fam = SHORT[key[0]], key[1]
        c = PER[key]
        eq_int(f"{mk}/{fam}: n pairs", c["n"], n)
        eq_int(f"{mk}/{fam}: agent-arm successes", c["agent"], na)
        eq_int(f"{mk}/{fam}: program-arm successes", c["prog"], np_)
        eq_int(f"{mk}/{fam}: both_success", c["both"], both)
        eq_int(f"{mk}/{fam}: only_agent (discordant)", c["only_agent"], oa)
        eq_int(f"{mk}/{fam}: only_program (discordant)", c["only_program"], op)
        eq_int(f"{mk}/{fam}: both_fail", c["both_fail"], bf)
        eq_int(f"{mk}/{fam}: paired cells sum to n",
               c["both"] + c["only_agent"] + c["only_program"] + c["both_fail"], n)
        eq_round(f"{mk}/{fam}: agent pass rate", c["agent_rate"], ra, 4)
        eq_round(f"{mk}/{fam}: program pass rate", c["prog_rate"], rp, 4)
        # discordant counts cross-check the direct recount
        eq_int(f"{mk}/{fam}: only_agent == agent - both", c["only_agent"], na - both)
        eq_int(f"{mk}/{fam}: only_program == program - both", c["only_program"], np_ - both)

# ================================================================ G2 pooled
if group(2, "t21 pooled per model vs literals (McNemar discordants, exact p)"):
    # literals: n, agent succ, prog succ, only_agent, only_program, exact p,
    # then the two pass rates at 4 decimals
    WANT = {
        "z-ai_glm-5.3-flash": (120, 86, 114, 6, 34,
                               8.364584573428147e-06, 0.7167, 0.95),
        "deepseek_deepseek-v4-flash-vision-exp": (60, 50, 58, 1, 9,
                                                  0.021484375, 0.8333, 0.9667),
        # qw pooled over its three families: 16 discordants (1 agent-only,
        # 15 program-only), exact p = 2*(1+16)/2^16 = 34/65536
        "qwen_qwen3.8-flash": (90, 75, 89, 1, 15,
                               0.000518798828125, 0.8333, 0.9889),
    }
    eq_int("t21: pooled pairs == 180 over glm/ds",
           sum(POOL[s]["n"] for s in MODELS), 180)
    eq_int("t21: pooled pairs == 270 over all three models",
           sum(POOL[s]["n"] for s in MODELS + (QWEN,)), 270)
    eq_int("t21: pooled families (glm covers all four)",
           POOL["z-ai_glm-5.3-flash"]["n"] // 30, 4)
    eq_int("t21: pooled families (ds covers two)",
           POOL["deepseek_deepseek-v4-flash-vision-exp"]["n"] // 30, 2)
    eq_int("t21: pooled families (qw covers three)",
           POOL[QWEN]["n"] // 30, 3)
    for slug, (n, na, np_, oa, op, p, ra, rp) in WANT.items():
        mk = SHORT[slug]
        c = POOL[slug]
        eq_int(f"{mk}: pooled n pairs", c["n"], n)
        eq_int(f"{mk}: pooled agent successes", c["agent"], na)
        eq_int(f"{mk}: pooled program successes", c["prog"], np_)
        eq_round(f"{mk}: pooled agent pass rate", c["agent_rate"], ra, 4)
        eq_round(f"{mk}: pooled program pass rate", c["prog_rate"], rp, 4)
        eq_int(f"{mk}: discordant only_agent", c["only_agent"], oa)
        eq_int(f"{mk}: discordant only_program", c["only_program"], op)
        eq_int(f"{mk}: discordant total", c["disc_total"], oa + op)
        # pooled discordants equal the sum over the model's families
        eq_int(f"{mk}: pooled discordants == family sum",
               (c["only_agent"], c["only_program"]),
               (sum(PER[k]["only_agent"] for k in PER if k[0] == slug),
                sum(PER[k]["only_program"] for k in PER if k[0] == slug)))
        eq_exact(f"{mk}: exact binomial p on discordants", c["p"], p)

# ================================================================ G3 JSON
if group(3, "accuracy_paired_summary.json agrees with the recompute"):
    for key, c in PER.items():
        slug, fam = key
        if slug not in MODELS:
            continue  # the JSON stays glm/ds-only; qw literals live in G1/G2
        e = SUM_D["per_family"][f"{slug}/{fam}"]
        mk = SHORT[slug]
        eq_int(f"{mk}/{fam}: json n_pairs", e["n_pairs"], c["n"])
        eq_int(f"{mk}/{fam}: json agent_successes", e["agent_successes"], c["agent"])
        eq_int(f"{mk}/{fam}: json program_successes", e["program_successes"], c["prog"])
        eq_exact(f"{mk}/{fam}: json agent_pass_rate", e["agent_pass_rate"], c["agent_rate"])
        eq_exact(f"{mk}/{fam}: json program_pass_rate", e["program_pass_rate"], c["prog_rate"])
        eq_int(f"{mk}/{fam}: json paired counts",
               (e["paired"]["both_success"], e["paired"]["only_agent"],
                e["paired"]["only_program"], e["paired"]["both_fail"]),
               (c["both"], c["only_agent"], c["only_program"], c["both_fail"]))
    for slug in MODELS:
        mk = SHORT[slug]
        e = SUM_D["pooled_per_model"][slug]
        c = POOL[slug]
        eq_int(f"{mk}: json pooled n", e["n_pairs"], c["n"])
        eq_int(f"{mk}: json pooled agent/program successes",
               (e["agent_successes"], e["program_successes"]),
               (c["agent"], c["prog"]))
        eq_exact(f"{mk}: json pooled rates",
                 (e["agent_pass_rate"], e["program_pass_rate"]),
                 (c["agent_rate"], c["prog_rate"]))
        eq_int(f"{mk}: json discordant",
               (e["discordant"]["only_agent"], e["discordant"]["only_program"],
                e["discordant_total"]),
               (c["only_agent"], c["only_program"], c["disc_total"]))
        eq_exact(f"{mk}: json exact_binomial_p", e["exact_binomial_p"], c["p"])
    # cross-check section: android t21 pass rate vs t16 headline q
    for slug in MODELS:
        for fam in FAMS:
            e = SUM_D["cross_check"]["android_t21_vs_t16"][f"{slug}/{fam}"]
            mk = SHORT[slug]
            h = T16_H[(slug, fam)]
            eq_exact(f"{mk}/{fam}: json cross t16_admitted", e["t16_admitted"],
                     bool(h["admitted"]))
            eq_exact(f"{mk}/{fam}: json cross t16_headline_q", e["t16_headline_q"],
                     h["q"])
            want_rate = PER[(slug, fam)]["prog_rate"] if (slug, fam) in PER else None
            eq_exact(f"{mk}/{fam}: json cross t21_program_pass_rate",
                     e["t21_program_pass_rate"], want_rate)
            eq_int(f"{mk}/{fam}: json cross n_t21_pairs", e["n_t21_pairs"],
                   PER[(slug, fam)]["n"] if (slug, fam) in PER else 0)
    for bench in ("osworld", "webarena"):
        for (b, slug, fam), c in BENCH_DEPLOY.items():
            if b != bench:
                continue
            e = SUM_D["cross_check"][bench][f"{slug}/{fam}"]
            mk = SHORT[slug]
            eq_int(f"{bench} {mk}/{fam}: json admitted", e["admitted"], c["admitted"])
            eq_int(f"{bench} {mk}/{fam}: json deploy_n", e["deploy_n"], c["n"])
            eq_exact(f"{bench} {mk}/{fam}: json deploy_successes", e["deploy_successes"],
                     c["succ"])
            eq_exact(f"{bench} {mk}/{fam}: json deploy_q", e["deploy_q"], c["q"])

# ================================================================ G4 t16 q
if group(4, "t16 headline q for the four Android families (frozen constants_table.json)"):
    # literals copied from constants_table.json (admitted, q)
    WANT = {
        ("z-ai_glm-5.3-flash", "ContactsAddContact"): (True, 0.0),
        ("z-ai_glm-5.3-flash", "MarkorDeleteNote"): (True, 0.19999999999999996),
        ("z-ai_glm-5.3-flash", "OsmAndMarker"): (True, 0.0),
        ("z-ai_glm-5.3-flash", "SimpleCalendarAddOneEvent"): (True, 0.0),
        ("deepseek_deepseek-v4-flash-vision-exp", "ContactsAddContact"):
            (True, 0.033333333333333326),
        ("deepseek_deepseek-v4-flash-vision-exp", "MarkorDeleteNote"):
            (True, 0.033333333333333326),
        ("deepseek_deepseek-v4-flash-vision-exp", "OsmAndMarker"): (False, None),
        ("deepseek_deepseek-v4-flash-vision-exp", "SimpleCalendarAddOneEvent"):
            (False, None),
    }
    for key, (adm, q) in WANT.items():
        slug, fam = key
        mk = SHORT[slug]
        h = T16_H[(slug, fam)]
        eq_int(f"{mk}/{fam}: t16 admitted", h["admitted"], adm)
        eq_exact(f"{mk}/{fam}: t16 headline q", h["q"], q)
        # q recomputed from the build-time deploy block of the same cell
        bp = os.path.join(T16_BUILD, slug, fam, "build.json")
        if adm:
            dep = json.load(open(bp))["deploy"]
            eq_int(f"{mk}/{fam}: t16 deploy n=30", dep["n"], 30)
            eq_exact(f"{mk}/{fam}: t16 q == 1 - successes/n",
                     1 - dep["success_count"] / dep["n"], h["q"])
        else:
            eq_int(f"{mk}/{fam}: rejected cell has no frozen q", h["q"] is None, True)
    # per-cell identity: t21 program pass rate == 1 - t16 q on the four
    # admitted families where t21 ran (different use samples; agreement is
    # reported, not asserted -- these two assertions only record the gaps)
    for slug in MODELS:
        for fam in FAMS:
            if (slug, fam) in PER and T16_H[(slug, fam)]["admitted"]:
                gap = abs(PER[(slug, fam)]["prog_rate"]
                          - (1 - T16_H[(slug, fam)]["q"]))
                eq_int(f"{SHORT[slug]}/{fam}: |t21 rate - (1-q)| within 0.01 "
                       f"(gap {gap:.4f})", gap <= 0.01, True,
                       "different use samples; side-by-side report only")

# ================================================================ G5 bench
if group(5, "osworld/webarena deploy pass rates vs literals (admitted cells)"):
    # literals: admitted, n, successes, q
    WANT = {
        ("osworld", "z-ai_glm-5.3-flash", "CalcTableSave"): (True, 30, 30, 1.0),
        ("osworld", "z-ai_glm-5.3-flash", "WriterMemoSave"): (True, 30, 30, 1.0),
        ("osworld", "deepseek_deepseek-v4-flash-vision-exp", "WriterMemoSave"):
            (True, 30, 30, 1.0),
        ("webarena", "z-ai_glm-5.3-flash", "CommentPost"): (True, 30, 30, 1.0),
        ("webarena", "deepseek_deepseek-v4-flash-vision-exp", "CommentPost"):
            (True, 30, 30, 1.0),
        ("osworld", "deepseek_deepseek-v4-flash-vision-exp", "CalcTableSave"):
            (False, 0, None, None),
    }
    for (bench, slug, fam), (adm, n, succ, q) in WANT.items():
        mk = SHORT[slug]
        c = BENCH_DEPLOY[(bench, slug, fam)]
        eq_int(f"{bench} {mk}/{fam}: admitted", c["admitted"], adm)
        eq_int(f"{bench} {mk}/{fam}: deploy n", c["n"], n)
        eq_exact(f"{bench} {mk}/{fam}: deploy successes", c["succ"], succ)
        eq_exact(f"{bench} {mk}/{fam}: deploy q", c["q"], q)
        if adm:
            eq_int(f"{bench} {mk}/{fam}: uses recount == success_count field",
                   c["succ"], c["field_succ"])
            eq_exact(f"{bench} {mk}/{fam}: q == success_rate field", c["q"],
                     c["field_rate"])
    eq_int("webarena: only CommentPost exists for both models",
           sorted({k[2] for k in BENCH_DEPLOY if k[0] == "webarena"}),
           ["CommentPost"])
    eq_int("osworld: CalcTableSave + WriterMemoSave for both models",
           sorted({k[2] for k in BENCH_DEPLOY if k[0] == "osworld"}),
           ["CalcTableSave", "WriterMemoSave"])

# ================================================================ summary
print()
if FAILURES:
    print("\n".join(FAILURES[:20]))
print("=" * 68)
print(f"checked {PASS + FAIL} assertions: {PASS} pass, {FAIL} fail")
print("=" * 68)
sys.exit(1 if FAIL else 0)
