"""Unit tests for environment-level fragility (E11).

Three new family constants (sim.ENV_DOC), all inert at their defaults:

  * `h_env`, a drift hazard that acts in the ENVIRONMENT rather than in the
    trigger.  It kills the artifact on a use, that use fails loudly, the
    manifest entry goes with it and the family has to pay C_eff again.
    Unlike the legacy `h` channel it does not replace `q0`, so both failure
    channels act on the same use.
  * `r_fallback`, a multiplier on the reactive episode a program failure
    falls back to.
  * `sigma` / `silent_penalty`, the fraction of failures nobody notices and
    what discovering one later costs.

Plus `ours_noinflate`, the trigger without the price multiplier of the
"inflate" cooldown, and the tax-aware lower bound that has to keep holding
once programs really die.
"""
from __future__ import annotations

import copy
import random
import statistics

import pytest

import experiments
import sim
import streams as streams_mod
import sweep_env_fragility as sweep


def fam(c=95296.0, d=347.0, C=14549.0, p=1.0, q0=0.0, h=0.0, **env):
    out = {"c": c, "d": d, "C": C, "p": p, "q0": q0, "h": h,
           "binding_space": 12}
    out.update(env)
    return out


def params(families, tau=None, horizon=60, **extra):
    out = {"families": families, "horizon": horizon,
           "tau": tau if tau is not None else {"m": 0.0, "tau0": 0.0},
           "epsilon": {"enabled": False}}
    out.update(extra)
    return out


MECH = dict(sim.MECH_DEFAULT)
TAU = {"m": 91.0, "tau0": 368.0}
EVICT = {"T": "t_star", "v_min": 10, "t_min": 1, "t_max": 100000}
EVICT_POLICIES = ("always_reactive", "always_compile_evict", "ours_evict",
                  "ours_noinflate", "oracle_tax")


def zipf(n=400, k=12, seed=3):
    fams = [f"F{i}" for i in range(k)]
    return fams, streams_mod.gen_stream("zipf", n, fams, random.Random(seed))


# ---------------------------------------------------------------------------
# the defaults are inert
# ---------------------------------------------------------------------------

def test_env_defaults_leave_every_policy_bit_identical():
    """Spelling the defaults out must not move a single token."""
    names, stream = zipf()
    plain = {n: fam(q0=0.17, p=0.5) for n in names}
    spelled = {n: fam(q0=0.17, p=0.5, h_env=0.0, r_fallback=1.0, sigma=0.0,
                      silent_penalty=1.0) for n in names}
    for pol in EVICT_POLICIES + ("ours", "always_compile", "breakeven",
                                 "on_second", "toolpro_port"):
        for rep in range(3):
            a = sim.run_policy(pol, stream,
                               params(plain, tau=TAU, eviction=dict(EVICT)),
                               random.Random(11 + rep), MECH,
                               sim.CoinBook(rep))
            b = sim.run_policy(pol, stream,
                               params(spelled, tau=TAU, eviction=dict(EVICT)),
                               random.Random(11 + rep), MECH,
                               sim.CoinBook(rep))
            assert a["final_tokens"] == b["final_tokens"], pol


def test_use_saving_reduces_to_the_published_expression():
    for q in (0.0, 0.17, 0.4, 0.6):
        assert sim.use_saving(95296.0, 347.0, q) == (1.0 - q) * 95296.0 - 347.0


def test_env_fragility_on_is_false_for_a_plain_family():
    assert not sim.env_fragility_on(fam(q0=0.4))
    assert sim.env_fragility_on(fam(h_env=0.02))
    assert sim.env_fragility_on(fam(sigma=0.3))
    assert sim.env_fragility_on(fam(r_fallback=2.2))


def test_offline_bounds_are_unchanged_with_the_channels_off():
    names, stream = zipf()
    plain = {n: fam(q0=0.17, p=0.5) for n in names}
    spelled = {n: fam(q0=0.17, p=0.5, h_env=0.0, r_fallback=1.0, sigma=0.0)
               for n in names}
    P1, P2 = (params(plain, tau=TAU, eviction=dict(EVICT)),
              params(spelled, tau=TAU, eviction=dict(EVICT)))
    assert sim.offline_optimum(stream, P1) == sim.offline_optimum(stream, P2)
    assert (sim.offline_optimum_tax(stream, P1, dict(EVICT))
            == sim.offline_optimum_tax(stream, P2, dict(EVICT)))


# ---------------------------------------------------------------------------
# the hazard acts in the environment
# ---------------------------------------------------------------------------

def test_certain_hazard_kills_the_program_on_its_first_use():
    stream = ["F"] * 40
    P = params({"F": fam(h_env=1.0)}, tau=TAU, eviction=dict(EVICT))
    r = sim.run_policy("always_compile_evict", stream, P, random.Random(1),
                       MECH, sim.CoinBook(3))
    # every arrival buys a program, uses it once, and loses it
    assert r["n_compiles"] == 40
    assert r["n_program_deaths"] == 40
    assert r["max_library"] == 1          # never two entries at once
    assert r["final_library"] == 0        # the last one died too


def test_a_dead_program_costs_d_plus_the_reactive_fallback():
    stream = ["F"] * 2
    P = params({"F": fam(h_env=1.0)}, eviction=dict(EVICT))
    r = sim.run_policy("always_compile_evict", stream, P, random.Random(1),
                       MECH, sim.CoinBook(3))
    f = fam()
    # two arrivals, each: one compile at C, then d + c for the failed use
    assert r["final_tokens"] == pytest.approx(
        2.0 * (f["C"] + f["d"] + f["c"]))


def test_r_fallback_multiplies_only_the_fallback_episode():
    stream = ["F"] * 2
    f = fam()
    plain = sim.run_policy(
        "always_compile_evict", stream,
        params({"F": fam(h_env=1.0)}, eviction=dict(EVICT)),
        random.Random(1), MECH, sim.CoinBook(3))["final_tokens"]
    perturbed = sim.run_policy(
        "always_compile_evict", stream,
        params({"F": fam(h_env=1.0, r_fallback=2.2)}, eviction=dict(EVICT)),
        random.Random(1), MECH, sim.CoinBook(3))["final_tokens"]
    assert perturbed - plain == pytest.approx(2.0 * 1.2 * f["c"])


def test_both_channels_act_on_the_same_use():
    """The legacy h channel REPLACES q0; the E11 one does not."""
    stream = ["F"] * 200
    # legacy: q0 = 1 is never read, so no use ever falls back on q0
    legacy = sim.run_policy(
        "always_compile_evict", stream,
        params({"F": fam(h=0.0, q0=1.0)}, eviction=dict(EVICT)),
        random.Random(1), MECH, sim.CoinBook(3))
    assert legacy["n_use_failures"] == 0          # counter is E11-only
    env = sim.run_policy(
        "always_compile_evict", stream,
        params({"F": fam(h_env=0.05, q0=0.5)}, eviction=dict(EVICT)),
        random.Random(1), MECH, sim.CoinBook(3))
    # failures come from both channels, and deaths are a strict subset
    assert env["n_program_deaths"] > 0
    assert env["n_use_failures"] > env["n_program_deaths"]


def test_the_q0_coin_index_does_not_move_when_the_hazard_is_switched_on():
    """The drift coin is a separate addressed stream, so turning the hazard
    on must not re-draw the q0 stream of a family."""
    book = sim.CoinBook(5)
    before = [book.use("F", i) for i in range(20)]
    _ = [book.death("F", i) for i in range(20)]
    _ = [book.silence("F", i) for i in range(20)]
    assert [book.use("F", i) for i in range(20)] == before


def test_a_dead_program_leaves_the_manifest():
    """A dead artifact must stop taxing every later arrival."""
    names = [f"F{i}" for i in range(6)]
    stream = [n for _ in range(30) for n in names]
    P = params({n: fam(h_env=0.5) for n in names}, tau=TAU,
               eviction=dict(EVICT))
    r = sim.run_policy("always_compile_evict", stream, P, random.Random(1),
                       MECH, sim.CoinBook(3))
    assert r["n_program_deaths"] > 0
    assert r["n_deaths_delisted"] == r["n_program_deaths"]
    assert r["max_library"] <= len(names)


def test_the_family_must_pay_c_eff_again_after_a_death():
    stream = ["F"] * 60
    P = params({"F": fam(h_env=0.2)}, tau=TAU, eviction=dict(EVICT))
    r = sim.run_policy("always_compile_evict", stream, P, random.Random(1),
                       MECH, sim.CoinBook(3))
    # one compile to start with, then one per death (bar a death on the
    # very last arrival, which nothing follows)
    assert r["n_compiles"] in (r["n_program_deaths"],
                               r["n_program_deaths"] + 1)


def test_k_min_still_gates_the_recompile():
    """A rebuild is a compile, so it waits for k_min demonstrations too."""
    stream = ["F"] * 20
    P = params({"F": fam(h_env=1.0)}, tau=TAU, eviction=dict(EVICT),
               k_min=3)
    r = sim.run_policy("always_compile_evict", stream, P, random.Random(1),
                       MECH, sim.CoinBook(3))
    assert r["n_compiles"] < 20           # the first two arrivals cannot buy


# ---------------------------------------------------------------------------
# silent failures
# ---------------------------------------------------------------------------

def test_a_silent_failure_charges_d_plus_the_penalty_and_does_not_kill():
    stream = ["F"] * 3
    f = fam()
    P = params({"F": fam(q0=1.0, sigma=1.0, silent_penalty=3.0)},
               eviction=dict(EVICT))
    r = sim.run_policy("always_compile_evict", stream, P, random.Random(1),
                       MECH, sim.CoinBook(3))
    assert r["n_compiles"] == 1           # bought once and never lost
    assert r["n_silent_failures"] == 3
    assert r["n_program_deaths"] == 0
    assert r["final_tokens"] == pytest.approx(
        f["C"] + 3.0 * (f["d"] + 3.0 * f["c"]))


def test_a_silent_death_does_not_mark_the_family_dead():
    stream = ["F"] * 10
    P = params({"F": fam(h_env=1.0, sigma=1.0, silent_penalty=3.0)},
               eviction=dict(EVICT))
    r = sim.run_policy("always_compile_evict", stream, P, random.Random(1),
                       MECH, sim.CoinBook(3))
    assert r["n_program_deaths"] == 0
    assert r["n_silent_failures"] == 10
    assert r["n_compiles"] == 1


def test_sigma_is_zero_by_default():
    assert sim.ENV_DEFAULTS["sigma"] == 0.0
    assert sim.ENV_DEFAULTS["h_env"] == 0.0
    assert sim.ENV_DEFAULTS["r_fallback"] == 1.0


# ---------------------------------------------------------------------------
# ours_noinflate
# ---------------------------------------------------------------------------

def test_noinflate_matches_ours_when_the_gate_never_misses():
    names, stream = zipf()
    P = params({n: fam(q0=0.17, p=1.0) for n in names}, tau=TAU,
               eviction=dict(EVICT))
    a = sim.run_policy("ours_evict", stream, P, random.Random(1), MECH,
                       sim.CoinBook(3))
    b = sim.run_policy("ours_noinflate", stream, P, random.Random(1), MECH,
                       sim.CoinBook(3))
    assert a["final_tokens"] == b["final_tokens"]


def test_noinflate_keeps_buying_where_the_multiplier_prices_ours_out():
    names, stream = zipf()
    P = params({n: fam(q0=0.17, p=0.3) for n in names}, tau=TAU,
               eviction=dict(EVICT))
    a = sim.run_policy("ours_evict", stream, P, random.Random(1), MECH,
                       sim.CoinBook(3))
    b = sim.run_policy("ours_noinflate", stream, P, random.Random(1), MECH,
                       sim.CoinBook(3))
    assert b["n_compiles"] > a["n_compiles"]


def test_noinflate_still_refuses_a_dead_gate():
    """C_eff = C/0 = inf, so dropping the multiplier does not make it buy."""
    stream = ["F"] * 20
    P = params({"F": fam(p=0.0)}, tau=TAU, eviction=dict(EVICT))
    r = sim.run_policy("ours_noinflate", stream, P, random.Random(1), MECH)
    assert r["n_compiles"] == 0


def test_noinflate_carries_the_residency_rule():
    assert sim.POLICIES_EVICT["ours_noinflate"] == "ours"
    names, stream = zipf()
    P = params({n: fam(q0=0.17) for n in names}, tau=TAU,
               eviction={"T": 5.0, "v_min": 0, "t_min": 1, "t_max": 100000})
    r = sim.run_policy("ours_noinflate", stream, P, random.Random(1), MECH,
                       sim.CoinBook(3))
    assert r["n_evictions"] > 0


# ---------------------------------------------------------------------------
# the lower bound
# ---------------------------------------------------------------------------

def test_offline_tax_stays_a_bound_with_both_channels_on():
    names, stream = zipf(n=300, k=8)
    P = params({n: fam(q0=0.4, p=0.5, h_env=0.1) for n in names}, tau=TAU,
               eviction=dict(EVICT))
    bound = sim.offline_optimum_tax(stream, P, dict(EVICT))
    for pol in EVICT_POLICIES:
        xs = [sim.run_policy(pol, stream, P, random.Random(7717 + i), MECH,
                             sim.CoinBook(i))["final_tokens"]
              for i in range(40)]
        assert statistics.mean(xs) >= bound, pol


def test_offline_tax_keeps_the_manifest_tax_under_the_hazard():
    """The fallback for the legacy channel drops the m * n_t term; the E11
    branch must not, or the bound is uninformative at the measured tax."""
    names, stream = zipf(n=200, k=6)
    fams = {n: fam(q0=0.4, h_env=0.1) for n in names}
    with_tax = sim.offline_optimum_tax(stream, params(fams, tau=TAU), None)
    no_tax = sim.offline_optimum_tax(
        stream, params(fams, tau={"m": 0.0, "tau0": 368.0}), None)
    assert with_tax > no_tax


def test_offline_tax_bound_is_tight_when_the_hazard_is_the_only_channel():
    """With p = 1 and no eviction the DP is close to attainable, so a large
    gap would mean the relaxation is wrong rather than loose.

    The bound is on the EXPECTED cost, and one death costs c + C here, so a
    few hundred repetitions still carry a percent or so of Monte-Carlo
    noise.  The test is the same paired one the sweep applies: within three
    standard errors of the bound from below, and not far above it.
    """
    stream = ["F"] * 120
    P = params({"F": fam(q0=0.0, h_env=0.05)}, tau=TAU)
    bound = sim.offline_optimum_tax(stream, P, None)
    xs = [sim.run_policy("always_compile", stream, P, random.Random(7717 + i),
                         MECH, sim.CoinBook(i))["final_tokens"]
          for i in range(400)]
    mean = statistics.mean(xs)
    se = statistics.stdev(xs) / len(xs) ** 0.5
    assert (mean - bound) / se > -3.0
    assert mean / bound < 1.15


def test_the_hazard_stops_the_tax_when_the_artifact_dies():
    """A dead entry leaves the manifest, so the bound must not keep charging
    it for the rest of the stream: that was a real violation before the DP
    counted the tax gap by gap."""
    stream = ["F"] * 120
    # h = 1: the artifact dies on its first use, so it is never listed for a
    # single later arrival and the manifest tax cannot touch the bound.
    taxed, untaxed = (sim.offline_optimum_tax(
        stream, params({"F": fam(q0=0.0, h_env=1.0)},
                       tau={"m": mm, "tau0": 368.0}), None)
        for mm in (91.0, 0.0))
    assert taxed == untaxed
    # a program that survives is listed, and then the tax does bite
    taxed, untaxed = (sim.offline_optimum_tax(
        stream, params({"F": fam(q0=0.0, h_env=0.01)},
                       tau={"m": mm, "tau0": 368.0}), None)
        for mm in (91.0, 0.0))
    assert taxed > untaxed


def test_the_env_dp_agrees_with_the_exact_scan_where_they_overlap():
    """sigma = 0.3 at silent_penalty = 1 and r = 1 costs exactly what q0
    costs and never kills anything, so the E11 DP is solving the same
    problem the published exact scan solves."""
    names, stream = zipf(n=300, k=8)
    plain = {n: fam(q0=0.4, p=0.5) for n in names}
    degenerate = {n: fam(q0=0.4, p=0.5, sigma=0.3, silent_penalty=1.0)
                  for n in names}
    a = sim.offline_optimum_tax(stream, params(plain, tau=TAU), None)
    b = sim.offline_optimum_tax(stream, params(degenerate, tau=TAU), None)
    assert abs(a - b) / a < 1e-9


# ---------------------------------------------------------------------------
# the trigger reads the environment's lifetime
# ---------------------------------------------------------------------------

def test_the_horizon_cap_comes_from_the_environment_hazard():
    names, stream = zipf()
    slow = params({n: fam(q0=0.17, h_env=0.02) for n in names}, tau=TAU,
                  eviction=dict(EVICT))
    fast = params({n: fam(q0=0.17, h_env=0.5) for n in names}, tau=TAU,
                  eviction=dict(EVICT))
    a = sim.run_policy("ours_noinflate", stream, slow, random.Random(1),
                       MECH, sim.CoinBook(3))
    b = sim.run_policy("ours_noinflate", stream, fast, random.Random(1),
                       MECH, sim.CoinBook(3))
    # a program expected to serve two uses is worth buying far less often
    assert b["max_library"] < a["max_library"]


def test_the_clairvoyant_caps_its_uses_at_the_program_lifetime():
    """oracle_tax knows the hazard, so it will not pay for 100 uses of a
    program the environment gives two.  The tax-blind `oracle` does not read
    the cap, which is what isolates it here."""
    stream = ["F"] * 100
    # h = 0.5 -> two expected uses; s = c - d - 0.5 c = 47,301 per use, so a
    # price of 150,000 cannot be repaid in two uses but is trivial in 100.
    P = params({"F": fam(q0=0.0, h_env=0.5, C=150000.0)},
               tau={"m": 0.0, "tau0": 0.0})
    capped = sim.run_policy("oracle_tax", stream, P, random.Random(1), MECH,
                            sim.CoinBook(3))
    uncapped = sim.run_policy("oracle", stream, P, random.Random(1), MECH,
                              sim.CoinBook(3))
    assert capped["n_compiles"] == 0
    assert uncapped["n_compiles"] > 0
    cheap = params({"F": fam(q0=0.0, h_env=0.5, C=50000.0)},
                   tau={"m": 0.0, "tau0": 0.0})
    assert sim.run_policy("oracle_tax", stream, cheap, random.Random(1),
                          MECH, sim.CoinBook(3))["n_compiles"] > 0


# ---------------------------------------------------------------------------
# the sweep wiring
# ---------------------------------------------------------------------------

def test_env_constants_does_not_mutate_the_base():
    base = experiments.load_constants(
        str(sweep.Path(__file__).resolve().parents[1]
            / "constants.measured.json"))
    snapshot = experiments.constants_fingerprint(base)
    point = {"block": "t", "h": 0.1, "q0": 0.6, "p": 0.3,
             "r_fallback": 2.2, "sigma": 0.3, "silent_penalty": 3.0}
    out = sweep.env_constants(base, point)
    assert experiments.constants_fingerprint(base) == snapshot
    for lay in out["cost_sets"]["openapps_glm"]["layouts"].values():
        assert lay["h_env"] == 0.1 and lay["q0"] == 0.6 and lay["p"] == 0.3
        assert lay["r_fallback"] == 2.2 and lay["sigma"] == 0.3
        assert lay["silent_penalty"] == 3.0


def test_cost_set_profiles_carry_the_env_channels():
    base = experiments.load_constants(
        str(sweep.Path(__file__).resolve().parents[1]
            / "constants.measured.json"))
    point = {"block": "t", "h": 0.3, "q0": 0.4, "p": 1.0,
             "r_fallback": 1.0, "sigma": 0.0, "silent_penalty": 1.0}
    cst = sweep.env_constants(base, point)
    profiles, _, _ = experiments.cost_set_profiles(cst, "openapps_glm")
    for prof in profiles.values():
        assert prof["h_env"] == 0.3
        # inert values stay out of the profile so nothing published moves
        assert "sigma" not in prof and "r_fallback" not in prof


def test_untouched_constants_produce_the_profile_they_always_did():
    base = experiments.load_constants(
        str(sweep.Path(__file__).resolve().parents[1]
            / "constants.measured.json"))
    profiles, _, _ = experiments.cost_set_profiles(base, "openapps_glm")
    for prof in profiles.values():
        assert set(prof) <= {"c", "d", "C", "p", "q0", "h", "binding_space",
                             "k_table"}


def test_the_grid_covers_every_value_of_every_axis():
    pts = sweep._grid_points()
    assert len(pts) == len({sweep.config_key(p) for p in pts})
    assert {p["h"] for p in pts} == set(sweep.CORE_H)
    assert {p["p"] for p in pts} == set(sweep.CORE_P)
    assert {p["q0"] for p in pts} == {0.17, 0.4, 0.6}
    assert {p["r_fallback"] for p in pts} == {1.0, 2.2}
    assert {p["sigma"] for p in pts} == {0.0, 0.3}
    assert {p["silent_penalty"] for p in pts if p["sigma"] > 0} == \
        {sweep.SILENT_PENALTY}


def test_the_reference_row_is_the_uninflated_trigger():
    assert sweep.REFERENCE == "ours_noinflate"
    assert "always_compile_evict_abandon" not in sweep.POLICIES
    assert sweep.NAIVE == "always_compile_evict"
