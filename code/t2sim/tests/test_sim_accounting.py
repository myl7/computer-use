"""Unit tests of the methods-v2 accounting in t2sim/sim.py.

Covers the three terms that are NEW relative to the old simulator and must
behave exactly as docs/methods-v2.md section 2 states:

  * tau(n) = m*n + tau0, paid by EVERY arrival against the library as it
    stands (compiles happen after service, so an arrival is never taxed by
    its own compile);
  * q(n) = eps(n) + (1 - eps(n))*q0 on a program hit (fallback pays c on
    top of d, the program STAYS in the library);
  * library growth: n_t counts programs ever admitted (a manifest entry is
    permanent), so always_compile -> every distinct family, while the
    trigger keeps n bounded.
"""
from __future__ import annotations

import math
import random

import pytest

import sim
import streams as streams_mod


def fam(c=1000.0, d=100.0, C=500.0, p=1.0, q0=0.0, h=0.0):
    return {"c": c, "d": d, "C": C, "p": p, "q0": q0, "h": h,
            "binding_space": 12}


def params(families, tau=None, eps=None, horizon=60):
    return {"families": families, "horizon": horizon,
            "tau": tau if tau is not None else {"m": 0.0, "tau0": 0.0},
            "epsilon": eps if eps is not None else {"enabled": False}}


MECH = dict(sim.MECH_DEFAULT)


# ---------------------------------------------------------------------------
# tau(n): the manifest tax
# ---------------------------------------------------------------------------

def test_tau_of_formula():
    cfg = {"m": 91.0, "tau0": 568.0}
    assert sim.tau_of(0, cfg) == 568.0
    assert sim.tau_of(7, cfg) == 568.0 + 7 * 91.0
    assert sim.tau_of(0, None) == 0.0
    assert sim.tau_of(5, {}) == 0.0


def test_tau_paid_by_every_arrival_when_nothing_compiles():
    tau = {"m": 91.0, "tau0": 50.0}
    P = params({"A": fam(), "B": fam()}, tau=tau)
    stream = ["A", "B", "A", "A", "B"] * 4
    r = sim.run_policy("always_reactive", stream, P, random.Random(0), MECH)
    n = len(stream)
    assert r["tau_total"] == n * 50.0            # n_lib stays 0
    assert r["final_library"] == 0
    assert r["final_tokens"] == n * 50.0 + n * 1000.0


def test_tau_accounting_per_arrival_exact_small_stream():
    """['A','A','B'] under always_compile (p=1, q0=0, h=0):

    t=0 A: n_lib=0 -> tau0 ; compile A (C) ; serve d
    t=1 A: n_lib=1 -> tau0+m ; serve d
    t=2 B: n_lib=1 -> tau0+m ; compile B (C) ; serve d
    """
    tau = {"m": 91.0, "tau0": 50.0}
    P = params({"A": fam(), "B": fam()}, tau=tau)
    r = sim.run_policy("always_compile", ["A", "A", "B"], P,
                       random.Random(0), MECH)
    assert r["tau_total"] == 3 * 50.0 + 2 * 91.0
    assert r["final_tokens"] == (3 * 50.0 + 2 * 91.0) + 2 * 500.0 + 3 * 100.0
    assert r["n_compiles"] == 2
    assert r["final_library"] == 2


def test_manifest_entry_is_permanent_under_q0_failures():
    """q(n) failures fall back but never de-register the program: the
    library count never decreases and the family is never re-compiled."""
    tau = {"m": 10.0, "tau0": 1.0}
    P = params({"A": fam(q0=0.5)}, tau=tau)
    r = sim.run_policy("always_compile", ["A"] * 8, P, random.Random(3), MECH)
    assert r["n_compiles"] == 1
    assert r["final_library"] == 1
    # tax: arrival 0 sees n_lib=0, arrivals 1..7 see n_lib=1
    assert r["tau_total"] == 8 * 1.0 + 7 * 10.0
    assert r["n_curve"] == sorted(r["n_curve"])


def test_tau_grows_linearly_with_library_on_long_stream():
    tau = {"m": 91.0, "tau0": 0.0}
    P = params({"A": fam(), "B": fam(), "C": fam()}, tau=tau)
    stream = ["A"] * 10 + ["B"] * 10 + ["C"] * 10
    r = sim.run_policy("always_compile", stream, P, random.Random(0), MECH)
    # arrivals seeing n_lib=1: t=1..9 (A) + t=10 (B) = 10;  n_lib=2:
    # t=11..19 (B) + t=20 (C) = 10;  n_lib=3: t=21..29 = 9
    assert r["tau_total"] == 91.0 * (1 * 10 + 2 * 10 + 3 * 9)
    assert r["final_library"] == 3


# ---------------------------------------------------------------------------
# q(n): the three-layer failure structure
# ---------------------------------------------------------------------------

def test_q_of_with_epsilon_disabled_is_q0():
    for n in (0, 1, 50, 500):
        assert sim.q_of(n, 0.17, None) == 0.17
        assert sim.q_of(n, 0.17, {"enabled": False, "eps0": 0.3,
                                  "cliff_start": 100,
                                  "cliff_slope": 20}) == 0.17


def test_q_of_composition_on_the_cliff():
    eps_cfg = {"enabled": True, "eps0": 0.3, "cliff_start": 100.0,
               "cliff_slope": 20.0}
    q0 = 0.17

    def eps(n):
        z = (n - 100.0) / 20.0
        sig = 1.0 / (1.0 + math.exp(-z)) if z >= 0 else \
            math.exp(z) / (1.0 + math.exp(z))
        return 0.3 * sig

    # at the cliff start the sigmoid is 1/2
    assert sim.q_of(100, q0, eps_cfg) == pytest.approx(
        0.15 + 0.85 * 0.17)
    # composition: q = eps + (1-eps) q0
    for n in (0, 60, 100, 140, 200, 400):
        e = eps(n)
        assert sim.q_of(n, q0, eps_cfg) == pytest.approx(e + (1 - e) * q0)
    # far left of the cliff q ~ q0, far right q -> eps0 + (1-eps0) q0
    assert sim.q_of(-200, q0, eps_cfg) == pytest.approx(q0, abs=1e-4)
    assert sim.q_of(10_000, q0, eps_cfg) == pytest.approx(
        0.3 + 0.7 * 0.17, abs=1e-6)
    # monotone in n
    qs = [sim.q_of(n, q0, eps_cfg) for n in range(-200, 400, 10)]
    assert all(x <= y for x, y in zip(qs, qs[1:]))


def test_q0_engine_fallback_costs():
    """always_compile, p=1, h=0: every use pays d, plus c iff the q(n)
    coin fails (q0=0 / q0=1 make the coin deterministic)."""
    for q0, per_use in ((0.0, 100.0), (1.0, 100.0 + 1000.0)):
        P = params({"A": fam(q0=q0)})
        r = sim.run_policy("always_compile", ["A"] * 5, P,
                           random.Random(0), MECH)
        # 5 arrivals, compile at t=0, tau = 0, uses = 5
        assert r["final_tokens"] == 500.0 + 5 * per_use
        assert r["n_compiles"] == 1     # fallback does not de-register


def test_q0_reduces_saving_and_delays_ours():
    """with q0 high the per-use saving shrinks, so a price that clears at
    q0=0 must not clear (or at least not more often) at q0=0.9."""
    stream = ["A"] * 30
    rng_seed = 11
    r0 = sim.run_policy("ours", stream, params({"A": fam(q0=0.0, C=800.0)}),
                        random.Random(rng_seed), MECH)
    r9 = sim.run_policy("ours", stream, params({"A": fam(q0=0.9, C=800.0)}),
                        random.Random(rng_seed), MECH)
    assert r0["n_compiles"] >= r9["n_compiles"]


# ---------------------------------------------------------------------------
# library growth: always-compile vs the trigger
# ---------------------------------------------------------------------------

def test_always_compile_grows_library_to_family_count():
    P = params({f"F{i}": fam() for i in range(6)})
    stream = streams_mod.gen_stream("zipf", 200, [f"F{i}" for i in range(6)],
                            random.Random(5))
    distinct = len(set(stream))
    r = sim.run_policy("always_compile", stream, P, random.Random(0), MECH)
    assert r["final_library"] == distinct
    assert r["n_compiles"] == distinct            # p=1: one pass each
    assert r["n_curve"] == sorted(r["n_curve"])   # monotone growth


def test_ours_with_prohibitive_price_never_compiles():
    P = params({"A": fam(C=1e12), "B": fam(C=1e12)}, tau={"m": 91.0,
                                                          "tau0": 0.0})
    stream = ["A", "B", "A", "B", "A"] * 6
    rr = sim.run_policy("always_reactive", stream, P, random.Random(2), MECH)
    ro = sim.run_policy("ours", stream, P, random.Random(2), MECH)
    assert ro["final_library"] == 0
    assert ro["n_compiles"] == 0
    # identical account: both pay tau(0) + c per arrival
    assert ro["final_tokens"] == rr["final_tokens"]
    assert ro["tau_total"] == rr["tau_total"]


def test_ours_compiles_only_the_hot_family():
    """Gamma(1,20) prior: a singleton's first arrival cannot clear a price
    of 5 episodes, while a 30-arrival family soon does (doubling horizon)."""
    mech = sim.mech_with(MECH, prior_mode="gamma", prior_shape=1.0,
                         prior_rate=20.0)
    P = params({"A": fam(C=5 * 1000.0), "B": fam(C=5 * 1000.0)},
               tau={"m": 91.0, "tau0": 0.0})
    stream = ["A"] * 30 + ["B"]
    ro = sim.run_policy("ours", stream, P, random.Random(7), mech)
    ra = sim.run_policy("always_compile", stream, P, random.Random(7), mech)
    assert ro["final_library"] == 1              # A only, never B
    assert ra["final_library"] == 2              # always-compile buys both
    assert ro["tau_total"] < ra["tau_total"]     # and pays less manifest tax


def test_ours_library_bounded_by_always_compile():
    P = params({f"F{i}": fam(C=3000.0) for i in range(8)},
               tau={"m": 91.0, "tau0": 0.0})
    fams = [f"F{i}" for i in range(8)]
    stream = streams_mod.gen_stream("zipf", 300, fams, random.Random(9))
    ro = sim.run_policy("ours", stream, P, random.Random(4), MECH)
    ra = sim.run_policy("always_compile", stream, P, random.Random(4), MECH)
    assert ro["final_library"] <= ra["final_library"]
    # a compiled family is never recompiled while its program is admitted
    assert ro["n_compiles"] <= ro["final_library"]


# ---------------------------------------------------------------------------
# derived quantities
# ---------------------------------------------------------------------------

def test_n_star_formula():
    P = params({"A": fam(c=1000.0, d=100.0, C=500.0, p=1.0, q0=0.0)})
    assert sim.n_star(P) == pytest.approx(500.0 / 900.0)
    P2 = params({"A": fam(c=1000.0, d=100.0, C=500.0, p=0.5, q0=0.2)})
    assert sim.n_star(P2) == pytest.approx(1000.0 / ((1 - 0.2) * 1000.0
                                                     - 100.0))


def test_offline_optimum_buys_when_cheaper_than_reacting():
    P = params({"A": fam()})
    assert sim.offline_optimum(["A", "A", "A"], P) == pytest.approx(
        500.0 + 3 * 100.0)          # C + 3d beats 3c
    assert sim.offline_optimum(["A"], P) == pytest.approx(
        min(1000.0, 500.0 + 100.0))  # single use: program still pays off
    P2 = params({"A": fam(C=5000.0)})
    assert sim.offline_optimum(["A", "A", "A"], P2) == pytest.approx(
        3 * 1000.0)                  # too dear: pure reactive


def test_offline_optimum_folds_tau0_per_arrival():
    P = params({"A": fam()}, tau={"m": 91.0, "tau0": 50.0})
    # every arrival pays at least tau0; the m*n part is deliberately omitted
    assert sim.offline_optimum(["A"] * 3, P) == pytest.approx(
        3 * 50.0 + 500.0 + 3 * 100.0)
