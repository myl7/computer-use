"""Unit tests of the tax-aware clairvoyant references in t2sim/sim.py.

The tax-blind rows ("oracle", "offline_optimum") price a compile at C_eff
and stop there.  Under the methods-v2 manifest tax that is the wrong
objective: an admitted entry taxes every LATER arrival m tokens, so on a
heavy-tailed stream the tax-blind oracle admits every family that ever
recurs and ends up many times more expensive than the evaluated policy.
These tests pin the two fixes:

  * oracle_tax -- same clairvoyance, admission price C_eff + m * (remaining
    stream arrivals), so it stops admitting singletons and cold families;
  * offline_optimum_tax -- the exact optimum of the FULL stream objective,
    which stays separable per family because tau is linear in n;

plus the two invariants that must not break: the tax-blind rows keep their
old behaviour bit-for-bit ("oracle_taxblind" is an alias of "oracle"), and
both new pieces are inert when m = 0.
"""
from __future__ import annotations

import random

import pytest

import sim
import streams as streams_mod


def fam(c=95296.0, d=347.0, C=14549.0, p=1.0, q0=0.0, h=0.0):
    return {"c": c, "d": d, "C": C, "p": p, "q0": q0, "h": h,
            "binding_space": 12}


def params(families, tau=None, eps=None, horizon=60):
    return {"families": families, "horizon": horizon,
            "tau": tau if tau is not None else {"m": 0.0, "tau0": 0.0},
            "epsilon": eps if eps is not None else {"enabled": False}}


MECH = dict(sim.MECH_DEFAULT)
TAU = {"m": 91.0, "tau0": 368.0}


def heavy_tail(n_hot: int, n_singleton: int) -> list[str]:
    """One hot family up front, then a long run of once-seen families."""
    return ["HOT"] * n_hot + [f"S{i}" for i in range(n_singleton)]


# ---------------------------------------------------------------------------
# the tax-blind rows must not move
# ---------------------------------------------------------------------------

def test_oracle_taxblind_is_bit_identical_to_oracle():
    fams = [f"F{i}" for i in range(12)]
    stream = streams_mod.gen_stream("zipf", 400, fams, random.Random(3))
    P = params({n: fam(q0=0.17) for n in fams}, tau=TAU)
    a = sim.run_policy("oracle", stream, P, random.Random(11), MECH)
    b = sim.run_policy("oracle_taxblind", stream, P, random.Random(11), MECH)
    assert a["final_tokens"] == b["final_tokens"]
    assert a["final_library"] == b["final_library"]
    assert a["n_compiles"] == b["n_compiles"]
    assert a["curve"] == b["curve"]


def test_oracle_tax_equals_oracle_when_m_is_zero():
    fams = [f"F{i}" for i in range(12)]
    stream = streams_mod.gen_stream("bursty", 400, fams, random.Random(4))
    P = params({n: fam(q0=0.17) for n in fams},
               tau={"m": 0.0, "tau0": 368.0})
    a = sim.run_policy("oracle", stream, P, random.Random(5), MECH)
    b = sim.run_policy("oracle_tax", stream, P, random.Random(5), MECH)
    assert a["final_tokens"] == b["final_tokens"]
    assert a["final_library"] == b["final_library"]


def test_offline_optimum_tax_equals_tax_blind_when_m_is_zero():
    fams = [f"F{i}" for i in range(8)]
    stream = streams_mod.gen_stream("zipf", 500, fams, random.Random(6))
    P = params({n: fam(q0=0.17) for n in fams},
               tau={"m": 0.0, "tau0": 368.0})
    assert sim.offline_optimum_tax(stream, P) == \
        pytest.approx(sim.offline_optimum(stream, P))


def test_offline_optimum_tax_falls_back_to_the_dp_under_legacy_drift():
    """h > 0 breaks the single-admission argument; the DP takes over."""
    fams = ["A", "B"]
    stream = ["A", "B"] * 40
    P = params({n: fam(h=0.02) for n in fams}, tau=TAU)
    assert sim.offline_optimum_tax(stream, P) == \
        sim.offline_optimum(stream, P)


# ---------------------------------------------------------------------------
# oracle_tax: the externality changes the admissions
# ---------------------------------------------------------------------------

def test_oracle_tax_refuses_singletons_that_taxblind_oracle_admits():
    """N* < 1 makes the tax-blind rule admit every family that ever arrives.

    This is the wiki_B pathology in miniature: one hot family plus 20,000
    families seen exactly once, at the measured openapps constants.  Per use
    a program saves s = 78,749 tokens and one manifest entry costs 91 per
    later arrival, so an entry admitted with more than s/m ~ 865 arrivals
    left is a loss no matter how often its family recurs.  The tax-blind
    oracle admits all 20,001 anyway; the tax-aware one admits only entries
    near the end of the stream, where the externality has run out.
    """
    stream = heavy_tail(300, 20000)
    P = params({n: fam(q0=0.17) for n in set(stream)}, tau=TAU)
    blind = sim.run_policy("oracle_taxblind", stream, P, random.Random(1),
                           MECH)
    taxed = sim.run_policy("oracle_tax", stream, P, random.Random(1), MECH)
    assert blind["final_library"] == 20001
    assert taxed["final_library"] < 0.1 * blind["final_library"]
    # S0 arrives right after the hot block, ~20,000 arrivals from the end
    assert "S0" in blind["first_compile_rank"]
    assert "S0" not in taxed["first_compile_rank"]
    assert taxed["final_tokens"] < blind["final_tokens"]


def test_oracle_tax_beats_taxblind_oracle_on_a_long_tailed_stream():
    stream = heavy_tail(300, 20000)
    P = params({n: fam(q0=0.17) for n in set(stream)}, tau=TAU)
    blind = sim.run_policy("oracle_taxblind", stream, P, random.Random(2),
                           MECH)
    taxed = sim.run_policy("oracle_tax", stream, P, random.Random(2), MECH)
    ours = sim.run_policy("ours", stream, P, random.Random(2), MECH)
    # the tax-blind row is the thing a reviewer objects to: a "clairvoyant"
    # that loses to the evaluated policy by a wide margin
    assert blind["final_tokens"] > 2.0 * ours["final_tokens"]
    # the tax-aware clairvoyant does not
    assert taxed["final_tokens"] < ours["final_tokens"]


def test_oracle_tax_admission_price_is_exactly_the_remaining_stream():
    """The threshold is remaining*s > C_eff + m*(T-1-t), checked at the
    boundary by moving m so the single decision flips."""
    # 5 arrivals of A, then 100 arrivals of other families: at t=0 the
    # remaining stream after admission is 104 arrivals.
    stream = ["A"] * 5 + [f"Z{i}" for i in range(100)]
    T = len(stream)
    c, d, C = 1000.0, 100.0, 500.0
    s = c - d                                     # q0 = 0
    remaining_at_0 = 5
    # m chosen so that remaining*s == C_eff + m*(T-1-0) exactly: strict ">"
    # must then refuse.
    m_tie = (remaining_at_0 * s - C) / (T - 1)
    fams = {n: fam(c=c, d=d, C=C, q0=0.0) for n in set(stream)}
    for zn in [n for n in fams if n != "A"]:
        fams[zn] = fam(c=c, d=d, C=1e18, q0=0.0)  # unbuyable, isolate A
    P_tie = params(dict(fams), tau={"m": m_tie, "tau0": 0.0})
    P_cheap = params(dict(fams), tau={"m": m_tie * 0.99, "tau0": 0.0})
    P_dear = params(dict(fams), tau={"m": m_tie * 1.01, "tau0": 0.0})
    lib = lambda P: sim.run_policy(          # noqa: E731
        "oracle_tax", stream, P, random.Random(0), MECH)["final_library"]
    assert lib(P_tie) == 0                   # ties refuse (strict >)
    assert lib(P_cheap) == 1
    assert lib(P_dear) == 0


# ---------------------------------------------------------------------------
# offline_optimum_tax: exactness and the bound property
# ---------------------------------------------------------------------------

def test_offline_optimum_tax_hand_computed_two_family_stream():
    """Every term of the objective, by hand, on a 6-arrival stream."""
    stream = ["A", "B", "A", "A", "B", "A"]      # A x4, B x2
    T = len(stream)
    m, tau0 = 91.0, 368.0
    c, d, C = 1000.0, 100.0, 500.0
    P = params({"A": fam(c=c, d=d, C=C, q0=0.0),
                "B": fam(c=c, d=d, C=C, q0=0.0)},
               tau={"m": m, "tau0": tau0})
    s = c - d                                     # 900
    # A's arrivals sit at global indices 0, 2, 3, 5; B's at 1, 4.
    def gain(pos, n_f):
        return max([0.0] + [(n_f - j) * s - C - m * (T - 1 - k)
                            for j, k in enumerate(pos)])
    want = T * tau0 + (4 * c - gain([0, 2, 3, 5], 4)) \
        + (2 * c - gain([1, 4], 2))
    assert sim.offline_optimum_tax(stream, P) == pytest.approx(want)
    # sanity on the arithmetic itself: A is worth buying at its first
    # arrival (4*900 - 500 - 91*5 = 2645), B only marginally (2*900 - 500
    # - 91*4 = 936 at index 1 vs 1*900 - 500 - 91*1 = 309 at index 4).
    assert gain([0, 2, 3, 5], 4) == pytest.approx(2645.0)
    assert gain([1, 4], 2) == pytest.approx(936.0)


def test_offline_optimum_tax_is_attained_by_the_clairvoyant_when_p_is_one():
    """p = 1, h = 0, q0 = 0: no randomness left, so the bound is tight."""
    stream = heavy_tail(40, 60)
    P = params({n: fam(q0=0.0) for n in set(stream)}, tau=TAU)
    bound = sim.offline_optimum_tax(stream, P)
    best = min(sim.run_policy(p, stream, P, random.Random(0),
                              MECH)["final_tokens"]
               for p in sim.POLICIES_TAX)
    assert bound <= best + 1e-6
    assert best == pytest.approx(bound, rel=1e-9)


def test_offline_optimum_tax_is_above_the_taxblind_bound():
    """Keeping a non-negative term can only raise the bound."""
    fams = [f"F{i}" for i in range(30)]
    for pattern in ("poisson", "zipf", "bursty"):
        stream = streams_mod.gen_stream(pattern, 800, fams,
                                        random.Random(7))
        P = params({n: fam(q0=0.17) for n in fams}, tau=TAU)
        assert sim.offline_optimum_tax(stream, P) >= \
            sim.offline_optimum(stream, P) - 1e-6


def test_offline_optimum_tax_reduces_to_always_reactive_when_nothing_pays():
    """C_eff = inf (gate p = 0) leaves T*tau0 + sum n_f c_f exactly."""
    fams = [f"F{i}" for i in range(5)]
    stream = streams_mod.gen_stream("poisson", 200, fams, random.Random(8))
    P = params({n: fam(p=0.0, q0=0.0) for n in fams}, tau=TAU)
    want = len(stream) * TAU["tau0"] + len(stream) * 95296.0
    assert sim.offline_optimum_tax(stream, P) == pytest.approx(want)
    r = sim.run_policy("always_reactive", stream, P, random.Random(0), MECH)
    assert r["final_tokens"] == pytest.approx(want)


@pytest.mark.parametrize("pattern", ["poisson", "zipf", "bursty"])
def test_offline_optimum_tax_lower_bounds_every_policy_in_expectation(
        pattern):
    """Averaged over reps (the bound is on the expectation, not on a draw)."""
    fams = [f"F{i}" for i in range(25)]
    P = params({n: fam(q0=0.17) for n in fams}, tau=TAU)
    for policy in sim.POLICIES_TAX:
        gaps = []
        for rep in range(8):
            stream = streams_mod.gen_stream(pattern, 600, fams,
                                            random.Random(100 + rep))
            got = sim.run_policy(policy, stream, P, random.Random(rep),
                                 MECH)["final_tokens"]
            gaps.append(got - sim.offline_optimum_tax(stream, P))
        assert sum(gaps) / len(gaps) >= 0.0, policy


def test_offline_optimum_tax_lower_bounds_the_heavy_tail_case():
    """The wiki_B shape, where the tax-blind bound is far too loose.

    q0 = 0 so the engine is deterministic and the comparison is exact
    rather than an expectation (test_..._in_expectation covers q0 > 0).
    """
    stream = heavy_tail(300, 5000)
    P = params({n: fam(q0=0.0) for n in set(stream)}, tau=TAU)
    tight = sim.offline_optimum_tax(stream, P)
    loose = sim.offline_optimum(stream, P)
    assert tight > loose
    for policy in sim.POLICIES_TAX:
        got = sim.run_policy(policy, stream, P, random.Random(0),
                             MECH)["final_tokens"]
        assert got >= tight * (1.0 - 1e-9), policy


def test_offline_optimum_tax_bounds_only_the_expectation_under_q0():
    """A single draw can dip below the bound; the mean must not.

    The bound charges a program-served arrival d + q0*c; the engine flips a
    Bernoulli(q0) and pays d + c or d.  On this stream a lucky seed lands
    ~2 sd below the mean, which is why the E3/E4 checker tests the paired
    gap against its standard error instead of the raw sign.
    """
    stream = heavy_tail(60, 400)
    P = params({n: fam(q0=0.17) for n in set(stream)}, tau=TAU)
    bound = sim.offline_optimum_tax(stream, P)
    draws = [sim.run_policy("oracle_tax", stream, P, random.Random(i),
                            MECH)["final_tokens"] for i in range(200)]
    assert min(draws) < bound                       # single draws can dip
    assert sum(draws) / len(draws) > bound          # the mean does not
