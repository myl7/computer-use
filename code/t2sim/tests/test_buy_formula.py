"""W1.5 (2026-09-19): the trigger's buy price B_hat (sim.BUY_FORMULA_DOC).

`ours` priced a compile attempt at the narrow C/p_hat from the first port
until W1.5, which prices a failed attempt at C.  Algorithm 1 prices the
whole buy: the passing attempt costs C and the expected (1/p_hat - 1)
misses cost C_fail each (C_FAIL_DOC), i.e.

    B_hat = C + (1/p_hat - 1) * C_fail        (mech["buy_formula"] = "full")

which revision_sim.expected_buy is the reference implementation of.
"narrow" keeps the pre-W1.5 C/p_hat verbatim for reproductions.  The two
agree exactly at C_fail = C and at p_hat = 0 (inf both), so every row run
without a measured C_fail_mult -- validate.py, E3/E4/E5/E8 as published --
is bit-identical under either setting.
"""
from __future__ import annotations

import json
import math
import random
from pathlib import Path

import pytest

import revision_sim
import sim

# The measured shapes: a failed attempt at several times the price of a
# successful one (t16_build: DS 10.5x; the engine tests' standing value 3x).
MULTS = (1.5, 2.0829936590907905, 3.0, 10.509389227369171)
P_HATS = (0.05, 0.2, 0.5, 4.6 / 7.0, 1.0)
CS = (100.0, 14549.0, 238134.36818181816)

# A minimal deployment for the end-to-end decision tests: no manifest tax,
# no eviction, add-one gate prior (p_hat = 1/2 before the first attempt).
MECH = sim.mech_with(sim.MECH_DEFAULT, gate_prior="add_one")
NARROW = sim.mech_with(MECH, buy_formula="narrow")


def fam(**kw):
    out = {"c": 150.0, "d": 8.0, "C": 100.0, "p": 1.0, "q0": 0.0, "h": 0.0,
           "binding_space": 12}
    out.update(kw)
    return out


def params(f):
    return {"families": {"F": f}, "horizon": 60,
            "tau": {"m": 0.0, "tau0": 0.0}, "epsilon": {"enabled": False}}


def first_arrival_buy(f, mech):
    """Does `ours` attempt on the family's very first arrival?

    At the first arrival of a population-prior family E_use = 2.0 exactly
    (1 + pi_hat*lam_ret*H = 1 + (1/3)*(1/20)*60), s = c - d, p_hat = 1/2
    under add-one, the inflate multiplier is 2^0 and the m*t externality is
    0, so the decision is exactly 2*(c - d) > price and the run reads the
    formula off the trigger's own threshold.
    """
    r = sim.run_policy("ours", ["F"], params(f), random.Random(0), mech)
    return r["n_compiles"]


# ---------------------------------------------------------------------------
# 1. the full formula is revision_sim.expected_buy
# ---------------------------------------------------------------------------

def test_expected_buy_is_the_revision_sim_reference():
    """Same constants, same expression: bitwise equality at C_fail != C."""
    for C in CS:
        for mult in MULTS:
            for p in P_HATS:
                assert sim.expected_buy(C, mult * C, p) \
                    == revision_sim.expected_buy(C, mult * C, p)


def test_expected_buy_composes_with_every_gate_prior_mode():
    """The estimator modes (GATE_PRIOR_DOC) are untouched by W1.5: whatever
    p_hat a mode returns, the full buy is revision_sim.expected_buy of it."""
    for mode in ("add_one", "pi", "population"):
        mech = sim.mech_with(sim.MECH_DEFAULT, gate_prior=mode)
        for pi in (1.0, 2.0 / 3.0, 1.0 / 3.0):
            for failed, passed in ((0, 0), (3, 0), (5, 2)):
                f = sim.FamilyState("F", fam(pi=pi, C_fail_mult=3.0),
                                    random.Random(0), mech)
                f.failed_attempts = failed
                f.passed_attempts = passed
                p_hat = f.p_hat(0.7, 0.5)
                assert sim.expected_buy(f.C, 3.0 * f.C, p_hat) \
                    == revision_sim.expected_buy(f.C, 3.0 * f.C, p_hat)


def test_expected_buy_at_c_fail_equal_c_is_the_narrow_expression_bitwise():
    """The C_fail = C case evaluates C/p, the exact expression the pre-W1.5
    engine evaluated, so old rows do not move by one ulp.  revision_sim's
    unsimplified C + (1/p - 1)*C can differ in the last ulp; algebraically
    it cannot."""
    for C in CS:
        for p in P_HATS:
            assert sim.expected_buy(C, C, p) == C / p
            assert sim.expected_buy(C, C, p) == pytest.approx(
                revision_sim.expected_buy(C, C, p))


def test_expected_buy_is_inf_at_p_zero():
    assert sim.expected_buy(100.0, 300.0, 0.0) is math.inf
    assert sim.expected_buy(100.0, 100.0, 0.0) is math.inf
    assert revision_sim.expected_buy(100.0, 300.0, 0.0) is math.inf


# ---------------------------------------------------------------------------
# 2. the switch
# ---------------------------------------------------------------------------

def test_buy_formula_mode_reads_the_switch():
    assert sim.buy_formula_mode({}) == "full"          # the W1.5 default
    assert sim.buy_formula_mode({"buy_formula": "full"}) == "full"
    assert sim.buy_formula_mode({"buy_formula": "narrow"}) == "narrow"
    assert "buy_formula" in sim.MECH_DEFAULT
    assert sim.MECH_DEFAULT["buy_formula"] == "full"
    with pytest.raises(ValueError):
        sim.buy_formula_mode({"buy_formula": "sometimes"})


def test_trigger_mech_reads_the_switch_from_the_constants_file():
    import experiments
    base = {"cooldown": "inflate", "cooldown_len": 3, "half_life": 120.0,
            "prior_mode": "population", "prior_shape": 1.0,
            "prior_rate": 5.0, "horizon_mode": "capped_doubling",
            "horizon_fixed": 60}
    assert experiments.trigger_mech({"trigger": dict(base)})["buy_formula"] \
        == "full"                          # absent = the W1.5 default
    assert experiments.trigger_mech(
        {"trigger": dict(base, buy_formula="narrow")})["buy_formula"] \
        == "narrow"
    with open(Path(sim.__file__).resolve().parent
              / "constants.template.json") as fh:
        assert json.load(fh)["trigger"]["buy_formula"] == "full"


# ---------------------------------------------------------------------------
# 3. the trigger's threshold, end to end
# ---------------------------------------------------------------------------
# One arrival, c = 150, d = 8: E_use * s = 2 * 142 = 284, which sits between
# the narrow price 100/0.5 = 200 and the full price 100 + 1*300 = 400.

def test_full_prices_the_misses_the_narrow_form_did_not():
    f = fam(C_fail_mult=3.0)      # p_hat = 1/2: narrow 200, full 400
    assert first_arrival_buy(f, NARROW) == 1   # 284 > 200: buys
    assert first_arrival_buy(f, MECH) == 0     # 284 < 400: does not
    # the absolute form prices the same buy (C_FAIL_DOC: absolute first)
    assert first_arrival_buy(fam(C_fail=300.0), NARROW) == 1
    assert first_arrival_buy(fam(C_fail=300.0), MECH) == 0
    # at C_fail = C the two formulas are the same threshold and both buy
    plain = fam()                 # C_fail = C: price 200 either way
    assert first_arrival_buy(plain, MECH) == 1
    assert first_arrival_buy(plain, NARROW) == 1


def test_narrow_reproduces_the_pre_w15_price_exactly():
    """At C_fail = C the two formulas are the same threshold bit for bit, so
    a family with no measured C_fail runs identically under either switch:
    this is the pre-W1.5 engine's behaviour reproduced exactly (the old code
    evaluated C/p_hat; both switches evaluate C/p_hat at C_fail = C)."""
    f = fam(p=0.3)                # mixed passes and misses over the run
    P = params(f)
    for policy in ("ours", "ours_noinflate"):
        r_full = sim.run_policy(policy, ["F"] * 400, P, random.Random(3), MECH)
        r_narrow = sim.run_policy(policy, ["F"] * 400, P, random.Random(3),
                                  NARROW)
        assert r_full["final_tokens"] == r_narrow["final_tokens"]
        assert r_full["n_compiles"] == r_narrow["n_compiles"]
        assert r_full["failed_attempts"] == r_narrow["failed_attempts"]
        assert r_full["passed_attempts"] == r_narrow["passed_attempts"]


def test_c_fail_follows_the_price_ladder_into_the_buy_price():
    """The price ladder overwrites C; C_fail = mult * C follows (C_FAIL_DOC),
    and the FULL buy follows with it.  At c = 150, d = 8 the threshold is
    E_use*s = 284: C = 50 gives B_hat = 50 + 150 = 200 (buys), C = 75 gives
    B_hat = 75 + 225 = 300 (does not) -- while narrow, which reads only C,
    buys at both.  Had C_fail NOT followed the ladder, C = 75 would be
    75 + 150 = 225 < 284 and full would buy too."""
    assert first_arrival_buy(fam(C=50.0, C_fail_mult=3.0), MECH) == 1
    assert first_arrival_buy(fam(C=75.0, C_fail_mult=3.0), MECH) == 0
    assert first_arrival_buy(fam(C=75.0, C_fail_mult=3.0), NARROW) == 1
    C, C_fail, _ = sim.compile_prices_for_k(fam(C=75.0, C_fail_mult=3.0), 1)
    assert C_fail == pytest.approx(3.0 * 75.0)
    assert sim.expected_buy(C, C_fail, 0.5) \
        == revision_sim.expected_buy(75.0, 3.0 * 75.0, 0.5) == 300.0


def test_p_zero_is_inf_under_both_formulas():
    """p_hat = 0 -> the buy price is inf either way, so `ours` never
    attempts a gate it knows it cannot pass (the pre-W1.5 behaviour kept)."""
    f = fam(p=0.0, C_fail_mult=3.0)
    for mech in (sim.MECH_DEFAULT, sim.mech_with(sim.MECH_DEFAULT,
                                                 buy_formula="narrow")):
        r = sim.run_policy("ours", ["F"] * 50, params(f), random.Random(0),
                           mech)
        assert r["n_compiles"] == 0
        assert r["failed_attempts"] == 0
