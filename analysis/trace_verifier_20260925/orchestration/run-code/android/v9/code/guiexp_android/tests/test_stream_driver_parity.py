"""Parity + behaviour tests for the online stream driver.

The acceptance gate: the SAME arrival stream and the SAME per-episode
outcome tape (a ``t2sim.sim.CoinBook``) fed to

  (a) ``t2sim.sim.run_policy`` -- the reference engine, its decisions read
      through a recording shim on ``sim.FamilyState`` (try_compile /
      serve calls mark each arrival), and
  (b) ``guiexp_android.stream_driver.run_stream`` in mock mode, whose
      executors consume the identical addressed tape,

must produce IDENTICAL decision sequences and IDENTICAL totals, bit for
bit.  Around that: the trivial fixed rules, the trigger reset after a
program break (legacy drift channel), the spend cap blocking a compile,
and the inflate-cooldown ordering.

Run:  ../.venv-android/bin/python -m pytest guiexp_android/tests/test_stream_driver_parity.py -q
"""

from __future__ import annotations

import copy
import random

import pytest

from guiexp_android import stream_driver as sd
from guiexp_android.trigger_online import OnlineTrigger
from t2sim import experiments as t2exp
from t2sim import sim

# ---------------------------------------------------------------------------
# shared world: the measured a1 constants (Algorithm 1's add-one variant) and
# the deployed mech (realized-saving spend cap, full buy formula, k_min = 3)
# ---------------------------------------------------------------------------

CONSTANTS = sd.load_constants()
CS_NAME = CONSTANTS["e4"]["cost_set"]
MECH = sd.deployment_mech(CONSTANTS)
K_MIN = 3


def _sim_params(stream, k_min: int = K_MIN) -> dict:
    """The params dict ``run_policy`` gets, built like the E4 real cells."""
    layouts, tau_cfg, _ = t2exp.cost_set_profiles(CONSTANTS, CS_NAME)
    eps = t2exp.eps_cfg(CONSTANTS)
    horizon = CONSTANTS["trigger"]["horizon_fixed"]
    return t2exp.real_params(layouts, set(stream), None, tau_cfg, eps,
                             horizon, deployment={"k_min": k_min})


def _decisions_from_events(events) -> list[str]:
    """One decision per arrival from the engine's recorded calls."""
    decisions: list[str] = []
    pending = {"compile": False, "program": False, "react": False}
    started = False
    for kind, _name in events:
        if kind == "arrival":
            if started:
                decisions.append(
                    "compile" if pending["compile"]
                    else ("serve" if pending["program"] else "react"))
            started = True
            pending = {k: False for k in pending}
        else:
            pending[kind] = True
    if started:
        decisions.append("compile" if pending["compile"]
                         else ("serve" if pending["program"] else "react"))
    return decisions


@pytest.fixture()
def engine_recorder(monkeypatch):
    """Record the engine's per-arrival compile / serve-path calls."""
    events: list[tuple[str, str]] = []

    class RecordingFamilyState(sim.FamilyState):
        def observe_arrival(self, t):
            events.append(("arrival", self.name))
            super().observe_arrival(t)

        def try_compile(self, price=None, p_gate=None, price_fail=None):
            events.append(("compile", self.name))
            return super().try_compile(price, p_gate, price_fail)

        def _serve_program(self, q_now):
            events.append(("program", self.name))
            return super()._serve_program(q_now)

        def serve(self, q_now, use_program):
            if not (use_program and self.compiled and self.program_alive
                    and self.listed):
                events.append(("react", self.name))
            return super().serve(q_now, use_program)

    monkeypatch.setattr(sim, "FamilyState", RecordingFamilyState)
    return events


def _sepsis(n: int) -> list[str]:
    sd._ensure_real_streams_source()
    return sd.load_arrival_stream("sepsis", n)


def _assert_parity(summary, res, decisions, sim_decisions):
    assert decisions == sim_decisions
    tot = summary["totals"]
    assert tot["tokens_pw"] == res["final_tokens"]
    assert tot["tau_pw"] == res["tau_total"]
    assert tot["compile_pw"] == res["compile_tokens"]
    assert tot["n_compiles"] == res["n_compiles"]
    assert tot["n_admitted"] == res["passed_attempts"]
    assert tot["n_failed_compiles"] == res["failed_attempts"]
    assert tot["final_library"] == res["final_library"]


# ---------------------------------------------------------------------------
# the parity gate
# ---------------------------------------------------------------------------

def test_parity_ours_sepsis_full(engine_recorder):
    """Identical decisions and totals over the WHOLE sepsis stream (1,050
    arrivals, 846 families), seed 7, k_min 3."""
    stream = _sepsis(None)
    seed = 7
    coins = sim.CoinBook(seed * 104729)     # the shared tape: one book, two runs
    res = sim.run_policy("ours", stream, _sim_params(stream),
                         random.Random(seed * 7717), MECH, coins)
    sim_decisions = _decisions_from_events(engine_recorder)
    assert len(sim_decisions) == len(stream)

    summary = sd.run_stream("ours", stream, CONSTANTS, seed=seed, mock=True,
                            tape=coins, k_min=K_MIN, mech=MECH,
                            stream_name="sepsis")
    decisions = [rec["decision"] for rec in summary["_ledger"]]
    assert "compile" in decisions and "serve" in decisions and \
        "react" in decisions          # the stream must exercise all three
    _assert_parity(summary, res, decisions, sim_decisions)


def test_parity_ours_sepsis_300_other_seed(engine_recorder):
    """A second stream/length/seed point for the same gate."""
    stream = _sepsis(300)
    seed = 3
    coins = sim.CoinBook(seed * 104729)
    res = sim.run_policy("ours", stream, _sim_params(stream),
                         random.Random(seed * 7717), MECH, coins)
    sim_decisions = _decisions_from_events(engine_recorder)

    summary = sd.run_stream("ours", stream, CONSTANTS, seed=seed, mock=True,
                            tape=coins, k_min=K_MIN, mech=MECH,
                            stream_name="sepsis")
    _assert_parity(summary, res,
                   [rec["decision"] for rec in summary["_ledger"]],
                   sim_decisions)


def test_parity_always_compile_sepsis(engine_recorder):
    stream = _sepsis(80)
    seed = 5
    coins = sim.CoinBook(seed * 104729)
    res = sim.run_policy("always_compile", stream, _sim_params(stream),
                         random.Random(seed * 7717), MECH, coins)
    sim_decisions = _decisions_from_events(engine_recorder)

    summary = sd.run_stream("always_compile", stream, CONSTANTS, seed=seed,
                            mock=True, tape=coins, k_min=K_MIN, mech=MECH,
                            stream_name="sepsis")
    _assert_parity(summary, res,
                   [rec["decision"] for rec in summary["_ledger"]],
                   sim_decisions)


def test_parity_ours_wiki_tools(engine_recorder):
    """The same gate on the other stream family: wiki_tools (wiki_A)."""
    stream = sd.load_arrival_stream("wiki_tools", 400)
    seed = 2
    coins = sim.CoinBook(seed * 104729)
    res = sim.run_policy("ours", stream, _sim_params(stream),
                         random.Random(seed * 7717), MECH, coins)
    sim_decisions = _decisions_from_events(engine_recorder)

    summary = sd.run_stream("ours", stream, CONSTANTS, seed=seed, mock=True,
                            tape=coins, k_min=K_MIN, mech=MECH,
                            stream_name="wiki_tools")
    _assert_parity(summary, res,
                   [rec["decision"] for rec in summary["_ledger"]],
                   sim_decisions)


def test_parity_ours_environment_fragility(engine_recorder):
    """The E11 channels (environment break hazard, silent failures, a 2.2x
    fallback) exercise the tape's death / silence coins and the manifest
    delisting on a break -- parity must hold with programs really dying."""
    stream = _sepsis(500)
    layouts, tau_cfg, _ = t2exp.cost_set_profiles(CONSTANTS, CS_NAME)
    override = {"h_env": 0.05, "sigma": 0.3, "silent_penalty": 3.0,
                "r_fallback": 2.2}
    families = {name: dict(layouts[layout], **override)
                for name, layout in
                sd.assign_layouts(set(stream),
                                  list(layouts)).items()}
    params = t2exp.real_params(families, set(stream), None, tau_cfg,
                               t2exp.eps_cfg(CONSTANTS),
                               CONSTANTS["trigger"]["horizon_fixed"],
                               deployment={"k_min": K_MIN})
    seed = 21
    coins = sim.CoinBook(seed * 104729)
    res = sim.run_policy("ours", stream, params, random.Random(seed * 7717),
                         MECH, coins)
    sim_decisions = _decisions_from_events(engine_recorder)

    from guiexp_android.trigger_online import OnlineTrigger as Trigger

    trig = Trigger(families=copy.deepcopy(families), mech=MECH, tau=tau_cfg,
                   eps=t2exp.eps_cfg(CONSTANTS),
                   horizon=CONSTANTS["trigger"]["horizon_fixed"],
                   k_min=K_MIN, policy="ours")
    summary = sd.run_stream("ours", stream,
                            {"cost_sets": {}, "trigger": {}}, seed=seed,
                            mock=True, tape=coins, k_min=K_MIN, mech=MECH,
                            trigger=trig)
    _assert_parity(summary, res,
                   [rec["decision"] for rec in summary["_ledger"]],
                   sim_decisions)
    assert trig.summary_state()["program_deaths"] > 0


def test_parity_toolpro_port_sepsis(engine_recorder):
    """The windowed-rate rule, including the rebinding bill: a fresh
    binding pays c even when the family's program served the use."""
    stream = _sepsis(300)
    seed = 9
    coins = sim.CoinBook(seed * 104729)
    res = sim.run_policy("toolpro_port", stream, _sim_params(stream),
                         random.Random(seed * 7717), MECH, coins)
    sim_decisions = _decisions_from_events(engine_recorder)

    summary = sd.run_stream("toolpro_port", stream, CONSTANTS, seed=seed,
                            mock=True, tape=coins, k_min=K_MIN, mech=MECH,
                            stream_name="sepsis")
    _assert_parity(summary, res,
                   [rec["decision"] for rec in summary["_ledger"]],
                   sim_decisions)


# ---------------------------------------------------------------------------
# the fixed rules
# ---------------------------------------------------------------------------

def test_always_reactive_trivial():
    stream = _sepsis(40)
    coins = sim.CoinBook(1)
    summary = sd.run_stream("always_reactive", stream, CONSTANTS, mock=True,
                            tape=coins, k_min=K_MIN, mech=MECH)
    ledger = summary["_ledger"]
    assert [rec["decision"] for rec in ledger] == ["react"] * len(stream)
    assert all(rec["executor"] == "agent" for rec in ledger)
    tot = summary["totals"]
    assert tot["n_compiles"] == 0 and tot["final_library"] == 0
    assert tot["compile_pw"] == 0.0
    assert tot["tokens_pw"] == pytest.approx(tot["tau_pw"]
                                             + tot["service_pw"])


def test_always_compile_compiles_when_unverifiable_and_serves_when_live():
    """always_compile fires whenever no live program serves the family; the
    k_min = 3 eligibility gate forces the three-react warm-up, and a passed
    gate turns every later arrival of the family into a serve."""
    profiles = {"A": {"c": 1000.0, "d": 10.0, "C": 100.0, "p": 1.0,
                      "q0": 0.0, "binding_space": 12}}
    mech = sim.mech_with(None)
    trig = OnlineTrigger(families=copy.deepcopy(profiles), mech=mech,
                         tau={"m": 0.0, "tau0": 0.0}, eps={"enabled": False},
                         horizon=60.0, k_min=K_MIN, policy="always_compile")
    decisions = []
    for _ in range(6):
        trig.observe_arrival("A", binding_coin=0.0)
        decision = trig.decide("A")
        if decision == "compile":
            # gate coin 0.5 < p = 1: every attempt passes
            trig.observe_compile("A", verified=True, cost_pw=100.0)
        # the deciding arrival is served too (by the new program on a
        # compile arrival, by the live program on a serve)
        served = decision in ("compile", "serve")
        trig.observe_episode("A", cost_pw=10.0 if served else 1000.0,
                             success=True, served_by_program=served)
        decisions.append(decision)
    assert decisions == ["react", "react", "compile", "serve", "serve",
                         "serve"]
    # the one gate attempt is the one the test reported; decide() itself
    # executes nothing
    assert trig.summary_state()["attempts"] == 1


# ---------------------------------------------------------------------------
# trigger reset after a program break (legacy drift channel h > 0)
# ---------------------------------------------------------------------------

def test_trigger_resets_after_program_break(engine_recorder):
    """With h = 1 every program use kills the artifact: the live program
    flag drops, the epoch spend restarts, and the trigger re-fires."""
    profiles = {"A": {"c": 1000.0, "d": 10.0, "C": 100.0, "p": 1.0,
                      "q0": 0.0, "h": 1.0, "binding_space": 12}}
    mech = sim.mech_with(None)
    params = {"families": copy.deepcopy(profiles), "horizon": 60,
              "tau": {"m": 0.0, "tau0": 0.0}, "epsilon": {"enabled": False},
              "k_min": K_MIN}
    stream = ["A"] * 12
    coins = sim.CoinBook(11)
    res = sim.run_policy("ours", stream, params, random.Random(0), mech,
                         coins)
    sim_decisions = _decisions_from_events(engine_recorder)

    trig = OnlineTrigger(families=copy.deepcopy(profiles), mech=mech,
                         tau={"m": 0.0, "tau0": 0.0},
                         eps={"enabled": False}, horizon=60.0, k_min=K_MIN,
                         policy="ours")
    summary = sd.run_stream("ours", stream,
                            {"cost_sets": {}, "trigger": {}},
                            mock=True, tape=coins, k_min=K_MIN, mech=mech,
                            trigger=trig)
    decisions = [rec["decision"] for rec in summary["_ledger"]]
    _assert_parity(summary, res, decisions, sim_decisions)

    ledger = summary["_ledger"]
    broke = [rec for rec in ledger if rec["program_broke"]]
    assert broke, "h = 1 must break every program use"
    st = trig.state("A")
    assert st.n_deaths == len(broke)
    assert not trig.has_live_program("A")     # the last use broke it again
    assert st.epoch_spend == 0.0              # the epoch accumulator reset
    # every broken arrival (but the stream's last) is followed by a fresh
    # compile, not a serve: the trigger saw the break and restarted.
    for i, rec in enumerate(ledger):
        if rec["program_broke"] and i + 1 < len(decisions):
            assert decisions[i + 1] == "compile"


# ---------------------------------------------------------------------------
# spend cap blocks a compile; cooldown inflation ordering
# ---------------------------------------------------------------------------

def _cap_world(C: float, extra_mech: dict) -> tuple[OnlineTrigger, dict]:
    profiles = {"A": {"c": 1000.0, "d": 10.0, "C": C, "p": 1.0, "q0": 0.0,
                      "binding_space": 12}}
    # add_one: Algorithm 1's verification estimate, so the first attempt is
    # priced at C / 0.5 and a miss moves it to C / (1/3)
    mech = sim.mech_with(None, gate_prior="add_one", **extra_mech)
    trig = OnlineTrigger(families=copy.deepcopy(profiles), mech=mech,
                         tau={"m": 0.0, "tau0": 0.0}, eps={"enabled": False},
                         horizon=60.0, k_min=1, policy="ours")
    return trig, mech


def test_spend_cap_blocks_compile():
    """One failed attempt at mult 0.02 pushes failed spend past the cap's
    realized-saving allowance, and the cap (not the price test) blocks."""
    trig, mech = _cap_world(100.0, {"spend_cap": "realized",
                                    "spend_cap_mult": 0.02})
    trig.observe_arrival("A", binding_coin=0.0)
    assert trig.decide("A") == "compile"
    snap1 = dict(trig.last_snapshot)
    assert snap1["cap_blocked"] is False
    # the gate misses: p_hat drops to 1/3, failed spend = 100
    trig.observe_compile("A", verified=False, cost_pw=100.0)
    act = trig.decide("A")
    snap2 = trig.last_snapshot
    # the price test still passes (1980 > 2 * 300): the CAP is the blocker
    assert snap2["E_use"] * snap2["s"] > snap2["price"]
    assert act == "react" and snap2["cap_blocked"] is True

    # control: cap off, the same tape world re-attempts
    trig0, _ = _cap_world(100.0, {"spend_cap": False})
    trig0.observe_arrival("A", binding_coin=0.0)
    assert trig0.decide("A") == "compile"
    trig0.observe_compile("A", verified=False, cost_pw=100.0)
    assert trig0.decide("A") == "compile"


def test_cooldown_inflation_ordering():
    """After one failed gate the inflate cooldown doubles the threshold
    price; a family that cleared it at 1x no longer clears it at 2x, with
    the cap off so inflation is the only blocker."""
    trig, _mech = _cap_world(600.0, {"spend_cap": False})
    trig.observe_arrival("A", binding_coin=0.0)
    assert trig.decide("A") == "compile"
    price1 = trig.last_snapshot["price"]
    assert trig.last_snapshot["multiplier"] == 1.0
    trig.observe_compile("A", verified=False, cost_pw=600.0)
    act = trig.decide("A")
    snap = trig.last_snapshot
    assert act == "react"
    assert snap["multiplier"] == 2.0
    assert snap["price"] > price1             # the threshold moved up...
    assert snap["E_use"] * snap["s"] > snap["price"] / 2.0  # ...past 1x pay
    assert snap["cap_blocked"] is False
