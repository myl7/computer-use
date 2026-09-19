"""Port-equivalence test: t2sim/sim.py vs the old openapps-exp/policy_sim.py.

With the methods-v2 channels off (tau = 0, eps disabled, q0 = 0) and the
legacy drift hazard h kept, the new engine must reproduce the old one
draw-for-draw: same stream, same seed -> identical totals for every policy.
This is the engine-side guarantee behind validate.py's Table-2 check."""
from __future__ import annotations

import copy
import random

import pytest

import policy_sim                                   # the old engine
import sim
import streams as streams_mod

# the constants of the old submitted table (sim_params.json)
OLD_FAMILIES = {
    "wizard": {"c_reactive": 516999, "d_program": 20000,
               "compile_cost": 24012, "gate_rate": 1.0,
               "drift_hazard": 0.02, "binding_space": 12},
    "single_page": {"c_reactive": 413253, "d_program": 20000,
                    "compile_cost": 24012, "gate_rate": 1.0,
                    "drift_hazard": 0.02, "binding_space": 12},
    "sectioned": {"c_reactive": 513743, "d_program": 20000,
                  "compile_cost": 24012, "gate_rate": 1.0,
                  "drift_hazard": 0.02, "binding_space": 12},
}


def old_and_new_params(families):
    old_p = {"families": copy.deepcopy(families), "horizon": 60}
    new_p = {"families": {}, "horizon": 60,
             "tau": {"m": 0.0, "tau0": 0.0},
             "epsilon": {"enabled": False}}
    for name, f in families.items():
        new_p["families"][name] = {
            "c": f["c_reactive"], "d": f["d_program"],
            "C": f["compile_cost"], "p": f["gate_rate"],
            "q0": 0.0, "h": f.get("drift_hazard", 0.0),
            "binding_space": f.get("binding_space", 12)}
    return old_p, new_p


MECH = dict(policy_sim.MECH_DEFAULT)


@pytest.mark.parametrize("pattern", ["poisson", "zipf", "bursty",
                                     "regime_shift"])
def test_gen_stream_ports_draw_for_draw(pattern):
    fams = list(OLD_FAMILIES)
    a = policy_sim.gen_stream(pattern, 200, fams, random.Random(123))
    b = streams_mod.gen_stream(pattern, 200, fams, random.Random(123))
    assert a == b


@pytest.mark.parametrize("policy", policy_sim.POLICIES)
def test_run_policy_matches_old_engine(policy):
    old_p, new_p = old_and_new_params(OLD_FAMILIES)
    fams = list(OLD_FAMILIES)
    for rep in range(3):
        stream = streams_mod.gen_stream("bursty", 300, fams,
                                        random.Random(50 + rep))
        ro = policy_sim.run_policy(policy, stream, old_p,
                                   random.Random(900 + rep), MECH)
        rn = sim.run_policy(policy, stream, new_p,
                            random.Random(900 + rep), MECH)
        assert rn["final_tokens"] == ro["final_tokens"], (policy, rep)
        assert rn["n_compiles"] == ro["n_compiles"]
        assert rn["wasted_compiles"] == ro["wasted_compiles"]
        assert rn["failed_attempts"] == ro["failed_attempts"]
        assert rn["passed_attempts"] == ro["passed_attempts"]
        assert rn["first_compile_rank"] == ro["first_compile_rank"]


def test_run_policy_matches_old_engine_with_gate_failures():
    """gate_rate < 1 exercises the gate coins, the inflate cooldown and
    recompile attempts."""
    fams = copy.deepcopy(OLD_FAMILIES)
    for f in fams.values():
        f["gate_rate"] = 0.6
        f["drift_hazard"] = 0.05
    old_p, new_p = old_and_new_params(fams)
    fam_names = list(fams)
    for pattern in ("poisson", "bursty"):
        for rep in range(2):
            stream = streams_mod.gen_stream(pattern, 300, fam_names,
                                            random.Random(70 + rep))
            ro = policy_sim.run_policy("ours", stream, old_p,
                                       random.Random(900 + rep), MECH)
            rn = sim.run_policy("ours", stream, new_p,
                                random.Random(900 + rep), MECH)
            assert rn["final_tokens"] == ro["final_tokens"]
            assert rn["failed_attempts"] == ro["failed_attempts"] > 0


def test_offline_optimum_matches_old_engine():
    old_p, new_p = old_and_new_params(OLD_FAMILIES)
    fam_names = list(OLD_FAMILIES)
    stream = streams_mod.gen_stream("zipf", 300, fam_names,
                                    random.Random(11))
    assert sim.offline_optimum(stream, new_p) == \
        pytest.approx(policy_sim.offline_optimum(stream, old_p))


def test_hottest_matches_old_engine():
    stream = streams_mod.gen_stream("zipf", 400, list(OLD_FAMILIES),
                                    random.Random(2))
    assert sim.hottest(stream) == policy_sim.hottest(stream)
