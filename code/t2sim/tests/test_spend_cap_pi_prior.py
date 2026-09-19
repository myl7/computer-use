"""The 2026-09-11 mechanisms: attempt-spend cap, pi prior, C_fail.

The regime under test is the one the t16_build batch put on the table and
E11 had never simulated: a family whose gate never passes, on a stream where
it keeps arriving, with a failed attempt costing several times a successful
one.  Under Algorithm 1 as written (C_eff = C / p_hat with the flat add-one
prior) such a family is retried while lambda_hat * H_hat * s clears
(n + 2) * C, which is dozens of times.
"""
from __future__ import annotations

import random

import pytest

import experiments
import sim
import sweep_env_fragility as sweep


def fam(c=95296.0, d=347.0, C=14549.0, p=1.0, q0=0.0, **kw):
    out = {"c": c, "d": d, "C": C, "p": p, "q0": q0, "h": 0.0,
           "binding_space": 12}
    out.update(kw)
    return out


def params(families, tau=None, horizon=60, **extra):
    out = {"families": families, "horizon": horizon,
           "tau": tau if tau is not None else {"m": 0.0, "tau0": 0.0},
           "epsilon": {"enabled": False}}
    out.update(extra)
    return out


TAU = {"m": 91.0, "tau0": 368.0}
EVICT = {"T": 50.0, "v_min": 10, "t_min": 1, "t_max": 100000}
# The deployed trigger of methods-v3 section 3: no price doubling, no abandon
# rule, and the gate rate ESTIMATED rather than known.
OLD = sim.mech_with(sim.MECH_DEFAULT, gate_prior="add_one")
CAP = sim.mech_with(OLD, spend_cap=True, spend_cap_mult=1.0)

# W1.5 (2026-09-19): the default buy formula became Algorithm 1's full
# B_hat = C + (1/p_hat - 1) * C_fail (sim.BUY_FORMULA_DOC), so a measured
# C_fail_mult now RAISES the trigger price and the p = 0 retry loop closes
# on its own.  The four tests pinned here were constructed against the
# pre-W1.5 narrow pricing C/p_hat, under which C_fail_mult does not move
# the trigger price at all -- their attempt counts, invariance claims and
# cap-block counters are narrow-formula numbers.  They are pinned to the
# switch and reproduce their pre-W1.5 results exactly; the mechanism they
# test (the cap, the C_fail bill) is unchanged by W1.5.
OLD_NARROW = sim.mech_with(OLD, buy_formula="narrow")
CAP_NARROW = sim.mech_with(CAP, buy_formula="narrow")


def hot(n=200):
    """One family, arriving at every arrival: the hot p = 0 case."""
    return ["F"] * n


def run(mech, fam_cfg, stream=None, policy="ours_noinflate", **extra):
    P = params({"F": fam_cfg}, tau=TAU, eviction=dict(EVICT), **extra)
    return sim.run_policy(policy, stream or hot(), P, random.Random(1), mech)


# ---------------------------------------------------------------------------
# 1. the p = 0 retry loop, and the cap that closes it
# ---------------------------------------------------------------------------

def test_old_policy_retries_a_p0_family_many_times():
    r = run(OLD, fam(p=0.0))
    assert r["failed_attempts"] >= 10, r["failed_attempts"]
    assert r["passed_attempts"] == 0
    # and it is the add-one prior doing it: the price it pays is C, the
    # price it THINKS it pays is C * (n + 2).
    assert r["compile_tokens"] == pytest.approx(
        r["failed_attempts"] * 14549.0)


def test_true_prior_never_attempts_a_p0_family():
    """The historical default, kept so every published cell reproduces."""
    r = run(sim.MECH_DEFAULT, fam(p=0.0))
    assert r["n_compiles"] == 0


def test_spend_cap_stops_within_one_attempt_of_the_expected_saving():
    # Pinned to buy_formula="narrow" (W1.5): the strict reduction of attempts
    # below the uncapped rule is a property of the narrow price, which the
    # full B_hat already carries C_fail in.
    dead = fam(p=0.0, C_fail_mult=3.0)     # the measured failure price
    r_old = run(OLD_NARROW, dead)
    r_cap = run(CAP_NARROW, dead)
    assert r_cap["failed_attempts"] < r_old["failed_attempts"]
    # The cap is a comparison against the CURRENT estimate, so the bound has
    # to be read against the largest expected saving the family ever showed.
    # An attempt can start below the cap and finish above it, hence "+ one".
    lam_h_s = _max_expected_saving("F", hot(), dead, OLD_NARROW)
    C_fail = 3.0 * 14549.0
    assert r_cap["failed_compile_tokens"] <= 1.0 * lam_h_s + C_fail
    # the rule it replaces has no such bound
    assert r_old["failed_compile_tokens"] > lam_h_s + C_fail


def test_spend_cap_scales_with_its_multiplier():
    spends = []
    for mult in (0.25, 1.0, 4.0):
        r = run(sim.mech_with(CAP, spend_cap_mult=mult),
                fam(p=0.0, C_fail_mult=3.0))
        spends.append(r["failed_compile_tokens"])
    assert spends[0] <= spends[1] <= spends[2]
    assert spends[0] < spends[2]


def test_spend_cap_is_a_comparison_not_a_permanent_flag():
    """A family that goes cold, then hot, may attempt again.

    The cap compares cumulative failed spend against the CURRENT expected
    saving, so a family whose arrival rate climbs can clear its own cap
    again.  Here the same family arrives sparsely, is capped, and then
    arrives densely; the dense phase must produce at least one more attempt
    than the sparse phase reached on its own.
    """
    sparse = [x for i in range(40) for x in (["F"] + ["G"] * 9)]
    P = params({"F": fam(p=0.0, C_fail_mult=3.0), "G": fam(p=1.0)}, tau=TAU,
               eviction=dict(EVICT))
    r_sparse = sim.run_policy("ours_noinflate", sparse, P, random.Random(1),
                              CAP)
    r_then_hot = sim.run_policy("ours_noinflate", sparse + ["F"] * 200, P,
                                random.Random(1), CAP)
    assert r_then_hot["failed_attempts"] > r_sparse["failed_attempts"]


def _max_expected_saving(name, stream, fam_cfg, mech):
    """lambda_hat * H_hat * s of `name`, maximised over the stream.

    Recomputed with the engine's own estimator by replaying the arrivals
    through a FamilyState, so the bound the test asserts is the quantity the
    cap actually reads and not a second implementation of it.
    """
    f = sim.FamilyState(name, fam_cfg, random.Random(0), mech)
    s = sim.use_saving(fam_cfg["c"], fam_cfg["d"], fam_cfg.get("q0", 0.0))
    best = 0.0
    for t, nm in enumerate(stream):
        if nm != name:
            continue
        f.k += 1
        f.observe_arrival(t)
        lam = f.lam_hat()
        if f.k >= 2:
            base = lam * (t - (f.first_seen or 0))
            E = min(base, mech.get("horizon_cap") or float("inf"))
        else:
            E = lam * 60
        best = max(best, E * s)
    return best


# ---------------------------------------------------------------------------
# 2. the pi-stratified prior
# ---------------------------------------------------------------------------

def test_pi_prior_mean_reads_the_table():
    assert sim.pi_prior_mean(1.0) == pytest.approx(4.6 / 7.0)
    assert sim.pi_prior_mean(2.0 / 3.0) == 0.5
    assert sim.pi_prior_mean(0.5) == 0.5
    assert sim.pi_prior_mean(1.0 / 3.0) == 0.2
    assert sim.pi_prior_mean(0.0) == 0.2


def test_pi_prior_moves_the_first_attempt_threshold_the_right_way():
    """Before any attempt, p_hat is the prior mean, so C_eff = C / mu.

    A family whose three demonstrations all succeeded is priced at C/0.657
    and a family whose demonstrations mostly failed at C/0.2, against C/0.5
    flat.  0.657 is the mean gate rate of the pi = 1 stratum of the
    t16_build batch (sim.PI_PRIOR_TABLE).
    """
    mech = sim.mech_with(OLD, gate_prior="pi")
    prices = {}
    for pi in (1.0, 2.0 / 3.0, 1.0 / 3.0):
        f = sim.FamilyState("F", fam(pi=pi), random.Random(0), mech)
        prices[pi] = 14549.0 / f.p_hat(0.5)
    assert prices[1.0] < prices[2.0 / 3.0] < prices[1.0 / 3.0]
    assert prices[1.0] == pytest.approx(14549.0 / (4.6 / 7.0))
    assert prices[1.0 / 3.0] == pytest.approx(14549.0 / 0.2)
    flat = sim.FamilyState("F", fam(), random.Random(0), OLD)
    assert flat.p_hat(0.5) == pytest.approx(0.5)


def test_pi_prior_attempts_a_low_pi_family_less_than_the_flat_prior():
    low = fam(p=0.0, pi=1.0 / 3.0)
    r_flat = run(OLD, low)
    r_pi = run(sim.mech_with(OLD, gate_prior="pi"), low)
    assert r_pi["failed_attempts"] < r_flat["failed_attempts"]


def test_pi_prior_attempts_a_high_pi_family_at_least_as_often():
    high = fam(p=0.0, pi=1.0)
    r_flat = run(OLD, high)
    r_pi = run(sim.mech_with(OLD, gate_prior="pi"), high)
    assert r_pi["failed_attempts"] >= r_flat["failed_attempts"]


def test_pi_posterior_still_converges_on_the_evidence():
    """The prior is worth two observations, not more."""
    mech = sim.mech_with(OLD, gate_prior="pi")
    f = sim.FamilyState("F", fam(pi=1.0), random.Random(0), mech)
    f.failed_attempts = 100
    assert f.p_hat(0.0) < 0.02


def test_draw_pi_matches_the_batch_split_and_is_addressed_by_name():
    assert sim.draw_pi("F", 3) == sim.draw_pi("F", 3)
    vals = [sim.draw_pi(f"fam{i}", 7) for i in range(4000)]
    frac_one = sum(1 for v in vals if v >= 1.0) / len(vals)
    frac_mid = sum(1 for v in vals if 0.5 <= v < 1.0) / len(vals)
    frac_low = sum(1 for v in vals if v < 0.5) / len(vals)
    assert frac_one == pytest.approx(7 / 14, abs=0.03)
    assert frac_mid == pytest.approx(2 / 14, abs=0.03)
    assert frac_low == pytest.approx(5 / 14, abs=0.03)


def test_pi_is_inert_at_its_default():
    """A family that says nothing about pi is priced by the top stratum."""
    f = sim.FamilyState("F", fam(), random.Random(0),
                        sim.mech_with(OLD, gate_prior="pi"))
    assert f.pi == sim.PI_DEFAULT == 1.0


# ---------------------------------------------------------------------------
# 3. C_fail
# ---------------------------------------------------------------------------

def test_c_fail_is_charged_only_on_failed_attempts():
    # Pinned to buy_formula="narrow" (W1.5): the attempt-count invariance to
    # C_fail_mult is the narrow-formula property (the price does not read
    # C_fail); under the default full formula the mult raises the price and
    # the pricey family attempts one time fewer.
    stream = hot(30)
    base = run(OLD_NARROW, fam(p=0.0), stream)
    pricey = run(OLD_NARROW, fam(p=0.0, C_fail_mult=3.0), stream)
    assert pricey["failed_attempts"] == base["failed_attempts"]
    assert pricey["compile_tokens"] == pytest.approx(
        3.0 * base["compile_tokens"])
    # a gate that always passes never pays it
    ok = run(OLD_NARROW, fam(p=1.0, C_fail_mult=3.0), stream)
    ok_base = run(OLD_NARROW, fam(p=1.0), stream)
    assert ok["failed_attempts"] == 0
    assert ok["compile_tokens"] == pytest.approx(ok_base["compile_tokens"])
    assert ok["failed_compile_tokens"] == 0.0


def test_c_fail_defaults_to_c():
    C, C_fail, p = sim.compile_prices_for_k(fam(), 3)
    assert C_fail == C == 14549.0
    C, C_fail, p = sim.compile_prices_for_k(fam(C_fail=99.0), 3)
    assert C_fail == 99.0


def test_c_fail_mult_survives_a_price_override():
    """The price ladder overwrites C, so the multiplier is the durable form."""
    f = fam(C_fail_mult=3.0)
    f["C"] = 233000.0
    C, C_fail, _ = sim.compile_prices_for_k(f, 3)
    assert C_fail == pytest.approx(3.0 * 233000.0)


def test_mixed_attempts_charge_both_prices():
    """The bill is C per pass and C_fail per miss, not C per try.

    A hazard that kills programs is what makes a family pay both prices on
    one stream: it passes the gate, the environment breaks the artifact, and
    the family has to buy its way back in.

    Pinned to buy_formula="narrow" (W1.5): the construction needs the family
    to clear the (narrow) price on a hot stream long enough to pass a gate
    at p = 0.2 while h_env kills the programs; the full B_hat at
    C_fail_mult = 3 prices it out and it never passes.
    """
    f = fam(p=0.2, C_fail_mult=3.0, h_env=0.3)
    P = params({"F": f}, tau=TAU, eviction=dict(EVICT))
    r = sim.run_policy("ours_noinflate", hot(300), P, random.Random(5),
                       OLD_NARROW)
    assert r["failed_attempts"] > 0 and r["passed_attempts"] > 0
    expected = (r["passed_attempts"] * 14549.0
                + r["failed_attempts"] * 3.0 * 14549.0)
    assert r["compile_tokens"] == pytest.approx(expected)
    assert r["failed_compile_tokens"] == pytest.approx(
        r["failed_attempts"] * 3.0 * 14549.0)


# ---------------------------------------------------------------------------
# 4. the E11 grid
# ---------------------------------------------------------------------------

def test_grid_carries_p0_and_both_c_fail_levels():
    pts = sweep._grid_points()
    assert 0.0 in {pt["p"] for pt in pts}
    assert {pt["c_fail"] for pt in pts} == {1.0, 3.0}
    # C_fail is inert at p = 1, so it is not duplicated there
    assert {pt["c_fail"] for pt in pts if pt["p"] >= 1.0} == {1.0}
    assert len({sweep.config_key(pt) for pt in pts}) == len(pts)


def test_grid_cell_count_is_the_reported_one():
    pts = sweep._grid_points()
    assert len(pts) == 50
    cells = sweep.build_cells(
        experiments.load_constants(sweep.Path(sweep.__file__).resolve()
                                   .parent / "constants.measured.json"),
        reps=2, seed=7)
    assert len(cells) == len(pts) * len(sweep.STREAMS) * len(sweep.PRICES)
    assert len(cells) == 400


def test_grid_cells_carry_the_mechanism_rows():
    base = experiments.load_constants(
        sweep.Path(sweep.__file__).resolve().parent
        / "constants.measured.json")
    cells = sweep.build_cells(base, reps=2, seed=7,
                              grid=[{"block": "t", "h": 0.1, "q0": 0.4,
                                     "p": 0.0, "r_fallback": 1.0,
                                     "sigma": 0.0, "silent_penalty": 1.0,
                                     "c_fail": 3.0}])
    cell = cells[0]
    assert set(cell["variants"]) == set(sweep.VARIANT_ROWS)
    assert cell["variant_policy"] == sweep.REFERENCE
    assert cell["pi_mode"] == "population"
    assert cell["mech"]["gate_prior"] == "add_one"
    assert cell["variants"]["ours_spend_cap"]["spend_cap"] == "horizon"
    assert cell["variants"]["ours_cap_epoch"]["spend_cap"] == "epoch"
    assert cell["variants"]["ours_cap_realized"]["spend_cap"] == "realized"
    assert cell["variants"]["ours_pi_prior"]["gate_prior"] == "pi"
    assert cell["variants"]["breakeven_cap_epoch"]["spend_cap"] == "epoch"
    assert cell["variant_policies"]["breakeven_cap_epoch"] \
        == sweep.BREAKEVEN_POLICY


def test_c_fail_ratio_is_a_grid_parameter():
    pts = sweep._grid_points(c_fail_high=5.0)
    assert {pt["c_fail"] for pt in pts} == {1.0, 5.0}


def test_population_pi_reaches_the_family_profiles():
    layouts = {"a": fam(), "b": fam(), "c": fam()}
    P = experiments.real_params(layouts, {"x", "y", "z", "w"}, None,
                                dict(TAU), {"enabled": False}, 60,
                                None, "population", 7)
    pis = {n: f["pi"] for n, f in P["families"].items()}
    assert all(v in {v0 for v0, _ in sim.PI_POPULATION} for v in pis.values())
    flat = experiments.real_params(layouts, {"x", "y"}, None, dict(TAU),
                                   {"enabled": False}, 60)
    assert all("pi" not in f for f in flat["families"].values())


# ---------------------------------------------------------------------------
# 5. the two cap variants (sim.SPEND_CAP_DOC)
# ---------------------------------------------------------------------------
# The horizon cap charges every failed attempt a family ever made against a
# budget that only projects its NEXT deployment, so a family that bought a
# program, served it, and then lost it to drift can be forbidden the
# recompile.  "epoch" restarts the accumulator at the break; "realized"
# widens the budget by what the family's programs already delivered.

CAP_EPOCH = sim.mech_with(OLD, spend_cap="epoch", spend_cap_mult=1.0)
CAP_REALIZED = sim.mech_with(OLD, spend_cap="realized", spend_cap_mult=1.0)


def test_spend_cap_mode_reads_the_boolean_and_the_names():
    assert sim.spend_cap_mode({}) is None
    assert sim.spend_cap_mode({"spend_cap": False}) is None
    assert sim.spend_cap_mode({"spend_cap": True}) == "horizon"
    for name in sim.SPEND_CAP_MODES:
        assert sim.spend_cap_mode({"spend_cap": name}) == name
    with pytest.raises(ValueError):
        sim.spend_cap_mode({"spend_cap": "sometimes"})


def _flaky(**kw):
    """A family whose gate sometimes passes and whose programs then die."""
    return fam(p=0.5, q0=0.4, C_fail_mult=3.0, h_env=0.02, **kw)


def test_epoch_gives_a_family_whose_program_dies_a_fresh_budget():
    """The event that restarts the budget is the observed break itself."""
    f = _flaky()
    r_h = run(CAP, f, hot(4000))
    r_e = run(CAP_EPOCH, f, hot(4000))
    assert r_h["n_program_deaths"] > 0
    # the horizon cap is blocking almost only families that already lost a
    # program, which is the failure this variant is aimed at
    assert r_h["cap_blocks_after_death"] > 10 * (r_h["cap_blocks_fresh"] + 1)
    # and the epoch restart lets them back in
    assert r_e["cap_blocks_after_death"] < r_h["cap_blocks_after_death"]
    assert r_e["passed_attempts"] > r_h["passed_attempts"]


def test_epoch_resets_the_accumulator_at_the_break_and_not_before():
    """Unit form: the counter the epoch cap reads is zeroed by a death."""
    f = sim.FamilyState("F", fam(p=1.0, h_env=1.0, C_fail_mult=3.0),
                        random.Random(0), CAP_EPOCH)
    f.epoch_spend = f.failed_spend = 12345.0
    f.compiled = f.program_alive = True
    assert f.serve(0.0, use_program=True) >= f.d      # this use kills it
    assert f.program_alive is False
    assert f.epoch_spend == 0.0
    assert f.failed_spend == 12345.0                  # metric is untouched


def test_realized_lets_a_family_that_has_served_many_uses_attempt_again():
    """The budget carries the saving already delivered, so a long-serving
    family can afford another attempt where the horizon cap cannot."""
    f = _flaky()
    r_h = run(CAP, f, hot(4000))
    r_r = run(CAP_REALIZED, f, hot(4000))
    assert r_r["realized_saving"] > 0.0
    assert r_r["cap_blocks_after_death"] < r_h["cap_blocks_after_death"]
    assert r_r["passed_attempts"] > r_h["passed_attempts"]


def test_realized_budget_is_the_ski_rental_sum():
    """Unit form: failed_spend is compared against mult * (realized + E*s)."""
    f = sim.FamilyState("F", fam(), random.Random(0), CAP_REALIZED)
    f.failed_spend = 100.0
    assert f.spend_cap_blocks(CAP_REALIZED, 60.0) is True   # 100 >= 60
    f.realized_saving = 50.0
    assert f.spend_cap_blocks(CAP_REALIZED, 60.0) is False  # 100 < 110
    # a program that lost money never makes the budget NARROWER than horizon
    f.realized_saving = -1e9
    assert f.spend_cap_blocks(CAP_REALIZED, 60.0) \
        == f.spend_cap_blocks(CAP, 60.0)


def test_realized_saving_is_the_use_by_use_counterfactual():
    f = sim.FamilyState("F", fam(c=100.0, d=10.0), random.Random(0),
                        CAP_REALIZED)
    f.compiled = f.program_alive = True
    assert f.serve(0.0, use_program=True) == 10.0
    assert f.realized_saving == pytest.approx(90.0)         # c - d
    assert f.serve(1.0, use_program=True) == 110.0          # q0 fallback
    assert f.realized_saving == pytest.approx(90.0 - 10.0)  # -d
    before = f.realized_saving
    assert f.serve(0.0, use_program=False) == 100.0         # reactive
    assert f.realized_saving == before                      # not a program use


def test_both_variants_reduce_to_the_horizon_cap_with_no_admission():
    """No program was ever admitted: nothing died and nothing was saved.

    Pinned to buy_formula="narrow" (W1.5): the cap's fresh-family blocks
    exist on this construction only under the narrow price, which retries
    past the point the cap forbids; the full B_hat stops first and the cap
    never binds.
    """
    dead = fam(p=0.0, C_fail_mult=3.0)
    ref = run(CAP_NARROW, dead)
    for mech in (sim.mech_with(CAP_EPOCH, buy_formula="narrow"),
                 sim.mech_with(CAP_REALIZED, buy_formula="narrow")):
        r = run(mech, dead)
        assert r["final_tokens"] == pytest.approx(ref["final_tokens"])
        assert r["failed_attempts"] == ref["failed_attempts"]
        assert r["cap_blocks_fresh"] == ref["cap_blocks_fresh"]
        assert r["cap_blocks_after_death"] == 0
    assert ref["cap_blocks_fresh"] > 0


def test_the_boolean_cap_is_still_the_horizon_cap():
    """Backward compatibility: every published spend_cap row is unmoved."""
    f = _flaky()
    r_bool = run(sim.mech_with(OLD, spend_cap=True, spend_cap_mult=1.0), f,
                 hot(1500))
    r_name = run(sim.mech_with(OLD, spend_cap="horizon", spend_cap_mult=1.0),
                 f, hot(1500))
    assert r_bool["final_tokens"] == r_name["final_tokens"]


def test_cap_counters_are_zero_when_the_cap_is_off():
    r = run(OLD, _flaky(), hot(1500))
    assert r["cap_blocks_fresh"] == r["cap_blocks_admitted"] == 0
    assert r["cap_blocks_after_death"] == 0


# ---------------------------------------------------------------------------
# 6. the population admission prior (gate_prior = "population")
# ---------------------------------------------------------------------------
# Empirical Bayes on the stream's own admission record: mu is the running
# (admitted + 1) / (attempts + 2) over every family that has attempted, and
# the family's own gate results move its estimate from there.

POP = sim.mech_with(OLD, gate_prior="population")


def test_population_prior_with_no_history_is_add_one():
    """At the default strength of two, mu = 1/2 gives (a0, b0) = (1, 1)."""
    assert sim.gate_prior_ab("population", 1.0, 2.0, None) == (1.0, 1.0)
    assert sim.gate_prior_ab("population", 1.0, 2.0, 0.5) == (1.0, 1.0)
    stats = sim.PopulationStats()
    assert stats.admit_mean() == pytest.approx(0.5)
    f = sim.FamilyState("F", fam(), random.Random(0), POP)
    g = sim.FamilyState("F", fam(), random.Random(0), OLD)
    assert f.p_hat(0.7, stats.admit_mean()) == pytest.approx(g.p_hat(0.7))
    # and end to end: an empty-history stream runs the add-one row exactly
    r_pop = run(POP, fam(p=0.0, C_fail_mult=3.0), hot(60))
    r_add = run(OLD, fam(p=0.0, C_fail_mult=3.0), hot(60))
    assert r_pop["failed_attempts"] <= r_add["failed_attempts"]


def test_population_prior_starts_a_fresh_family_low_after_rejections():
    stats = sim.PopulationStats()
    for _ in range(50):
        stats.observe_attempt(False)
    assert stats.admit_mean() == pytest.approx(1.0 / 52.0)
    fresh = sim.FamilyState("new", fam(), random.Random(0), POP)
    assert fresh.p_hat(0.9, stats.admit_mean()) < 0.05
    # the flat prior would have started it at 1/2
    flat = sim.FamilyState("new", fam(), random.Random(0), OLD)
    assert flat.p_hat(0.9) == pytest.approx(0.5)


def test_population_prior_is_moved_up_by_the_family_own_admission():
    stats = sim.PopulationStats()
    for _ in range(50):
        stats.observe_attempt(False)
    mu = stats.admit_mean()
    f = sim.FamilyState("F", fam(), random.Random(0), POP)
    before = f.p_hat(0.9, mu)
    f.passed_attempts += 1
    after = f.p_hat(0.9, mu)
    assert after > before
    # the prior is worth two observations, so one admission carries the
    # estimate most of the way from the population rate to 1/3
    assert before < 0.03 and after == pytest.approx(1.0385 / 3.0, abs=1e-3)
    # and the other way: a rejection moves it down
    g = sim.FamilyState("G", fam(), random.Random(0), POP)
    g.failed_attempts += 1
    assert g.p_hat(0.9, mu) < before


def test_population_prior_strength_is_a_parameter():
    strong = sim.mech_with(POP, gate_prior_strength=4.0)
    f = sim.FamilyState("F", fam(), random.Random(0), strong)
    f.failed_attempts = 1
    weak = sim.FamilyState("F", fam(), random.Random(0), POP)
    weak.failed_attempts = 1
    # a prior worth four observations at mu = 0.9 resists one rejection more
    assert f.p_hat(0.0, 0.9) > weak.p_hat(0.0, 0.9)
    assert sim.gate_prior_ab("population", 1.0, 4.0, 0.5) == (2.0, 2.0)


def test_population_admission_record_tracks_the_run():
    """The counters the prior reads are fed by the engine's own attempts."""
    r = run(POP, fam(p=0.0, C_fail_mult=3.0), hot(80))
    assert r["failed_attempts"] > 0 and r["passed_attempts"] == 0
    stats = sim.PopulationStats()
    assert stats.attempts == 0
    stats.observe_attempt(True)
    stats.observe_attempt(False)
    assert (stats.attempts, stats.admits) == (2, 1)
    assert stats.admit_mean() == pytest.approx(2.0 / 4.0)


def test_population_prior_prices_a_hopeless_stream_out_of_retrying():
    """Many p = 0 families: the stream learns the regime and stops paying."""
    names = [f"F{i}" for i in range(12)]
    stream = [n for _ in range(120) for n in names]
    P = params({n: fam(p=0.0, C_fail_mult=3.0) for n in names}, tau=TAU,
               eviction=dict(EVICT))
    r_add = sim.run_policy("ours_noinflate", stream, P, random.Random(1), OLD)
    r_pop = sim.run_policy("ours_noinflate", stream, P, random.Random(1), POP)
    assert r_pop["failed_attempts"] < r_add["failed_attempts"]
    assert r_pop["final_tokens"] < r_add["final_tokens"]


# ---------------------------------------------------------------------------
# 7. the heterogeneous admission regime (H_mixed)
# ---------------------------------------------------------------------------

def _base_v2():
    return experiments.load_constants(
        sweep.Path(sweep.__file__).resolve().parent
        / "constants.measured.v2.json")


def test_draw_admitted_is_addressed_by_name_and_hits_the_rate():
    assert sim.draw_admitted("F", 0.5, 3) == sim.draw_admitted("F", 0.5, 3)
    names = [f"fam{i}" for i in range(4000)]
    for rate in (5 / 7, 2 / 7):
        frac = sum(sim.draw_admitted(n, rate, 7) for n in names) / len(names)
        assert frac == pytest.approx(rate, abs=0.03)


def test_mixed_regime_reads_the_cost_set_measured_block():
    base = _base_v2()
    glm = sweep.mixed_regime(base, "android_glm", 7)
    ds = sweep.mixed_regime(base, "android_ds", 7)
    assert glm["admission_rate"] == pytest.approx(5 / 7)
    assert ds["admission_rate"] == pytest.approx(2 / 7)
    assert glm["C"] == pytest.approx(916335.4666666666)
    assert ds["C"] == pytest.approx(106194.5)
    assert glm["C_fail_mult"] == pytest.approx(2.0829936590907905)
    assert ds["C_fail_mult"] == pytest.approx(11.087667012373094)


def test_mixed_regime_splits_the_families_into_two_priced_groups():
    base = _base_v2()
    regime = sweep.mixed_regime(base, "android_glm", 7)
    layouts = base["cost_sets"]["android_glm"]["layouts"]
    names = {f"v{i}" for i in range(600)}
    P = experiments.real_params(layouts, names, None, dict(TAU),
                                {"enabled": False}, 60, None, None, 0,
                                regime)
    ps = [f["p"] for f in P["families"].values()]
    assert set(ps) == {0.0, 1.0}
    assert sum(ps) / len(ps) == pytest.approx(5 / 7, abs=0.06)
    for f in P["families"].values():
        assert f["C"] == pytest.approx(regime["C"])
        assert f["C_fail_mult"] == pytest.approx(regime["C_fail_mult"])
        C, C_fail, _ = sim.compile_prices_for_k(f, 3)
        assert C_fail == pytest.approx(regime["C_fail_mult"] * C)
    # c and d are still the measured layouts, not the regime
    assert len({f["c"] for f in P["families"].values()}) > 1


def test_mixed_regime_is_inert_when_absent_and_the_ladder_still_wins():
    base = _base_v2()
    layouts = base["cost_sets"]["android_glm"]["layouts"]
    names = {f"v{i}" for i in range(40)}
    plain = experiments.real_params(layouts, names, None, dict(TAU),
                                    {"enabled": False}, 60)
    # no regime: every family keeps the p and C of the layout it was given
    src = {(l["p"], l["C"]) for l in layouts.values()}
    assert {(f["p"], f["C"]) for f in plain["families"].values()} <= src
    regime = sweep.mixed_regime(base, "android_glm", 7)
    priced = experiments.real_params(layouts, names, 233000.0, dict(TAU),
                                     {"enabled": False}, 60, None, None, 0,
                                     regime)
    assert all(f["C"] == 233000.0 for f in priced["families"].values())
    with pytest.raises(ValueError):
        experiments.real_params(layouts, names, None, dict(TAU),
                                {"enabled": False}, 60, None, None, 0,
                                {"mode": "nonsense"})


def test_mixed_grid_and_cells_carry_the_regime():
    pts = sweep._grid_points_mixed()
    assert len(pts) == 10
    assert all(pt["mixed"] for pt in pts)
    assert len({sweep.config_key(pt) for pt in pts}) == len(pts)
    assert "p=mixed" in sweep.config_key(pts[0])
    cells = sweep.build_cells(_base_v2(), reps=2, seed=7, grid=pts,
                              cost_set="android_ds",
                              rows=list(sweep.MIXED_VARIANT_ROWS))
    assert len(cells) == 10 * len(sweep.STREAMS) * len(sweep.PRICES)
    cell = cells[0]
    assert cell["regime"]["mode"] == "mixed"
    assert set(cell["variants"]) == set(sweep.MIXED_VARIANT_ROWS)
    assert cell["variants"]["ours_pop_prior"]["gate_prior"] == "population"
    s4 = cell["variants"]["ours_pop_prior_s4"]
    assert s4["gate_prior_strength"] == sweep.POP_PRIOR_STRENGTH_HIGH
    both = cell["variants"]["ours_pop_cap_epoch"]
    assert both["gate_prior"] == "population" and both["spend_cap"] == "epoch"
    # env_constants left the layouts' measured p alone on a mixed point;
    # the regime is what sets p, per family, inside real_params
    assert [lay["p"] for lay in cell["layouts"].values()] \
        == [lay["p"] for lay
            in _base_v2()["cost_sets"]["android_ds"]["layouts"].values()]
