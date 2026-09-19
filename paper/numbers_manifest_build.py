#!/usr/bin/env python3
"""Emit paper/numbers_manifest.json.

The manifest is the checked-in artifact; this script writes it by looping over
models, families, probe arms and the n grid.  A handful of expr entries name a
fixed set of cells, taken from the constants table when this runs: the admitted
cells, the cells that failed the gate, and the admitted cells whose exploration
success rate is 1.  Rerun this script whenever an admission changes (the GLM
FilesMoveFile redeploy, for instance), then rerun gen_numbers.py.
"""
import json, os

ROOT = "/Users/myl/app/computer-use"
OUT = os.path.join(ROOT, "paper", "numbers_manifest.json")

CONST = "experimental-results/guiexp_android/t16_build/constants_table.json"
FRAG = "experimental-results/guiexp_android/t18_fragility/%s/fragility.json"
ROUTER = "experimental-results/guiexp/m_library_android/analysis.json"

MODELS = [
    ("GLM", "z-ai/glm-5.3-flash", "z-ai_glm-5.3-flash"),
    ("DS", "deepseek/deepseek-v4-flash-vision-exp", "deepseek_deepseek-v4-flash-vision-exp"),
]
FAMILIES = [
    ("Contacts", "ContactsAddContact"),
    ("FilesMove", "FilesMoveFile"),
    ("MarkorCreate", "MarkorCreateNote"),
    ("MarkorDelete", "MarkorDeleteNote"),
    ("OsmFav", "OsmAndFavorite"),
    ("OsmMarker", "OsmAndMarker"),
    ("Calendar", "SimpleCalendarAddOneEvent"),
]
ARMS = [
    ("Clean", "clean"),
    ("FontLarge", "font_large"),
    ("FontSmall", "font_small"),
    ("DensitySmall", "density_small"),
    ("LocaleFr", "locale_fr"),
    ("DarkTheme", "dark_theme"),
    ("Notification", "notification"),
    ("LowBattery", "low_battery"),
    ("PermissionDialog", "permission_dialog"),
    ("UpdatePrompt", "update_prompt"),
]
NGRID = [("Nzero", "0"), ("Nseven", "7"), ("Nten", "10"),
         ("Ntwentyfive", "25"), ("Nfifty", "50"), ("Nhundred", "100")]

# suffix, path under the cell, fmt, optional, note
CELL_Q = [
    ("c", "headline.c", "k1", False, "reactive cost c, price-weighted tokens, floor subtracted"),
    ("Ldoc", "headline.L_doc", "k1", False, "doc-arm cost L_doc, price-weighted tokens"),
    ("d", "headline.d", "int", True, "per-use cost d of the compiled program"),
    ("q", "headline.q", "f2", True, "deployment failure rate q"),
    ("ShareDoc", "headline.share_doc", "f2", False, "share of c saved by the doc arm"),
    ("ShareProg", "headline.share_prog", "f2", True, "share of c saved by the program"),
    ("C", "headline.C_with_repair", "k1", False, "compile price C including repair"),
    ("CNoRepair", "headline.C_without_repair", "k1", False, "compile price C excluding repair"),
    ("pOne", "gate.p_initial_k1", "f2", False, "initial gate pass rate at k=1"),
    ("pTwo", "gate.p_initial_k2", "f2", False, "initial gate pass rate at k=2"),
    ("pThree", "gate.p_initial_k3", "f2", False, "initial gate pass rate at k=3"),

    ("pHead", "headline.p", "f2", False, "headline p used for the break-even"),
    ("Adm", "headline.admitted", "bool", False, "admitted to deployment"),
    ("NstarMarg", "headline.nstar_marginal", "f2", True, "marginal break-even N*"),
    ("NstarBuild", "headline.nstar_build", "f2", True, "build-price break-even N*"),
    ("pi", "headline.pi", "f2", False, "exploration success rate pi"),
    ("Usd", "cost_usd.total_summed", "usd2", False, "total spend on this cell"),
    ("Ref", "verification.refinements", "int", False, "repair rounds used"),
    ("DeployN", "deploy.n", "int", None, "deployment uses"),
    ("DeploySucc", "deploy.successes", "int", None, "deployment successes"),
    ("DeployProgErr", "deploy.failures_by_error_type.program_error", "int", None,
     "deployment failures raised by the program (loud)"),
    ("ReactPerSucc", "per_success_appendix.reactive_per_delivered_success", "k1", False,
     "reactive cost per delivered success, c_attempt / pi"),
    ("ProgPerSucc", "per_success_appendix.program_per_delivered_success", "int", True,
     "program cost per delivered success"),
    ("NstarSucc", "per_success_appendix.nstar_success", "f2", True,
     "break-even N* on the per-success accounting"),
]

entries = []


def add(**kw):
    entries.append(kw)


def cellpath(slug, family, tail):
    return "cells[family=%s,model=%s].%s" % (family, slug, tail)


# The constants table, loaded once: admission status per cell drives which
# N*_incl entries are expressions (P0.1) and which stay source reads (inf).
CONSTANTS = json.load(open(os.path.join(ROOT, CONST)))
by_key = {(c["model_slug"], c["family"]): c for c in CONSTANTS["cells"]}


# ---------------------------------------------------------------- per cell
for mtok, mname, slug in MODELS:
    for ftok, family in FAMILIES:
        for suffix, tail, fmt, optional, note in CELL_Q:
            if suffix == "NstarBuild":
                # handled right after this loop (P0.1 floored numerator)
                continue
            e = dict(macro="n%s%s%s" % (mtok, ftok, suffix),
                     source=CONST,
                     path=cellpath(slug, family, tail),
                     fmt=fmt,
                     note="%s / %s: %s" % (mtok, ftok, note))
            if optional is None:
                e["default"] = 0
            elif optional:
                e["optional"] = True
            add(**e)
        # N*_incl, P0.1 ruling (2026-09-17): the numerator's three traces are
        # charged at the FLOORED per-attempt cost, (C + 3*c) / ((1-q)*c - d),
        # the same floored c the denominator's saving uses.  This replaces the
        # constants table's headline.nstar_build, whose numerator added the
        # unsubtracted exploration total (mixed accounting: floor subtracted in
        # the denominator but not in the numerator).  Admitted cells become
        # expr entries; rejected cells keep the source read, which is "inf"
        # under either accounting.
        if by_key[(slug, family)]["headline"]["admitted"] is True:
            add(macro="n%s%sNstarBuild" % (mtok, ftok),
                expr="(n%s%sC + 3*n%s%sc) / ((1-n%s%sq)*n%s%sc - n%s%sd)"
                     % (mtok, ftok, mtok, ftok, mtok, ftok, mtok, ftok, mtok, ftok),
                fmt="f2",
                note="%s / %s: build-price break-even N*, floored traces "
                     "(C + 3*c)/s, P0.1 full-floored numerator" % (mtok, ftok))
        else:
            add(macro="n%s%sNstarBuild" % (mtok, ftok),
                source=CONST, path=cellpath(slug, family, "headline.nstar_build"),
                fmt="f2", optional=True,
                note="%s / %s: build-price break-even N* (rejected cell, inf)" % (mtok, ftok))
        # The final gate is the redeploy gate when a cell was redeployed, the
        # post-repair gate otherwise, and "--" when neither was run.
        add(macro="n%s%spAfter" % (mtok, ftok), source=CONST, compute="cell_gate_margin",
            args={"model": slug, "family": family}, fmt="f2", optional=True,
            note="%s / %s: gate margin on the final gate, bindings passed / 5" % (mtok, ftok))

# ------------------------------------------------------------- per model
PM_MEDIAN = [
    ("cMedian", "headline.c", "median", "k1", "median reactive cost c over the 7 cells"),
    ("cMin", "headline.c", "min", "k1", "smallest reactive cost c"),
    ("cMax", "headline.c", "max", "k1", "largest reactive cost c"),
    ("LdocMedian", "headline.L_doc", "median", "k1", "median doc-arm cost"),
    ("CMedian", "headline.C_with_repair", "median", "k1", "median compile price C"),
    ("CNoRepairMedian", "headline.C_without_repair", "median", "k1",
     "median compile price excluding repair"),
    ("NstarMargMedian", "headline.nstar_marginal", "median", "f2", "median marginal break-even N*"),
    ("NstarMargMin", "headline.nstar_marginal", "min", "f2", "smallest marginal break-even N*"),
    ("NstarMargMax", "headline.nstar_marginal", "max", "f2", "largest finite marginal break-even N*"),
    ("piMedian", "headline.pi", "median", "f2", "median exploration success rate"),
]
PM_COUNT = [
    ("Cells", "cells", "int", "cells run for this model"),
    ("Admitted", "cells_admitted", "int", "cells admitted to deployment"),
    ("Deployed", "cells_deployed", "int", "cells actually deployed"),
    ("Unautomatable", "cells_unautomatable", "int", "cells the agent could not automate"),
    ("Usd", "total_cost_usd_summed", "usd2", "total spend for this model"),
]

for mtok, mname, slug in MODELS:
    for suffix, qty, stat, fmt, note in PM_MEDIAN:
        add(macro="n%s%s" % (mtok, suffix), source=CONST,
            path="per_model['%s'].quantities['%s'].%s" % (slug, qty, stat),
            fmt=fmt, note="%s: %s" % (mtok, note))
    add(macro="n%sNstarMargInf" % mtok, source=CONST,
        path="per_model['%s'].quantities['headline.nstar_marginal'].n_infinite" % slug,
        fmt="int", note="%s: cells whose marginal break-even is infinite" % mtok)
    for suffix, key, fmt, note in PM_COUNT:
        add(macro="n%s%s" % (mtok, suffix), source=CONST,
            path="per_model['%s'].%s" % (slug, key), fmt=fmt,
            note="%s: %s" % (mtok, note))
    add(macro="n%sAdmRate" % mtok, source=CONST,
        path="population['%s'].admission_rate" % mname, fmt="f2",
        note="%s: admitted cells / cells attempted" % mtok)
    add(macro="n%sCAdmMedian" % mtok, source=CONST,
        path="population['%s'].C_admitted_median" % mname, fmt="k1",
        note="%s: median compile price C over the admitted cells" % mtok)
    add(macro="n%sCFailMedian" % mtok, source=CONST,
        path="population['%s'].C_fail" % mname, fmt="k1",
        note="%s: median compile price C over the cells that failed the gate" % mtok)
    add(macro="n%sCeffPop" % mtok, source=CONST,
        path="population['%s'].C_eff_population" % mname, fmt="k1",
        note="%s: expected price of an admitted program, failures charged to it" % mtok)
    add(macro="n%sSMedian" % mtok, source=CONST,
        path="population['%s'].s_median" % mname, fmt="k1",
        note="%s: median per-arrival saving s over the admitted cells" % mtok)
    add(macro="n%sNstarPop" % mtok, source=CONST,
        path="population['%s'].nstar_population" % mname, fmt="f1",
        note="%s: population break-even N*, C_eff_population / s_median" % mtok)
    add(macro="n%sFloor" % mtok, source=CONST,
        path="floor_raw_tokens['%s']" % mname, fmt="int",
        note="%s: per-episode token floor subtracted from c and L_doc" % mtok)
    add(macro="n%src" % mtok, source=CONST,
        path=cellpath(slug, FAMILIES[0][1], "prices.r_c"),
        fmt="f2" if mtok == "GLM" else "f3",
        note="%s: cached-prompt price ratio r_c = p_c / p_in" % mtok)
    add(macro="n%sro" % mtok, source=CONST,
        path=cellpath(slug, FAMILIES[0][1], "prices.r_o"), fmt="f2",
        note="%s: completion price ratio r_o = p_o / p_in" % mtok)

# ------------------------------------------------- replacement lottery (B_pop)
# A family that replaces a drift-killed program faces the population lottery,
# not its own realized price: B_pop = C + (1/p_pop - 1) * C_fail, with p_pop
# the observed verified fraction and C_fail the median price over the rejected
# cells (the pooled retry price of sec:price at the median).  Two per-use
# break rates follow from the stationary comparison (prop:ss): the per-arrival
# cost doubles at h_dbl = (d + q c)/(B_pop + (1-q)c), and replacing stops
# beating never compiling at h_fail = s/(B_pop + (1-q)c).  Cross-check table:
# docs/bpop-sensitivity-2026-09.md.
bpop_hdbl = []
for mtok, mname, slug in MODELS:
    admitted = [ftok for ftok, family in FAMILIES
                if by_key[(slug, family)]["headline"]["admitted"] is True]
    for ftok in admitted:
        add(macro="n%s%sBpop" % (mtok, ftok),
            expr="n%s%sC + (1/n%sAdmRate - 1) * n%sCFailMedian"
                 % (mtok, ftok, mtok, mtok),
            fmt="k1",
            note="%s / %s: replacement-lottery price, realized C plus the "
                 "population failure charge" % (mtok, ftok))
        add(macro="n%s%sNstarBpop" % (mtok, ftok),
            expr="n%s%sBpop / ((1-n%s%sq)*n%s%sc - n%s%sd)"
                 % (mtok, ftok, mtok, ftok, mtok, ftok, mtok, ftok),
            fmt="f1",
            note="%s / %s: accounting break-even at the replacement-lottery "
                 "price" % (mtok, ftok))
        add(macro="n%s%sHdblBpop" % (mtok, ftok),
            expr="(n%s%sd + n%s%sq*n%s%sc) / (n%s%sBpop + (1-n%s%sq)*n%s%sc)"
                 % (mtok, ftok, mtok, ftok, mtok, ftok,
                    mtok, ftok, mtok, ftok, mtok, ftok),
            fmt="f4",
            note="%s / %s: per-use break rate at which the per-arrival cost "
                 "doubles" % (mtok, ftok))
        add(macro="n%s%sHfailBpop" % (mtok, ftok),
            expr="((1-n%s%sq)*n%s%sc - n%s%sd) / (n%s%sBpop + (1-n%s%sq)*n%s%sc)"
                 % (mtok, ftok, mtok, ftok, mtok, ftok,
                    mtok, ftok, mtok, ftok, mtok, ftok),
            fmt="f3",
            note="%s / %s: per-use break rate beyond which replacing is not "
                 "cheaper than never compiling" % (mtok, ftok))
        bpop_hdbl.append("n%s%sHdblBpop" % (mtok, ftok))
    add(macro="n%sNstarBpopMin" % mtok,
        expr="min(%s)" % ", ".join("n%s%sNstarBpop" % (mtok, f) for f in admitted),
        fmt="f1", note="%s: smallest break-even at the replacement-lottery "
             "price" % mtok)
    add(macro="n%sNstarBpopMax" % mtok,
        expr="max(%s)" % ", ".join("n%s%sNstarBpop" % (mtok, f) for f in admitted),
        fmt="f1", note="%s: largest break-even at the replacement-lottery "
             "price" % mtok)
    add(macro="n%sHfailBpopMin" % mtok,
        expr="min(%s)" % ", ".join("n%s%sHfailBpop" % (mtok, f) for f in admitted),
        fmt="f3", note="%s: smallest never-compile break rate at the "
             "replacement-lottery price" % mtok)
    add(macro="n%sHfailBpopMax" % mtok,
        expr="max(%s)" % ", ".join("n%s%sHfailBpop" % (mtok, f) for f in admitted),
        fmt="f3", note="%s: largest never-compile break rate at the "
             "replacement-lottery price" % mtok)
add(macro="nHdblBpopMin", expr="min(%s)" % ", ".join(bpop_hdbl),
    fmt="f4", note="smallest per-use break rate at which the per-arrival cost "
         "doubles")
add(macro="nHdblBpopMax", expr="max(%s)" % ", ".join(bpop_hdbl),
    fmt="f4", note="largest per-use break rate at which the per-arrival cost "
         "doubles")

# ------------------------------------------------------------ probe, per arm
PROBE_Q = [
    ("Pass", "pass_rate", "f2", "pass rate"),
    ("PassN", "pass", "int", "runs passed"),
    ("N", "n", "int", "runs attempted"),
    ("Loud", "loud", "int", "loud failures"),
    ("Silent", "silent_wrong", "int", "silent wrong outcomes"),
    ("Break", "break_given_change", "f2", "break probability given the change"),
]
PROBE_TOTAL = [
    ("BreakUniform", "break_given_change_uniform", "f2", "break probability under a uniform arm draw"),
    ("Qclean", "q_clean", "f2", "failure rate on the clean arm"),
    ("LoudShare", "loud_share_overall", "pct0", "share of failures that were loud"),
    ("Silent", "silent_total", "int", "silent wrong outcomes over all arms"),
    ("Loud", "loud_total", "int", "loud failures over all arms"),
    ("Runs", "runs_total", "int", "probe runs over all arms"),
]
for mtok, mname, slug in MODELS:
    src = FRAG % slug
    for atok, arm in ARMS:
        for suffix, key, fmt, note in PROBE_Q:
            add(macro="nProbe%s%s%s" % (mtok, atok, suffix), source=src,
                path="summary.per_arm.%s.%s" % (arm, key), fmt=fmt,
                note="probe %s / %s: %s" % (mtok, arm, note))
    for suffix, key, fmt, note in PROBE_TOTAL:
        add(macro="nProbe%s%s" % (mtok, suffix), source=src,
            path="summary.%s" % key, fmt=fmt, note="probe %s: %s" % (mtok, note))
    add(macro="nProbe%sK" % mtok, source=src, path="k", fmt="int",
        note="probe %s: bindings replayed per arm and family" % mtok)

# --------------------------------------------------------------- router tax
for mtok, mname, slug in MODELS:
    add(macro="nRouter%sm" % mtok, source=ROUTER,
        path="m['%s'].tau_fit.m_slope" % mname, fmt="f1",
        note="router %s: tokens per library entry, slope of tau(n)" % mtok)
    add(macro="nRouter%smApi" % mtok, source=ROUTER,
        path="m['%s'].m_api_best_n100" % mname, fmt="f1",
        note="router %s: per-entry tokens measured at n=100" % mtok)
    add(macro="nRouter%sIntercept" % mtok, source=ROUTER,
        path="m['%s'].tau_fit.intercept" % mname, fmt="int",
        note="router %s: intercept of tau(n)" % mtok)
    for ntok, n in NGRID:
        add(macro="nRouter%sEps%s" % (mtok, ntok), source=ROUTER,
            path="routing['epsilon|%s']['%s'].epsilon" % (mname, n), fmt="f3",
            note="router %s: epsilon(n=%s), routing error rate" % (mtok, n))

add(macro="nRouterCalls", source=ROUTER, path="total_calls", fmt="int",
    note="router: routing calls in the m-library sweep")
add(macro="nRouterUsd", source=ROUTER, path="routing.total_cost_usd", fmt="usd2",
    default=0.29, note="router: spend on the routing sweep")
add(macro="nRouterQueriesPerCell", source=ROUTER,
    path="routing.cells['%s|n=0'].calls" % MODELS[0][1], fmt="int",
    note="router: routing queries per (model, n) cell")
add(macro="nRouterUsdAll", source=ROUTER, path="routing.total_cost_usd_incl_retry_and_smoke",
    fmt="usd2", note="router: spend including the retry and smoke runs")
add(macro="nRouterMdesign", source=ROUTER, path="manifest.m_design_o200k_all100", fmt="f1",
    note="router: designed manifest tokens per entry, o200k count at n=100")
add(macro="nRouterTauZero", value=368, fmt="int",
    note="router: fixed prompt overhead tau(0), from the earlier measurement")

# ------------------------------------------------------------- expr entries
fam_tok = {family: tok for tok, family in FAMILIES}

for mtok, mname, slug in MODELS:
    add(macro="n%sCFailRatio" % mtok,
        expr="n%sCFailMedian / n%sCAdmMedian" % (mtok, mtok), fmt="f1",
        note="%s: a failed compile costs this many times an admitted one" % mtok)
    add(macro="n%sUnadmitted" % mtok, expr="n%sCells - n%sAdmitted" % (mtok, mtok), fmt="int",
        note="%s: cells not admitted to deployment" % mtok)
    add(macro="n%sDocNegative" % mtok,
        expr="count(%s)" % ", ".join("n%s%sShareDoc < 0" % (mtok, f) for f, _ in FAMILIES),
        fmt="int", note="%s: cells where the doc arm cost more than exploring again" % mtok)
    add(macro="nRouter%sEpsMax" % mtok,
        expr="max(%s)" % ", ".join("nRouter%sEps%s" % (mtok, t) for t, _ in NGRID), fmt="f3",
        note="router %s: worst routing error rate over the n grid" % mtok)

add(macro="nCellsTotal", expr="nGLMCells + nDSCells", fmt="int",
    note="cells in the build grid, both models")
add(macro="nAdmittedTotal", expr="nGLMAdmitted + nDSAdmitted", fmt="int",
    note="cells admitted to deployment, both models")
add(macro="nDocNegativeTotal", expr="nGLMDocNegative + nDSDocNegative", fmt="int",
    note="cells where the doc arm cost more than exploring again, both models")
add(macro="nBuildUsd", expr="nGLMUsd + nDSUsd", fmt="usd2",
    note="total spend on the build grid")
add(macro="nDeployFailuresTotal",
    expr="sum(%s) - sum(%s)" % (
        ", ".join("n%s%sDeployN" % (m, f) for m, _, _ in MODELS for f, _ in FAMILIES),
        ", ".join("n%s%sDeploySucc" % (m, f) for m, _, _ in MODELS for f, _ in FAMILIES)),
    fmt="int", note="deployment failures over every deployed cell")
add(macro="nDeployFailuresLoud",
    expr="sum(%s)" % ", ".join("n%s%sDeployProgErr" % (m, f) for m, _, _ in MODELS for f, _ in FAMILIES),
    fmt="int", note="deployment failures that the program raised loudly")
add(macro="nProbeBreakUniformMax", expr="max(nProbeGLMBreakUniform, nProbeDSBreakUniform)",
    fmt="f2", note="worse of the two models' break probability under a uniform arm draw")
add(macro="nProbeSilentTotal", expr="nProbeGLMSilent + nProbeDSSilent", fmt="int",
    note="silent wrong outcomes over both models")
add(macro="nProbeRunsTotal", expr="nProbeGLMRuns + nProbeDSRuns", fmt="int",
    note="probe runs over both models")


# ------------------------------------------------- derived shares and ranges
ALL_CELLS = [(mtok, ftok) for mtok, _, _ in MODELS for ftok, _ in FAMILIES]
ADMITTED = [(mtok, ftok) for mtok, _, slug in MODELS for ftok, family in FAMILIES
            if by_key[(slug, family)]["headline"]["admitted"] is True]
EASY = [(m, f) for m, f in ADMITTED
        if by_key[(dict((t, s) for t, _, s in MODELS)[m], dict(FAMILIES)[f])]["headline"]["pi"] == 1.0]
HARD = [mf for mf in ADMITTED if mf not in EASY]


def names(pairs, suffix):
    return ", ".join("n%s%s%s" % (m, f, suffix) for m, f in pairs)


add(macro="nDocShareBest", expr="max(%s)" % names(ALL_CELLS, "ShareDoc"), fmt="f2",
    note="best share of c the doc arm saved, over all 14 cells")
add(macro="nDocShareMin", expr="min(%s)" % names(ALL_CELLS, "ShareDoc"), fmt="f2",
    note="worst share of c the doc arm saved, over all 14 cells")
add(macro="nDocShareMedian", expr="median(%s)" % names(ALL_CELLS, "ShareDoc"), fmt="f2",
    note="median share of c the doc arm saved, over all 14 cells")
add(macro="nDocShareMinAll", expr="nDocShareMin", fmt="f2",
    note="worst share of c the doc arm saved, over all 14 cells")
add(macro="nDocShareAboveQuarter",
    expr="count(%s)" % ", ".join("n%s%sShareDoc > 0.25" % (m, f) for m, f in ALL_CELLS),
    fmt="int", note="cells where the doc arm saved more than a quarter of an attempt")
AUTORPA_FAMILIES = ("Contacts", "Calendar", "MarkorCreate", "MarkorDelete")
AUTORPA = [mf for mf in ADMITTED if mf[1] in AUTORPA_FAMILIES]
add(macro="nAutorpaNstarBuildMin", expr="min(%s)" % names(AUTORPA, "NstarBuild"), fmt="f1",
    note="build break-even N* over the admitted cells of the four families AutoRPA reports (%s)"
         % ", ".join("%s/%s" % mf for mf in AUTORPA))
add(macro="nAutorpaNstarBuildMax", expr="max(%s)" % names(AUTORPA, "NstarBuild"), fmt="f1",
    note="build break-even N* over the admitted cells of the four families AutoRPA reports")
add(macro="nProbeGLMCleanFail", expr="1 - nProbeGLMCleanPass", fmt="f2",
    note="probe GLM: failure rate on the clean arm")
add(macro="nProgShareMin", expr="min(%s)" % names(ADMITTED, "ShareProg"), fmt="f2",
    note="smallest share of c the program saved, over the admitted cells")
add(macro="nProgShareMax", expr="max(%s)" % names(ADMITTED, "ShareProg"), fmt="f2",
    note="largest share of c the program saved, over the admitted cells")
add(macro="nPkOneTwoAdmittedCells",
    expr="count(%s)" % ", ".join(
        ["n%s%spOne >= 0.8" % (m, f) for m, f in ALL_CELLS]
        + ["n%s%spTwo >= 0.8" % (m, f) for m, f in ALL_CELLS]),
    fmt="int",
    note="of the 28 (cell, k) cases at k=1 and k=2, how many reached an initial gate rate of 0.8")
add(macro="nAdmittedCRange",
    parts=["min(%s)" % names(ADMITTED, "C"), "max(%s)" % names(ADMITTED, "C")],
    fmt="k0", note="compile price C over the admitted cells, smallest to largest")
add(macro="nAdmittedNstarBuildRange",
    parts=["min(%s)" % names(ADMITTED, "NstarBuild"), "max(%s)" % names(ADMITTED, "NstarBuild")],
    fmt="f1", note="build-price break-even N* over the admitted cells")
# Per-model N*_incl aggregates under the P0.1 floored numerator: over the
# model's admitted cells (the constants table's per_model quantities still
# carry the superseded mixed accounting, so they are not read here).
for mtok, _, _ in MODELS:
    adm_m = [mf for mf in ADMITTED if mf[0] == mtok]
    for stat in ("Median", "Min", "Max"):
        fn = stat.lower()
        add(macro="n%sNstarBuild%s" % (mtok, stat),
            expr="%s(%s)" % (fn, names(adm_m, "NstarBuild")), fmt="f2",
            note="%s: %s build-price break-even N* over the admitted cells (%s), "
                 "P0.1 floored numerator" % (mtok, fn, ", ".join("%s/%s" % mf for mf in adm_m)))

add(macro="nEasyNstarBuildRange",
    parts=["min(%s)" % names(EASY, "NstarBuild"), "max(%s)" % names(EASY, "NstarBuild")],
    fmt="f1",
    note="build-price break-even N* over the admitted cells that explored with pi = 1 (%s)"
         % ", ".join("%s/%s" % mf for mf in EASY))
add(macro="nHardNstarBuildRange",
    parts=["min(%s)" % names(HARD, "NstarBuild"), "max(%s)" % names(HARD, "NstarBuild")],
    fmt="f1",
    note="build-price break-even N* over the admitted cells that explored with pi < 1 (%s)"
         % ", ".join("%s/%s" % mf for mf in HARD))

# ------------------------------------------------------------------- unit
add(macro="nUnitRegressionCalls", value=1071, fmt="int",
    note="calls in the per-call price-weighted regression, docs/cache-adjusted-accounting.md section 10")

# =====================================================================
# Section 6: the simulation layer
# =====================================================================
SIM = "experimental-results/guiexp/t2_sim/"
E3 = SIM + "E3_v3ao.json"
E4 = SIM + "E4_tax_v3ao.json"
E5 = SIM + "E5_v2.json"
E11 = {"GLM": SIM + "E11_env_fragility_v3_android_glm.json",
       "DS": SIM + "E11_env_fragility_v3_android_ds.json"}
EMIXED = {"GLM": SIM + "E11_env_fragility_v3_mixed_android_glm.json",
          "DS": SIM + "E11_env_fragility_v3_mixed_android_ds.json"}

# E3: arrival streams crossed with the two measured cost sets.
E3_ROWS = [("React", "always_reactive"), ("Compile", "always_compile"),
           ("Second", "on_second"), ("Succ", "success_count"),
           ("Toolpro", "toolpro_port"), ("Breakeven", "breakeven_cap"),
           ("Oracle", "oracle"), ("Offline", "offline_opt")]
E3_STREAMS = [("Poisson", "poisson"), ("Zipf", "zipf"), ("Bursty", "bursty")]
E3_MODELS = [("GLM", "android_glm"), ("DS", "android_ds")]
for rtok, row in E3_ROWS:
    for stok, stream in E3_STREAMS:
        for mtok, cost in E3_MODELS:
            add(macro="nEthree%s%s%s" % (rtok, stok, mtok), source=E3,
                path="cells['%s/%s'].%s.rel_to_ours" % (stream, cost, row), fmt="f2",
                note="E3 %s / %s / %s: tokens relative to ours" % (row, stream, cost))

# E4: the router tax, four real streams at two price sheets.
E4_ROWS = [("React", "always_reactive"), ("Compile", "always_compile"),
           ("Second", "on_second"), ("Toolpro", "toolpro_port"),
           ("Breakeven", "breakeven_cap"), ("Truep", "ours_trueP"),
           ("Oracle", "oracle_tax"), ("Offline", "offline_opt_tax")]
E4_STREAMS = [("Sepsis", "sepsis"), ("Bpi", "bpi2019"),
              ("WikiA", "wiki_A"), ("WikiB", "wiki_B")]
E4_PRICES = [("Native", "native"), ("Fivem", "5M")]
for rtok, row in E4_ROWS:
    for stok, stream in E4_STREAMS:
        for ptok, price in E4_PRICES:
            add(macro="nEfour%s%s%s" % (rtok, stok, ptok), source=E4,
                path="cells['%s/price=%s'].%s.rel_to_ours" % (stream, price, row), fmt="f2",
                note="E4 %s / %s / price=%s: tokens relative to ours" % (row, stream, price))
E4_OFFLINE = ["nEfourOffline%s%s" % (stok, ptok) for stok, _ in E4_STREAMS for ptok, _ in E4_PRICES]
add(macro="nEfourOfflineGapMin",
    expr="min(%s)" % ", ".join("1 / %s" % n for n in E4_OFFLINE), fmt="f2",
    note="E4: smallest factor by which ours costs more than the offline optimum, over the 8 cells")
add(macro="nEfourOfflineGapMax",
    expr="max(%s)" % ", ".join("1 / %s" % n for n in E4_OFFLINE), fmt="f2",
    note="E4: largest factor by which ours costs more than the offline optimum, over the 8 cells")

# E11: every policy against the better of the two naive rules, cell by cell.
E11_ROWS = [("React", "always_reactive"), ("Compile", "always_compile_evict"),
            ("Breakeven", "breakeven"), ("BreakevenCap", "breakeven_cap_epoch"),
            ("OursNocap", "ours_noinflate"), ("Ours", "${E11_OURS_ROW}"),
            ("OursHorizon", "ours_spend_cap"), ("OursEpoch", "ours_cap_epoch"),
            ("Offline", "offline_opt_tax")]
E11_STATS = [("Worst", "max", None), ("Median", "median", None),
             ("WorstPzero", "max", "p_zero"), ("WorstDrift", "max", "drift")]
for mtok, src in E11.items():
    for rtok, row in E11_ROWS:
        for sttok, stat, subset in E11_STATS:
            args = {"row": row, "stat": stat}
            if subset:
                args["subset"] = subset
            add(macro="nEeleven%s%s%s" % (mtok, rtok, sttok), source=src,
                compute="e11_ratio", args=args, fmt="f2",
                note="E11 %s / %s: %s ratio to the better naive rule%s"
                     % (mtok, row, stat, ", " + subset + " cells" if subset else ""))
    add(macro="nEeleven%sOfflineBeatsPct" % mtok, source=src,
        compute="e11_beats_share", args={"row": "offline_opt_tax", "margin": 0.05},
        fmt="pct0",
        note="E11 %s: share of cells where the offline optimum beats both naive rules by over 5%%" % mtok)
add(macro="nEelevenCells", source=E11["GLM"], compute="e11_cell_count", fmt="int",
    note="E11: grid cells per model")
add(macro="nEelevenOursWorstPct", expr="nEelevenGLMOursWorst - 1", fmt="pct0",
    note="E11: how far ours sits above the better naive rule in its worst cell, GLM costs")
add(macro="nEelevenACWorst", expr="nEelevenGLMCompileWorst", fmt="f2",
    note="E11: worst ratio of always-compile-with-eviction to the better naive rule, GLM costs")
add(macro="nEelevenARWorst", expr="nEelevenGLMReactWorst", fmt="f2",
    note="E11: worst ratio of always-reactive to the better naive rule, GLM costs")

# E11 mixed block: the same comparison on a mixed-cost-set grid.
EMIXED_ROWS = [("React", "always_reactive"), ("Compile", "always_compile_evict"),
               ("OursNocap", "ours_noinflate"), ("Ours", "${E11_OURS_ROW}"),
               ("OursPop", "ours_pop_cap_realized"), ("Breakeven", "breakeven"),
               ("Oracle", "oracle_tax"), ("Offline", "offline_opt_tax")]
for mtok, src in EMIXED.items():
    for rtok, row in EMIXED_ROWS:
        for sttok, stat in (("Worst", "max"), ("Median", "median")):
            add(macro="nEmixed%s%s%s" % (mtok, rtok, sttok), source=src,
                compute="e11_ratio", args={"row": row, "stat": stat}, fmt="f2",
                note="E11 mixed %s / %s: %s ratio to the better naive rule" % (mtok, row, stat))
        add(macro="nEmixed%s%sBeats" % (mtok, rtok), source=src,
            compute="e11_beats_share",
            args={"row": row, "margin": 0.05, "as_count": True}, fmt="int",
            note="E11 mixed %s / %s: cells beating both naive rules by over 5%%" % (mtok, row))
    add(macro="nEmixed%sOursMedReact" % mtok, source=src, compute="e11_pair_ratio",
        args={"row": "${E11_OURS_ROW}", "vs": "always_reactive", "stat": "median"}, fmt="f2",
        note="E11 mixed %s: median ours / always-reactive" % mtok)
    add(macro="nEmixed%sOursMedCompile" % mtok, source=src, compute="e11_pair_ratio",
        args={"row": "${E11_OURS_ROW}", "vs": "always_compile_evict", "stat": "median"}, fmt="f2",
        note="E11 mixed %s: median ours / always-compile-with-eviction" % mtok)
add(macro="nEmixedCells", source=EMIXED["GLM"], compute="e11_cell_count", fmt="int",
    note="E11 mixed: grid cells per model")

# E5: each ablation block, mean over its cells of candidate / clairvoyant.
E5_BLOCKS = [
    ("Cooldown", "a_cooldown/", [("Fixed", "fixed_3"), ("Inflate", "inflate_2x"),
                                 ("Blacklist", "blacklist")]),
    ("Decay", "b_decay/", [("None", "T_inf"), ("Sixty", "T_60"),
                           ("OneTwenty", "T_120"), ("Thirty", "T_30")]),
    ("Prior", "c_prior/", [("Population", "population"), ("GammaOneTwenty", "gamma_1_20"),
                           ("GammaOneFive", "gamma_1_5"), ("GammaHalfTen", "gamma_05_10")]),
    ("Horizon", "d_horizon/", [("Doubling", "doubling"), ("CappedDoubling", "capped_doubling"),
                               ("Fixed", "fixed_60"), ("CappedFixed", "capped_fixed")]),
    ("Gate", "f_gate_prior/", [("TrueP", "trueP"), ("AddOne", "add_one"), ("Pi", "pi_prior"),
                               ("AddOneCap", "add_one_cap"), ("PiCap", "pi_prior_cap")]),
]
for btok, prefix, cands in E5_BLOCKS:
    for ctok, cand in cands:
        add(macro="nEfive%s%s" % (btok, ctok), source=E5,
            path="cells[*key^=%s].%s.rel_clairvoyant" % (prefix, cand),
            agg="mean", fmt="f2", optional=True,
            note="E5 %s / %s: mean tokens over the block's cells, relative to clairvoyant"
                 % (prefix.rstrip("/"), cand))

# ------------------------------------------------------ probe, arm families
APPEARANCE = ["FontLarge", "FontSmall", "DensitySmall", "LocaleFr", "DarkTheme"]
INTERRUPTION = ["Notification", "LowBattery", "PermissionDialog", "UpdatePrompt"]
for mtok, _, _ in MODELS:
    add(macro="nProbe%sAppearanceBreak" % mtok,
        expr="mean(%s)" % ", ".join("nProbe%s%sBreak" % (mtok, a) for a in APPEARANCE),
        fmt="f2", note="probe %s: mean RSR-based break rate over the five appearance arms" % mtok)
    add(macro="nProbe%sInterruptionBreak" % mtok,
        expr="mean(%s)" % ", ".join("nProbe%s%sBreak" % (mtok, a) for a in INTERRUPTION),
        fmt="f2", note="probe %s: mean RSR-based break rate over the four interruption arms" % mtok)
add(macro="nProbeAppearanceBreak",
    expr="mean(nProbeGLMAppearanceBreak, nProbeDSAppearanceBreak)",
    fmt="f2", note="probe: mean RSR-based break rate over the five appearance arms, both models")
add(macro="nProbeInterruptionBreak",
    expr="mean(nProbeGLMInterruptionBreak, nProbeDSInterruptionBreak)",
    fmt="f2", note="probe: mean RSR-based break rate over the four interruption arms, both models")

seen = set()
for e in entries:
    assert e["macro"] not in seen, e["macro"]
    seen.add(e["macro"])

with open(OUT, "w") as fh:
    json.dump({"vars": {"E11_OURS_ROW": "ours_cap_realized"}, "entries": entries}, fh, indent=1)
    fh.write("\n")
print("wrote %s with %d entries" % (OUT, len(entries)))
