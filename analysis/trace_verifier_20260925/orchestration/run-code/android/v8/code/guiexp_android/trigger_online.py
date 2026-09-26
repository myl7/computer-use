"""Online stream trigger: Algorithm 1 (``alg:policy``) as a pure state machine.

A faithful port of the ``ours`` branch of ``t2sim.sim.run_policy`` (plus the
``FamilyState`` / ``PopulationStats`` bookkeeping it reads) to the ONLINE
decision logic an end-to-end mixed-family deployment needs: one object that
observes arrivals, episodes and compile outcomes one at a time and answers
``decide(family) -> "react" | "compile" | "serve"`` per arrival.

What the port carries (paper/body.tex, Compilation Decision section):

  * lambda_hat posterior  -- population cold start (``prior_mode
    == "population"``: the deciding first arrival projects
    ``1 + pop.future(t, horizon)`` uses) and the Gamma(prior_shape,
    prior_rate) posterior from arrival two, with the half-life evidence
    decay (``half_life``, 120 arrivals in the constants);
  * the family-deployment-age doubling horizon (``horizon_mode ==
    "capped_doubling"``: ``H = lambda_hat * age``) capped at the program
    lifetime (legacy drift hazard, then environment hazard, then
    ``horizon_cap``);
  * the inflate cooldown (``2 ** consec_failures`` price multiplier after a
    failed gate attempt);
  * the full buy formula ``B_hat = C + (1/p_hat - 1) * C_fail`` with the
    failed-attempt price inside the estimate (``sim.expected_buy``), the
    manifest externality ``m * t`` on the threshold, and the add-one
    gate-prior verification estimate ``p_hat = (g + 1) / (a + 2)``;
  * the attempt-spend cap that credits realized saving
    (``spend_cap == "realized"``: failed spend against realized + projected
    saving), re-evaluated at every arrival.

Everything decision-relevant is bit-faithful to the engine: the arithmetic
reuses ``t2sim.sim``'s own helpers (``expected_buy``, ``compile_prices_for_k``,
``use_saving_fam``, ``q_of``, ``tau_of``, ``PopulationStats``) in the same
expression order, so on identical inputs this class and ``sim.run_policy``
reach the same decision at every arrival.  The parity test
(``tests/test_stream_driver_parity.py``) holds that over a real stream.

Pure Python, no I/O, no RNG: outcomes arrive through ``observe_*`` calls and
any randomness lives in the caller's tape (the driver's mock executor reads
a ``t2sim.sim.CoinBook``-shaped tape so the parity pairing is exact).
"""

from __future__ import annotations

import math

# t2sim sits beside guiexp_android under code/; both are importable when the
# computer-use directory is on sys.path (tests/conftest.py puts it there and
# stream_driver bootstraps it for the CLI).
from t2sim import sim


class _FamilyState:
    """Per-family online state, the decision-relevant slice of sim.FamilyState.

    Attribute names ``k`` and ``first_seen`` match the engine on purpose:
    ``sim.PopulationStats.observe`` reads exactly those.
    """

    __slots__ = (
        "name", "profile", "c", "d", "C", "C_fail", "p_gate", "pi", "q0",
        "h", "h_env", "r_fallback", "sigma", "silent_penalty",
        "binding_space", "k", "first_seen", "last_t", "stat_n", "stat_gap",
        "inter_arrivals", "bindings_seen", "reactive_served",
        "episodes_since_program", "compiled", "program_alive", "listed",
        "use_index", "failed_attempts", "passed_attempts", "consec_failures",
        "cooldown", "blacklisted", "failed_spend", "epoch_spend",
        "realized_saving", "n_deaths", "excess_since_compile",
        "n_cap_blocks_fresh", "n_cap_blocks_admitted",
        "n_cap_blocks_after_death",
    )

    def __init__(self, name: str, profile: dict):
        self.name = name
        self.profile = profile
        self.c = profile["c"]
        self.d = profile["d"]
        self.C = profile["C"]
        self.C_fail = float(profile.get("C_fail", self.C))
        self.p_gate = profile.get("p", 1.0)
        self.pi = float(profile.get("pi", sim.PI_DEFAULT))
        self.q0 = profile.get("q0", 0.0)
        self.h = profile.get("h", 0.0)
        self.h_env = profile.get("h_env", 0.0)
        self.r_fallback = profile.get("r_fallback", 1.0)
        self.sigma = profile.get("sigma", 0.0)
        self.silent_penalty = profile.get("silent_penalty", 1.0)
        self.binding_space = profile.get("binding_space", 12)
        self.k = 0
        self.first_seen = None
        self.last_t = None
        self.stat_n = 0.0
        self.stat_gap = 0.0
        self.inter_arrivals: list[float] = []
        self.bindings_seen: set[str] = set()
        self.reactive_served = 0
        self.episodes_since_program = 0
        self.compiled = False
        self.program_alive = False
        self.listed = True
        self.use_index = 0
        self.failed_attempts = 0
        self.passed_attempts = 0
        self.consec_failures = 0
        self.cooldown = 0
        self.blacklisted = False
        self.failed_spend = 0.0
        self.epoch_spend = 0.0
        self.realized_saving = 0.0
        self.n_deaths = 0
        self.excess_since_compile = 0.0
        self.n_cap_blocks_fresh = 0
        self.n_cap_blocks_admitted = 0
        self.n_cap_blocks_after_death = 0

    # -- arrival posterior (decay + prior), verbatim sim.FamilyState -------

    def observe_arrival(self, t: int, half_life: float | None) -> None:
        if self.first_seen is None:
            self.first_seen = t
        if self.last_t is not None:
            gap = float(t - self.last_t)
            self.inter_arrivals.append(gap)
            if half_life:
                w = 0.5 ** (gap / half_life)
                self.stat_n *= w
                self.stat_gap *= w
            self.stat_n += 1.0
            self.stat_gap += gap
        self.last_t = t

    def lam_hat(self, prior_shape: float, prior_rate: float) -> float:
        return (prior_shape + self.stat_n) / (prior_rate + self.stat_gap)

    # -- gate-rate estimate, verbatim sim.FamilyState.p_hat -----------------

    def p_hat(self, mode: str, p_true: float, pop_mu: float | None,
              strength: float) -> float:
        if mode == "true":
            return p_true
        a0, b0 = sim.gate_prior_ab(mode, self.pi, strength, pop_mu)
        n = self.failed_attempts + self.passed_attempts
        return (self.passed_attempts + a0) / (n + a0 + b0)

    # -- inflate cooldown, verbatim -----------------------------------------

    def price_multiplier(self, cooldown_mode: str) -> float:
        if cooldown_mode == "inflate":
            return 2.0 ** self.consec_failures
        return 1.0

    def compile_blocked(self, cooldown_mode: str) -> bool:
        if cooldown_mode == "fixed":
            return self.cooldown > 0
        if cooldown_mode == "blacklist":
            return self.blacklisted
        return False  # "inflate" prices instead of blocking


class OnlineTrigger:
    """Algorithm 1's online decision state machine over one arrival stream.

    Fed by the driver: ``observe_arrival`` per stream arrival, then
    ``decide``; the execution layer reports back through ``observe_compile``
    and ``observe_episode``.  Mirrors ``sim.run_policy``'s per-arrival order
    (observe -> estimates -> decision -> compile bookkeeping -> serve
    bookkeeping) so the two agree arrival for arrival.
    """

    def __init__(self, families: dict[str, dict], mech: dict | None = None,
                 tau: dict | None = None, eps: dict | None = None,
                 horizon: float = 60.0, k_min: int = 1,
                 policy: str = "ours", router: dict | None = None):
        if policy not in ("ours", "always_reactive", "always_compile",
                          "toolpro_port"):
            raise ValueError(f"unknown policy {policy!r}")
        self.policy = policy
        self.families = dict(families)      # stream family -> engine profile
        self.mech = dict(mech or sim.MECH_DEFAULT)
        self.tau = tau or {}
        self.eps = eps or {}
        self.router = router
        self.horizon = horizon
        self.k_min = int(k_min or 1)
        self.m_tax = float(self.tau.get("m", 0.0))
        self._states: dict[str, _FamilyState] = {}
        self.pop = sim.PopulationStats()
        self._t = -1                        # global arrival index
        self._n_lib = 0                     # entries currently in the manifest
        self._last_binding = ""
        self._last_rebinding = False
        self._last_s = 0.0                  # this arrival's per-use saving
        self._last_q = 0.0                  # this arrival's per-use failure
        self.last_snapshot: dict | None = None

    # -- construction from a t2sim constants dict ---------------------------

    @classmethod
    def from_constants(cls, constants: dict, layout_of: dict, cost_set: str,
                       k_min: int = 3, mech: dict | None = None,
                       policy: str = "ours") -> "OnlineTrigger":
        """Build from a constants dict shaped like constants.measured.v3.json.

        ``layout_of`` maps each stream family to a layout name of the cost
        set (the driver passes the engine's round-robin over sorted family
        names, ``t2sim.experiments.real_params``' rule).  ``mech`` overrides
        the ``trigger`` block wholesale; absent, the block is read exactly
        like ``experiments.trigger_mech`` reads it, so the parity test can
        hand the sim side the same dict.
        """
        t2exp = _t2sim_experiments()
        cs_name = cost_set or constants.get("e4", {}).get(
            "cost_set") or next(iter(constants["cost_sets"]))
        profiles, tau_cfg, _ = t2exp.cost_set_profiles(constants, cs_name)
        fams = {name: dict(profiles[layout])
                for name, layout in layout_of.items()}
        return cls(families=fams,
                   mech=mech or t2exp.trigger_mech(constants),
                   tau=tau_cfg, eps=t2exp.eps_cfg(constants),
                   horizon=constants["trigger"]["horizon_fixed"],
                   k_min=k_min, policy=policy)

    # -- family registry -----------------------------------------------------

    def state(self, family: str) -> _FamilyState:
        st = self._states.get(family)
        if st is None:
            if family not in self.families:
                raise KeyError(f"no profile assigned for family {family!r}")
            st = self._states[family] = _FamilyState(family,
                                                     self.families[family])
        return st

    def has_state(self, family: str) -> bool:
        return family in self._states

    def library_size(self) -> int:
        return self._n_lib

    def peek_k(self, family: str) -> int:
        """The 0-based per-family arrival index this arrival will get."""
        return self.state(family).k

    def peek_use_index(self, family: str) -> int:
        return self.state(family).use_index

    def peek_attempt_index(self, family: str) -> int:
        st = self.state(family)
        return st.failed_attempts + st.passed_attempts

    def has_live_program(self, family: str) -> bool:
        st = self.state(family)
        return st.compiled and st.program_alive and st.listed

    def q_now(self, family: str) -> float:
        st = self.state(family)
        return sim.q_of(self._n_lib, st.q0, self.eps, self.router, self.tau)

    def last_q_now(self) -> float:
        """The q this arrival was PRICED with (before any compile of the
        same arrival moved the library) -- what the engine serves under."""
        return self._last_q

    def tau_now(self) -> float:
        return sim.tau_of(self._n_lib, self.tau, self.router)

    # -- (1)-(2) arrival: posterior, population, cooldown --------------------

    def observe_arrival(self, family: str, binding_coin: float | None = None,
                        binding: str | None = None) -> str:
        """Advance the stream by one arrival of ``family``.

        ``binding_coin`` is the RAW uniform in [0, 1); the derived binding id
        ``b<idx>`` then follows the engine's draw
        (``min(space-1, int(coin * space))``).  ``binding`` overrides the id
        outright -- the driver passes ``f"b{tape.binding(...)}"`` because the
        CoinBook returns the index already transformed.  Returns the id.
        """
        self._t += 1
        st = self.state(family)
        st.k += 1
        if binding is None and binding_coin is not None:
            idx = min(st.binding_space - 1,
                      int(binding_coin * st.binding_space))
            binding = f"b{idx}"
        binding = binding if binding is not None else f"b{st.k - 1}"
        rebinding = binding not in st.bindings_seen
        st.bindings_seen.add(binding)
        st.observe_arrival(self._t, self.mech.get("half_life"))
        self.pop.observe(st)             # reads st.k / st.first_seen
        if st.cooldown > 0:
            st.cooldown -= 1
        self._last_binding = binding
        self._last_rebinding = rebinding
        return binding

    def last_binding(self) -> str:
        return self._last_binding

    def last_rebinding(self) -> bool:
        return self._last_rebinding

    # -- (3) the decision ----------------------------------------------------

    def decide(self, family: str) -> str:
        """``"react"``, ``"compile"`` or ``"serve"`` for the current arrival.

        ``"compile"`` means the trigger fired for this arrival; execution
        (compile attempt) and this arrival's own service are reported back
        through ``observe_compile`` / ``observe_episode``, in that order --
        the engine decides before serving so the bought program can serve
        the deciding arrival itself.
        """
        st = self.state(family)
        q_now = self.q_now(family)
        s = sim.use_saving_fam(st.profile, q_now)
        self._last_s = s
        self._last_q = q_now
        had_program = self.has_live_program(family)
        act = False
        snapshot = {"t": self._t, "family": family, "k": st.k, "s": s,
                    "q_now": q_now, "had_program": had_program,
                    "library": self._n_lib}
        if self.policy == "ours":
            act, snap = self._decide_ours(st, s, had_program)
            snapshot.update(snap)
        elif self.policy == "always_reactive":
            act = False
        elif self.policy == "always_compile":
            act = (not had_program) and st.cooldown == 0
        elif self.policy == "toolpro_port":
            act = self._decide_toolpro(st, s, had_program)
        if act and self.k_min > 1:
            demos = st.reactive_served + (0 if had_program else 1)
            if demos < self.k_min:
                act = False
                snapshot["k_min_blocked"] = True
        snapshot["decision"] = ("compile" if act
                                else ("serve" if had_program else "react"))
        self.last_snapshot = snapshot
        return snapshot["decision"]

    def _decide_ours(self, st: _FamilyState, s: float, had_program: bool):
        """The ``ours`` branch, in ``sim.run_policy``'s expression order."""
        mech = self.mech
        horizon = self.horizon
        lam_hat = st.lam_hat(mech["prior_shape"], mech["prior_rate"])
        h_mode = mech.get("horizon_mode", "fixed")
        E_future = lam_hat * horizon
        if st.k >= 2 and h_mode != "fixed":
            base = lam_hat * (self._t if h_mode in ("doubling",
                                                    "capped_doubling")
                              else horizon)
            if h_mode == "doubling":
                E_future = base
            else:
                if h_mode == "capped_doubling":
                    base = lam_hat * (self._t - (st.first_seen or 0))
                if st.h > 0:
                    cap = 1.0 / st.h
                elif st.h_env > 0:
                    cap = 1.0 / st.h_env
                else:
                    cap = mech.get("horizon_cap")
                E_future = min(base, cap) if cap else lam_hat * horizon
        demos = st.reactive_served + (0 if had_program else 1)
        C_k, C_fail_k, p_k = sim.compile_prices_for_k(st.profile, demos)
        p_est = st.p_hat(mech.get("gate_prior", "true"), p_k,
                         self.pop.admit_mean(),
                         float(mech.get("gate_prior_strength",
                                        sim.PI_PRIOR_STRENGTH)))
        c_eff = sim.expected_buy(C_k, C_fail_k, p_est)
        price = c_eff * st.price_multiplier(mech["cooldown"])
        rest = float(self._t)
        price += self.m_tax * rest
        E_use = E_future
        if mech.get("prior_mode") == "population" and st.k == 1:
            E_use = 1.0 + self.pop.future(self._t, horizon)
        act = ((not st.compile_blocked(mech["cooldown"]))
               and not had_program and E_use * s > price)
        cap_blocked = False
        if act and self._spend_cap_blocks(st, E_use * s):
            act = False
            cap_blocked = True
        return act, {"lam_hat": lam_hat, "E_future": E_future, "E_use": E_use,
                     "demos": demos, "C_k": C_k, "C_fail_k": C_fail_k,
                     "p_k": p_k, "p_hat": p_est, "c_eff": c_eff,
                     "price": price, "multiplier":
                     st.price_multiplier(mech["cooldown"]),
                     "cap_blocked": cap_blocked}

    def _decide_toolpro(self, st: _FamilyState, s: float,
                        had_program: bool) -> bool:
        """The ToolPro port's windowed-rate rule, verbatim from the engine.

        Greedy per-arrival on the last five inter-arrivals; artifacts do not
        generalize across bindings, so a NEW binding re-pays the compile.
        """
        window = st.inter_arrivals[-5:]
        k_hat = (len(window) / (sum(window) / len(window))) if window else 0.0
        price = st.C / st.p_gate if st.p_gate > 0 else math.inf
        rebinding = self._last_rebinding
        return (k_hat * s > price) and (rebinding or not st.compiled)

    def _spend_cap_blocks(self, st: _FamilyState, projected: float) -> bool:
        """The attempt-spend cap, verbatim ``FamilyState.spend_cap_blocks``.

        Algorithm 1's allowance credits realized saving, so the deployment
        runs ``spend_cap = "realized"``; ``"horizon"`` / ``"epoch"`` stay
        available for ablations exactly as in the engine.
        """
        mech = self.mech
        mode = sim.spend_cap_mode(mech)
        if mode is None:
            return False
        mult = float(mech.get("spend_cap_mult", 1.0))
        if mode == "epoch":
            spent, budget = st.epoch_spend, mult * projected
        elif mode == "realized":
            spent = st.failed_spend
            budget = mult * (max(0.0, st.realized_saving) + projected)
        else:
            spent, budget = st.failed_spend, mult * projected
        if spent < budget:
            return False
        if st.n_deaths > 0:
            st.n_cap_blocks_after_death += 1
        elif st.passed_attempts > 0:
            st.n_cap_blocks_admitted += 1
        else:
            st.n_cap_blocks_fresh += 1
        return True

    # -- (4) compile bookkeeping ---------------------------------------------

    def compile_price_info(self, family: str) -> tuple[float, float, float]:
        """(C, C_fail, p) of the attempt this arrival would make."""
        st = self.state(family)
        had_program = self.has_live_program(family)
        demos = st.reactive_served + (0 if had_program else 1)
        return sim.compile_prices_for_k(st.profile, demos)

    def observe_compile(self, family: str, verified: bool,
                        cost_pw: float) -> None:
        """Report one executed compile attempt and its price-weighted bill.

        Mirrors ``FamilyState.try_compile``: a pass makes the program live
        (and adds the manifest entry), a miss burns the failed-attempt price,
        counts into the add-one estimate and arms the inflate cooldown.
        """
        st = self.state(family)
        self.pop.observe_attempt(verified)
        if verified:
            st.compiled = True
            st.program_alive = True
            st.listed = True
            st.excess_since_compile = 0.0
            st.passed_attempts += 1
            st.consec_failures = 0
            self._n_lib += 1
            st.episodes_since_program = 0
        else:
            st.failed_attempts += 1
            st.failed_spend += cost_pw
            st.epoch_spend += cost_pw
            st.consec_failures += 1
            if self.mech["cooldown"] == "fixed":
                st.cooldown = self.mech["cooldown_len"]
            elif self.mech["cooldown"] == "blacklist":
                st.blacklisted = True

    # -- (5) serve bookkeeping -------------------------------------------------

    def observe_episode(self, family: str, cost_pw: float, success: bool,
                        served_by_program: bool,
                        program_broke: bool = False) -> None:
        """Report one executed episode (reactive or program-served).

        ``program_broke`` marks a use that killed the artifact (legacy drift
        coin or a loud environment failure).  The engine's per-arrival
        bookkeeping is mirrored: realized saving, the epoch restart of the
        cap, the demonstration counter, the evidence-earning counter and --
        under an environment hazard -- the manifest delisting.
        """
        st = self.state(family)
        if served_by_program:
            st.use_index += 1        # the use coin at this index is consumed
            st.realized_saving += st.c - cost_pw
            if program_broke:
                st.epoch_spend = 0.0
        else:
            st.reactive_served += 1
        # The engine adds the s computed at the START of this arrival (before
        # any compile moved the library); reuse it rather than recomputing.
        st.excess_since_compile += self._last_s
        if program_broke:
            st.program_alive = False
            st.n_deaths += 1
            if st.h_env > 0.0:
                # E11: the manifest entry goes with the artifact.  The engine
                # clears the residency record but keeps listed=True (only the
                # eviction rule touches that flag); the library shrinks.
                self._n_lib -= 1
            st.episodes_since_program = 0
        else:
            st.episodes_since_program += 1

    def summary_state(self) -> dict:
        """End-of-run counters for the ledger summary."""
        return {
            "library_size": self._n_lib,
            "attempts": self.pop.attempts,
            "admits": self.pop.admits,
            "failed_spend": sum(st.failed_spend
                                for st in self._states.values()),
            "realized_saving": sum(st.realized_saving
                                   for st in self._states.values()),
            "cap_blocks": {k: sum(getattr(st, f"n_cap_blocks_{k}")
                                  for st in self._states.values())
                           for k in ("fresh", "admitted", "after_death")},
            "program_deaths": sum(st.n_deaths
                                  for st in self._states.values()),
        }


def _t2sim_experiments():
    """t2sim.experiments, imported lazily (constants parsing helpers only).

    experiments.py does BARE intra-package imports (import sim / import
    streams), so the t2sim directory itself must be on sys.path -- the
    bootstrap t2sim's own entry modules perform.  stream_driver.py does it
    at import time; this covers constructing an OnlineTrigger standalone.
    """
    import sys
    from pathlib import Path

    t2sim_dir = Path(sim.__file__).resolve().parent
    if str(t2sim_dir) not in sys.path:
        sys.path.insert(0, str(t2sim_dir))
    from t2sim import experiments

    return experiments
