"""Experiment builders for the T2 layer: E3, E4, E5, E8.

Each experiment is decomposed into independent cells (a pattern/cost-set, a
stream/price, a mechanism-variant set, ...).  Cells are pure dicts, executed
by a multiprocessing pool (one cell per task), and the parent assembles
means, relative-to-ours ratios and 95% bootstrap CIs and prints the tables.

Experiments (docs/experiment-design-audit.md rows 9-13, T2.3-T2.6):

  E3  synthetic headline: 3 arrival patterns x 2 cost sets (OpenApps +
      Android constants) at the native price; 8 policy rows + offline
      optimum, relative to ours, +-95% bootstrap CI over reps.
  E4  real streams: Wikipedia-A, Wikipedia-B, Sepsis x prices {native,
      autorpa_233k, 1M, 5M} + ours ablations (Gamma(1,5), Gamma(1,20),
      fixed 60-step horizon) + oracle/offline/break-even; BPI 2019
      replayed held-out.
  E5  mechanism stress under the new constants: cooldown x gate rate,
      evidence decay, arrival prior, horizon mode -- at stress prices, the
      30k-arrival stream, and the real selection streams; paired seeding
      with a clairvoyant reference and paired SEs.
  E8  price x artifact-strength sweep (both seedings) plus tau/eps
      sensitivity: always-compile with the growing manifest tax and the
      selection-failure cliff vs ours.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import multiprocessing as mp
import random
import time
import zlib
from pathlib import Path

if __package__ in (None, ""):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import sim
import stats
import streams as streams_mod

REPO_ROOT = streams_mod.REPO_ROOT
DEFAULT_OUT_DIR = REPO_ROOT / "experimental-results" / "guiexp" / "t2_sim"

E4_POLICIES = ["always_reactive", "always_compile", "on_second",
               "success_count", "toolpro_port", "breakeven", "oracle"]

# E3_TAX / E4_TAX: the same cells with the clairvoyant references split into
# a tax-blind and a tax-aware version (sim.ORACLES, sim.offline_optimum_tax).
# The tax-blind rows are bit-identical to the E3/E4 "oracle"/"offline_opt"
# rows -- paired seeding gives every row the same seed regardless of the row
# order -- and are relabelled so a table cannot be read as an upper bound
# when it is not one.
E4_TAX_POLICIES = ["always_reactive", "always_compile", "on_second",
                   "success_count", "toolpro_port", "breakeven",
                   "oracle_taxblind", "oracle_tax"]

# E4 under the deployment options added on 2026-09-09
# (docs/harder-families-and-routers.md part B).  Each is E4's 16 stream x
# price cells with one thing changed, so the columns are comparable:
#   E4_CRN        common random numbers, everything else as published
#   E4_EVICT      + the residency rule at T*, on always_compile and ours
#   E4_RETRIEVAL  + the top-k retrieval router instead of full listing
#   E4_KMIN3      + a compiler that needs 3 demonstrations (AutoRPA-style)
E4_ROUTER_POLICIES = ["always_reactive", "always_compile", "ours",
                      "oracle_tax"]
E4_EVICT_POLICIES = ["always_reactive", "always_compile",
                     "always_compile_evict", "ours", "ours_evict",
                     "oracle_tax"]
OUT_NAME = {"E3_TAX": "E3_tax", "E4_TAX": "E4_tax",
            "E4_CRN": "E4_crn", "E4_EVICT": "E4_evict",
            "E4_RETRIEVAL": "E4_retrieval", "E4_KMIN3": "E4_kmin3",
            "E5_TAX": "E5_tax", "E12": "E12_ablation"}


# ---------------------------------------------------------------------------
# constants handling
# ---------------------------------------------------------------------------

def load_constants(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())


def json_safe(obj):
    """Replace inf/nan with null so the output is standard JSON.

    Python's json module writes `Infinity`, which its own loader accepts and
    every strict parser (jq, JavaScript) rejects.  Nothing produced one until
    p = 0 became a grid level: N* = C_eff / s is infinite for a family whose
    gate never passes, which is the right answer and an unreadable file.
    """
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(v) for v in obj]
    return obj


def constants_fingerprint(constants: dict) -> str:
    return hashlib.sha256(
        json.dumps(constants, sort_keys=True).encode()).hexdigest()[:16]


def check_constants(constants: dict) -> tuple[list[str], list[str]]:
    """Return (hard issues, placeholder notices). Hard issues stop the run."""
    issues: list[str] = []
    placeholders: list[str] = []
    if constants.get("schema") != "t2sim.constants/1":
        issues.append("schema must be 't2sim.constants/1'")
    cs = constants.get("cost_sets") or {}
    if not cs:
        issues.append("cost_sets is empty")
    for name, cset in cs.items():
        if cset.get("status") == "placeholder":
            placeholders.append(f"cost_sets.{name} (placeholder)")
        for req in ("m", "tau0", "r_cache"):
            v = cset.get(req)
            if not isinstance(v, (int, float)) or v < 0:
                issues.append(f"cost_sets.{name}.{req} must be a number >= 0")
        layouts = cset.get("layouts") or {}
        if not layouts:
            issues.append(f"cost_sets.{name}.layouts is empty")
        for lname, lay in layouts.items():
            if lay.get("status") == "placeholder":
                placeholders.append(f"cost_sets.{name}.layouts.{lname}")
            for req in ("c", "L", "rho", "C", "p", "d", "q0"):
                v = lay.get(req)
                if not isinstance(v, (int, float)):
                    issues.append(
                        f"cost_sets.{name}.layouts.{lname}.{req} must be a "
                        f"number (fill the measured value)")
    ladder = constants.get("price_ladder") or []
    names = [p.get("name") for p in ladder]
    if "native" not in names:
        issues.append("price_ladder must contain a 'native' entry")
    if not constants.get("streams", {}).get("synthetic"):
        issues.append("streams.synthetic is required")
    if not constants.get("trigger"):
        issues.append("trigger is required")
    return issues, placeholders


def trigger_mech(constants: dict) -> dict:
    t = constants["trigger"]
    return {"cooldown": t["cooldown"],
            "cooldown_len": t["cooldown_len"],
            "half_life": t["half_life"],
            "prior_mode": t["prior_mode"],
            "prior_shape": t["prior_shape"],
            "prior_rate": t["prior_rate"],
            "horizon_mode": t["horizon_mode"],
            "horizon_cap": t.get("horizon_cap"),
            # Mechanisms (e) and (f).  Absent from every constants file that
            # predates them, and their absent values are the historical
            # behaviour, so no published cell moves.
            "gate_prior": t.get("gate_prior", "true"),
            "gate_prior_strength": float(
                t.get("gate_prior_strength", sim.PI_PRIOR_STRENGTH)),
            # The buy-price formula (sim.BUY_FORMULA_DOC).  Absent = "full",
            # Algorithm 1's B_hat (the W1.5 default); a constants file that
            # predates the key gets it, and at C_fail = C "full" evaluates
            # C/p_hat exactly, so no pre-W1.5 cell moves.
            "buy_formula": t.get("buy_formula", "full"),
            # A string selects a cap VARIANT (sim.SPEND_CAP_DOC); the
            # boolean is the historical "horizon" cap.
            "spend_cap": (t["spend_cap"]
                          if isinstance(t.get("spend_cap"), str)
                          else bool(t.get("spend_cap", False))),
            "spend_cap_mult": float(t.get("spend_cap_mult", 1.0))}


def price_C(constants: dict, price_name: str) -> float | None:
    """Absolute compile price for a ladder name; None = native (keep C)."""
    for entry in constants["price_ladder"]:
        if entry["name"] == price_name:
            return entry.get("C")
    raise KeyError(f"price {price_name!r} not in ladder")


def cost_set_profiles(constants: dict, cs_name: str):
    """(family-name -> engine profile, tau cfg, [layout names]) for a cost set."""
    cset = constants["cost_sets"][cs_name]
    tau_cfg = {"m": float(cset["m"]), "tau0": float(cset["tau0"])}
    layouts = cset["layouts"]
    profiles: dict[str, dict] = {}
    for lname, lay in layouts.items():
        profiles[lname] = {"c": float(lay["c"]), "d": float(lay["d"]),
                           "C": float(lay["C"]), "p": float(lay["p"]),
                           "q0": float(lay["q0"]), "h": 0.0,
                           "binding_space": 12}
        # Environment-level fragility (E11, sim.ENV_DOC).  Written into the
        # profile only when a layout sets a non-default value, so a cost set
        # that says nothing about them produces exactly the profile dict it
        # produced before the channels existed.
        for opt, dflt in sim.ENV_DEFAULTS.items():
            val = float(lay.get(opt, dflt))
            if val != dflt:
                profiles[lname][opt] = val
        # pi (demonstration success rate) and the failed-attempt price, both
        # written only when the layout says something, for the same reason.
        for opt, dflt in (("pi", sim.PI_DEFAULT), ("C_fail_mult", 1.0)):
            val = float(lay.get(opt, dflt))
            if val != dflt:
                profiles[lname][opt] = val
        if "C_fail" in lay:
            profiles[lname]["C_fail"] = float(lay["C_fail"])
        if lay.get("k_table"):
            # C(k), p(k) for a compiler that reads k demonstrations.  Use
            # C_scale/p_scale entries rather than absolute values: the price
            # ladder overwrites C, and a multiplier survives that.
            profiles[lname]["k_table"] = copy.deepcopy(lay["k_table"])
    return profiles, tau_cfg, list(layouts)


def eps_cfg(constants: dict) -> dict:
    e = copy.deepcopy(constants.get("epsilon_cliff") or {})
    e.setdefault("enabled", False)
    return e


def base_params(constants: dict, cs_name: str) -> dict:
    profiles, tau_cfg, _ = cost_set_profiles(constants, cs_name)
    return {"families": profiles, "horizon": constants["trigger"]["horizon_fixed"],
            "tau": tau_cfg, "epsilon": eps_cfg(constants)}


def scaled_params(params: dict, price: float | None = None,
                  strength: float | None = None,
                  tau: dict | None = None,
                  epsilon: dict | None = None) -> dict:
    """Copy of params at a hypothetical price / artifact strength / tau / eps.

    Artifact strength is the fraction of a reactive episode the deployed
    artifact removes, so d = (1 - strength) * c.
    """
    out = copy.deepcopy(params)
    for fam in out["families"].values():
        if price is not None:
            fam["C"] = float(price)
        if strength is not None:
            fam["d"] = (1.0 - strength) * fam["c"]
    if tau is not None:
        out["tau"] = dict(tau)
    if epsilon is not None:
        out["epsilon"] = dict(epsilon)
    return out


def with_gate(params: dict, rate: float) -> dict:
    out = copy.deepcopy(params)
    for fam in out["families"].values():
        fam["p"] = rate
    return out


def real_params(layout_profiles: dict, fam_names, price_C: float | None,
                tau_cfg: dict, eps: dict, horizon: float,
                deployment: dict | None = None,
                pi_mode: str | None = None, pi_seed: int = 0,
                regime: dict | None = None) -> dict:
    """Costs for a replayed log: measured layout profiles round-robin over
    the sorted family names, compile price overridden (same construction as
    realstream/real_stream_pilot.py).

    `deployment` carries the router / eviction / k_min options; absent, the
    engine runs the measured full-listing router with a monotone library and
    the one-demonstration compiler, which is what the published tables used.

    `pi_mode` gives the families a demonstration success rate.  None (the
    default) leaves every family at the layout's own pi, which is 1.0 unless
    the constants say otherwise; "population" draws one per family name from
    sim.PI_POPULATION, the t16_build batch's 6 / 3 / 5 split.  The draw is
    addressed by the name, so it is the same family in every repetition and
    under every policy.

    `regime` with mode "mixed" draws each family's ADMISSION regime from the
    model's measured population instead of giving every family the cell's
    one p (sim.MIXED_REGIME_DOC).  It overwrites p, C and C_fail_mult and
    leaves c, d and the environment channels alone; the price ladder still
    overrides C afterwards, exactly as it does for every other cell.
    """
    profiles = list(layout_profiles.values())
    families: dict[str, dict] = {}
    names = sorted(fam_names)
    mixed = bool(regime and regime.get("mode") == "mixed")
    if regime is not None and regime.get("mode") not in (None, "fixed",
                                                         "mixed"):
        raise ValueError(f"unknown regime {regime.get('mode')!r}")
    for i, name in enumerate(names):
        prof = dict(profiles[i % len(profiles)])
        if mixed:
            ok = sim.draw_admitted(name, regime["admission_rate"],
                                   regime.get("seed", 0))
            prof["p"] = 1.0 if ok else 0.0
            prof["C"] = float(regime["C"])
            prof["C_fail_mult"] = float(regime["C_fail_mult"])
        if price_C is not None:
            prof["C"] = float(price_C)
        if pi_mode == "population":
            prof["pi"] = sim.draw_pi(name, pi_seed)
        elif pi_mode not in (None, "fixed"):
            raise ValueError(f"unknown pi_mode {pi_mode!r}")
        families[name] = prof
    out = {"families": families, "horizon": horizon, "tau": tau_cfg,
           "epsilon": eps}
    if deployment:
        out.update(copy.deepcopy(deployment))
    return out


def native_strength(params: dict) -> float:
    """1 - d/c averaged over families: the measured artifact-strength point."""
    vals = [1.0 - f["d"] / f["c"] for f in params["families"].values()]
    return sum(vals) / len(vals)


# ---------------------------------------------------------------------------
# worker side
# ---------------------------------------------------------------------------

_W_CONSTANTS: dict = {}


def _init_worker(constants: dict) -> None:
    global _W_CONSTANTS
    _W_CONSTANTS = constants


def _seed_for(key: str, salt: str = "") -> int:
    return zlib.crc32((key + "|" + salt).encode())


# Metrics every cell has always reported.  The extended set is added only
# when a cell asks for it, so the published E3/E4/E5/E8 JSONs keep exactly
# the fields they had.
BASE_EXTRA = (("final_library", "final_library"), ("tau_total", "tau_total"),
              ("wasted", "wasted_compiles"), ("n_compiles", "n_compiles"))
EXTENDED_EXTRA = (("tax_share", "tax_share"),
                  ("max_library", "max_library"),
                  ("wasted_tax", "wasted_compiles_tax"),
                  ("wasted_tax_tokens", "wasted_tax_tokens"),
                  ("evictions", "n_evictions"),
                  ("relists", "n_relists"),
                  ("residency_T", "mean_residency_T"))
# E11 only: the environment-fragility counters.  Kept off the extended set
# so every published cell keeps exactly the fields it had.
ENV_EXTRA = (("program_deaths", "n_program_deaths"),
             ("silent_failures", "n_silent_failures"),
             ("use_failures", "n_use_failures"),
             ("deaths_delisted", "n_deaths_delisted"),
             ("failed_attempts", "failed_attempts"),
             ("failed_tokens", "failed_compile_tokens"))


def _extra_keys(extended: bool, env: bool = False):
    return (BASE_EXTRA + (EXTENDED_EXTRA if extended else ())
            + (ENV_EXTRA if env else ()))


def _extra_slots(extended: bool, env: bool = False) -> dict:
    return {k: [] for k, _ in _extra_keys(extended, env)}


def _extra_push(slot: dict, r: dict, extended: bool,
                env: bool = False) -> None:
    for k, field in _extra_keys(extended, env):
        slot[k].append(r[field])


def _coin_book(spec: dict, rep: int):
    """One CoinBook per (stream, rep), shared by every policy of that rep."""
    if not spec.get("crn"):
        return None
    return sim.CoinBook(spec["seed"] * 104729 + rep)


def _run_cell(spec: dict):
    t0 = time.time()
    kind = spec["kind"]
    if kind == "synthetic":
        res = _cell_synthetic(spec)
    elif kind == "real":
        res = _cell_real(spec)
    elif kind == "paired":
        res = _cell_paired(spec)
    else:
        raise ValueError(kind)
    res["wall_s"] = round(time.time() - t0, 3)
    return spec["key"], res


def _cell_synthetic(spec: dict) -> dict:
    params = spec["params"]
    mech = spec["mech"]
    fam_names = list(params["families"])
    policies = spec["policies"]
    ext = bool(spec.get("extended_metrics"))
    env = bool(spec.get("env_metrics"))
    ev_bound = params.get("eviction") if spec.get("offline_evict") else None
    rows = {p: [] for p in policies}
    extra = {p: _extra_slots(ext, env) for p in policies}
    traces: dict[str, list] = {}
    opts: list[float] = []
    opts_tax: list[float] = []
    for rep in range(spec["reps"]):
        srng = random.Random(spec["seed"] * 1000 + rep)
        cur = streams_mod.gen_stream(spec["pattern"], spec["n"], fam_names,
                                     srng)
        coins = _coin_book(spec, rep)
        if spec.get("offline"):
            opts.append(sim.offline_optimum(cur, params))
        if spec.get("offline_tax"):
            opts_tax.append(sim.offline_optimum_tax(cur, params, ev_bound))
        for p in policies:
            if spec.get("seeding", "paired") == "unpaired":
                # the submitted old table's seeding: each policy takes the
                # next draw of the stream rng as its own seed
                prng = random.Random(srng.random())
            else:
                prng = random.Random(spec["seed"] * 7717 + rep)
            r = sim.run_policy(p, cur, params, prng, mech, coins)
            rows[p].append(r["final_tokens"])
            _extra_push(extra[p], r, ext, env)
            if ext and rep == 0:
                traces[p] = r["n_curve"]
        # Variants: the same rule under a different mechanism block, or a
        # named rule of their own.  Absent from every cell that predates
        # them, so no existing synthetic row moves.
        for vname, vmech in (spec.get("variants") or {}).items():
            vpol = spec.get("variant_policies", {}).get(
                vname, spec.get("variant_policy", "ours"))
            prng = random.Random(spec["seed"] * 7717 + rep)
            r = sim.run_policy(vpol, cur, params, prng, vmech, coins)
            rows.setdefault(vname, []).append(r["final_tokens"])
            if vname not in extra:
                extra[vname] = _extra_slots(ext, env)
            _extra_push(extra[vname], r, ext, env)
            if ext and rep == 0:
                traces[vname] = r["n_curve"]
    return {"rows": rows, "extra": extra, "offline": opts,
            "offline_tax": (opts_tax or None),
            "n_lib_trace": (traces or None),
            "n_star": sim.n_star(params),
            "stream_summary": streams_mod.stream_summary(
                streams_mod.gen_stream(
                    spec["pattern"], spec["n"], fam_names,
                    random.Random(spec["seed"] * 1000)))}


def _cell_real(spec: dict) -> dict:
    stream = streams_mod.load_stream(spec["stream_spec"])
    params = real_params(spec["layouts"], set(stream), spec.get("price_C"),
                         spec["tau"], spec["epsilon"], spec["horizon"],
                         spec.get("deployment"), spec.get("pi_mode"),
                         spec.get("pi_seed", 0), spec.get("regime"))
    ext = bool(spec.get("extended_metrics"))
    env = bool(spec.get("env_metrics"))
    ev_bound = params.get("eviction") if spec.get("offline_evict") else None
    opt = sim.offline_optimum(stream, params) if spec.get("offline", True) \
        else None
    opt_tax = sim.offline_optimum_tax(stream, params, ev_bound) \
        if spec.get("offline_tax") else None
    rows: dict[str, list[float]] = {p: [] for p in spec["policies"]}
    extra = {p: _extra_slots(ext, env) for p in spec["policies"]}
    traces: dict[str, list] = {}
    for vname in spec["variants"]:
        rows[vname] = []
        extra[vname] = _extra_slots(ext, env)
    for rep in range(spec["reps"]):
        coins = _coin_book(spec, rep)
        for p in spec["policies"]:
            # Same SEED per policy (paired seeding, like _cell_synthetic),
            # which is what the joint-index bootstrap in
            # stats.bootstrap_ratio_ci pairs on.  It is not common random
            # numbers: policies draw different NUMBERS of coins (a compile
            # attempt draws a gate coin, a program-served arrival draws a
            # failure coin, a reactive one draws neither), so the streams
            # desynchronise at the first divergent decision and rep i of two
            # policies sees different bindings and different gate coins from
            # then on.
            r = sim.run_policy(p, stream, params,
                               random.Random(spec["seed"] * 7717 + rep),
                               spec["mech"], coins)
            rows[p].append(r["final_tokens"])
            _extra_push(extra[p], r, ext, env)
            if ext and rep == 0:
                traces[p] = r["n_curve"]
        for vname, vmech in spec["variants"].items():
            # A variant is the same DECISION RULE under a different mechanism
            # block.  E4's ours_g15/g120/hfix run it as plain "ours"; E11's
            # spend_cap / pi_prior rows have to run it as the evicting,
            # non-inflating deployment its reference row uses, so the policy
            # the variants are run under is a property of the cell.
            # A variant may name its own decision rule; without one the
            # cell's variant_policy applies, which is the historical path.
            vpol = spec.get("variant_policies", {}).get(
                vname, spec.get("variant_policy", "ours"))
            r = sim.run_policy(vpol, stream,
                               params,
                               random.Random(spec["seed"] * 7717 + rep),
                               vmech, coins)
            rows[vname].append(r["final_tokens"])
            _extra_push(extra[vname], r, ext, env)
            if ext and rep == 0:
                traces[vname] = r["n_curve"]
    return {"rows": rows, "extra": extra, "offline": opt,
            "offline_tax": opt_tax,
            "n_lib_trace": (traces or None),
            "n_star": sim.n_star(params),
            "stream_summary": streams_mod.stream_summary(stream),
            "price_name": spec.get("price_name")}


def _cell_paired(spec: dict) -> dict:
    """Every variant of 'ours' plus the clairvoyant on identical streams."""
    variants = spec["variants"]
    if "stream_spec" in spec:
        stream = streams_mod.load_stream(spec["stream_spec"])
        params = real_params(spec["layouts"], set(stream),
                             spec.get("price_C"), spec["tau"],
                             spec["epsilon"], spec["horizon"],
                             spec.get("deployment"))
        patterns = [spec.get("pattern_label", "real")]
        fixed_stream = stream
    else:
        params = spec["params"]
        patterns = spec["patterns"]
        fixed_stream = None
    n = spec.get("n", 0)
    reps = spec["reps"]
    seed = spec["seed"]
    acc = {v: {"tokens": [], "wasted": [], "compiles": [],
               "first_rank": []} for v in variants}
    acc["_clairvoyant"] = {"tokens": []}
    fam_names = list(params["families"])
    hot_fixed = sim.hottest(fixed_stream) if fixed_stream is not None else None
    for pattern in patterns:
        for rep in range(reps):
            if fixed_stream is None:
                srng = random.Random(seed * 1000 + rep)
                cur = streams_mod.gen_stream(pattern, n, fam_names, srng)
                hot = sim.hottest(cur)
            else:
                cur, hot = fixed_stream, hot_fixed
            for vname, vmech in variants.items():
                r = sim.run_policy("ours", cur, params,
                                   random.Random(seed * 7717 + rep), vmech)
                a = acc[vname]
                a["tokens"].append(r["final_tokens"])
                a["wasted"].append(r["wasted_compiles"])
                a["compiles"].append(r["n_compiles"])
                a["first_rank"].append(r["first_compile_rank"].get(hot, -1))
            # E5 as published built its reference with the tax-blind
            # "oracle" and no mech argument, so it ran under MECH_LEGACY.
            # Both are fixed in the E5_TAX variant, which asks for
            # clairvoyant="oracle_tax" and passes the deployed mech; the
            # published E5 keeps its defaults so its numbers do not move
            # (audit section 5.6).
            r = sim.run_policy(spec.get("clairvoyant", "oracle"), cur, params,
                               random.Random(seed * 7717 + rep),
                               spec.get("mech"))
            acc["_clairvoyant"]["tokens"].append(r["final_tokens"])
    ref = stats.mean(acc["_clairvoyant"]["tokens"])
    out: dict = {"clairvoyant_tokens": ref, "n_star": sim.n_star(params)}
    for vname in variants:
        a = acc[vname]
        m = stats.mean(a["tokens"])
        ranks = [x for x in a["first_rank"] if x > 0]
        out[vname] = {
            "mean_tokens": m,
            "regret_vs_clairvoyant": m - ref,
            "rel_clairvoyant": m / ref,
            "mean_compiles": stats.mean(a["compiles"]),
            "mean_wasted": stats.mean(a["wasted"]),
            "first_compile_rank_hot": (stats.mean(ranks) if ranks else -1),
            "never_compiled_hot_frac": (
                sum(1 for x in a["first_rank"] if x < 0) / len(a["first_rank"])),
        }
    best = min(variants, key=lambda v: out[v]["mean_tokens"])
    for vname in variants:
        d = stats.paired_diff_summary(acc[vname]["tokens"],
                                      acc[best]["tokens"])
        out[vname]["paired_diff_vs_best"] = d["mean_diff"]
        out[vname]["paired_se"] = d["se"]
    out["best_variant"] = best
    return out


# ---------------------------------------------------------------------------
# cell builders
# ---------------------------------------------------------------------------

def _e3_cells(constants: dict, reps: int, seed: int) -> list[dict]:
    mech = trigger_mech(constants)
    n = constants["streams"]["synthetic"]["n_arrivals"]
    patterns = constants["streams"]["synthetic"]["patterns"]
    price_name = constants.get("e3", {}).get("price", "native")
    cells = []
    for cs_name in constants.get("e3", {}).get(
            "cost_sets", ["openapps_glm"]):
        params = scaled_params(base_params(constants, cs_name),
                               price=price_C(constants, price_name))
        for pattern in patterns:
            cells.append({
                "kind": "synthetic",
                "key": f"{pattern}/{cs_name}",
                "pattern": pattern, "n": n, "reps": reps, "seed": seed,
                "policies": list(sim.POLICIES), "params": params,
                "mech": mech, "seeding": "paired", "offline": True,
                # Theorem 1's rule with the attempt-spend cap on top; the
                # plain breakeven row is already in sim.POLICIES.
                "variants": {"breakeven_cap": sim.mech_with(
                    mech, spend_cap=True, spend_cap_mult=1.0)},
                "variant_policies": {"breakeven_cap": "breakeven"},
            })
    return cells


def _e4_variants(mech: dict) -> dict:
    return {
        "ours": mech,
        "ours_g15": sim.mech_with(mech, prior_mode="gamma",
                                  prior_shape=1.0, prior_rate=5.0),
        "ours_g120": sim.mech_with(mech, prior_mode="gamma",
                                   prior_shape=1.0, prior_rate=20.0),
        "ours_hfix": sim.mech_with(mech, horizon_mode="fixed"),
        # The old oracle-p reference: the engine hands the trigger the
        # family's real p instead of estimating it (sim.GATE_PRIOR_DOC).
        # Every published E4 row before 2026-09-11 was this row.  It is
        # inert -- identical to "ours" -- when the constants leave
        # gate_prior at "true".
        "ours_trueP": sim.mech_with(mech, gate_prior="true"),
        # Theorem 1's accumulated-excess rule with the attempt-spend cap;
        # run under the "breakeven" decision rule, not under ours.
        "breakeven_cap": sim.mech_with(mech, spend_cap=True,
                                       spend_cap_mult=1.0),
    }


E4_VARIANT_POLICIES = {"breakeven_cap": "breakeven"}


def _e4_cells(constants: dict, reps: int, seed: int) -> list[dict]:
    e4 = constants.get("e4", {})
    cs_name = e4.get("cost_set", "openapps_glm")
    layouts, tau_cfg, _ = cost_set_profiles(constants, cs_name)
    eps = eps_cfg(constants)
    horizon = constants["trigger"]["horizon_fixed"]
    mech = trigger_mech(constants)
    cells = []
    stream_entries = []
    for sname in e4.get("streams", ["wiki_A", "wiki_B", "sepsis"]):
        stream_entries.append((sname, constants["streams"][sname]))
    if e4.get("bpi_heldout", True):
        stream_entries.append(("bpi2019", constants["streams"]["bpi2019"]))
    bpi_reps = e4.get("bpi_reps") or reps
    for sname, sspec in stream_entries:
        reps_here = bpi_reps if sname == "bpi2019" else reps
        for price_name in e4.get(
                "prices", ["native", "autorpa_233k", "1M", "5M"]):
            cells.append({
                "kind": "real",
                "key": f"{sname}/price={price_name}",
                "stream_spec": sspec, "layouts": layouts, "tau": tau_cfg,
                "epsilon": eps, "horizon": horizon,
                "price_C": price_C(constants, price_name),
                "price_name": price_name,
                "reps": reps_here, "seed": seed,
                "policies": list(E4_POLICIES),
                "variants": _e4_variants(mech),
                "variant_policies": dict(E4_VARIANT_POLICIES),
                "mech": mech,
                "offline": True,
            })
    return cells


def _e5_cells(constants: dict, reps: int, seed: int) -> list[dict]:
    e5 = constants.get("e5", {})
    cs_name = e5.get("cost_set", "openapps_glm")
    base = base_params(constants, cs_name)
    mech = trigger_mech(constants)
    n = constants["streams"]["synthetic"]["n_arrivals"]
    n_long = e5.get("long_n", 30000)
    hot_pats = constants["streams"]["synthetic"]["patterns"][:2] \
        if len(constants["streams"]["synthetic"]["patterns"]) >= 2 \
        else ["poisson", "zipf"]
    cells: list[dict] = []

    def paired_syn(key, params, patterns, variants, n_arr):
        return {"kind": "paired", "key": key, "patterns": list(patterns),
                "n": n_arr, "params": params, "variants": variants,
                "reps": reps, "seed": seed}

    def paired_real(key, stream_spec, price_name, variants, reps_here):
        return {"kind": "paired", "key": key, "stream_spec": stream_spec,
                "layouts": cost_set_profiles(constants, cs_name)[0],
                "tau": base["tau"], "epsilon": base["epsilon"],
                "horizon": base["horizon"],
                "price_C": price_C(constants, price_name),
                "pattern_label": f"{key.split('/')[0]}",
                "variants": variants, "reps": reps_here, "seed": seed}

    # (a) cooldown after a failed compile, under gate-rate stress
    cooldown_variants = {
        "fixed_3": sim.mech_with(mech, cooldown="fixed"),
        "inflate_2x": sim.mech_with(mech, cooldown="inflate"),
        "blacklist": sim.mech_with(mech, cooldown="blacklist"),
    }
    for g in e5.get("gate_rates", [0.3, 0.6, 1.0]):
        cells.append(paired_syn(
            f"a_cooldown/gate={g}", with_gate(base, g),
            constants["streams"]["synthetic"]["patterns"],
            cooldown_variants, n))

    # (b) evidence decay, at stress prices / hot-to-cold / the 30k stream
    decay_variants = {
        "T_inf": sim.mech_with(mech, half_life=None),
        "T_120": sim.mech_with(mech, half_life=120.0),
        "T_60": sim.mech_with(mech, half_life=60.0),
        "T_30": sim.mech_with(mech, half_life=30.0),
    }
    for pname in e5.get("stress_prices", ["native", "5M"]):
        rp = scaled_params(base, price=price_C(constants, pname))
        for pat in ("bursty", "regime_shift"):
            cells.append(paired_syn(f"b_decay/{pname}/{pat}", rp, [pat],
                                    decay_variants, n))
        cells.append(paired_syn(f"b_decay/{pname}/hot_streams", rp, hot_pats,
                                decay_variants, n))
        cells.append(paired_syn(f"b_decay/{pname}/long{n_long}_bursty", rp,
                                ["bursty"], decay_variants, n_long))

    # (c) arrival prior, across the price range + real selection streams
    prior_variants = {
        "gamma_1_20": sim.mech_with(mech, prior_mode="gamma",
                                    prior_shape=1.0, prior_rate=20.0),
        "gamma_1_5": sim.mech_with(mech, prior_mode="gamma",
                                   prior_shape=1.0, prior_rate=5.0),
        "population": sim.mech_with(mech, prior_mode="population"),
    }
    for pname in e5.get("prices", ["native", "1M", "5M"]):
        rp = scaled_params(base, price=price_C(constants, pname))
        cells.append(paired_syn(
            f"c_prior/{pname}", rp,
            list(streams_mod.ALL_PATTERNS), prior_variants, n))
    for sname in e5.get("selection_streams", ["sepsis", "helpdesk"]):
        for pname in e5.get("real_prices", ["native", "1M", "5M"]):
            cells.append(paired_real(
                f"c_prior/real:{sname}/{pname}",
                constants["streams"][sname], pname, prior_variants, reps))

    # (d)+(e) horizon mode: the deployment-age estimate and its ablations
    horizon_variants = {
        "fixed_60": sim.mech_with(mech, horizon_mode="fixed"),
        "doubling": sim.mech_with(mech, horizon_mode="doubling"),
        "capped_doubling": sim.mech_with(mech,
                                         horizon_mode="capped_doubling"),
        "capped_fixed": sim.mech_with(mech, horizon_mode="capped_fixed"),
    }
    for pname in e5.get("stress_prices", ["native", "5M"]):
        rp = scaled_params(base, price=price_C(constants, pname))
        for pat in ("bursty", "regime_shift"):
            cells.append(paired_syn(f"d_horizon/{pname}/{pat}", rp, [pat],
                                    horizon_variants, n))
        cells.append(paired_syn(f"d_horizon/{pname}/long{n_long}_bursty", rp,
                                ["bursty"], horizon_variants, n_long))
    # (f) gate-rate estimator and attempt-spend cap (the two mechanisms of
    # 2026-09-11; sim.GATE_PRIOR_DOC, sim.SPEND_CAP_DOC).  The gate rate is
    # the axis they act on, so this block sweeps it, p = 0 included: a
    # family that can never be compiled is where the estimator decides
    # whether the trigger keeps paying for attempts.
    gate_variants = {
        "trueP": sim.mech_with(mech, gate_prior="true", spend_cap=False),
        "add_one": sim.mech_with(mech, gate_prior="add_one",
                                 spend_cap=False),
        "pi_prior": sim.mech_with(mech, gate_prior="pi", spend_cap=False),
        "add_one_cap": sim.mech_with(mech, gate_prior="add_one",
                                     spend_cap=True, spend_cap_mult=1.0),
        "pi_prior_cap": sim.mech_with(mech, gate_prior="pi", spend_cap=True,
                                      spend_cap_mult=1.0),
    }
    for pname in e5.get("stress_prices", ["native", "5M"]):
        rp = scaled_params(base, price=price_C(constants, pname))
        for g in e5.get("gate_rates", [0.3, 0.6, 1.0]):
            cells.append(paired_syn(
                f"f_gate_prior/{pname}/gate={g}", with_gate(rp, g),
                constants["streams"]["synthetic"]["patterns"],
                gate_variants, n))

    if e5.get("bpi_horizon_heldout", True):
        bpi_reps = e5.get("bpi_reps") or max(1, reps // 4)
        for pname in e5.get("stress_prices", ["native", "5M"]):
            cells.append(paired_real(
                f"d_horizon/heldout:bpi2019/{pname}",
                constants["streams"]["bpi2019"], pname, horizon_variants,
                bpi_reps))
    return cells


def _e8_cells(constants: dict, reps: int, seed: int) -> list[dict]:
    e8 = constants.get("e8", {})
    cs_name = e8.get("cost_set", "openapps_glm")
    base = base_params(constants, cs_name)
    mech = trigger_mech(constants)
    n = constants["streams"]["synthetic"]["n_arrivals"]
    patterns = constants["streams"]["synthetic"]["patterns"]
    cells: list[dict] = []

    strengths = list(e8.get("strengths", [0.5, 0.75, 1.0]))
    if e8.get("include_native_strength", True):
        strengths = strengths + ["native"]

    # part 1: price x artifact-strength grid, both seedings
    for pname in e8.get("prices", ["native", "autorpa_233k", "1M", "5M",
                                   "xu_10M"]):
        for strength in strengths:
            sp = scaled_params(base, price=price_C(constants, pname),
                               strength=(None if strength == "native"
                                         else float(strength)))
            for seeding in ("unpaired", "paired"):
                for pattern in patterns:
                    cells.append({
                        "kind": "synthetic",
                        "key": (f"grid/{pname}/s={strength}/{seeding}/"
                                f"{pattern}"),
                        "pattern": pattern, "n": n, "reps": reps,
                        "seed": seed,
                        "policies": list(sim.POLICIES), "params": sp,
                        "mech": mech, "seeding": seeding, "offline": False,
                    })

    # parts 2+3: tau / eps sensitivity at easy and hard price points
    tau_modes = {
        "off": {"m": 0.0, "tau0": 0.0},
        "measured": {"m": base["tau"]["m"], "tau0": base["tau"]["tau0"]},
        "10x": {"m": 10.0 * base["tau"]["m"], "tau0": base["tau"]["tau0"]},
    }
    tau_modes = {k: v for k, v in tau_modes.items()
                 if k in e8.get("tau_modes", list(tau_modes))}
    eps_modes = {
        "off": {"enabled": False},
        "cliff": {"enabled": True, "eps0": 0.3, "cliff_start": 100,
                  "cliff_slope": 20},
        "harsh": {"enabled": True, "eps0": 0.5, "cliff_start": 60,
                  "cliff_slope": 15},
    }
    eps_modes = {k: v for k, v in eps_modes.items()
                 if k in e8.get("eps_modes", list(eps_modes))}
    for pname in e8.get("sens_prices", ["native", "5M"]):
        for strength in ("native", 1.0):
            cond = scaled_params(base, price=price_C(constants, pname),
                                 strength=(None if strength == "native"
                                           else 1.0))
            for tname, tcfg in tau_modes.items():
                tp = scaled_params(cond, tau=tcfg)
                for pattern in patterns:
                    cells.append({
                        "kind": "synthetic",
                        "key": (f"tau/{pname}/s={strength}/{tname}/"
                                f"{pattern}"),
                        "pattern": pattern, "n": n, "reps": reps,
                        "seed": seed,
                        "policies": ["always_reactive", "always_compile",
                                     "ours", "oracle"],
                        "params": tp, "mech": mech, "seeding": "paired",
                        "offline": False,
                    })
            for ename, ecfg in eps_modes.items():
                ep = scaled_params(cond, epsilon=ecfg)
                for pattern in patterns:
                    cells.append({
                        "kind": "synthetic",
                        "key": (f"eps/{pname}/s={strength}/{ename}/"
                                f"{pattern}"),
                        "pattern": pattern, "n": n, "reps": reps,
                        "seed": seed,
                        "policies": ["always_reactive", "always_compile",
                                     "ours"],
                        "params": ep, "mech": mech, "seeding": "paired",
                        "offline": False,
                    })
    return cells


def _e3_tax_cells(constants: dict, reps: int, seed: int) -> list[dict]:
    """E3 with the clairvoyant references split tax-blind / tax-aware."""
    cells = _e3_cells(constants, reps, seed)
    for cell in cells:
        cell["policies"] = list(sim.POLICIES_TAX)
        cell["offline_tax"] = True
    return cells


def _e4_tax_cells(constants: dict, reps: int, seed: int) -> list[dict]:
    """E4 with the clairvoyant references split tax-blind / tax-aware."""
    cells = _e4_cells(constants, reps, seed)
    for cell in cells:
        cell["policies"] = list(E4_TAX_POLICIES)
        cell["offline_tax"] = True
    return cells


def _deployment(constants: dict, kind: str) -> dict:
    """The router / eviction / k_min block a deployment cell runs under."""
    dep: dict = {}
    d = constants.get("deployment") or {}
    if kind in ("evict", "retrieval", "kmin3"):
        dep["eviction"] = copy.deepcopy(
            d.get("eviction") or sim.EVICTION_DEFAULT)
    if kind == "retrieval":
        dep["router"] = copy.deepcopy(
            d.get("retrieval_router") or sim.ROUTER_RETRIEVAL)
    if kind == "kmin3":
        dep["k_min"] = int(d.get("k_min_demo", 3))
    return dep


def _e4_deploy_cells(constants: dict, reps: int, seed: int, kind: str,
                     policies: list[str]) -> list[dict]:
    """E4's 16 stream x price cells with one deployment option changed.

    Common random numbers are on in all four so the columns are paired coin
    for coin; the ours ablations (g15/g120/hfix) are dropped because the
    comparison under test is between policies, not between variants of ours.
    """
    dep = _deployment(constants, kind)
    cells = _e4_cells(constants, reps, seed)
    evicting = any(p in sim.POLICIES_EVICT for p in policies)
    for cell in cells:
        cell["policies"] = list(policies)
        cell["variants"] = {}
        cell["crn"] = True
        cell["extended_metrics"] = True
        cell["offline_tax"] = True
        cell["offline_evict"] = evicting
        cell["deployment"] = dep
    return cells


def _e4_crn_cells(constants, reps, seed):
    return _e4_deploy_cells(constants, reps, seed, "crn",
                            E4_ROUTER_POLICIES)


def _e4_evict_cells(constants, reps, seed):
    return _e4_deploy_cells(constants, reps, seed, "evict",
                            E4_EVICT_POLICIES)


def _e4_retrieval_cells(constants, reps, seed):
    return _e4_deploy_cells(constants, reps, seed, "retrieval",
                            E4_EVICT_POLICIES)


def _e4_kmin3_cells(constants, reps, seed):
    return _e4_deploy_cells(constants, reps, seed, "kmin3",
                            E4_EVICT_POLICIES)


def _e5_tax_cells(constants: dict, reps: int, seed: int) -> list[dict]:
    """E5 with its clairvoyant reference recomputed under the stream objective.

    The published E5 measures every variant against sim.run_policy("oracle"),
    the tax-blind rule, and calls it with no mech, so the reference also ran
    under MECH_LEGACY.  Six of its 100 variant rows come out below their own
    clairvoyant, one of them at 0.085 (audit section 5.6).  This rebuilds the
    same cells against oracle_tax under the deployed mechanism.
    """
    mech = trigger_mech(constants)
    cells = _e5_cells(constants, reps, seed)
    for cell in cells:
        cell["clairvoyant"] = "oracle_tax"
        cell["mech"] = mech
    return cells


# E12 (2026-09-20): one-at-a-time mechanism ablation of Algorithm 1 on the
# real streams.  Every cell runs the full mechanism (the E4 "ours" row and
# its always_reactive anchor) plus six rows that each remove ONE piece: five
# mechanism blocks switched via mech_with (E5's stress axes), and the
# Theorem-1 narrow-price accumulated-excess rule run as its own policy, so
# the table can put formula ablation next to mechanism ablation.
#
# The ours / always_reactive rows of the cost set E4 ran on are bit-identical
# to the authoritative E4 file's cells by construction -- same constants
# fingerprint (the config is built by build_constants_e12.py to hash exactly
# like the E4 run's), same cell construction, same paired seeds, no CRN.
# e12_postcheck.py verifies that against the E4 result file and records
# meta.e4_identity_ok.
E12_ROW_ORDER = ["ours", "always_reactive", "narrow_trigger",
                 "fixed_cooldown", "no_decay", "gamma_prior",
                 "fixed_horizon", "no_spend_cap"]


def _e12_cells(constants: dict, reps: int, seed: int) -> list[dict]:
    e4 = constants.get("e4", {})
    e12 = constants.get("e12", {})
    mech = trigger_mech(constants)
    eps = eps_cfg(constants)
    horizon = constants["trigger"]["horizon_fixed"]
    cs_names = e12.get("cost_sets", ["android_glm", "android_ds"])
    stream_names = e12.get(
        "streams",
        list(e4.get("streams", ["wiki_A", "wiki_B", "sepsis"]))
        + (["bpi2019"] if e4.get("bpi_heldout", True) else []))
    prices = e12.get("prices", ["native", "5M"])
    bpi_reps = e4.get("bpi_reps") or reps
    variants = {
        "fixed_cooldown": sim.mech_with(mech, cooldown="fixed"),
        "no_decay": sim.mech_with(mech, half_life=None),
        # E5's c_prior gamma_1_20 row: the Gamma(1, 20) arrival prior.
        "gamma_prior": sim.mech_with(mech, prior_mode="gamma",
                                     prior_shape=1.0, prior_rate=20.0),
        "fixed_horizon": sim.mech_with(mech, horizon_mode="fixed"),
        "no_spend_cap": sim.mech_with(mech, spend_cap=False),
        # Not a mechanism change: the same cells under the Theorem-1
        # narrow-price rule, so formula vs mechanism ablation share a table.
        "narrow_trigger": mech,
    }
    variant_policies = {"narrow_trigger": "breakeven"}
    cells = []
    for cs_name in cs_names:
        layouts, tau_cfg, _ = cost_set_profiles(constants, cs_name)
        for sname in stream_names:
            reps_here = bpi_reps if sname == "bpi2019" else reps
            for pname in prices:
                cells.append({
                    "kind": "real",
                    "key": f"{cs_name}/{sname}/price={pname}",
                    "stream_spec": constants["streams"][sname],
                    "layouts": layouts, "tau": tau_cfg, "epsilon": eps,
                    "horizon": horizon,
                    "price_C": price_C(constants, pname),
                    "price_name": pname,
                    "reps": reps_here, "seed": seed,
                    "policies": ["always_reactive", "ours"],
                    "variants": variants,
                    "variant_policies": variant_policies,
                    "mech": mech,
                    "offline": False,
                })
    return cells


def _assemble_e12(results: dict, B: int) -> dict:
    out = {}
    for key, res in sorted(results.items()):
        table = assemble_table(key, res["rows"], E12_ROW_ORDER, "ours",
                               None, B, res["extra"])
        table["_n_star"] = res["n_star"]
        table["_stream"] = res["stream_summary"]
        # The ablation summary column: variant mean over the ours mean,
        # emitted under its table name (assemble_table already stored the
        # same quantity as rel_to_ours).
        m_ours = table["ours"]["mean_tokens"]
        for name, rec in table.items():
            if not name.startswith("_"):
                rec["ratio_to_ours"] = rec["mean_tokens"] / m_ours
        out[key] = table
        _print_cell(key, table, E12_ROW_ORDER)
    return out


CELL_BUILDERS = {"E3": _e3_cells, "E4": _e4_cells, "E5": _e5_cells,
                 "E8": _e8_cells,
                 "E3_TAX": _e3_tax_cells, "E4_TAX": _e4_tax_cells,
                 "E4_CRN": _e4_crn_cells, "E4_EVICT": _e4_evict_cells,
                 "E4_RETRIEVAL": _e4_retrieval_cells,
                 "E4_KMIN3": _e4_kmin3_cells,
                 "E5_TAX": _e5_tax_cells,
                 "E12": _e12_cells}


def apply_quick(constants: dict) -> dict:
    """Shrink every stream/grid so a smoke run covers all code paths."""
    c = copy.deepcopy(constants)
    c["streams"]["synthetic"]["n_arrivals"] = 60
    c["streams"]["synthetic"]["n_arrivals_long"] = 300
    for k in ("wiki_A", "wiki_B"):
        c["streams"][k]["window"] = 600
    c["streams"]["bpi2019"]["window"] = 1500
    c["e4"]["prices"] = ["native", "1M"]
    c["e5"]["gate_rates"] = [0.3]
    c["e5"]["stress_prices"] = ["native"]
    c["e5"]["prices"] = ["native"]
    c["e5"]["real_prices"] = ["native"]
    c["e5"]["long_n"] = 300
    c["e8"]["prices"] = ["native", "5M"]
    c["e8"]["strengths"] = [1.0]
    c["e8"]["include_native_strength"] = False
    c["e8"]["tau_modes"] = ["off", "measured"]
    c["e8"]["eps_modes"] = ["off", "cliff"]
    c.setdefault("e12", {})["cost_sets"] = ["android_ds"]
    c["e12"]["prices"] = ["native"]
    return c


# ---------------------------------------------------------------------------
# assembly + printing
# ---------------------------------------------------------------------------

def assemble_table(key: str, rows: dict, order: list[str], ours_key: str,
                   offline, B: int, extra: dict | None = None,
                   offline_label: str = "offline_opt",
                   offline_tax=None) -> dict:
    m_ours = stats.mean(rows[ours_key])
    table: dict = {}
    for p in order:
        if p not in rows:      # a row the cell did not run (deployment sets
            continue           # differ in which policies they compare)
        m = stats.mean(rows[p])
        rec = {"mean_tokens": m, "n_reps": len(rows[p])}
        if p == ours_key:
            rec["rel_to_ours"] = 1.0
            rec["ci95_tokens"] = list(stats.bootstrap_mean_ci(
                rows[p], B=B, seed=_seed_for(key, p)))
        else:
            rec["rel_to_ours"] = m / m_ours
            rec["rel_to_ours_ci95"] = list(stats.bootstrap_ratio_ci(
                rows[p], rows[ours_key], B=B, seed=_seed_for(key, p)))
        if extra and p in extra:
            for k, v in extra[p].items():
                rec["mean_" + k] = stats.mean(v)
        table[p] = rec
    for label, off in ((offline_label, offline),
                       ("offline_opt_tax", offline_tax)):
        if off is None:
            continue
        vals = off if isinstance(off, list) else [off] * len(rows[ours_key])
        m = stats.mean(vals)
        rec = {"mean_tokens": m, "rel_to_ours": m / m_ours}
        if isinstance(off, list) and len(off) > 1:
            # "opt" is the historical salt of the tax-blind offline row;
            # keep it so E3/E4 reproduce bitwise under any relabelling.
            salt = "opt" if off is offline else label
            rec["rel_to_ours_ci95"] = list(stats.bootstrap_ratio_ci(
                off, rows[ours_key], B=B, seed=_seed_for(key, salt)))
        table[label] = rec
    return table


def _print_cell(key: str, table: dict, order: list[str]) -> None:
    print(f"\n== {key}  (N*={table.get('_n_star', float('nan')):.3f}) ==")
    for p in order:
        rec = table.get(p)
        if rec is None:
            continue
        ci_str = ""
        if "rel_to_ours_ci95" in rec:
            ci = rec["rel_to_ours_ci95"]
            ci_str = f"  CI95 [{ci[0]:.3f}, {ci[1]:.3f}]"
        elif "ci95_tokens" in rec:
            ci = rec["ci95_tokens"]
            ci_str = f"  CI95 [{ci[0]:,.0f}, {ci[1]:,.0f}] tok"
        extra = ""
        if "mean_final_library" in rec:
            extra = f"  n_lib={rec['mean_final_library']:.1f}"
        if "mean_tau_total" in rec and rec["mean_tau_total"]:
            extra += f"  tau={rec['mean_tau_total'] / 1e6:.2f}M"
        if "mean_tax_share" in rec:
            extra += f"  tax%={100 * rec['mean_tax_share']:.1f}"
        if rec.get("mean_evictions"):
            extra += (f"  evict={rec['mean_evictions']:.0f}"
                      f"/relist={rec.get('mean_relists', 0):.0f}"
                      f"  T={rec.get('mean_residency_T', 0):.1f}")
        print(f"  {p:<15} {rec['mean_tokens']:>14,.0f} tok"
              f"  rel_ours {rec['rel_to_ours']:6.3f}{ci_str}{extra}")


E4_ROW_ORDER = ["always_reactive", "always_compile", "on_second",
                "success_count", "toolpro_port", "breakeven",
                "breakeven_cap", "ours", "ours_trueP", "ours_g15", "ours_g120", "ours_hfix", "oracle",
                "offline_opt"]


def _assemble_e3(results: dict, B: int) -> dict:
    out = {}
    for key, res in sorted(results.items()):
        order = list(sim.POLICIES)      # offline_opt is appended by
        if "breakeven_cap" in res["rows"]:
            order.append("breakeven_cap")
        table = assemble_table(key, res["rows"], order, "ours",   # assemble
                               res["offline"], B, res["extra"])   # _table
        table["_n_star"] = res["n_star"]
        table["_stream"] = res["stream_summary"]
        out[key] = table
        _print_cell(key, table, order + ["offline_opt"])
    return out


def _assemble_e4(results: dict, B: int) -> dict:
    out = {}
    # rows present in the cell (the variants are rows too); offline_opt is
    # appended by assemble_table itself
    row_order = [p for p in E4_ROW_ORDER if p != "offline_opt"]
    for key, res in sorted(results.items()):
        table = assemble_table(key, res["rows"], row_order, "ours",
                               res["offline"], B, res["extra"])
        table["_n_star"] = res["n_star"]
        table["_stream"] = res["stream_summary"]
        out[key] = table
        _print_cell(key, table, E4_ROW_ORDER)
    return out


def _assemble_e5(results: dict, B: int) -> dict:
    out = {}
    print("\n== E5 mechanism stress (rel to clairvoyant; paired SE vs best) ==")
    for key, res in sorted(results.items()):
        out[key] = res
        print(f"\n-- {key}  (N*={res['n_star']:.3f}, "
              f"best={res['best_variant']})")
        for vname, v in res.items():
            if not isinstance(v, dict) or "mean_tokens" not in v:
                continue
            print(f"   {vname:<16} {v['mean_tokens']:>13,.0f} tok"
                  f"  rel_clv {v['rel_clairvoyant']:6.3f}"
                  f"  compiles {v['mean_compiles']:7.2f}"
                  f"  wasted {v['mean_wasted']:6.2f}"
                  f"  paired_se {v['paired_se']:>10,.0f}")
    return out


def _assemble_e8(results: dict, B: int) -> dict:
    out: dict = {"grid": {}, "tau": {}, "eps": {}}
    # grid: rel_to_ours per policy, printed compactly like the old sweep
    grid_acc: dict[tuple, dict] = {}
    for key, res in results.items():
        if not key.startswith("grid/"):
            continue
        table = assemble_table(key, res["rows"], list(sim.POLICIES), "ours",
                               None, B, res["extra"])
        table["_n_star"] = res["n_star"]
        out["grid"][key] = table
        parts = key.split("/")
        # grid/<price>/s=<strength>/<seeding>/<pattern>
        gk = (parts[1], parts[2], parts[3])
        acc = grid_acc.setdefault(gk, {"n_star": res["n_star"], "rows": {}})
        for p in sim.POLICIES:
            acc["rows"].setdefault(p, []).append(
                table[p]["rel_to_ours"])
    print("\n== E8 price x artifact-strength grid (rel_to_ours, "
          "mean over patterns) ==")
    for (price, strength, seeding), acc in sorted(grid_acc.items()):
        cells = "  ".join(
            f"{p[:6]}:{stats.mean(v):.2f}"
            for p, v in sorted(acc["rows"].items()))
        print(f"  {price:<12} s={strength:<6} {seeding:<8} "
              f"N*={acc['n_star']:8.2f}  {cells}")
    for section, pats in (("tau", ["always_reactive", "always_compile",
                                   "ours", "oracle"]),
                          ("eps", ["always_reactive", "always_compile",
                                   "ours"])):
        print(f"\n== E8 {section} sensitivity (rel_to_ours | mean final "
              f"library | mean tau total) ==")
        for key, res in sorted(results.items()):
            if not key.startswith(section + "/"):
                continue
            table = assemble_table(key, res["rows"], pats, "ours", None, B,
                                   res["extra"])
            table["_n_star"] = res["n_star"]
            out[section][key] = table
            ac = table["always_compile"]
            ours = table["ours"]
            print(f"  {key:<42} N*={res['n_star']:7.2f}"
                  f"  AC rel {ac['rel_to_ours']:6.2f}"
                  f" (n_lib {ac['mean_final_library']:6.1f},"
                  f" tau {ac['mean_tau_total'] / 1e6:6.2f}M)"
                  f"  ours n_lib {ours['mean_final_library']:5.1f},"
                  f" tau {ours['mean_tau_total'] / 1e6:5.2f}M")
    return out


E3_TAX_ROW_ORDER = list(sim.POLICIES_TAX) + ["offline_taxblind",
                                             "offline_opt_tax"]
E4_TAX_ROW_ORDER = ["always_reactive", "always_compile", "on_second",
                    "success_count", "toolpro_port", "breakeven",
                    "breakeven_cap", "ours", "ours_trueP", "ours_g15", "ours_g120", "ours_hfix",
                    "oracle_taxblind", "oracle_tax", "offline_taxblind",
                    "offline_opt_tax"]


def _check_lower_bound(key: str, table: dict, rows: dict,
                       bound_vals) -> list[dict]:
    """Every policy row must sit at or above offline_opt_tax.

    offline_opt_tax bounds the EXPECTED cost, not a single realisation: a
    program-served arrival is charged d + q0*c in the bound but pays a real
    Bernoulli(q0) fallback in the engine.  So the honest test is a paired
    one -- per rep, policy minus bound -- and a mean gap inside a couple of
    standard errors is Monte-Carlo noise of the engine, not a broken bound.
    Anything materially and significantly negative is a real failure.
    """
    bound = table.get("offline_opt_tax")
    if bound is None:
        return []
    viol = []
    for name, rec in table.items():
        if name.startswith("_") or name.startswith("offline_"):
            continue
        if rec["mean_tokens"] >= bound["mean_tokens"] * (1.0 - 1e-12):
            continue
        xs = rows[name]
        bs = bound_vals if isinstance(bound_vals, list) \
            else [bound_vals] * len(xs)
        d = stats.paired_diff_summary(xs, bs)
        z = d["mean_diff"] / d["se"] if d["se"] else float("-inf")
        viol.append({"cell": key, "row": name,
                     "mean_tokens": rec["mean_tokens"],
                     "bound": bound["mean_tokens"],
                     "ratio": rec["mean_tokens"] / bound["mean_tokens"],
                     "paired_gap": d["mean_diff"], "paired_se": d["se"],
                     "z": z,
                     "significant": z < -2.0})
    return viol


def _assemble_tax(results: dict, B: int, row_order: list[str],
                  policy_rows: list[str]) -> dict:
    out: dict = {}
    violations: list[str] = []
    for key, res in sorted(results.items()):
        table = assemble_table(key, res["rows"], policy_rows, "ours",
                               res["offline"], B, res["extra"],
                               offline_label="offline_taxblind",
                               offline_tax=res.get("offline_tax"))
        table["_n_star"] = res["n_star"]
        table["_stream"] = res["stream_summary"]
        out[key] = table
        _print_cell(key, table, row_order)
        violations.extend(_check_lower_bound(key, table, res["rows"],
                                             res.get("offline_tax")))
    hard = [v for v in violations if v["significant"]]
    if violations:
        print(f"\n  {len(violations)} row(s) below offline_opt_tax "
              f"({len(hard)} beyond 2 paired SE):")
        for v in violations:
            print(f"   {'SIGNIFICANT' if v['significant'] else 'noise     '} "
                  f"{v['cell']}/{v['row']}: {v['mean_tokens']:,.0f} vs bound "
                  f"{v['bound']:,.0f} ({v['ratio']:.6f}x), paired gap "
                  f"{v['paired_gap']:,.0f} +- {v['paired_se']:,.0f} "
                  f"(z={v['z']:.2f})")
    if not hard:
        print("\n  offline_opt_tax <= every policy row in every cell "
              "(no gap beyond 2 paired SE): OK")
    out["_lower_bound_violations"] = violations
    return out


def _assemble_e3_tax(results: dict, B: int) -> dict:
    return _assemble_tax(results, B, E3_TAX_ROW_ORDER,
                         list(sim.POLICIES_TAX))


def _assemble_e4_tax(results: dict, B: int) -> dict:
    return _assemble_tax(results, B, E4_TAX_ROW_ORDER,
                         [p for p in E4_TAX_ROW_ORDER
                          if not p.startswith("offline_")])


E4_DEPLOY_ROW_ORDER = ["always_reactive", "always_compile",
                       "always_compile_evict", "ours", "ours_evict",
                       "oracle_tax", "offline_taxblind", "offline_opt_tax"]


def _assemble_e4_deploy(results: dict, B: int) -> dict:
    """The deployment tables: the same assembly plus the library trace.

    assemble_table skips rows a cell did not run, so one row order covers
    E4_CRN (no evicting rows) and the three that carry them.
    """
    policy_rows = [p for p in E4_DEPLOY_ROW_ORDER
                   if not p.startswith("offline_")]
    out = _assemble_tax(results, B, E4_DEPLOY_ROW_ORDER, policy_rows)
    for key, res in results.items():
        if key in out and res.get("n_lib_trace"):
            out[key]["_n_lib_trace"] = res["n_lib_trace"]
    return out


ASSEMBLERS = {"E3": _assemble_e3, "E4": _assemble_e4, "E5": _assemble_e5,
              "E8": _assemble_e8,
              "E3_TAX": _assemble_e3_tax, "E4_TAX": _assemble_e4_tax,
              "E4_CRN": _assemble_e4_deploy,
              "E4_EVICT": _assemble_e4_deploy,
              "E4_RETRIEVAL": _assemble_e4_deploy,
              "E4_KMIN3": _assemble_e4_deploy,
              "E5_TAX": _assemble_e5,
              "E12": _assemble_e12}


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------

def run_experiment(exp: str, constants: dict, out_dir: Path | None = None,
                   reps: int = 20, seed: int = 7, jobs: int | None = None,
                   quick: bool = False, B: int = 2000,
                   tag: str = "",
                   constants_path: str | None = None) -> Path:
    """Run one experiment and write its JSON.

    `tag` suffixes the output file name, for a rerun that must not land on
    a published table; a tagged run refuses to overwrite an existing file.
    """
    if exp not in CELL_BUILDERS:
        raise ValueError(f"unknown experiment {exp!r}")
    if quick:
        constants = apply_quick(constants)
        reps = min(reps, 2)
    issues, placeholders = check_constants(constants)
    if issues:
        raise ValueError("constants problems: " + "; ".join(issues))
    if placeholders:
        print("NOTE: placeholder constants in use (results are provisional "
              "until T1.x numbers land):")
        for p in placeholders:
            print(f"  - {p}")

    cells = CELL_BUILDERS[exp](constants, reps, seed)
    # k_min_global (W10 D1, 2026-09-19): raise the compiler's demonstration
    # requirement for every cell of the run, so the main tables evaluate
    # Algorithm 1's three-trace eligibility instead of the engine's k_min=1
    # default.  Synthetic cells read spec["params"], real cells merge
    # spec["deployment"] in real_params; setting both covers every kind.
    kg = (constants.get("deployment") or {}).get("k_min_global")
    if kg:
        for cell in cells:
            if "params" in cell:
                cell["params"]["k_min"] = int(kg)
            cell.setdefault("deployment", {})["k_min"] = int(kg)
    out_dir = Path(out_dir) if out_dir else DEFAULT_OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    results: dict = {}
    if jobs == 1:
        _init_worker(constants)
        for spec in cells:
            k, r = _run_cell(spec)
            results[k] = r
    else:
        ctx = mp.get_context("spawn")
        with ctx.Pool(processes=jobs or None, initializer=_init_worker,
                      initargs=(constants,)) as p:
            for k, r in p.imap_unordered(_run_cell, cells, chunksize=1):
                results[k] = r
    wall = time.time() - t0

    assembled = ASSEMBLERS[exp](results, B)

    statuses = {}
    for name, cset in constants.get("cost_sets", {}).items():
        statuses[name] = {"status": cset.get("status"),
                          "layouts": {k: v.get("status")
                                      for k, v in cset.get("layouts",
                                                           {}).items()}}
    meta = {
        "experiment": exp,
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "constants_fingerprint": constants_fingerprint(constants),
        "reps": reps, "seed": seed, "jobs": jobs, "bootstrap_B": B,
        "quick": quick,
        "n_cells": len(cells),
        "cells_wall_s_sum": round(sum(r.get("wall_s", 0.0)
                                      for r in results.values()), 3),
        "wall_s": round(wall, 3),
        "cost_set_status": statuses,
        "trigger": dict(constants.get("trigger", {})),
        "units": "cache-adjusted tokens (fresh + r*cached)",
    }
    if tag:
        meta["tag"] = tag
    if constants_path:
        # Provenance: which constants file the run loaded (the fingerprint
        # above hashes the patched dict, which several files can produce).
        meta["constants_file"] = constants_path
    dep = next((c.get("deployment") for c in cells if c.get("deployment")),
               None)
    if dep or any(c.get("crn") for c in cells):
        meta["deployment"] = {"crn": bool(cells[0].get("crn")),
                              "router": (dep or {}).get("router",
                                                        sim.ROUTER_LISTING),
                              "eviction": (dep or {}).get("eviction"),
                              "k_min": (dep or {}).get("k_min", 1)}
    out_path = out_dir / (f"{OUT_NAME.get(exp, exp)}"
                          f"{'_quick' if quick else ''}"
                          f"{('_' + tag) if tag else ''}.json")
    if tag and out_path.exists():
        raise SystemExit(f"refusing to overwrite {out_path}")
    out_path.write_text(json.dumps({"meta": meta, "cells": assembled},
                                   indent=1))
    print(f"\n  wrote {out_path}  ({len(cells)} cells, wall {wall:.1f}s)")
    return out_path
