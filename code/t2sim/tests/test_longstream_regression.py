"""Long-stream regression: 'ours' must not lose to always-reactive on the
BPI 2019 replay with the manifest tax off (the validate-old account).

The 2026-09-07 E4 regression: on the quarter-million-arrival BPI stream at
sweep prices, ours' raw deployment-age projection lambda_hat * t compiled
late-blooming rare families (81% wasted compiles), and under the measured
manifest tax every admission added a permanent per-arrival cost, flipping
ours to 0.61x reactive at price=1M.  With tau off the service account alone
must still match the old validated engine's behaviour (old pilot on the same
stream: reactive 6.0x ours at 1M), i.e. ours <= 1.05x always_reactive at
every ladder price.

Guards the trigger projection (family deployment-age window, lifetime cap)
and would catch a reintroduction of the raw global-age horizon.
"""
from __future__ import annotations

import random

import pytest

import sim
import streams as streams_mod

# measured openapps_glm layouts (constants.measured.json), native C
LAYOUTS = [
    {"c": 95296, "d": 347, "C": 14549, "p": 1.0, "q0": 0.17, "h": 0.0,
     "binding_space": 12},
    {"c": 68655, "d": 347, "C": 14549, "p": 1.0, "q0": 0.17, "h": 0.0,
     "binding_space": 12},
    {"c": 55793, "d": 347, "C": 14549, "p": 1.0, "q0": 0.17, "h": 0.0,
     "binding_space": 12},
]
DEPLOYED_MECH = {"cooldown": "inflate", "cooldown_len": 3, "half_life": 120.0,
                 "prior_mode": "population", "prior_shape": 1.0,
                 "prior_rate": 5.0, "horizon_mode": "capped_doubling",
                 "horizon_cap": 50.0}
PRICES = (14549, 233000, 1e6, 5e6)


def _bpi_or_skip():
    try:
        return streams_mod.load_stream(
            {"format": "old_real_streams", "key": "bpi2019"})
    except FileNotFoundError:
        pytest.skip("real_streams.json (BPI 2019) not present")


@pytest.mark.parametrize("price", PRICES)
def test_long_stream_ours_beats_reactive_bpi_tau_off(price):
    stream = _bpi_or_skip()
    assert len(stream) > 100_000, "regression needs the full long stream"
    families = {}
    for i, name in enumerate(sorted(set(stream))):
        families[name] = dict(LAYOUTS[i % len(LAYOUTS)])
        families[name]["C"] = price
    params = {"families": families, "horizon": 60,
              "tau": {"m": 0.0, "tau0": 0.0},      # tau OFF (validate-old)
              "epsilon": {"enabled": False}}
    ours = sim.run_policy("ours", stream, params, random.Random(1000),
                          dict(DEPLOYED_MECH))
    reac = sim.run_policy("always_reactive", stream, params,
                          random.Random(1000), dict(DEPLOYED_MECH))
    ratio = reac["final_tokens"] / ours["final_tokens"]
    assert ratio >= 1.0 / 1.05, (
        f"ours loses to always-reactive on BPI with tau off at price="
        f"{price:g}: reactive/ours = {ratio:.3f} (ours "
        f"{ours['final_tokens']:.0f} vs reactive {reac['final_tokens']:.0f})")


def _crafted_stream():
    """A stream whose population looks cold (many singletons), one hot
    family from t=0, and a late-born family ('late', first arrival at
    t=1000, second at t=1002, then never again)."""
    cold = [f"c{i}" for i in range(500)]
    stream = []
    for i in range(1000):
        stream.append("hot" if i % 5 == 0 else cold[i % 500])
    stream += ["late", "hot", "late"]
    families = {"hot": dict(LAYOUTS[0]), "late": dict(LAYOUTS[1])}
    for i, nm in enumerate(cold):
        families[nm] = dict(LAYOUTS[i % len(LAYOUTS)])
    return stream, families


def test_projection_is_family_age_and_lifetime_capped():
    """The deployed projection must not be the raw global-age horizon.

    At 'late's 2nd arrival (t=1002, family age 2, lambda_hat ~ 2/7) the raw
    projection lambda_hat * t = ~286 expected uses; the repaired projection
    is min(lambda_hat * 2, 50) ~ 0.57 uses, which must NOT clear an N*=10
    bar.  The cold population also keeps the k=1 cold-start path quiet.
    """
    stream, families = _crafted_stream()
    for fam in families.values():
        fam["C"] = 10 * ((1 - 0.17) * fam["c"] - fam["d"])   # N* = 10 uses
    params = {"families": families, "horizon": 60,
              "tau": {"m": 0.0, "tau0": 0.0},
              "epsilon": {"enabled": False}}
    r = sim.run_policy("ours", stream, params, random.Random(7),
                       dict(DEPLOYED_MECH))
    assert r["first_compile_rank"].get("late") is None, (
        "late-born 2-arrival family compiled: the projection is the raw "
        "global-age horizon again (lambda_hat * stream_age)")


def test_raw_doubling_mode_is_the_old_engine_arithmetic():
    """The 'doubling' ablation row keeps the old global-age projection, so
    the same late family DOES compile at its 2nd arrival; this anchors the
    E5 ablation and the old-constants validation."""
    stream, families = _crafted_stream()
    for fam in families.values():
        fam["C"] = 10 * ((1 - 0.17) * fam["c"] - fam["d"])   # N* = 10 uses
    params = {"families": families, "horizon": 60,
              "tau": {"m": 0.0, "tau0": 0.0},
              "epsilon": {"enabled": False}}
    raw = sim.mech_with(DEPLOYED_MECH, horizon_mode="doubling")
    r = sim.run_policy("ours", stream, params, random.Random(7), raw)
    assert r["first_compile_rank"].get("late") == 2, (
        "raw doubling mode should compile the late family at arrival 2 "
        "(lambda_hat * stream_age clears any bar)")
