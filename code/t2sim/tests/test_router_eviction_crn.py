"""Unit tests of the deployment options added on 2026-09-09.

Four things move out of the engine and into the deployment:

  * common random numbers (sim.CoinBook), so two policies that reach the
    same compile attempt or the same program use of the same family see the
    same coin instead of desynchronising at their first divergent decision;
  * the router (tau_R, eps_R), so full listing is one point in the design
    space rather than the model;
  * library residency (eviction), which bounds the externality of a mistaken
    admission at m*T and bounds the trigger's own tax term with it;
  * k_min demonstrations before a family may be compiled, with an optional
    C(k), p(k) table for compilers whose price depends on how many
    demonstrations they read.

Every one of them is off by default, and the first block below pins that:
with the options off the engine is the one that produced E3/E4/E8.
"""
from __future__ import annotations

import math
import random

import pytest

import sim
import streams as streams_mod


def fam(c=95296.0, d=347.0, C=14549.0, p=1.0, q0=0.0, h=0.0, k_table=None):
    f = {"c": c, "d": d, "C": C, "p": p, "q0": q0, "h": h,
         "binding_space": 12}
    if k_table:
        f["k_table"] = k_table
    return f


def params(families, tau=None, eps=None, horizon=60, **extra):
    p = {"families": families, "horizon": horizon,
         "tau": tau if tau is not None else {"m": 0.0, "tau0": 0.0},
         "epsilon": eps if eps is not None else {"enabled": False}}
    p.update(extra)
    return p


MECH = dict(sim.MECH_DEFAULT)
TAU = {"m": 91.0, "tau0": 368.0}
CLIFF = {"enabled": True, "eps0": 0.3, "cliff_start": 100,
         "cliff_slope": 20}
EVICT = {"T": 50.0, "v_min": 10, "t_min": 1, "t_max": 100000}


def zipf(n=400, k=12, seed=3):
    fams = [f"F{i}" for i in range(k)]
    return fams, streams_mod.gen_stream("zipf", n, fams, random.Random(seed))


def hot_then_singletons(n_hot: int, n_singleton: int) -> list[str]:
    """One hot family up front, then a long run of once-seen families."""
    return ["HOT"] * n_hot + [f"S{i}" for i in range(n_singleton)]


# ---------------------------------------------------------------------------
# nothing moves when the options are off
# ---------------------------------------------------------------------------

def test_listing_router_is_the_historical_tau_and_eps():
    for n in (0, 1, 7, 100, 846, 68773):
        assert sim.tau_of(n, TAU) == sim.tau_of(n, TAU, sim.ROUTER_LISTING)
        assert sim.tau_of(n, TAU) == 368.0 + 91.0 * n
        assert sim.epsilon_of(n, CLIFF) == \
            sim.epsilon_of(n, CLIFF, sim.ROUTER_LISTING)


def test_evicting_alias_without_an_eviction_config_is_the_base_policy():
    fams, stream = zipf()
    P = params({n: fam(q0=0.17) for n in fams}, tau=TAU)
    for base, alias in sim.POLICIES_EVICT.items():
        a = sim.run_policy(alias, stream, P, random.Random(1), MECH)
        b = sim.run_policy(base, stream, P, random.Random(1), MECH)
        assert a["final_tokens"] == b["final_tokens"]
        assert a["curve"] == b["curve"]
        assert b["n_evictions"] == 0 and b["n_relists"] == 0


def test_k_min_one_is_inert_for_every_policy():
    fams, stream = zipf(n=300, k=8, seed=9)
    fs = {n: fam(q0=0.17, p=0.6) for n in fams}
    base = params(fs, tau=TAU)
    with_k = params(fs, tau=TAU, k_min=1)
    for p in sim.POLICIES:
        a = sim.run_policy(p, stream, base, random.Random(2), MECH)
        b = sim.run_policy(p, stream, with_k, random.Random(2), MECH)
        assert a["final_tokens"] == b["final_tokens"], p
        assert a["curve"] == b["curve"], p


def test_empty_k_table_leaves_the_price_and_the_gate_alone():
    f = fam(C=14549.0, p=0.8)
    assert sim.compile_price_for_k(f, 1) == (14549.0, 0.8)
    assert sim.compile_price_for_k(f, 9) == (14549.0, 0.8)


# ---------------------------------------------------------------------------
# common random numbers
# ---------------------------------------------------------------------------

class RecordingBook(sim.CoinBook):
    """A CoinBook that remembers which coin each policy actually asked for."""

    def __init__(self, seed):
        super().__init__(seed)
        self.gates: dict[tuple, float] = {}
        self.uses: dict[tuple, float] = {}

    def gate(self, family, attempt):
        v = super().gate(family, attempt)
        self.gates[(family, attempt)] = v
        return v

    def use(self, family, use_index):
        v = super().use(family, use_index)
        self.uses[(family, use_index)] = v
        return v


def test_coinbook_value_does_not_depend_on_the_order_it_is_asked():
    a = sim.CoinBook(11)
    forward = [a.gate("F", j) for j in range(6)]
    b = sim.CoinBook(11)
    backward = [b.gate("F", j) for j in (5, 4, 3, 2, 1, 0)]
    assert forward == list(reversed(backward))
    assert a.use("F", 3) == sim.CoinBook(11).use("F", 3)
    assert a.gate("F", 0) != a.gate("G", 0)


def test_two_policies_share_the_gate_and_failure_coins():
    """The point of CRN: same (f, j) and (f, u) means the same coin."""
    fams, stream = zipf(n=400, k=10, seed=17)
    P = params({n: fam(q0=0.3, p=0.5) for n in fams}, tau=TAU)
    seen = {}
    for policy in ("always_compile", "ours", "on_second"):
        book = RecordingBook(99)
        sim.run_policy(policy, stream, P, random.Random(4), MECH, book)
        seen[policy] = (dict(book.gates), dict(book.uses))
    ref_g, ref_u = seen["always_compile"]
    shared_g = shared_u = 0
    for policy in ("ours", "on_second"):
        g, u = seen[policy]
        for key, v in g.items():
            if key in ref_g:
                assert v == ref_g[key], key
                shared_g += 1
        for key, v in u.items():
            if key in ref_u:
                assert v == ref_u[key], key
                shared_u += 1
    # the assertion above is vacuous unless the policies really do overlap
    assert shared_g > 0 and shared_u > 0


def test_crn_pairs_the_gate_outcome_of_the_first_attempt():
    """Without CRN two policies can get opposite outcomes on the same gate."""
    fams = ["A"]
    stream = ["A"] * 40
    P = params({"A": fam(p=0.5, C=1.0)}, tau=TAU)
    for seed in range(12):
        book = sim.CoinBook(seed)
        a = sim.run_policy("always_compile", stream, P, random.Random(0),
                           MECH, book)
        b = sim.run_policy("on_second", stream, P, random.Random(0), MECH,
                           book)
        # both reach attempt 0 of "A"; the gate coin is the same one, so the
        # number of attempts they need to pass it must agree
        assert a["failed_attempts"] == b["failed_attempts"]
        assert a["passed_attempts"] == b["passed_attempts"]


def test_coins_leave_the_policy_generator_untouched():
    fams, stream = zipf(n=200, k=6, seed=21)
    P = params({n: fam(q0=0.2, p=0.7) for n in fams}, tau=TAU)
    book = sim.CoinBook(5)
    rng = random.Random(8)
    sim.run_policy("ours", stream, P, rng, MECH, book)
    assert rng.random() == random.Random(8).random()


# ---------------------------------------------------------------------------
# routers
# ---------------------------------------------------------------------------

def test_retrieval_tau_is_flat_once_the_library_passes_k():
    r = dict(sim.ROUTER_RETRIEVAL)
    flat = sim.tau_of(1000, TAU, r)
    assert flat == 368.0 + 91.0 * 5
    for n in (5, 50, 846, 68773):
        assert sim.tau_of(n, TAU, r) == flat
    # below k only the entries that exist can be listed
    assert sim.tau_of(0, TAU, r) == 368.0
    assert sim.tau_of(3, TAU, r) == 368.0 + 91.0 * 3


def test_hierarchical_tau_is_about_two_m_root_n():
    r = dict(sim.ROUTER_HIERARCHICAL)
    for n in (100, 846, 10000, 68773):
        got = sim.tau_of(n, TAU, r)
        want = 2 * 368.0 + 2 * 91.0 * math.sqrt(n)
        assert got == pytest.approx(want, rel=0.05)
    # and far below full listing at the library sizes always-compile reaches
    assert sim.tau_of(846, TAU, r) < 0.1 * sim.tau_of(846, TAU)


def test_router_tau_ordering_at_every_library_size():
    listing, retr, hier = (sim.ROUTER_LISTING, dict(sim.ROUTER_RETRIEVAL),
                           dict(sim.ROUTER_HIERARCHICAL))
    for n in (50, 200, 846, 5000, 68773):
        assert sim.tau_of(n, TAU, retr) <= sim.tau_of(n, TAU, hier) \
            <= sim.tau_of(n, TAU, listing)


def test_retrieval_epsilon_rises_with_n_and_listing_stays_at_zero():
    r = dict(sim.ROUTER_RETRIEVAL)
    off = {"enabled": False}
    assert sim.epsilon_of(10000, off, sim.ROUTER_LISTING) == 0.0
    vals = [sim.epsilon_of(n, off, r) for n in (5, 200, 2000, 20000)]
    assert vals[0] == pytest.approx(r["eps_select"])
    assert vals[1] == pytest.approx(r["eps_select"])       # at n_ref
    assert vals[2] > vals[1] and vals[3] > vals[2]
    # SkillOps' worst arm loses 7.5 points over one decade of n
    assert vals[2] - vals[1] == pytest.approx(0.075)
    assert sim.epsilon_of(20000, off, {**r, "eps_enabled": False}) == 0.0


def test_hierarchical_epsilon_stays_left_of_the_cliff():
    n = 2500                               # branching sqrt(2500) = 50
    assert sim.epsilon_of(n, CLIFF, sim.ROUTER_LISTING) == \
        pytest.approx(CLIFF["eps0"], rel=1e-3)
    hier = sim.epsilon_of(n, CLIFF, sim.ROUTER_HIERARCHICAL, TAU)
    assert hier < 0.25 * CLIFF["eps0"]
    # at n = 10,000 both branchings sit exactly on the onset, so a hierarchy
    # buys tokens rather than accuracy once the library is large enough
    assert sim.epsilon_of(10000, CLIFF, sim.ROUTER_HIERARCHICAL, TAU) \
        == pytest.approx(0.2775)


def test_retrieval_router_cuts_the_manifest_tax_of_always_compile():
    stream = hot_then_singletons(40, 400)
    fs = {n: fam(q0=0.17) for n in set(stream)}
    base = params(fs, tau=TAU)
    retr = params(fs, tau=TAU, router=dict(sim.ROUTER_RETRIEVAL))
    a = sim.run_policy("always_compile", stream, base, random.Random(1), MECH)
    b = sim.run_policy("always_compile", stream, retr, random.Random(1), MECH)
    assert b["tau_total"] < 0.1 * a["tau_total"]
    assert b["final_library"] == a["final_library"]   # the library is the same


def test_unknown_router_kind_is_rejected():
    with pytest.raises(ValueError):
        sim.tau_of(10, TAU, {"kind": "telepathy"})


# ---------------------------------------------------------------------------
# eviction
# ---------------------------------------------------------------------------

def test_t_star_matches_the_closed_form():
    # B.3.2: C_eff = 14.5k, m = 91, lambda = 1/100 -> about 47 arrivals
    got = sim.t_star(14549.0, 0.01, 91.0)
    assert got == pytest.approx(100.0 * math.log(14549.0 * 0.01 / 91.0))
    assert got == pytest.approx(47.0, abs=1.0)
    # residency is not worth paying for when C_eff * lambda <= m
    assert sim.t_star(14549.0, 0.001, 91.0) == 0.0
    assert sim.eviction_T({"T": "t_star", "t_min": 1.0}, 14549.0, 0.001,
                          91.0) == 1.0
    assert sim.eviction_T({"T": 25.0}, 14549.0, 0.01, 91.0) == 25.0


def test_eviction_bounds_the_externality_of_a_mistaken_admission():
    """A singleton's entry is resident for at most T arrivals, so its tax is
    at most m*T however long the stream runs."""
    T = 50.0
    for tail in (400, 1200):
        stream = hot_then_singletons(30, tail)
        fs = {n: fam(q0=0.17) for n in set(stream)}
        P = params(fs, tau=TAU, eviction={**EVICT, "T": T})
        r = sim.run_policy("always_compile_evict", stream, P,
                           random.Random(3), MECH)
        assert r["n_evictions"] > 0
        # every wasted admission's tax, per admission, under the bound
        n_wasted = max(1, r["wasted_compiles_tax"])
        assert r["wasted_tax_tokens"] / n_wasted <= 91.0 * T + 1e-9
        # and the whole manifest tax is far below the monotone library's
        mono = sim.run_policy("always_compile", stream,
                              params(fs, tau=TAU), random.Random(3), MECH)
        assert r["tau_total"] < 0.5 * mono["tau_total"]


def test_eviction_caps_the_library_and_the_trace_comes_back_down():
    # 200 singletons fill the library, then one hot family runs alone and
    # every singleton ages out
    stream = [f"S{i}" for i in range(200)] + ["HOT"] * 400
    fs = {n: fam(q0=0.17) for n in set(stream)}
    P = params(fs, tau=TAU, eviction=EVICT)
    r = sim.run_policy("always_compile_evict", stream, P, random.Random(3),
                       MECH)
    assert r["max_library"] <= EVICT["T"] + 2      # bounded by the window
    assert r["final_library"] == 1                 # only HOT survives
    assert r["final_library"] < r["max_library"]
    assert r["final_library"] == r["n_curve"][-1]
    assert min(r["n_curve"]) < max(r["n_curve"])
    mono = sim.run_policy("always_compile", stream, params(fs, tau=TAU),
                          random.Random(3), MECH)
    assert mono["final_library"] == 201


def test_relisting_costs_no_compile_tokens():
    """The artifact survives delisting, so re-admission is a manifest edit."""
    stream = (["A"] * 3 + ["B"] * 60) * 4
    fs = {"A": fam(q0=0.0), "B": fam(q0=0.0)}
    P = params(fs, tau=TAU, eviction={**EVICT, "T": 20.0, "v_min": 1})
    r = sim.run_policy("always_compile_evict", stream, P, random.Random(5),
                       MECH)
    assert r["n_relists"] > 0
    # tokens spent on compiling = one gate-passing attempt per family
    assert r["compile_tokens"] == pytest.approx(
        r["n_compiles"] * 14549.0)
    assert r["n_compiles"] < r["n_relists"] + r["n_compiles"]


def test_the_evidence_floor_protects_a_young_entry():
    stream = hot_then_singletons(5, 300)
    fs = {n: fam(q0=0.0) for n in set(stream)}
    short = sim.run_policy(
        "always_compile_evict", stream,
        params(fs, tau=TAU, eviction={"T": 1.0, "v_min": 1}),
        random.Random(7), MECH)
    floored = sim.run_policy(
        "always_compile_evict", stream,
        params(fs, tau=TAU, eviction={"T": 1.0, "v_min": 40}),
        random.Random(7), MECH)
    assert floored["tau_total"] > short["tau_total"]
    assert floored["n_evictions"] <= short["n_evictions"]


def test_ours_evict_tax_term_is_bounded_so_it_compiles_more_late():
    """m*min(T, Lambda_rest) instead of m*Lambda_rest: the externality term
    stops growing with the stream, so a family that turns hot late is no
    longer priced out of the library."""
    stream = ["FILL%d" % i for i in range(3000)] + ["LATE"] * 40
    fs = {n: fam(q0=0.17) for n in set(stream)}
    plain = sim.run_policy("ours", stream, params(fs, tau=TAU),
                           random.Random(2), MECH)
    ev = sim.run_policy("ours_evict", stream,
                        params(fs, tau=TAU, eviction=EVICT),
                        random.Random(2), MECH)
    assert ev["n_compiles"] > plain["n_compiles"]
    assert ev["final_tokens"] < plain["final_tokens"]


# ---------------------------------------------------------------------------
# minimum demonstrations
# ---------------------------------------------------------------------------

def test_k_min_delays_every_first_compile_to_the_kth_arrival():
    fams, stream = zipf(n=400, k=10, seed=13)
    fs = {n: fam(q0=0.17) for n in fams}
    for k_min in (2, 3, 5):
        r = sim.run_policy("always_compile", stream,
                           params(fs, tau=TAU, k_min=k_min),
                           random.Random(6), MECH)
        assert r["first_compile_rank"], k_min
        assert min(r["first_compile_rank"].values()) >= k_min


def test_k_min_three_is_the_autorpa_protocol_and_costs_the_early_uses():
    stream = ["A"] * 30
    fs = {"A": fam(q0=0.0)}
    one = sim.run_policy("always_compile", stream, params(fs, tau=TAU),
                         random.Random(6), MECH)
    three = sim.run_policy("always_compile", stream,
                           params(fs, tau=TAU, k_min=3), random.Random(6),
                           MECH)
    # two extra reactive episodes, minus the two arrivals that no longer
    # pay for a manifest entry
    assert three["final_tokens"] - one["final_tokens"] == pytest.approx(
        2.0 * (95296.0 - 347.0) - 2.0 * 91.0)
    assert three["n_compiles"] == one["n_compiles"] == 1


def test_c_of_k_and_p_of_k_come_from_the_table():
    f = fam(C=10000.0, p=0.5, k_table={"3": {"C_scale": 1.4, "p_scale": 2.0}})
    assert sim.compile_price_for_k(f, 1) == (10000.0, 0.5)
    assert sim.compile_price_for_k(f, 3) == (14000.0, 1.0)
    assert sim.compile_price_for_k(f, 7) == (14000.0, 1.0)
    absolute = fam(C=10000.0, p=0.5, k_table={"2": {"C": 33.0, "p": 0.9}})
    assert sim.compile_price_for_k(absolute, 2) == (33.0, 0.9)


def test_k_table_price_reaches_the_engine():
    stream = ["A"] * 30
    plain = {"A": fam(q0=0.0, C=10000.0)}
    priced = {"A": fam(q0=0.0, C=10000.0,
                       k_table={"3": {"C_scale": 2.0}})}
    a = sim.run_policy("always_compile", stream, params(plain, tau=TAU,
                                                        k_min=3),
                       random.Random(6), MECH)
    b = sim.run_policy("always_compile", stream, params(priced, tau=TAU,
                                                        k_min=3),
                       random.Random(6), MECH)
    assert b["compile_tokens"] == pytest.approx(2.0 * a["compile_tokens"])


# ---------------------------------------------------------------------------
# accounting
# ---------------------------------------------------------------------------

def test_wasted_admissions_report_the_tax_they_caused():
    """Audit section 5.5: at N* < 1 every entry repays its own C on its first
    use, so wasted_compiles reads 0 while the manifest is taxing every
    arrival for entries that will never be used again."""
    # long enough that an early entry's residency tax, 91 per arrival,
    # outweighs the 78,749 its single use saves
    stream = hot_then_singletons(30, 1500)
    fs = {n: fam(q0=0.17) for n in set(stream)}
    r = sim.run_policy("always_compile", stream, params(fs, tau=TAU),
                       random.Random(3), MECH)
    assert r["wasted_compiles"] == 0
    assert r["wasted_compiles_tax"] > 400
    assert r["wasted_tax_tokens"] > 0
    # a singleton admitted at index k taxes exactly (T-1-k) later arrivals
    assert r["wasted_tax_tokens"] <= 91.0 * r["wasted_compiles_tax"] \
        * len(stream)
    # at a price no single use can repay, both counts fire
    dear = {n: fam(q0=0.17, C=1000000.0) for n in set(stream)}
    r2 = sim.run_policy("always_compile", stream, params(dear, tau=TAU),
                        random.Random(3), MECH)
    assert r2["wasted_compiles"] > 1000
    assert r2["wasted_compiles_tax"] >= r2["wasted_compiles"]


def test_no_tax_means_no_wasted_tax_tokens():
    stream = hot_then_singletons(20, 200)
    fs = {n: fam(q0=0.17) for n in set(stream)}
    r = sim.run_policy("always_compile", stream,
                       params(fs, tau={"m": 0.0, "tau0": 368.0}),
                       random.Random(3), MECH)
    assert r["wasted_tax_tokens"] == 0.0
    assert r["wasted_compiles_tax"] == r["wasted_compiles"]


def test_tax_share_is_the_manifest_fraction_of_the_bill():
    stream = hot_then_singletons(20, 400)
    fs = {n: fam(q0=0.17) for n in set(stream)}
    r = sim.run_policy("always_compile", stream, params(fs, tau=TAU),
                       random.Random(3), MECH)
    assert r["tax_share"] == pytest.approx(r["tau_total"] / r["final_tokens"])
    assert 0.0 < r["tax_share"] < 1.0


def test_library_trace_is_reported_alongside_the_final_size():
    fams, stream = zipf(n=400, k=12, seed=3)
    r = sim.run_policy("always_compile", stream,
                       params({n: fam(q0=0.17) for n in fams}, tau=TAU),
                       random.Random(3), MECH)
    assert len(r["n_curve"]) == len(r["curve"])
    assert r["n_curve"][-1] == r["final_library"] == r["max_library"] == 12
    assert r["n_curve"] == sorted(r["n_curve"])   # monotone without eviction


# ---------------------------------------------------------------------------
# the offline optimum under the new deployments
# ---------------------------------------------------------------------------

def test_offline_opt_tax_under_eviction_is_a_lower_bound_for_both_arms():
    stream = hot_then_singletons(40, 600)
    fs = {n: fam(q0=0.17) for n in set(stream)}
    P = params(fs, tau=TAU, eviction=EVICT)
    bound = sim.offline_optimum_tax(stream, P, EVICT)
    plain = sim.offline_optimum_tax(stream, P)
    assert bound <= plain            # eviction can only lower the optimum
    for policy in ("always_reactive", "always_compile",
                   "always_compile_evict", "ours", "ours_evict"):
        r = sim.run_policy(policy, stream, P, random.Random(3), MECH)
        assert r["final_tokens"] >= bound * (1.0 - 1e-9), policy


def test_offline_opt_tax_drops_the_marginal_under_a_flat_router():
    stream = hot_then_singletons(40, 600)
    fs = {n: fam(q0=0.17) for n in set(stream)}
    listing = sim.offline_optimum_tax(stream, params(fs, tau=TAU))
    retr = sim.offline_optimum_tax(
        stream, params(fs, tau=TAU, router=dict(sim.ROUTER_RETRIEVAL)))
    assert retr < listing
    assert sim.tau_marginal_lower_bound(TAU, sim.ROUTER_LISTING) == 91.0
    assert sim.tau_marginal_lower_bound(TAU, sim.ROUTER_RETRIEVAL) == 0.0
    for policy in ("always_reactive", "always_compile", "ours"):
        r = sim.run_policy(policy, stream,
                           params(fs, tau=TAU,
                                  router=dict(sim.ROUTER_RETRIEVAL)),
                           random.Random(3), MECH)
        assert r["final_tokens"] >= retr * (1.0 - 1e-9), policy


def test_oracle_tax_stops_charging_a_listing_externality_under_retrieval():
    """A flat-tau router imposes no marginal cost per entry, so the
    clairvoyant must not price one; under full listing the term is
    unchanged, which is what keeps E4_tax reproducing."""
    stream = hot_then_singletons(40, 1500)
    fs = {n: fam(q0=0.17) for n in set(stream)}
    listing = sim.run_policy("oracle_tax", stream, params(fs, tau=TAU),
                             random.Random(3), MECH)
    explicit = sim.run_policy("oracle_tax", stream,
                              params(fs, tau=TAU,
                                     router=dict(sim.ROUTER_LISTING)),
                              random.Random(3), MECH)
    assert listing["final_tokens"] == explicit["final_tokens"]
    retr = sim.run_policy("oracle_tax", stream,
                          params(fs, tau=TAU,
                                 router=dict(sim.ROUTER_RETRIEVAL)),
                          random.Random(3), MECH)
    assert retr["final_library"] > listing["final_library"]
