"""Unit tests for the fragility sweep (E9).

Two new pieces:

  * `always_compile_evict_abandon`, the naive rule with the one protection
    `ours` gets for free from its inflating threshold: after ABANDON_AFTER
    consecutive failed gate attempts the family is dropped.  Without it
    `always_compile*` re-pays C at every arrival of a family whose gate never
    passes, which is not a comparison anybody should want to win.
  * `sweep_fragility.fragility_constants`, which builds one grid point's
    constants in memory so `constants.measured.json` is never edited.
"""
from __future__ import annotations

import copy
import json
import random
from pathlib import Path

import pytest

import experiments
import sim
import streams as streams_mod
import sweep_fragility as sweep


def fam(c=95296.0, d=347.0, C=14549.0, p=1.0, q0=0.0, h=0.0):
    return {"c": c, "d": d, "C": C, "p": p, "q0": q0, "h": h,
            "binding_space": 12}


def params(families, tau=None, horizon=60, **extra):
    out = {"families": families, "horizon": horizon,
           "tau": tau if tau is not None else {"m": 0.0, "tau0": 0.0},
           "epsilon": {"enabled": False}}
    out.update(extra)
    return out


MECH = dict(sim.MECH_DEFAULT)
TAU = {"m": 91.0, "tau0": 368.0}
EVICT = {"T": 50.0, "v_min": 10, "t_min": 1, "t_max": 100000}


def zipf(n=400, k=12, seed=3):
    fams = [f"F{i}" for i in range(k)]
    return fams, streams_mod.gen_stream("zipf", n, fams, random.Random(seed))


# ---------------------------------------------------------------------------
# the abandon variant
# ---------------------------------------------------------------------------

def test_naive_rule_retries_a_dead_gate_at_every_arrival():
    """The behaviour the variant exists to fix, pinned so it cannot drift."""
    stream = ["F"] * 20
    P = params({"F": fam(p=0.0)}, tau=TAU, eviction=dict(EVICT))
    r = sim.run_policy("always_compile_evict", stream, P, random.Random(1),
                       MECH)
    assert r["n_compiles"] == 20
    assert r["passed_attempts"] == 0


def test_abandon_stops_after_three_failed_gate_attempts():
    stream = ["F"] * 20
    P = params({"F": fam(p=0.0)}, tau=TAU, eviction=dict(EVICT))
    r = sim.run_policy("always_compile_evict_abandon", stream, P,
                       random.Random(1), MECH)
    assert r["n_compiles"] == sim.ABANDON_AFTER == 3
    assert r["failed_attempts"] == 3


def test_abandon_threshold_is_configurable():
    stream = ["F"] * 20
    for k in (1, 2, 5):
        P = params({"F": fam(p=0.0)}, tau=TAU, eviction=dict(EVICT),
                   abandon_after=k)
        r = sim.run_policy("always_compile_evict_abandon", stream, P,
                           random.Random(1), MECH)
        assert r["n_compiles"] == k


def test_ours_already_abandons_a_dead_gate():
    """The rule the variant copies: c_eff = C/0 = inf, so ours never buys."""
    stream = ["F"] * 20
    P = params({"F": fam(p=0.0)}, tau=TAU, eviction=dict(EVICT))
    r = sim.run_policy("ours_evict", stream, P, random.Random(1), MECH)
    assert r["n_compiles"] == 0


def test_abandon_is_bit_identical_to_the_plain_rule_when_the_gate_never_misses():
    fams, stream = zipf()
    P = params({n: fam(q0=0.17) for n in fams}, tau=TAU,
               eviction=dict(EVICT), k_min=3)
    a = sim.run_policy("always_compile_evict", stream, P, random.Random(1),
                       MECH, sim.CoinBook(5))
    b = sim.run_policy("always_compile_evict_abandon", stream, P,
                       random.Random(1), MECH, sim.CoinBook(5))
    assert a["final_tokens"] == b["final_tokens"]
    assert a["curve"] == b["curve"]
    assert a["n_compiles"] == b["n_compiles"]


def test_abandon_costs_attempts_but_never_extra_ones():
    """At p < 1 the variant is a strict subset of the plain rule's attempts."""
    fams, stream = zipf(n=600, k=10, seed=21)
    P = params({n: fam(q0=0.17, p=0.1) for n in fams}, tau=TAU,
               eviction=dict(EVICT), k_min=3)
    plain = sim.run_policy("always_compile_evict", stream, P,
                           random.Random(1), MECH, sim.CoinBook(5))
    aband = sim.run_policy("always_compile_evict_abandon", stream, P,
                           random.Random(1), MECH, sim.CoinBook(5))
    assert aband["n_compiles"] < plain["n_compiles"]
    assert aband["failed_attempts"] <= plain["failed_attempts"]


def test_abandon_leaves_every_existing_policy_bit_identical():
    """abandon_after is read only by the new alias, so nothing else moves."""
    fams, stream = zipf(n=400, k=10, seed=13)
    fs = {n: fam(q0=0.17, p=0.4) for n in fams}
    base = params(fs, tau=TAU, eviction=dict(EVICT))
    with_cfg = params(fs, tau=TAU, eviction=dict(EVICT), abandon_after=1)
    for p in list(sim.POLICIES) + ["always_compile_evict", "ours_evict",
                                   "oracle_tax"]:
        a = sim.run_policy(p, stream, base, random.Random(2), MECH,
                           sim.CoinBook(7))
        b = sim.run_policy(p, stream, with_cfg, random.Random(2), MECH,
                           sim.CoinBook(7))
        assert a["final_tokens"] == b["final_tokens"], p
        assert a["curve"] == b["curve"], p


def test_abandon_runs_the_base_rule_when_no_eviction_config_is_given():
    fams, stream = zipf(n=300, k=8, seed=4)
    P = params({n: fam(q0=0.17, p=0.5) for n in fams}, tau=TAU)
    r = sim.run_policy("always_compile_evict_abandon", stream, P,
                       random.Random(1), MECH, sim.CoinBook(3))
    assert r["policy"] == "always_compile"
    assert r["n_evictions"] == 0 and r["n_relists"] == 0


# ---------------------------------------------------------------------------
# the constants-variant builder
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def measured() -> dict:
    path = Path(experiments.__file__).resolve().parent \
        / "constants.measured.json"
    return json.loads(path.read_text())


def test_fragility_constants_does_not_touch_the_base_object(measured):
    before = copy.deepcopy(measured)
    sweep.fragility_constants(measured, 0.1, 0.4, 3)
    assert measured == before


def test_fragility_constants_sets_p_and_q0_on_every_layout(measured):
    c = sweep.fragility_constants(measured, 0.3, 0.4, 10)
    layouts = c["cost_sets"]["openapps_glm"]["layouts"]
    assert layouts
    for lay in layouts.values():
        assert lay["p"] == 0.3 and lay["q0"] == 0.4
    # the other measured constants of the cost set are untouched
    for name, lay in layouts.items():
        src = measured["cost_sets"]["openapps_glm"]["layouts"][name]
        for k in ("c", "L", "rho", "C", "d"):
            assert lay[k] == src[k]


def test_fragility_constants_leaves_other_cost_sets_alone(measured):
    c = sweep.fragility_constants(measured, 0.1, 0.4, 3)
    for name in measured["cost_sets"]:
        if name == "openapps_glm":
            continue
        assert c["cost_sets"][name] == measured["cost_sets"][name]


def test_fragility_constants_sets_the_trigger_horizon_cap(measured):
    for life in sweep.LIFETIME_GRID:
        c = sweep.fragility_constants(measured, 1.0, 0.17, life)
        assert c["trigger"]["horizon_cap"] == float(life)
        assert experiments.trigger_mech(c)["horizon_cap"] == float(life)
    # everything else in the trigger block survives
    c = sweep.fragility_constants(measured, 1.0, 0.17, 3)
    for k in ("cooldown", "half_life", "prior_mode", "horizon_mode"):
        assert c["trigger"][k] == measured["trigger"][k]


def test_fragility_constants_restricts_the_grid_to_the_sweep(measured):
    c = sweep.fragility_constants(measured, 1.0, 0.17, 50)
    assert c["e4"]["prices"] == list(sweep.PRICES)
    assert c["e4"]["bpi_heldout"] is True
    assert "bpi2019" not in c["e4"]["streams"]
    assert set(c["e4"]["streams"]) | {"bpi2019"} == set(sweep.STREAMS)
    assert experiments.check_constants(c) == ([], [])


def test_fragility_constants_honours_a_stream_subset(measured):
    c = sweep.fragility_constants(measured, 1.0, 0.17, 50,
                                  streams=("sepsis", "wiki_A"))
    assert c["e4"]["streams"] == ["sepsis", "wiki_A"]
    assert c["e4"]["bpi_heldout"] is False


def test_the_grid_is_the_documented_one():
    grid = sweep._full_grid()
    assert len(grid) == len(sweep.P_GRID) * len(sweep.Q0_GRID) \
        * len(sweep.LIFETIME_GRID) == 24
    assert len(set(grid)) == len(grid)
    assert (1.0, 0.17, 50) in grid          # the E4_KMIN3 reproduction point


def test_build_cells_tags_every_cell_with_its_grid_point(measured):
    cells = sweep.build_cells(measured, reps=2, seed=7,
                              grid=[(1.0, 0.17, 50), (0.1, 0.4, 3)],
                              streams=("sepsis", "wiki_A"))
    assert len(cells) == 2 * 2 * len(sweep.PRICES)
    assert len({c["key"] for c in cells}) == len(cells)
    for cell in cells:
        cfg, stream, price = sweep._split_key(cell["key"])
        assert cfg == cell["_config"]["config_key"]
        assert stream in ("sepsis", "wiki_A")
        assert price in sweep.PRICES
        assert cell["crn"] is True
        assert cell["deployment"]["k_min"] == 3
        assert cell["deployment"]["eviction"]["T"] == "t_star"
        assert cell["policies"] == sweep.POLICIES
        assert cell["offline_tax"] and cell["offline_evict"]


def test_build_cells_carries_the_grid_point_into_the_family_constants(
        measured):
    cells = sweep.build_cells(measured, reps=1, seed=7,
                              grid=[(0.3, 0.4, 10)], streams=("sepsis",))
    layouts = cells[0]["layouts"]
    for prof in layouts.values():
        assert prof["p"] == 0.3 and prof["q0"] == 0.4
        assert prof["h"] == 0.0        # the drift channel stays off
    assert cells[0]["mech"]["horizon_cap"] == 10.0


def test_reference_row_is_the_policy_that_carries_eviction():
    assert sweep.REFERENCE == "ours_evict"
    assert sweep.REFERENCE in sim.POLICIES_EVICT
    assert "oracle_tax" not in sweep.DEPLOYABLE
