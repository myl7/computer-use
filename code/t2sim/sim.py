"""methods-v2 stream replay engine (T2 simulation layer). Zero API cost.

Implements the stream account of docs/methods-v2.md section 2:

    Cost = sum over arrivals [ tau(n_t)                            (common routing tax)
                               + 1_reactive * c
                               + 1_program * (d + q(n_t) * c) ]   (fallback on failure)
           + sum over compiles C_eff                               (attempt price C,
                                                                    gate coin p, price
                                                                    doubles after a
                                                                    gate failure)

with

    tau(n) = m * n + tau0                  manifest tax, every arrival pays it
    q(n)   = eps(n) + (1 - eps(n)) * q0    per-use failure of a program hit
    eps(n) = eps0 * sigmoid((n - cliff_start) / cliff_slope)
                                           selection-failure cliff (sweepable;
                                           measured 0 for n <= 100)

n_t counts programs ever admitted to the library (a manifest entry is
permanent), so always-compile drives n to the family count while the trigger
keeps it bounded.

Policies: always_reactive, always_compile, on_second, success_count(10),
toolpro_port, ours (Algorithm 1: lambda_hat * H_hat * (c_hat - d_hat) >
C_eff_hat with population cold-start prior, Gamma(1,5) posterior from arrival
two, T-half=120 evidence decay, inflate cooldown, family-deployment-age
doubling horizon capped at the program lifetime), oracle, breakeven
(Theorem 1), offline_optimum (DP lower bound).

Two reference rows come in a tax-blind and a tax-aware version, because the
manifest tax is what makes the stream objective couple families:

  oracle / oracle_taxblind   threshold on the TRUE remaining arrival count,
                             ignoring tau.  At N* < 1 it admits every family
                             that ever recurs -- on wiki_B that is 68,773
                             entries and 27x ours, so it is a reference rule,
                             not an upper bound.
  oracle_tax                 same clairvoyance, priced under the objective it
                             is charged with: admission also pays
                             m * (remaining stream arrivals).
  offline_optimum            per-family DP with the m*n term dropped.
  offline_optimum_tax        exact optimum of the full objective (the tax is
                             linear in n, so it stays separable per family).

The "ours" projection (HORIZON fix, 2026-09-07): the deployed mode is
capped_doubling, whose doubling window is the FAMILY's own deployment age
(t - first_seen) -- a family that has arrived k times in its lifetime is
projected to arrive about k more times -- and whose expected-uses projection
is capped at the program lifetime (old engine Theorem 2: H_f = 1/h, i.e. 50
uses at the validated drift hazard h=0.02; with the legacy h = 0 the trigger
constant horizon_cap supplies it).  The threshold also carries the manifest
externality m * t (methods-v2 section 2 one-line extension: an admitted entry
taxes every future arrival m tokens; t is the deployment-age estimate of the
remaining stream).  Both terms are inert when m = 0, and the raw "doubling"
mode keeps the old engine's global-age arithmetic untouched, so old-constants
validation stays bitwise (validate.py, 27/27).  Without the fix the raw
projection lambda_hat * t (old engine semantics, validated only where
families are born early and N* is small) compiles late-blooming rare families
on 100k+-arrival streams, and under the new manifest tax every such admission
is a permanent per-arrival cost that flips ours above always-reactive.

This is a port of openapps-exp/policy_sim.py; validate.py checks the port
against the old paper's Table 2 with the old constants.  Two legacy knobs are
kept ONLY for that validation and are inert under methods-v2 constants:
per-family drift hazard h (a program breaks and is de-registered) and the
binding space of the ToolPro port.  In old-constants mode tau = 0 and eps = 0
identically, so the accounting reduces to the old one.

Dropped from the old simulator on purpose (cut from the method before the
methods-v2 lock): the macro/two-tier rule and its Chao1/Good-Turing coverage
estimator (old default tier="off"), the empirical-Bayes shrink priors and
loss-aversion trigger variants (audition-only).  None of them can affect the
validation target, which ran with the defaults.

Four deployment options were added on 2026-09-09
(docs/harder-families-and-routers.md part B, docs/t2sim-audit-2026-09-09.md).
Every one of them is OFF by default and inert when off, so E3/E4/E8 and
validate.py reproduce bitwise:

  * common random numbers (CoinBook): the gate coin of the j-th compile
    attempt of a family and the failure coin of its u-th program use are
    drawn once per (stream, rep) and shared by every policy, instead of each
    policy pulling from its own stream of draws and desynchronising at the
    first divergent decision (audit section 1.2);
  * routers: tau_R(n) and eps_R(n) are a parameter of the deployment
    ("listing", "retrieval", "hierarchical") rather than the single full
    listing tau(n) = tau0 + m*n that our own harness measures;
  * eviction: a residency rule that delists a program after T arrivals
    without a use, which bounds the externality of a mistaken admission at
    m*T instead of m*(rest of the stream);
  * k_min: the compiler needs k_min demonstrations, so no family may be
    compiled before it has been served k_min reactive episodes, with an
    optional C(k), p(k) table for protocols whose price depends on how many
    demonstrations they read.
"""
from __future__ import annotations

import heapq
import math
import random

POLICIES = ["always_reactive", "always_compile", "on_second", "success_count",
            "toolpro_port", "ours", "oracle", "breakeven"]

# Clairvoyant reference rules.  "oracle" is the historical, TAX-BLIND
# threshold (it knows the true remaining arrival count of the family and
# ignores tau entirely); "oracle_taxblind" is an exact alias kept so tables
# can say what the row is without breaking validate.py, whose comparison
# keys come from the old paper's policy_sim.json and spell it "oracle".
# "oracle_tax" is the same rule evaluated under the stream objective the
# policies are actually charged with: admitting an entry taxes every later
# arrival m tokens, so the admission price carries m * (remaining stream).
ORACLES = ("oracle", "oracle_taxblind", "oracle_tax")
POLICIES_TAX = ["always_reactive", "always_compile", "on_second",
                "success_count", "toolpro_port", "ours", "oracle_taxblind",
                "oracle_tax", "breakeven"]

# Evicting variants.  A policy name on the left runs the decision rule on the
# right with the library residency rule of params["eviction"] switched on; the
# base names keep the monotone library they have always had, so no existing
# row moves.  See EVICTION_DOC and the "eviction" section of run_policy.
POLICIES_EVICT = {"always_compile_evict": "always_compile",
                  "always_compile_evict_abandon": "always_compile",
                  "ours_evict": "ours",
                  "ours_noinflate": "ours",
                  # Theorem 1's accumulated-excess rule under the same
                  # residency deployment the E11 reference row runs, so the
                  # two differ in the decision rule and nothing else.
                  "breakeven_evict": "breakeven"}

# `ours` without the price multiplier of the "inflate" cooldown.  E9 measured
# that the multiplier, not C_eff = C/p, is what loses the trigger money at
# 0 < p < 1: doubling the threshold after each missed gate prices a family
# out permanently, and a rule that simply keeps trying spends exactly C/p per
# program in expectation and repays it.  This alias prices at the trigger's
# buy price B_hat (expected_buy, mech["buy_formula"]) WITHOUT the multiplier,
# and has no abandon rule, so it never gives up on a family whose gate can
# still pass.  It carries the residency rule, like `ours_evict`, so the two
# differ in exactly the multiplier.
POLICIES_NOINFLATE = ("ours_noinflate",)

# Naive rules retry the gate forever: `always_compile` fires whenever the
# family has no live program, so on a family with p = 0 it re-pays C at every
# single arrival (the audit measured 194 attempts on one android family).
# `ours` does not, because the "inflate" cooldown multiplies its threshold by
# 2^consec_failures and the threshold stops clearing after a few misses.
# ABANDON_AFTER gives the naive rule the same protection in its plainest
# form: after this many CONSECUTIVE failed gate attempts the family is
# dropped and never retried (a pass resets the counter, so a program that
# breaks can still be rebuilt).  Only the policies in POLICIES_ABANDON read
# it, so every existing row is bit-identical.
ABANDON_AFTER = 3
POLICIES_ABANDON = ("always_compile_evict_abandon",)
POLICIES_ROUTER = ["always_reactive", "always_compile",
                   "always_compile_evict", "ours", "ours_evict",
                   "oracle_tax"]

# The deployed trigger configuration (methods-v2 section 3, third block).
MECH_DEFAULT = {
    "cooldown": "inflate",       # (a) price penalty after a gate failure
    "cooldown_len": 3,
    "half_life": 120.0,          # (b) evidence decay, in arrivals (T12)
    "prior_mode": "population",  # (c) cold start from the family population
    "prior_shape": 1.0,          #     Gamma(1, 5) posterior from arrival two
    "prior_rate": 5.0,
    "horizon_mode": "capped_doubling",
                                 # (d) deployment-age horizon: H = lambda_hat*t
                                 #     capped at the program lifetime (below)
    "horizon_cap": 50.0,         # (d') H_hat = min(1/h, horizon_cap): expected
                                 #     uses a program can serve.  Old engine:
                                 #     1/h at drift hazard h=0.02 -> 50 uses
                                 #     (Theorem 2); methods-v2 keeps the value
                                 #     as a trigger constant (h is legacy).
    "gate_prior": "true",        # (e) how p_hat is estimated; see GATE_PRIOR_DOC.
                                 #     "true" keeps the historical behaviour
                                 #     (the engine hands `ours` the family's
                                 #     real p), so every published number is
                                 #     bit-identical until a caller asks for
                                 #     an estimator.
    "buy_formula": "full",       # (e') how p_hat prices the attempt; see
                                 #      BUY_FORMULA_DOC.  "full" is
                                 #      Algorithm 1's B_hat = C + (1/p_hat -
                                 #      1) * C_fail (W1.5, 2026-09-19); at
                                 #      C_fail = C it evaluates C/p_hat
                                 #      exactly, so every pre-W1.5 row (all
                                 #      run at C_fail = C) stays bit-identical.
                                 #      "narrow" keeps the old C/p_hat form
                                 #      verbatim for reproductions.
    "spend_cap": False,          # (f) attempt-spend cap; see SPEND_CAP_DOC
    "spend_cap_mult": 1.0,
}
# The pre-selection configuration, kept for reference and ablations.
MECH_LEGACY = {
    "cooldown": "fixed",
    "cooldown_len": 3,
    "half_life": None,
    "prior_mode": "gamma",
    "prior_shape": 1.0,
    "prior_rate": 20.0,
    "horizon_mode": "fixed",
}
HORIZON_MODES = ("fixed", "doubling", "capped_doubling", "capped_fixed")
EB_FALLBACK_RATE = 1.0 / 20.0


def mech_with(base: dict | None = None, **kw) -> dict:
    m = dict((base or MECH_DEFAULT))
    m.update(kw)
    return m


# ---------------------------------------------------------------------------
# gate-rate estimator, demonstration success rate pi, attempt-spend cap
# ---------------------------------------------------------------------------

GATE_PRIOR_DOC = """How `ours` prices a compile attempt (methods-v3 section 3).

The deployed trigger does not know p_f.  It estimates it from its own gate
history, and the estimate is what sets the attempt's buy price
B_hat = C(k) + (1/p_hat_f - 1) * C_fail(k) (BUY_FORMULA_DOC, the default
since W1.5; the pre-W1.5 narrow form was C(k) / p_hat_f).  Three modes:

  "true"      p_hat = p(k), the family's real gate rate.  This is what the
              engine did before the estimator existed, and it is the default
              so that E3/E4/E5/E8/E9/E11-as-published and validate.py stay
              bit-identical.  It is also optimistic in the author's favour at
              p = 0: C_eff = inf, so `ours` never attempts a gate it cannot
              pass, which is information a deployment does not have.
  "add_one"   p_hat = (passes + 1) / (attempts + 2), the flat Beta(1,1)
              posterior mean written in Algorithm 1.  On a family with p = 0
              this rises in price one pseudo-observation at a time, so a hot
              family is retried while lambda_hat*H_hat*s > (n + 2) * C.
  "pi"        Beta prior stratified by pi_f, the success rate of the k = 3
              demonstrations, which is known BEFORE the first attempt:
              p_hat = (passes + a0) / (attempts + a0 + b0) with
              a0 = PI_PRIOR_STRENGTH * mu(pi_f), b0 = PI_PRIOR_STRENGTH - a0.
              mu comes from PI_PRIOR_TABLE, the build batch's population
              statistics.  Same arithmetic as "add_one" at mu = 0.5.
  "population" Empirical Bayes on the stream's OWN admission record, the
              same idea the arrival prior already uses (PopulationStats):
              mu is the running admission rate over every family that has
              attempted so far, (admitted + 1) / (attempts + 2), and the
              family's own gate results then move its estimate exactly as
              they do under "add_one".  Before any family has attempted,
              mu = 1/2 and the prior IS add-one at the default strength of
              two pseudo-observations, so the mode costs nothing on a
              stream that has no history yet.  A deployment that has
              watched most of its families get rejected starts the next one
              cheap in expectation and stops paying to find out.

`mech["gate_prior_strength"]` is how many pseudo-observations the prior is
worth; it applies to "pi" and "population" and defaults to
PI_PRIOR_STRENGTH.
"""
GATE_PRIOR_MODES = ("true", "add_one", "pi", "population")

# Population statistics of the t16_build batch (14 family x model cells):
# the demonstration success rate pi predicts the gate rate.  Entries are
# (pi_floor, prior mean), read from the top down; the first row whose floor
# pi clears wins.  Regenerated 2026-09-11 from
# experimental-results/guiexp_android/t16_build/constants_table.json; the
# prior mean of a stratum is the MEAN headline p of its cells.  The earlier
# hand-set table was ((1.0, 0.8), (0.5, 0.5), (0.0, 0.2)) on a 6 / 3 / 5
# split.
PI_PRIOR_TABLE = ((1.0, 4.6 / 7.0),  # pi = 1      : 7 of 14 cells, mean p .657
                  (0.5, 0.5),        # pi in [.5,1): 2 of 14 cells, mean p .5
                  (0.0, 0.2))        # pi < 0.5    : 5 of 14 cells, mean p .2
PI_PRIOR_STRENGTH = 2.0            # pseudo-observations the prior is worth

PI_DEFAULT = 1.0
# The pi a family is given when a stream draws one instead of declaring it.
# Matched to the same batch: with k = 3 demonstrations pi lives on
# {0, 1/3, 2/3, 1}, and one representative value per stratum reproduces the
# 7 / 2 / 5 split exactly.  (value, weight).
PI_POPULATION = ((1.0, 7), (2.0 / 3.0, 2), (1.0 / 3.0, 5))

SPEND_CAP_DOC = """Attempt-spend cap (mechanism "spend_cap").

Ski rental applied to the ATTEMPTS rather than to the uses.  A family stops
being offered to the compiler once the tokens it has already burned on FAILED
attempts reach `spend_cap_mult` times the saving the trigger currently
expects from it, lambda_hat_f * H_hat_f * (c_hat_f - d_hat_f):

    failed_spend_f  >=  spend_cap_mult * E_use * s     ->  do not attempt

It is a COMPARISON re-evaluated at every arrival, not a permanent flag: the
left side only grows when an attempt fails, but the right side moves with the
estimates, so a family whose arrival rate climbs later can clear its own cap
again and re-enter.  That is deliberate -- a family that turns out to be ten
times hotter than it looked is worth another attempt at exactly the point
where the ski-rental comparison says so.

The cap bounds the waste on a p = 0 family at (1 + spend_cap_mult) times its
expected saving: at most one attempt can start below the cap and finish above
it.

THREE VARIANTS.  `mech["spend_cap"]` selects which accumulator and which
budget the comparison uses.  False is off; True is "horizon", so every
published row is unchanged.

  "horizon"   The rule above, verbatim.  failed_spend_f is the tokens the
              family has burned on failed attempts over the WHOLE run and is
              never reset, while the budget lambda_hat*H_hat*s is a forward
              projection that does not grow with the family's history.  On a
              drifting environment the two sides are therefore measuring
              different spans: a family that bought a program, served it
              profitably and then lost it to drift still carries the failed
              attempts of its first epoch against a budget that only covers
              its next one, so the cap can forbid the recompile of a family
              that has already proved it pays.
  "epoch"     Same budget, but the accumulator restarts whenever the
              family's deployed program DIES (an observed break, which is
              exactly the event cor:epoch restarts the analysis at).  Each
              epoch of a family gets its own cap, so the bound above holds
              per epoch instead of per run.  The lifetime failed_spend
              counter is kept untouched for the metrics; the epoch
              accumulator is a second counter, epoch_spend.
  "realized"  Same accumulator as "horizon", wider budget: the saving the
              family's programs have ALREADY delivered is added to the one
              they are projected to deliver,

                  failed_spend_f >= spend_cap_mult *
                                    (realized_saving_f + E_use * s)

              with realized_saving_f the sum over program-served uses of
              (c - cost of that use), so a clean hit contributes c - d, a
              q0 fallback contributes -d, and a death or a silent failure
              contributes (1 - r)*c - d or (1 - Pi)*c - d.  This is the
              ski-rental comparison written on the buys: do not spend more
              on buying than renting has cost so far, plus what buying is
              projected to save next.  The realized term is floored at zero,
              so the budget is never narrower than "horizon" gives.

Both variants reduce to "horizon" on a family that was never admitted: with
no admission there is no death to restart the epoch and no realized saving
to widen the budget.
"""
SPEND_CAP_MODES = ("horizon", "epoch", "realized")


def spend_cap_mode(mech: dict) -> str | None:
    """The cap variant a mechanism block asks for, or None when it is off.

    `True` is the historical boolean and means "horizon", so a constants
    file or a mech_with() call that predates the variants keeps its
    behaviour.
    """
    v = mech.get("spend_cap", False)
    if v is False or v is None:
        return None
    if v is True:
        return "horizon"
    v = str(v)
    if v not in SPEND_CAP_MODES:
        raise ValueError(f"unknown spend_cap {v!r}")
    return v


def pi_prior_mean(pi: float, table=PI_PRIOR_TABLE) -> float:
    """Prior mean of the gate rate for a family whose demonstrations ran pi."""
    for floor, mean in table:
        if pi >= floor:
            return float(mean)
    return float(table[-1][1])


def gate_prior_ab(mode: str, pi: float,
                  strength: float = PI_PRIOR_STRENGTH,
                  pop_mu: float | None = None) -> tuple[float, float]:
    """(a0, b0) pseudo-counts of the Beta prior on the gate rate.

    `pop_mu` is the stream's running admission rate and is read only by the
    "population" mode.  At the default strength of two and the empty-history
    mu of 1/2 that mode returns (1, 1), which is "add_one" exactly.
    """
    if mode == "add_one":
        return 1.0, 1.0
    if mode == "pi":
        mu = pi_prior_mean(pi)
        return strength * mu, strength * (1.0 - mu)
    if mode == "population":
        mu = 0.5 if pop_mu is None else float(pop_mu)
        return strength * mu, strength * (1.0 - mu)
    raise ValueError(mode)


def draw_pi(name: str, seed: int = 0, table=PI_POPULATION) -> float:
    """A per-family pi drawn from the batch-matched population.

    Addressed by the family NAME, like the CoinBook's coins, so the value is
    a property of the environment: every policy and every repetition of a
    stream sees the same family with the same demonstrations.
    """
    total = float(sum(w for _, w in table))
    x = random.Random(f"{seed}|pi|{name}").random() * total
    acc = 0.0
    for val, w in table:
        acc += w
        if x < acc:
            return float(val)
    return float(table[-1][0])


MIXED_REGIME_DOC = """Heterogeneous admission regime (E11 block "H_mixed").

Every other E11 cell gives all of its families the SAME gate rate p, which
is the one thing the t16_build batch says is false: within one model some
families are compilable and the rest never pass, and the two groups carry
different prices.  A cell of this block draws each family's regime instead,
from the model's own measured population:

  with probability `admission_rate` the family is compilable -- p = 1, and
  an attempt costs the model's median price over its ADMITTED cells;
  otherwise it never passes -- p = 0, and each attempt costs that median
  times the model's measured C_fail / C ratio.

The draw is addressed by the family NAME, so it is a property of the
environment: the same family is compilable for every policy, every
repetition and every grid point, and the policies are compared on one world
rather than on one average.  The measured rates are 5/7 for GLM 5.3 Flash
and 2/7 for DeepSeek V4 Flash; c, d and the environment channels are
untouched, so this block differs from the core one in exactly the regime.

This is the block where a per-family rule has something to learn that no
fixed rule can express: `always_compile_evict` buys the hopeless families
and `always_reactive` refuses the profitable ones, while a trigger that
prices each family separately can do both.
"""


def draw_admitted(name: str, rate: float, seed: int = 0) -> bool:
    """Whether a family is compilable at all (MIXED_REGIME_DOC).

    Addressed by the family NAME, like draw_pi and the CoinBook's coins.
    """
    return random.Random(f"{seed}|admit|{name}").random() < float(rate)


# ---------------------------------------------------------------------------
# common random numbers
# ---------------------------------------------------------------------------

class CoinBook:
    """Common random numbers for one (stream, repetition).

    The engine draws exactly three kinds of coin: the gate pass of a compile
    attempt, the per-use failure of a program hit, and the ToolPro port's
    binding index.  Handing every policy a generator seeded the same way is
    NOT common random numbers, because policies consume different numbers of
    draws (a compile attempt draws a gate coin, a program-served arrival
    draws a failure coin, a reactive arrival draws neither), so two policies
    desynchronise at their first divergent decision and rep i of one sees
    different coins from rep i of the other (audit section 1.2).

    Here each coin is addressed instead of dealt: coin (f, j) is the j-th
    draw of a generator that belongs to family f and to nothing else, so a
    policy that reaches the j-th compile attempt of f sees the same gate coin
    whatever it did before, and two policies that reach it both see it.  The
    lists grow on demand and are never re-drawn, which makes the value at an
    index independent of the order in which policies ask for it.

    One book is built per (stream, rep) and shared by every policy of that
    rep; with the book in place the policy's own generator is not consulted
    at all, so the pairing is exact rather than nominal.
    """

    __slots__ = ("seed", "_streams", "_draws")

    def __init__(self, seed: int) -> None:
        self.seed = seed
        self._streams: dict[str, random.Random] = {}
        self._draws: dict[str, list[float]] = {}

    def _at(self, key: str, index: int) -> float:
        vals = self._draws.get(key)
        if vals is None:
            # A str seed goes through sha512 in CPython's Random, so this is
            # stable across processes and runs (unlike hash() of a tuple).
            self._streams[key] = random.Random(f"{self.seed}|{key}")
            vals = self._draws[key] = []
        if index >= len(vals):
            r = self._streams[key]
            vals.extend(r.random() for _ in range(index + 1 - len(vals)))
        return vals[index]

    def gate(self, family: str, attempt: int) -> float:
        """Coin of the `attempt`-th (0-based) compile attempt of `family`."""
        return self._at("g:" + family, attempt)

    def use(self, family: str, use_index: int) -> float:
        """Coin of the `use_index`-th (0-based) program use of `family`."""
        return self._at("u:" + family, use_index)

    def death(self, family: str, use_index: int) -> float:
        """Environment drift coin of the `use_index`-th program use.

        A separate addressed stream, so switching the environment hazard on
        does not move a single value of the q0 stream above and every policy
        of the repetition sees the same program die at the same use.
        """
        return self._at("x:" + family, use_index)

    def silence(self, family: str, use_index: int) -> float:
        """Loud-or-silent coin of the `use_index`-th program use.

        Addressed by the use index rather than by a failure counter: two
        policies can reach the same use with different failure histories,
        and indexing by the use keeps them paired.
        """
        return self._at("s:" + family, use_index)

    def binding(self, family: str, arrival_index: int, space: int) -> int:
        """Binding index of the `arrival_index`-th arrival of `family`."""
        return min(space - 1, int(self._at("b:" + family, arrival_index)
                                  * space))


# ---------------------------------------------------------------------------
# failure structure and the router
# ---------------------------------------------------------------------------

# Router architectures (docs/harder-families-and-routers.md B.1, B.3.1).  The
# deployment declares one; everything downstream (q(n), N*(n), the stream
# account) reads tau_R and eps_R and is otherwise unchanged.  "listing" with
# no extra keys is the measured harness and reproduces the old tau/eps
# exactly, so it is the default and no existing number moves.
ROUTER_KINDS = ("listing", "retrieval", "hierarchical")
ROUTER_LISTING = {"kind": "listing"}
# Retrieval defaults, from the survey: Voyager/RAG-MCP/SkillOps/PreAct all
# sit at top-k with k in 5..20 and report tau flat in n; the eps numbers are
# SkillOps' 200 -> 2,000 sweep (retrieval baselines lose 1.6-7.5 SR points
# over one decade of n; 7.5 points is the Hybrid_blind arm) plus a selection
# floor at k, which has an interior optimum (SkillRouter K=20 beats K=50 by
# 2.0 pp; BoR reaches 90.3% at 7 candidates vs 90.8% at 50).
ROUTER_RETRIEVAL = {"kind": "retrieval", "k": 5, "eps_select": 0.02,
                    "eps_recall_per_decade": 0.075, "n_ref": 200,
                    "eps_max": 0.5}
# Hierarchical: group then item, G = sqrt(n*m/m_g) balanced groups, which is
# ~2*m*sqrt(n) at m_g = m (AnyTool, MCP-Zero, HiSkill, Graph-of-Skills).
ROUTER_HIERARCHICAL = {"kind": "hierarchical", "m_group": None}


def _router_kind(router: dict | None) -> str:
    kind = (router or {}).get("kind", "listing")
    if kind not in ROUTER_KINDS:
        raise ValueError(f"unknown router kind {kind!r}")
    return kind


def _cliff_eps(n: float, cfg: dict | None) -> float:
    """eps0 * sigmoid((n - cliff_start)/cliff_slope), 0 when disabled."""
    if not cfg or not cfg.get("enabled"):
        return 0.0
    z = (n - cfg["cliff_start"]) / max(float(cfg["cliff_slope"]), 1e-9)
    if z >= 0.0:
        sig = 1.0 / (1.0 + math.exp(-z)) if z < 500 else 1.0
    else:
        e = math.exp(z)
        sig = e / (1.0 + e)
    return cfg["eps0"] * sig


def _hier_split(n: int, router: dict | None, m: float) -> tuple[int, int]:
    """(#groups G, items per group) at the token-optimal balanced split.

    G = sqrt(n*m/m_g) minimises m_g*G + m*n/G; at m_g = m it is sqrt(n) and
    the tax is about 2*m*sqrt(n).
    """
    if n <= 1:
        return (n, n)
    m_g = (router or {}).get("m_group")
    ratio = 1.0 if (m_g is None or not m_g or not m) else m / float(m_g)
    g = max(1, min(int(round(math.sqrt(n * ratio))), n))
    return g, -(-n // g)


def epsilon_of(n: int, cfg: dict | None, router: dict | None = None,
               tau_cfg: dict | None = None) -> float:
    """Selection-failure rate eps_R(n) of the deployed router.

    listing       eps(n) = eps0 * sigmoid((n - cliff_start)/cliff_slope), the
                  documented in-prompt discrimination cliff.  Measured 0 for
                  n <= 100 on our library, so the constants file's
                  cliff_start is 100; the public onset is 30-50 tools
                  (Anthropic) or the 200 -> 500 skill drop of
                  Graph-of-Skills, and is reached by moving cliff_start, not
                  by a different curve.
    retrieval     eps(n) = eps_select(k) + recall decay in log10 n.  This
                  channel is NOT the cliff and is NOT governed by the
                  cliff's enabled flag: our eps = 0 is a measurement of our
                  own full-listing router, and says nothing about an index
                  we do not run.  The decay is SkillOps' 200 -> 2,000 sweep
                  (retrieval baselines lose 1.6-7.5 SR points over a decade
                  of n; the default 0.075 per decade is the worst arm) and
                  eps_select is the shortlist's own floor, which has an
                  interior optimum in k (SkillRouter K=20 beats K=50 by
                  2.0 pp).  Set router["eps_enabled"] = False to price the
                  token side alone.
    hierarchical  eps = eps_g(G) + (1 - eps_g(G)) * eps_l(n/G), the cliff
                  curve applied to each branching factor; both are ~sqrt(n),
                  so both sit far left of the onset.

    Returns 0 under listing and hierarchical when the cliff is disabled,
    which is the headline configuration.
    """
    kind = _router_kind(router)
    if kind == "listing":
        return _cliff_eps(n, cfg)
    if kind == "hierarchical":
        m = (tau_cfg or {}).get("m", 0.0)
        g, leaf = _hier_split(n, router, m)
        e_g = _cliff_eps(g, cfg)
        e_l = _cliff_eps(leaf, cfg)
        return e_g + (1.0 - e_g) * e_l
    r = router or {}
    if not r.get("eps_enabled", True):
        return 0.0
    k = int(r.get("k", ROUTER_RETRIEVAL["k"]))
    recall_loss = 0.0
    if n > k:
        n_ref = float(r.get("n_ref", ROUTER_RETRIEVAL["n_ref"]))
        per_dec = float(r.get("eps_recall_per_decade",
                              ROUTER_RETRIEVAL["eps_recall_per_decade"]))
        recall_loss = max(0.0, per_dec * math.log10(max(n, 1)
                                                    / max(n_ref, 1.0)))
    eps = float(r.get("eps_select", ROUTER_RETRIEVAL["eps_select"])) \
        + recall_loss
    return min(eps, float(r.get("eps_max", ROUTER_RETRIEVAL["eps_max"])))


def q_of(n: int, q0: float, eps_cfg: dict | None,
         router: dict | None = None, tau_cfg: dict | None = None) -> float:
    """q(n) = eps(n) + (1 - eps(n)) q0 -- per-use failure of a program hit."""
    eps = epsilon_of(n, eps_cfg, router, tau_cfg)
    return eps + (1.0 - eps) * q0


# ---------------------------------------------------------------------------
# environment-level fragility (E11)
# ---------------------------------------------------------------------------

ENV_DEFAULTS = {"h_env": 0.0, "r_fallback": 1.0, "sigma": 0.0,
                "silent_penalty": 1.0}
ENV_DOC = """Environment-level fragility (docs/t2sim-env-fragility.md).

Three family constants, all inert at their defaults, so every published
number is untouched when they are unset.

  h_env   drift hazard IN THE ENVIRONMENT.  On each use of a live program a
          coin decides whether the perturbation that arrived with this
          episode kills the artifact.  A dead program's use fails loudly:
          the arrival falls back to a reactive episode, the manifest entry
          goes with the artifact, and the family must pay C_eff again before
          it can serve from a program.  Unlike the legacy `h` channel this
          does NOT replace q0: the q0 coin is drawn on every live-program
          use whatever the drift coin did, so both channels act together.
  r_fallback  multiplier on the reactive episode that a program failure
          falls back to.  The perturbation that broke the program also makes
          the reactive agent work harder; the WAREX pilot measured 1.0 to
          2.2 (docs/warex-pilot-2026-09-10.md section 3).
  sigma / silent_penalty  a fraction sigma of program failures are SILENT:
          the use is charged d, counts as served, and additionally costs
          silent_penalty * c, which stands for discovering and repairing the
          wrong record later.  A silent failure does not kill the family.
          sigma = 0 by default: the WAREX pilot and the e7 drift probe both
          measured zero silent failures out of 35 and 20 program failures.
"""


def env_fragility_on(fam: dict) -> bool:
    """True when a family carries any of the E11 environment channels."""
    return (fam.get("h_env", 0.0) > 0.0 or fam.get("sigma", 0.0) > 0.0
            or fam.get("r_fallback", 1.0) != 1.0)


def use_saving(c: float, d: float, q: float, h_env: float = 0.0,
               sigma: float = 0.0, silent_penalty: float = 1.0,
               r_fallback: float = 1.0) -> float:
    """Expected tokens one program-served use saves over a reactive episode.

    With the environment channels off this is the published
    s = (1 - q) c - d, evaluated by the same expression so no number moves.
    With them on, a use fails with probability F = h + (1 - h) q, and a
    failure costs silent_penalty * c when silent and r_fallback * c when
    loud, on top of the d every program-served use pays.
    """
    if h_env <= 0.0 and sigma <= 0.0 and r_fallback == 1.0:
        return (1.0 - q) * c - d
    fail = h_env + (1.0 - h_env) * q
    loss = fail * (sigma * silent_penalty + (1.0 - sigma) * r_fallback) * c
    return c - (d + loss)


def use_saving_fam(fam: dict, q: float) -> float:
    """`use_saving` read off a family constants dict."""
    return use_saving(fam["c"], fam["d"], q, fam.get("h_env", 0.0),
                      fam.get("sigma", 0.0), fam.get("silent_penalty", 1.0),
                      fam.get("r_fallback", 1.0))


def tau_of(n: int, tau_cfg: dict | None, router: dict | None = None) -> float:
    """Per-arrival router tax tau_R(n), in cache-adjusted tokens.

    listing       tau0 + m*n            (measured: m = 91, tau0 = 368)
    retrieval     tau0 + m*min(k, n)    flat in n once the library has k
                  entries.  min(k, n) rather than a bare k because a
                  deployment with fewer than k programs cannot list k of
                  them, and charging an empty library for k entries would
                  hand every compiling policy a discount against
                  always_reactive that the router does not actually give.
    hierarchical  2*tau0 + m_g*G + m*ceil(n/G) at the optimal G, i.e. about
                  2*m*sqrt(n); an empty library needs no group call and pays
                  tau0 like everyone else.
    """
    if not tau_cfg:
        return 0.0
    tau0 = tau_cfg.get("tau0", 0.0)
    m = tau_cfg.get("m", 0.0)
    kind = _router_kind(router)
    if kind == "listing":
        return tau0 + m * n
    if kind == "retrieval":
        k = int((router or {}).get("k", ROUTER_RETRIEVAL["k"]))
        return tau0 + m * min(k, n)
    if n <= 0:
        return tau0
    m_g = (router or {}).get("m_group")
    m_g = m if m_g is None else float(m_g)
    g, leaf = _hier_split(n, router, m)
    return 2.0 * tau0 + m_g * g + m * leaf


def tau_marginal_lower_bound(tau_cfg: dict | None,
                             router: dict | None) -> float:
    """The per-arrival tax an extra manifest entry can add, lower-bounded.

    Used by the offline optimum, which must not charge more externality than
    the router really imposes.  Exactly m under full listing; 0 under the two
    routers whose tax stops growing (the marginal is m only while n < k, and
    m/G under a hierarchy, both of which this understates on purpose so the
    result stays a valid lower bound).
    """
    if not tau_cfg:
        return 0.0
    return tau_cfg.get("m", 0.0) if _router_kind(router) == "listing" else 0.0


# ---------------------------------------------------------------------------
# library residency (eviction)
# ---------------------------------------------------------------------------

EVICTION_DOC = """Library residency rule (docs/harder-families-and-routers.md B.3.2).

RULE.  A program is delisted after T consecutive STREAM arrivals in which it
was not used -- stream arrivals, not arrivals of its own family, because the
manifest is what the arrival pays for and every arrival pays for all of it.
The admitting arrival counts as a use (it is served by the program it just
bought), so a program admitted at index k and never used again is resident
for arrivals k+1 .. k+T and is gone by k+T+1.  A mistaken admission therefore
costs at most m*T tokens of externality, independent of stream length,
against m*(rest of the stream) with a monotone library.

EVIDENCE FLOOR.  No program may be delisted before it has been offered v_min
routing opportunities, i.e. before it has been resident for v_min stream
arrivals (under full listing every arrival offers every entry).  This is
D2Skill's T_prot = 10 steps of post-creation immunity and WhenToForget's
V_min = 10 retrievals before a memory may be deprecated at all; both papers
build it in, and SKILL.nb measures the cost of skipping it (its round-2 dip
coincides with demotions of workflows created before enough evidence had
accumulated).  Default v_min = 10, which is inert at every T we run.

WHAT RE-ADMISSION COSTS.  Zero compile tokens, and no gate.  Delisting
removes the manifest entry, not the artifact: the compiled program still
exists on disk and has already passed its gate, so relisting it is a
manifest edit.  What re-admission does cost is the decision: a delisted
family is invisible to the router, so its arrivals are served reactively
until the policy's own trigger fires again, by exactly the same rule it uses
for a first compile (always_compile relists on the next arrival of the
family; ours relists only when its threshold clears again).  From the
relisting arrival on, the entry taxes every arrival at m again.  This is the
cheap-relist reading, and it is the favourable one for eviction: a design
that had to re-pay C_eff on every re-admission would thrash at small T, and
the T* below is derived under exactly that pessimistic assumption, so both
readings are represented (T* prices the thrash, the engine does not charge
it).

T*.  For a family arriving at rate lambda, paying m per arrival of residency
and C_eff per re-admission at a Poisson gap, the optimum solves
m = C_eff*lambda*exp(-lambda*T), i.e.

    T* = (1/lambda) * ln(C_eff * lambda / m)

which is the eviction analogue of the break-even law and uses the same two
constants.  At C_eff = 14,549, m = 91, lambda = 1/100 arrivals this is about
47 arrivals.  Set eviction["T"] = "t_star" to have each admission size its
own residency from the family's online lambda_hat -- the same estimator the
trigger already reads, so nothing new has to be defended.  When
C_eff*lambda <= m the logarithm is non-positive: residency is not worth
paying for at all and T is clipped to t_min.
"""

EVICTION_DEFAULT = {"T": "t_star", "v_min": 10, "t_min": 1, "t_max": 100000}


def t_star(c_eff: float, lam: float, m: float) -> float:
    """T* = (1/lambda) ln(C_eff lambda / m); 0 when residency does not pay."""
    if lam <= 0.0 or m <= 0.0 or c_eff <= 0.0 or math.isinf(c_eff):
        return math.inf if m <= 0.0 else 0.0
    z = c_eff * lam / m
    if z <= 1.0:
        return 0.0
    return math.log(z) / lam


def eviction_T(cfg: dict | None, c_eff: float, lam: float,
               m: float) -> float:
    """The residency bound T this admission gets, in stream arrivals."""
    if not cfg:
        return math.inf
    spec = cfg.get("T", EVICTION_DEFAULT["T"])
    if spec == "t_star":
        val = t_star(c_eff, lam, m)
    else:
        val = float(spec)
    lo = float(cfg.get("t_min", EVICTION_DEFAULT["t_min"]))
    hi = float(cfg.get("t_max", EVICTION_DEFAULT["t_max"]))
    return min(max(val, lo), hi)


# ---------------------------------------------------------------------------
# minimum demonstrations
# ---------------------------------------------------------------------------

def compile_price_for_k(fam: dict, k: int) -> tuple[float, float]:
    """(C(k), p(k)) for a compiler that read k demonstrations.

    The deployed protocol compiles from the one reactive episode it just
    served, and C and p are measured at that point, so the default table is
    empty and (C, p) are the family's own constants.  AutoRPA-style protocols
    read several demonstrations, which changes both the price and the gate
    rate; when those numbers land they go in the constants file as
    layout["k_table"] = {"3": {"C": ..., "p": ...}} or as C_scale/p_scale
    multipliers, and the largest key <= k applies.
    """
    table = fam.get("k_table")
    C, p = fam["C"], fam.get("p", 1.0)
    if not table:
        return C, p
    keys = sorted(int(x) for x in table)
    hit = None
    for key in keys:
        if key <= k:
            hit = key
    if hit is None:
        return C, p
    row = table[str(hit)] if str(hit) in table else table[hit]
    C = float(row["C"]) if "C" in row else C * float(row.get("C_scale", 1.0))
    p = float(row["p"]) if "p" in row else min(
        1.0, p * float(row.get("p_scale", 1.0)))
    return C, p


C_FAIL_DOC = """What a FAILED compile attempt costs (constant C_fail).

C is the price of an attempt that PASSES the gate.  An attempt that does not
pass is a different bill: the t16_build batch runs translator, builder and
all three repair rounds to exhaustion before it gives up, which measured 1 to
3 million raw tokens, several times the price of a compile that passed on the
first or second repair round.  The engine charged one price for both until
now, so every sweep that varied p was pricing a failure as if it were a
success.

Two ways to say it in a family profile, both optional:

    "C_fail"        absolute tokens for a failed attempt
    "C_fail_mult"   multiplier on C (the price ladder overwrites C, so a
                    multiplier is what survives a price sweep)

Absent both, C_fail = C and nothing moves.  A k_table row may carry "C_fail"
or "C_fail_scale" the same way it carries C / C_scale.
"""


def compile_prices_for_k(fam: dict, k: int) -> tuple[float, float, float]:
    """(C(k), C_fail(k), p(k)) for a compiler that read k demonstrations.

    C and p are exactly what `compile_price_for_k` returns; C_fail is the
    price of an attempt that misses the gate (C_FAIL_DOC), defaulting to C.
    """
    C, p = compile_price_for_k(fam, k)
    if "C_fail" in fam:
        C_fail = float(fam["C_fail"])
    else:
        C_fail = C * float(fam.get("C_fail_mult", 1.0))
    table = fam.get("k_table")
    if table:
        keys = sorted(int(x) for x in table)
        hit = None
        for key in keys:
            if key <= k:
                hit = key
        if hit is not None:
            row = table[str(hit)] if str(hit) in table else table[hit]
            if "C_fail" in row:
                C_fail = float(row["C_fail"])
            elif "C_fail_scale" in row:
                C_fail = C_fail * float(row["C_fail_scale"])
    return C, C_fail, p


BUY_FORMULA_DOC = """How the trigger turns p_hat into an attempt's buy price.

A family whose gate passes with probability p needs 1/p attempts in
expectation: one that passes (price C) and 1/p - 1 that miss (price C_fail
each, C_FAIL_DOC).  Algorithm 1 prices the whole buy:

    "full"      B_hat = C + (1/p_hat - 1) * C_fail

which is revision_sim.expected_buy verbatim and is the DEFAULT since W1.5
(2026-09-19).  The engine had priced the attempt at

    "narrow"    C_eff = C / p_hat

from its first port until W1.5, which prices a miss at C -- right only where
C_fail = C.  The two forms agree exactly at C_fail = C (the algebra reduces)
and at p_hat = 0 (both are inf, so `ours` never attempts a gate it estimates
as impossible -- the pre-W1.5 behaviour, kept); they differ only where a
measured C_fail_mult != 1 meets 0 < p_hat < 1, where narrow UNDER-prices the
buy whenever C_fail > C.  At C_fail = C "full" is evaluated as C / p_hat, so
every row run without a measured C_fail (all of validate.py, E3/E4/E5/E8
as published) is bit-identical under either setting; a pre-W1.5 row with a
measured C_fail_mult reproduces exactly under mech["buy_formula"] =
"narrow".  The gate-rate ESTIMATOR (GATE_PRIOR_DOC) and the inflate
multiplier are unchanged; the switch rewrites only the C/p_hat expression
inside `ours`.  The `breakeven` reference row keeps the narrow form.
"""
BUY_FORMULA_MODES = ("full", "narrow")


def expected_buy(C: float, C_fail: float, p: float) -> float:
    """B_hat = C + (1/p - 1) * C_fail, the Algorithm 1 buy price.

    Same expression as revision_sim.expected_buy, inf at p = 0.  The
    C_fail == C case is evaluated as C / p (the narrow form): the two are
    algebraically identical, and taking the narrow branch keeps the result
    bitwise with every pre-W1.5 row, which was run at C_fail = C.
    """
    if p <= 0:
        return math.inf
    if C_fail == C:
        return C / p
    return C + (1 / p - 1) * C_fail


def buy_formula_mode(mech: dict) -> str:
    """The trigger's buy-price formula (BUY_FORMULA_DOC); "full" by default."""
    v = mech.get("buy_formula", "full")
    if v not in BUY_FORMULA_MODES:
        raise ValueError(f"unknown buy_formula {v!r}")
    return v


# ---------------------------------------------------------------------------
# per-family state (port of policy_sim.FamilyState, minus the macro tier)
# ---------------------------------------------------------------------------

class FamilyState:
    """Per-family accounting shared by all policies."""

    __slots__ = ("name", "rng", "mech", "c", "d", "C", "p_gate", "h",
                 "binding_space", "q0", "compiled", "program_alive", "k",
                 "episodes_since_program", "excess_since_compile",
                 "bindings_seen", "binding_counts", "n_binding_obs",
                 "failed_attempts", "passed_attempts", "consec_failures",
                 "blacklisted", "cooldown", "inter_arrivals", "last_t",
                 "first_seen", "stat_n", "stat_gap",
                 "coins", "use_index", "listed", "reactive_served",
                 "h_env", "r_fallback", "sigma", "silent_penalty",
                 "n_deaths", "n_silent", "n_use_failures",
                 "pi", "failed_spend",
                 # attempt-spend cap variants, SPEND_CAP_DOC
                 "epoch_spend", "realized_saving",
                 "n_cap_blocks_fresh", "n_cap_blocks_admitted",
                 "n_cap_blocks_after_death")

    def __init__(self, name: str, p: dict, rng: random.Random,
                 mech: dict | None = None, coins: "CoinBook | None" = None):
        self.name = name
        self.rng = rng
        self.coins = coins              # common random numbers, or None
        self.use_index = 0              # program uses so far (coin index)
        self.listed = True              # in the manifest (eviction only)
        self.reactive_served = 0        # demonstrations available to compile
        self.mech = mech or MECH_LEGACY
        self.c = p["c"]                 # one reactive episode (tokens)
        self.d = p["d"]                 # one program-served use (extraction)
        self.C = p["C"]                 # one compile attempt
        self.p_gate = p.get("p", 1.0)   # gate pass rate
        # pi: fraction of the k demonstrations that succeeded, known before
        # the first attempt and read by the "pi" gate prior (GATE_PRIOR_DOC).
        self.pi = float(p.get("pi", PI_DEFAULT))
        self.failed_spend = 0.0         # tokens burned on failed attempts
        # Attempt-spend cap variants (SPEND_CAP_DOC).  epoch_spend is the
        # same quantity as failed_spend, restarted at every observed break;
        # realized_saving is what the family's programs have already
        # delivered.  The three counters record how many attempts the cap
        # actually stopped, split by what the family had done before.
        self.epoch_spend = 0.0
        self.realized_saving = 0.0
        self.n_cap_blocks_fresh = 0        # blocked, never admitted
        self.n_cap_blocks_admitted = 0     # blocked, admitted, none died
        self.n_cap_blocks_after_death = 0  # blocked after a program died
        self.q0 = p.get("q0", 0.0)      # extraction/execution failure rate
        self.h = p.get("h", 0.0)        # LEGACY drift hazard (validation only)
        # environment-level fragility (E11); see ENV_DOC.  All inert at
        # their defaults, so a family that does not set them behaves exactly
        # as it did before the channels existed.
        self.h_env = p.get("h_env", 0.0)
        self.r_fallback = p.get("r_fallback", 1.0)
        self.sigma = p.get("sigma", 0.0)
        self.silent_penalty = p.get("silent_penalty", 1.0)
        self.n_deaths = 0               # programs the environment killed
        self.n_silent = 0               # failures that went unnoticed
        self.n_use_failures = 0         # program uses that failed at all
        self.binding_space = p.get("binding_space", 12)  # LEGACY (toolpro port)
        self.compiled = False
        self.program_alive = False
        self.k = 0                      # episodes seen
        self.episodes_since_program = 0
        self.excess_since_compile = 0.0
        self.bindings_seen: set[str] = set()
        self.binding_counts: dict[str, int] = {}
        self.n_binding_obs = 0
        self.failed_attempts = 0
        self.passed_attempts = 0
        self.consec_failures = 0
        self.blacklisted = False
        self.cooldown = 0
        self.inter_arrivals: list[float] = []
        self.last_t = None
        self.first_seen: int | None = None
        # Decayed sufficient statistics of the arrival posterior. With no
        # decay these reproduce the plain Gamma counters exactly:
        # shape = prior_shape + stat_n, rate = prior_rate + stat_gap.
        self.stat_n = 0.0
        self.stat_gap = 0.0

    # -- arrival posterior (decay + prior) -------------------------------

    def observe_arrival(self, t: int) -> None:
        if self.first_seen is None:
            self.first_seen = t
        if self.last_t is not None:
            gap = float(t - self.last_t)
            self.inter_arrivals.append(gap)
            T = self.mech["half_life"]
            if T:
                w = 0.5 ** (gap / T)
                self.stat_n *= w
                self.stat_gap *= w
            self.stat_n += 1.0
            self.stat_gap += gap
        self.last_t = t

    def lam_hat(self, prior_shape: float | None = None,
                prior_rate: float | None = None) -> float:
        a = self.mech["prior_shape"] if prior_shape is None else prior_shape
        b = self.mech["prior_rate"] if prior_rate is None else prior_rate
        return (a + self.stat_n) / (b + self.stat_gap)

    # -- cooldown after a failed compile (mechanism a) --------------------

    def compile_blocked(self) -> bool:
        mode = self.mech["cooldown"]
        if mode == "fixed":
            return self.cooldown > 0
        if mode == "blacklist":
            return self.blacklisted
        return False                      # "inflate" prices instead of blocking

    def price_multiplier(self) -> float:
        if self.mech["cooldown"] == "inflate":
            return 2.0 ** self.consec_failures
        return 1.0

    # -- gate-rate estimate (mechanism e, GATE_PRIOR_DOC) -----------------

    def p_hat(self, p_true: float, pop_mu: float | None = None) -> float:
        """The gate rate the trigger prices with.

        `p_true` is p(k) from the family's constants; it is what the engine
        hands the policy in the default "true" mode and it is always what the
        gate COIN is compared against.  In the estimator modes the policy
        sees only its own attempt history plus the prior.

        `pop_mu` is the stream's running admission rate, read only by the
        "population" prior (GATE_PRIOR_DOC).
        """
        mode = self.mech.get("gate_prior", "true")
        if mode == "true":
            return p_true
        if mode not in GATE_PRIOR_MODES:
            raise ValueError(f"unknown gate_prior {mode!r}")
        a0, b0 = gate_prior_ab(
            mode, self.pi,
            float(self.mech.get("gate_prior_strength", PI_PRIOR_STRENGTH)),
            pop_mu)
        n = self.failed_attempts + self.passed_attempts
        return (self.passed_attempts + a0) / (n + a0 + b0)

    def on_gate_fail(self) -> None:
        self.consec_failures += 1
        if self.mech["cooldown"] == "fixed":
            self.cooldown = self.mech["cooldown_len"]
        elif self.mech["cooldown"] == "blacklist":
            self.blacklisted = True

    # -- spending ---------------------------------------------------------

    def try_compile(self, price: float | None = None,
                    p_gate: float | None = None,
                    price_fail: float | None = None) -> tuple[bool, float]:
        """One compile attempt; returns (passed, tokens spent).

        `price` is charged when the attempt PASSES the gate and `price_fail`
        when it does not (C_FAIL_DOC).  `price_fail = None` keeps the two
        equal, which is what every caller did before the constant existed.

        Under common random numbers the gate coin is addressed by (family,
        attempt index) rather than dealt from this family's own generator, so
        two policies that reach the same attempt of the same family see the
        same coin whatever they did before it.
        """
        c = price if price is not None else self.C
        c_fail = c if price_fail is None else price_fail
        spent = c
        p_gate = self.p_gate if p_gate is None else p_gate
        if self.coins is not None:
            coin = self.coins.gate(
                self.name, self.failed_attempts + self.passed_attempts)
        else:
            coin = self.rng.random()
        if coin < p_gate:
            self.compiled = True
            self.program_alive = True
            self.excess_since_compile = 0.0
            self.passed_attempts += 1
            self.consec_failures = 0
            return True, spent
        self.failed_attempts += 1
        self.failed_spend += c_fail
        self.epoch_spend += c_fail
        self.on_gate_fail()
        return False, c_fail

    # -- attempt-spend cap (mechanism f, SPEND_CAP_DOC) -------------------

    def spend_cap_blocks(self, mech: dict, projected: float) -> bool:
        """True if the cap forbids the attempt this arrival would make.

        `projected` is the saving the trigger currently expects from the
        family, E_use * s, computed by the caller because the two call
        sites project it differently.  Counts the block it returns, split
        by the family's history, so a run can be asked whether the cap is
        stopping fresh families or families that have already paid for
        themselves.
        """
        mode = spend_cap_mode(mech)
        if mode is None:
            return False
        mult = float(mech.get("spend_cap_mult", 1.0))
        if mode == "epoch":
            spent, budget = self.epoch_spend, mult * projected
        elif mode == "realized":
            spent = self.failed_spend
            budget = mult * (max(0.0, self.realized_saving) + projected)
        else:
            spent, budget = self.failed_spend, mult * projected
        if spent < budget:
            return False
        if self.n_deaths > 0:
            self.n_cap_blocks_after_death += 1
        elif self.passed_attempts > 0:
            self.n_cap_blocks_admitted += 1
        else:
            self.n_cap_blocks_fresh += 1
        return True

    def serve(self, q_now: float, use_program: bool) -> float:
        """Serve one episode; returns tokens.

        Wrapper around `_serve_program` that keeps the two book-keeping
        quantities the attempt-spend cap variants read (SPEND_CAP_DOC): the
        saving this use realized against the reactive counterfactual, and
        the epoch restart at an observed break.  Neither changes what the
        episode costs.
        """
        if not (use_program and self.compiled and self.program_alive
                and self.listed):
            return self.c
        cost = self._serve_program(q_now)
        self.realized_saving += self.c - cost
        if not self.program_alive:
            # The artifact died on this use: "epoch" restarts the cap here.
            self.epoch_spend = 0.0
        return cost

    def _serve_program(self, q_now: float) -> float:
        """One episode served from a live, listed program; returns tokens.

        Program hit: pays d, plus a reactive fallback c with probability
        q_now = q(n_t) (extraction/execution failure; the program stays in
        the library).  LEGACY drift mode (h > 0, validation only): the break
        de-registers the program and this use pays c on top of d.

        Under common random numbers the failure coin is addressed by
        (family, use index) instead of dealt from this family's generator.
        A delisted program (eviction) is invisible to the router, so its
        family is served reactively until the entry is relisted.

        E11 environment mode (h_env > 0, sigma > 0 or r_fallback != 1): both
        failure channels act on the same use and the drift hazard really
        kills the artifact.  See ENV_DOC.
        """
        if self.coins is not None:
            coin = self.coins.use(self.name, self.use_index)
        else:
            coin = self.rng.random()
        idx = self.use_index
        self.use_index += 1
        if self.h > 0.0:
            if coin < self.h:
                self.program_alive = False
                return self.c + self.d
            return self.d
        if self.h_env <= 0.0 and self.sigma <= 0.0 \
                and self.r_fallback == 1.0:
            if coin < q_now:
                return self.d + self.c
            return self.d
        # ENVIRONMENT-LEVEL FRAGILITY (E11, ENV_DOC).  Both channels act on
        # this use: the drift coin decides whether the artifact survives it,
        # the q0 coin decides whether the extraction worked, and the use
        # fails if either says so.  The q0 coin is drawn either way, so the
        # use index advances exactly as it does with the hazard off and the
        # two channels stay paired across policies.
        died = False
        if self.h_env > 0.0:
            if self.coins is not None:
                dcoin = self.coins.death(self.name, idx)
            else:
                dcoin = self.rng.random()
            died = dcoin < self.h_env
        if not (died or coin < q_now):
            return self.d
        self.n_use_failures += 1
        silent = False
        if self.sigma > 0.0:
            if self.coins is not None:
                scoin = self.coins.silence(self.name, idx)
            else:
                scoin = self.rng.random()
            silent = scoin < self.sigma
        if silent:
            # nobody notices: the wrong record is written, the artifact is
            # left in the library, and the bill arrives later.
            self.n_silent += 1
            return self.d + self.silent_penalty * self.c
        if died:
            self.program_alive = False
            self.n_deaths += 1
        return self.d + self.r_fallback * self.c


# ---------------------------------------------------------------------------
# population prior (O(1) per arrival; port verbatim)
# ---------------------------------------------------------------------------

class PopulationStats:
    """Population arrival statistics, folded in one arrival at a time.

    A family seen once has no per-family evidence, so borrow the
    population's: pi_hat is the smoothed fraction of families that ever
    recur, lam_ret the pooled recurrence rate of the families that did.
    """

    __slots__ = ("K", "R", "extra", "sum_first_seen",
                 "attempts", "admits")

    def __init__(self) -> None:
        self.K = self.R = self.extra = self.sum_first_seen = 0
        # The stream's admission record, read by the "population" gate prior
        # (GATE_PRIOR_DOC).  Counted over every family that has attempted,
        # so it is the empirical-Bayes analogue of lam_ret for the gate.
        self.attempts = self.admits = 0

    def observe_attempt(self, passed: bool) -> None:
        self.attempts += 1
        self.admits += int(bool(passed))

    def admit_mean(self) -> float:
        """Add-one smoothed admission rate; 1/2 before any attempt."""
        return (self.admits + 1.0) / (self.attempts + 2.0)

    def observe(self, f: FamilyState) -> None:
        if f.k == 1:
            self.K += 1
            return
        if f.k == 2:
            self.R += 1
            self.sum_first_seen += f.first_seen
        self.extra += 1

    def lam_ret(self, t: int) -> float:
        span = self.R * t - self.sum_first_seen
        if self.R == 0 or span <= 0:
            return EB_FALLBACK_RATE
        return self.extra / span

    def future(self, t: int, H: float) -> float:
        pi_hat = (self.R + 1.0) / (self.K + 2.0)
        return pi_hat * self.lam_ret(t) * H


# ---------------------------------------------------------------------------
# the replay
# ---------------------------------------------------------------------------

def run_policy(policy: str, stream: list[str], params: dict,
               rng: random.Random, mech: dict | None = None,
               coins: "CoinBook | None" = None) -> dict:
    """Replay one stream under one policy; returns the accounting record.

    params layout:
      families:  {name: {c, d, C, p, q0, h?, binding_space?, k_table?}}
      horizon:   fixed projection window (default 60)
      tau:       {"m": float, "tau0": float}            (0/0 in old mode)
      epsilon:   {"enabled": bool, eps0, cliff_start, cliff_slope}
      router:    {"kind": "listing"|"retrieval"|"hierarchical", ...}
                 (absent = listing, the measured harness)
      eviction:  {"T": float|"t_star", "v_min", "t_min", "t_max"}
                 (read only by the policies in POLICIES_EVICT)
      k_min:     demonstrations the compiler needs (default 1, inert)

    `coins` switches on common random numbers: pass one CoinBook per (stream,
    repetition) and every policy of that repetition sees the same gate coin
    at the same compile attempt and the same failure coin at the same program
    use.  Without it each policy pulls from `rng`, which is the historical
    behaviour and is what the existing outputs were produced with.
    """
    mech = mech or MECH_LEGACY
    fam_cfgs = params["families"]
    tau_cfg = params.get("tau") or {}
    eps_cfg = params.get("epsilon") or {}
    horizon = params.get("horizon", 60)
    router = params.get("router")
    _router_kind(router)                     # validate once, fail loudly
    k_min = int(params.get("k_min", 1) or 1)

    # evicting variants run the base rule with the residency bound on
    evict_cfg = params.get("eviction") if policy in POLICIES_EVICT else None
    abandon_after = int(params.get("abandon_after", ABANDON_AFTER)) \
        if policy in POLICIES_ABANDON else None
    no_inflate = policy in POLICIES_NOINFLATE
    # The trigger's buy-price formula (BUY_FORMULA_DOC): "full" (Algorithm 1's
    # B_hat = C + (1/p_hat - 1) * C_fail) is the default; "narrow" (the
    # pre-W1.5 C/p_hat) is read by name for reproductions.  Validated once so
    # a typo fails loudly at run start rather than mid-run.
    buy_formula = buy_formula_mode(mech)
    policy = POLICIES_EVICT.get(policy, policy)
    m_tax = tau_cfg.get("m", 0.0)
    ev_v_min = float((evict_cfg or {}).get("v_min",
                                           EVICTION_DEFAULT["v_min"]))

    fams: dict[str, FamilyState] = {}        # created lazily on first arrival
    total = 0.0
    tau_total = 0.0
    n_compile_tokens = 0.0
    n_lib = 0                                # entries currently in the manifest
    max_lib = 0
    compiles: list[dict] = []
    residencies: list[dict] = []             # one per admission, for the tax
    listed: dict[str, dict] = {}             # name -> its residency record
    ev_heap: list[tuple[float, int, str]] = []
    ev_stamp = 0
    n_evictions = 0
    n_relists = 0
    n_deaths_delisted = 0
    t_star_seen: list[float] = []
    live_rec: dict[str, dict] = {}
    first_compile_rank: dict[str, int] = {}
    be_excess: dict[str, float] = {}
    remaining_counts: dict[str, int] = {}
    pop = PopulationStats()
    prior_mode = mech.get("prior_mode")
    h_mode = mech.get("horizon_mode", "fixed")
    if h_mode not in HORIZON_MODES:
        raise ValueError(h_mode)
    if policy in ORACLES:
        for nm in stream:
            remaining_counts[nm] = remaining_counts.get(nm, 0) + 1
    n_stream = len(stream)

    curve: list[float] = []
    n_curve: list[int] = []
    curve_step = max(1, len(stream) // 30)

    for t, name in enumerate(stream):
        f = fams.get(name)
        if f is None:
            f = fams[name] = FamilyState(name, fam_cfgs[name], rng, mech,
                                         coins)
        f.k += 1
        if coins is not None:
            binding = f"b{coins.binding(name, f.k - 1, f.binding_space)}"
        else:
            binding = f"b{rng.randrange(f.binding_space)}"
        rebinding = binding not in f.bindings_seen
        f.bindings_seen.add(binding)
        f.binding_counts[binding] = f.binding_counts.get(binding, 0) + 1
        f.n_binding_obs += 1
        f.observe_arrival(t)
        pop.observe(f)
        if f.cooldown > 0:
            f.cooldown -= 1

        # (0) residency: delist every program whose idle window has run out
        # before this arrival is priced, so the arrival pays for the manifest
        # it actually gets.  Stale heap entries (a program used since the
        # entry was pushed) carry an old stamp and are dropped.
        while ev_heap and ev_heap[0][0] < t:
            _dl, stamp, ev_name = heapq.heappop(ev_heap)
            st = listed.get(ev_name)
            if st is None or st["stamp"] != stamp:
                continue
            floor = st["admit_t"] + st["v_min"]
            if t < floor:                    # evidence floor still protecting
                ev_stamp += 1
                st["stamp"] = ev_stamp
                heapq.heappush(ev_heap, (floor, ev_stamp, ev_name))
                continue
            st["taxed_arrivals"] += t - 1 - st["admit_t"]
            del listed[ev_name]
            n_lib -= 1
            n_evictions += 1
            fams[ev_name].listed = False

        # (1) selection step: every arrival pays tau(n_t) with the library
        # as it stands (compiles happen after service, so this arrival's
        # own compile cannot tax it).
        tax = tau_of(n_lib, tau_cfg, router)
        total += tax
        tau_total += tax
        q_now = q_of(n_lib, f.q0, eps_cfg, router, tau_cfg)
        # saving per program use; identical to (1 - q) c - d unless the E11
        # environment channels are on for this family
        s = use_saving(f.c, f.d, q_now, f.h_env, f.sigma, f.silent_penalty,
                       f.r_fallback)

        # (2) the trigger's online estimates
        lam_hat = f.lam_hat()
        E_future = lam_hat * horizon
        if f.k >= 2 and h_mode != "fixed":
            base = lam_hat * (t if h_mode in ("doubling", "capped_doubling")
                              else horizon)
            if h_mode == "doubling":
                E_future = base
            else:
                # capped modes: the doubling window is the FAMILY's own
                # deployment age (t - first_seen; a family that has arrived
                # k times in its own lifetime is projected to arrive k more
                # times), NOT the global stream age -- a late-born family
                # projecting lambda_hat * t is the late-first-arrival
                # pathology.  The projection is then capped at the program's
                # expected service count (old engine Theorem 2: H_f = 1/h;
                # with the legacy h = 0 the trigger constant horizon_cap
                # supplies the lifetime, default 50 = 1/0.02).
                if h_mode == "capped_doubling":
                    base = lam_hat * (t - (f.first_seen or 0))
                # The environment's own program lifetime wins over the
                # trigger constant when there is one: legacy h first (that
                # is what the old engine's Theorem 2 read), then the E11
                # environment hazard, then the constant.
                if f.h > 0:
                    cap = 1.0 / f.h
                elif f.h_env > 0:
                    cap = 1.0 / f.h_env
                else:
                    cap = mech.get("horizon_cap")
                E_future = min(base, cap) if cap else lam_hat * horizon
        had_program = f.compiled and f.program_alive and f.listed
        # The compiler needs k_min demonstrations.  A demonstration is a
        # reactive episode of the family, and the deciding arrival supplies
        # one itself (the deployed order is serve, then decide; the engine
        # decides first only so the bought program can serve that same
        # arrival), so k_min = 1 is the deployed protocol and is inert.
        demos = f.reactive_served + (0 if had_program else 1)
        C_k, C_fail_k, p_k = compile_prices_for_k(fam_cfgs[name], demos)

        # (3) the policy's compile decision
        act = False
        spend_cost = C_k
        spend_cost_fail = C_fail_k
        if policy == "always_reactive":
            pass
        elif policy == "always_compile":
            act = (not had_program) and f.cooldown == 0
        elif policy == "on_second":
            # compile on recurrence: first on the 2nd episode, and again on
            # the 2nd episode after a program breaks (TraceCompiler-style)
            act = (not f.program_alive) and f.episodes_since_program >= 2
        elif policy == "success_count":
            # evidence-based promotion: 10 episodes, re-earned after a break
            act = (not f.program_alive) and f.episodes_since_program >= 10
        elif policy == "toolpro_port":
            # per-arrival greedy with point estimates, and programs do not
            # generalize across bindings: every NEW binding re-pays compile
            window = f.inter_arrivals[-5:]
            k_hat = (len(window) / (sum(window) / len(window))) if window else 0.0
            act = (k_hat * s > (f.C / f.p_gate if f.p_gate > 0 else math.inf)) and (rebinding or not f.compiled)
        elif policy == "breakeven":
            # Theorem 1: react until the accumulated excess reaches the
            # effective price, then compile; Corollary 1 restarts the count
            # after an observed break.
            #
            # The price is the NARROW form C(k) / p_hat with the SAME
            # estimator the `ours` rows use (GATE_PRIOR_DOC).  The buy-
            # formula switch (BUY_FORMULA_DOC) applies to the `ours` trigger
            # only; this reference row keeps the expression every published
            # breakeven number was run with, character for character.  In
            # the default "true" mode p_hat IS the family's real p.
            if mech.get("gate_prior", "true") == "true":
                price = f.C / f.p_gate if f.p_gate > 0 else math.inf
            else:
                p_est = f.p_hat(p_k, pop.admit_mean())
                price = C_k / p_est if p_est > 0 else math.inf
            act = ((not f.program_alive)
                   and be_excess.get(name, 0.0) >= price)
            if act and f.spend_cap_blocks(mech, E_future * s):
                # SPEND_CAP_DOC, capping the same quantity it caps for
                # `ours`: the tokens already burned on failed attempts
                # against the saving the family is currently expected to
                # deliver.  Absent from every published breakeven row.
                act = False
        elif policy == "ours":
            # p_hat is the estimator the deployment actually has; in the
            # default "true" mode it IS p_k, so nothing moves (GATE_PRIOR_DOC).
            # The buy price is Algorithm 1's B_hat = C(k) + (1/p_hat - 1) *
            # C_fail(k), the passing attempt at C plus the expected
            # (1/p_hat - 1) misses at C_fail each (BUY_FORMULA_DOC, the
            # default since W1.5).  "narrow" keeps the pre-W1.5 C(k)/p_hat
            # verbatim; at C_fail = C the two are the same expression, so
            # every row run without a measured C_fail is bit-identical
            # either way.  The spend cap below is unchanged.
            p_est = f.p_hat(p_k, pop.admit_mean())
            if buy_formula == "narrow":
                c_eff = C_k / p_est if p_est > 0 else math.inf
            else:
                c_eff = expected_buy(C_k, C_fail_k, p_est)
            price = c_eff if no_inflate else c_eff * f.price_multiplier()
            # methods-v2 section 2 one-line extension: an admitted manifest
            # entry taxes EVERY future arrival m tokens (tau(n) = m*n + tau0),
            # so the threshold carries the externality m * Lambda_rest with
            # Lambda_rest the deployment-age estimate of the remaining stream
            # (a stream that has run t arrivals is assumed to run t more).
            # Inert when m = 0, which keeps old-constants mode bitwise.
            #
            # Under the residency rule the entry is delisted after T idle
            # arrivals, so the externality is bounded by m*T whatever the
            # stream does and the term becomes m * min(T, Lambda_rest):
            # strictly tighter, and no longer dependent on estimating how
            # much stream is left (B.3.2 consequence 2).
            rest = float(t)
            if evict_cfg is not None:
                rest = min(rest, eviction_T(evict_cfg, c_eff, lam_hat, m_tax))
            price += m_tax * rest
            E_use = E_future
            if prior_mode == "population" and f.k == 1:
                # No per-family evidence yet: population cold start. The
                # decision precedes serve(), so a program bought here serves
                # the deciding arrival itself at d; that arrival counts too.
                E_use = 1.0 + pop.future(t, horizon)
            act = ((not f.compile_blocked())
                   and not had_program
                   and E_use * s > price)
            if act and f.spend_cap_blocks(mech, E_use * s):
                # SPEND_CAP_DOC: ski rental on the attempts.  Re-evaluated
                # here at every arrival against the CURRENT estimate, so a
                # family whose expected saving grows can re-enter.
                act = False
        elif policy in ORACLES:
            # knows the true remaining count of this family in the stream,
            # counting the arrival being decided
            remaining = remaining_counts[name]
            remaining_counts[name] = remaining - 1
            price = f.C / f.p_gate if f.p_gate > 0 else math.inf
            uses = remaining
            if policy == "oracle_tax":
                # The manifest externality this admission would create: an
                # entry admitted at arrival t is in the manifest for every
                # arrival t+1 .. T-1, each of which pays m more (tau(n) =
                # m*n + tau0; the admitting arrival itself was already taxed
                # at the old n).  Inert when m = 0, and inert under a router
                # whose tax stops growing with n -- charging the clairvoyant
                # a listing externality it does not pay would make it refuse
                # free admissions.  Identical to m under full listing, so no
                # published number moves.
                price += tau_marginal_lower_bound(tau_cfg, router) \
                    * (n_stream - 1 - t)
                # A program cannot serve more uses than its own lifetime.
                # The only lifetime the ENVIRONMENT has is the legacy drift
                # hazard (expected 1/h uses); under methods-v2 constants
                # h = 0, a program never breaks and uses = remaining.  The
                # trigger constant horizon_cap is deliberately NOT applied:
                # it is a property of our estimator, not of the world, and
                # capping a clairvoyant with it would hide information the
                # rule is defined to have.
                if f.h > 0:
                    uses = min(uses, 1.0 / f.h)
                elif f.h_env > 0:
                    # E11: the environment really does kill programs, so the
                    # clairvoyant knows the expected service count too.
                    uses = min(uses, 1.0 / f.h_env)
            act = ((not f.program_alive or not f.compiled)
                   and uses * s > price)
        else:
            raise ValueError(policy)

        # (4) compile, or relist an artifact the residency rule delisted
        if act and k_min > 1 and demos < k_min:
            act = False           # the compiler has not seen k_min demos yet
        if act and abandon_after is not None \
                and f.consec_failures >= abandon_after:
            act = False           # abandoned: the gate missed too many times
        if act:
            relist = f.compiled and f.program_alive and not f.listed
            if relist:
                # Re-admission: delisting removed the manifest entry, not the
                # artifact, and the artifact has already passed its gate.
                # Zero compile tokens, no gate coin; what it cost is the
                # reactive episodes served while the family was invisible,
                # plus having to clear the policy's own trigger again.
                f.listed = True
                n_relists += 1
                f.episodes_since_program = 0
                ok = True
            else:
                ok, spent = f.try_compile(spend_cost, p_k, spend_cost_fail)
                pop.observe_attempt(ok)   # GATE_PRIOR_DOC, "population"
                total += spent
                n_compile_tokens += spent
                rec = {"family": name, "t": t, "price": spent, "ok": ok,
                       "uses": 0}
                compiles.append(rec)
                if name not in first_compile_rank:
                    first_compile_rank[name] = f.k
                if ok:
                    f.episodes_since_program = 0
                    # True everywhere else already; a family the E11 hazard
                    # killed can also have been delisted by the residency
                    # rule, and a fresh artifact is listed either way.
                    f.listed = True
                    live_rec[name] = rec
                    if policy == "breakeven":
                        be_excess[name] = 0.0
            if ok:
                n_lib += 1            # one more entry in the manifest
                if n_lib > max_lib:
                    max_lib = n_lib
                st = {"family": name, "admit_t": t, "stamp": 0,
                      "v_min": 0.0, "T": math.inf, "taxed_arrivals": 0}
                if evict_cfg is not None:
                    ev_stamp += 1
                    st["stamp"] = ev_stamp
                    st["v_min"] = ev_v_min
                    st["T"] = eviction_T(
                        evict_cfg,
                        C_k / p_k if p_k > 0 else math.inf, lam_hat, m_tax)
                    t_star_seen.append(st["T"])
                    heapq.heappush(ev_heap, (t + st["T"], ev_stamp, name))
                listed[name] = st
                residencies.append(st)

        # (5) serve
        alive_before = f.compiled and f.program_alive and f.listed
        served_by = live_rec.get(name) if alive_before else None
        cost = f.serve(q_now, use_program=(policy != "always_reactive"))
        if served_by is not None:
            served_by["uses"] += 1
            if evict_cfg is not None:
                # a use resets the idle window; the old heap entry goes stale
                st = listed.get(name)
                if st is not None and st["T"] < math.inf:
                    ev_stamp += 1
                    st["stamp"] = ev_stamp
                    heapq.heappush(ev_heap, (t + st["T"], ev_stamp, name))
        else:
            f.reactive_served += 1        # one more demonstration on file
        if alive_before and not f.program_alive and f.h_env > 0.0:
            # E11: the environment killed the artifact on this use.  The
            # manifest entry goes with it -- a dead program cannot serve, so
            # leaving it listed would tax every later arrival for nothing --
            # and the family has to pay C_eff again before it serves from a
            # program.  The residency record is closed at this arrival,
            # which it was still taxed for; any heap entry left behind
            # carries a stale stamp and is dropped when it surfaces.
            st = listed.pop(name, None)
            if st is not None:
                st["taxed_arrivals"] += t - st["admit_t"]
                n_lib -= 1
                n_deaths_delisted += 1
        if policy == "toolpro_port" and rebinding:
            cost = f.c                # fresh binding cannot reuse the program
        total += cost
        f.excess_since_compile += s
        if policy == "breakeven":
            if had_program and not f.program_alive:
                be_excess[name] = 0.0     # Corollary 1: restart after break
            elif not had_program:
                be_excess[name] = be_excess.get(name, 0.0) + s
        if had_program and not f.program_alive:
            f.episodes_since_program = 0  # program broke; re-earn evidence
        else:
            f.episodes_since_program += 1

        if t % curve_step == 0:
            curve.append(total)
            n_curve.append(n_lib)

    if not curve or curve[-1] != total:
        curve.append(total)
        n_curve.append(n_lib)

    # every entry still listed at the end taxed the rest of the stream
    for st in listed.values():
        st["taxed_arrivals"] += n_stream - 1 - st["admit_t"]
    taxed_by_family: dict[str, int] = {}
    for st in residencies:
        taxed_by_family[st["family"]] = taxed_by_family.get(
            st["family"], 0) + st["taxed_arrivals"]

    # A compile is wasted if the artifact it bought never served enough
    # episodes to pay its own price back.  That test knows nothing about the
    # manifest: an entry can repay its own C and still be a large net loss,
    # because it taxes every arrival it is resident for (audit section 5.5).
    # wasted_compiles keeps its published meaning.  wasted_compiles_tax
    # counts an admission wasted when it fails to repay price + externality,
    # and wasted_tax_tokens is the tax those admissions caused.  At our
    # measured price the difference is the whole story: N* < 1, so every
    # entry repays its own C on its first use and wasted_compiles reads 0
    # even where the library is taxing every arrival for entries that will
    # never be used again.
    wasted = 0
    wasted_tax = 0
    wasted_tax_tokens = 0.0
    m_marg = tau_marginal_lower_bound(tau_cfg, router)
    for rec in compiles:
        fam = fam_cfgs[rec["family"]]
        s_fam = use_saving_fam(fam, fam.get("q0", 0.0))
        ext = m_marg * taxed_by_family.get(rec["family"], 0) \
            if rec["ok"] else 0.0
        if rec["uses"] * s_fam < rec["price"]:
            wasted += 1
        if rec["uses"] * s_fam < rec["price"] + ext:
            wasted_tax += 1
            wasted_tax_tokens += ext
    return {"policy": policy,
            "final_tokens": total,
            "curve": curve,
            "tau_total": tau_total,
            "tax_share": (tau_total / total) if total else 0.0,
            "compile_tokens": n_compile_tokens,
            "n_compiles": len(compiles),
            "wasted_compiles": wasted,
            "wasted_compiles_tax": wasted_tax,
            "wasted_tax_tokens": wasted_tax_tokens,
            "final_library": n_lib,
            "max_library": max_lib,
            "n_evictions": n_evictions,
            "n_relists": n_relists,
            "n_program_deaths": sum(f.n_deaths for f in fams.values()),
            "n_silent_failures": sum(f.n_silent for f in fams.values()),
            "n_use_failures": sum(f.n_use_failures for f in fams.values()),
            "n_deaths_delisted": n_deaths_delisted,
            "mean_residency_T": (sum(t_star_seen) / len(t_star_seen)
                                 if t_star_seen else 0.0),
            "n_curve": n_curve,
            "first_compile_rank": first_compile_rank,
            "failed_attempts": sum(f.failed_attempts for f in fams.values()),
            "failed_compile_tokens": sum(f.failed_spend
                                         for f in fams.values()),
            "passed_attempts": sum(f.passed_attempts for f in fams.values()),
            # attempt-spend cap diagnostics (SPEND_CAP_DOC); all zero when
            # the cap is off
            "cap_blocks_fresh": sum(f.n_cap_blocks_fresh
                                    for f in fams.values()),
            "cap_blocks_admitted": sum(f.n_cap_blocks_admitted
                                       for f in fams.values()),
            "cap_blocks_after_death": sum(f.n_cap_blocks_after_death
                                          for f in fams.values()),
            "realized_saving": sum(f.realized_saving
                                   for f in fams.values())}


# ---------------------------------------------------------------------------
# offline optimum (DP lower bound; port with the tau0 term folded in)
# ---------------------------------------------------------------------------

def offline_optimum(stream: list[str], params: dict) -> float:
    """Deterministic lower bound on any rule's expected cost for this stream.

    Grants full knowledge of each family's arrival count and the drift
    hazard (break outcomes stay unseen) and relaxes the action set to:
    reactive at c per use, or one purchase at C / p buying a program that
    serves every use at d (+ q0 * c expected fallback) and costs its
    reactive fallback c on a breaking use.  V is the optimal cost-to-go
    without a program, A with a fresh program; the recursion is exact, so
    the bound needs no sampling.

    tau: every arrival pays at least tau0, which is folded in per arrival.
    The m * n_t part of the manifest tax is OMITTED on purpose: it depends
    on the global library trajectory, is >= 0, and leaving it out keeps the
    result a lower bound.  eps is likewise omitted (the bound may assume the
    best case; measured eps = 0 at the n the trigger stops at anyway).
    """
    counts: dict[str, int] = {}
    for name in stream:
        counts[name] = counts.get(name, 0) + 1
    tau_cfg = params.get("tau") or {}
    tau0 = tau_cfg.get("tau0", 0.0)
    total = 0.0
    for name, n in counts.items():
        fam = params["families"][name]
        c, d = fam["c"], fam["d"]
        c_eff = fam["C"] / fam["p"] if fam.get("p", 1.0) > 0 else math.inf
        h = fam.get("h", 0.0)
        q0 = fam.get("q0", 0.0)
        V, A = 0.0, 0.0
        if env_fragility_on(fam):
            # E11 environment mode.  A use fails with F = h + (1-h) q0; a
            # failure is silent with probability sigma (cost Pi*c, program
            # survives) and loud otherwise (cost r*c, program dies iff the
            # drift coin was the one that fired).  The extra min() lets the
            # clairvoyant react even while holding a live program, which can
            # only lower the value, so the result stays a lower bound.
            h_e = fam.get("h_env", 0.0)
            sg = fam.get("sigma", 0.0)
            pen = fam.get("silent_penalty", 1.0)
            rf = fam.get("r_fallback", 1.0)
            fail = h_e + (1.0 - h_e) * q0
            extra = fail * (sg * pen + (1.0 - sg) * rf) * c
            dead = (1.0 - sg) * h_e
            for _ in range(n):
                A_prev, V_prev = A, V
                A = tau0 + min(d + extra + (1.0 - dead) * A_prev
                               + dead * V_prev, c + A_prev)
                V = min(tau0 + c + V_prev, c_eff + A)
            total += V
            continue
        for _ in range(n):
            A = tau0 + d + q0 * c + (1.0 - h) * A + h * (c + V)
            V = min(tau0 + c + V, c_eff + A)
        total += V
    return total


def _offline_optimum_tax_env(stream: list[str], params: dict,
                             evict_cfg: dict | None) -> float:
    """Tax-aware lower bound with the E11 environment channels on.

    The exact scan in `offline_optimum_tax` assumes a family is admitted at
    most once, which the environment hazard breaks: a program that dies has
    to be rebought, so the per-family choice is a policy over arrivals, not
    a single index.  Falling back to the tax-blind DP would be valid but
    useless here, because the m * n_t term is most of the account at the
    measured tax (m = 91 per arrival per entry).

    So keep the tax and solve the per-family problem exactly, using the same
    separability: the tax an entry causes depends only on which global
    arrivals it is listed for, not on what else is in the library.  A listed
    entry is removed when the artifact dies, and the death happens during an
    arrival of its own family, so between the family's j-th and (j+1)-th
    arrival the entry taxes exactly the arrivals in that gap, and only if it
    survived the j-th use.  That makes the tax a per-gap charge inside a
    two-state backward pass (a live program, or none) over the family's own
    arrival positions.

    Relaxations, each of which can only lower the value:
      * eps(n) dropped (q >= q0, and the cost is increasing in q);
      * the gate charged its expectation C_eff = C/p rather than a random
        number of C-priced attempts (exact in the mean, not per realisation);
      * a gap longer than the residency bound T_res is charged only T_res
        arrivals of tax, and the artifact stays available for free after the
        delisting, which is cheaper than the relist the engine makes a
        policy earn;
      * k_min ignored: the clairvoyant may compile at any arrival;
      * with a live program it may still choose to serve reactively and
        keep the artifact at no tax.
    """
    tau_cfg = params.get("tau") or {}
    router = params.get("router")
    tau0 = tau_of(0, tau_cfg, router)
    m = tau_marginal_lower_bound(tau_cfg, router)
    m_tax = tau_cfg.get("m", 0.0)
    fam_cfgs = params["families"]
    T = len(stream)
    positions: dict[str, list[int]] = {}
    for k, name in enumerate(stream):
        positions.setdefault(name, []).append(k)
    total = 0.0
    for name, pos in positions.items():
        fam = fam_cfgs[name]
        c, d = fam["c"], fam["d"]
        q0 = fam.get("q0", 0.0)
        h_e = fam.get("h_env", 0.0)
        sg = fam.get("sigma", 0.0)
        pen = fam.get("silent_penalty", 1.0)
        rf = fam.get("r_fallback", 1.0)
        fail = h_e + (1.0 - h_e) * q0
        extra = fail * (sg * pen + (1.0 - sg) * rf) * c
        dead = (1.0 - sg) * h_e
        c_eff = fam["C"] / fam["p"] if fam.get("p", 1.0) > 0 else math.inf
        n_f = len(pos)
        T_res = math.inf
        if evict_cfg is not None:
            T_res = eviction_T(evict_cfg, c_eff, n_f / T, m_tax)
        A = V = 0.0
        for j in range(n_f - 1, -1, -1):
            # global arrivals the entry taxes if it survives this use: the
            # ones between this arrival of the family and its next (or the
            # rest of the stream after the family's last arrival)
            gap = float(pos[j + 1] - pos[j]) if j + 1 < n_f \
                else float(T - 1 - pos[j])
            hold = m * min(gap, T_res)
            serve_prog = d + extra + (1.0 - dead) * (hold + A) + dead * V
            A, V = (tau0 + min(serve_prog, c + A),
                    tau0 + min(c + V, c_eff + serve_prog))
        total += V
    return total


def offline_optimum_tax(stream: list[str], params: dict,
                        evict_cfg: dict | None = None) -> float:
    """Offline optimum of the FULL stream objective, manifest tax included.

    The tax-blind `offline_optimum` above drops the m * n_t term, which is
    fine as a bound but makes the "bound" uninformative once m > 0 dominates
    the account.  This one keeps it, and stays exactly solvable because the
    tax is linear in the library size.

    Separability.  Fix any policy and any realisation.  Let A be the set of
    arrival indices at which a manifest entry is admitted, T = len(stream),
    and n_t = |{k in A : k < t}| the library size arrival t is taxed with
    (an arrival is taxed before its own compile, sim step (1) vs (4)).  Then

        Cost = T * tau0                                     (a)
             + m * sum_t n_t                                (b)
             + sum_t service_t                              (c)
             + sum_attempts C                               (d)

    and (b) rearranges by swapping the order of summation:

        sum_t n_t = sum_{k in A} |{t : t > k}| = sum_{k in A} (T - 1 - k),

    i.e. the tax an admission costs depends only on WHEN it happened, not on
    what else is in the library.  A compile can only be issued at an arrival
    of its own family (sim step (3) runs inside that family's arrival), and
    the admitting arrival is itself served by the program (step (4) precedes
    step (5)), so if family f admits at its (j+1)-th arrival, at global index
    k_j, it serves n_f - j arrivals.  Each served arrival costs d + q0 * c
    instead of c, a saving of s_f = (1 - q0) c - d.  Therefore

        Cost >= T*tau0 + sum_f [ n_f*c_f
                 - max(0, max_j ( (n_f - j)*s_f - C_eff,f - m*(T-1-k_j) )) ]

    which is what this function evaluates: one scan over each family's
    arrival positions, exact, no sampling.  With p = 1 and h = 0 it is
    ATTAINED by the clairvoyant policy that compiles each family at its
    argmax index (and never otherwise), so it is the true optimum, not just
    a bound.

    Relaxations used, each of which can only LOWER the value (so the result
    stays a valid lower bound where it is not attained):
      * eps(n) is dropped: q(n) = eps + (1-eps) q0 >= q0, so charging q0
        understates every program-served arrival and overstates s_f.
      * the gate is charged its expectation C_eff = C/p rather than a random
        number of C-priced attempts.  E[attempts up to the first pass] =
        1/p, so this is exact in expectation but NOT per realisation: a rep
        that passes the gate on the first try at p < 1 can undercut the
        value.  All measured openapps constants have p = 1 (and the android
        cells have p = 0, i.e. C_eff = inf and no admission is possible), so
        the caveat is vacuous for E3/E4; it bites only under E5's gate-rate
        stress, and there only rep-by-rep, never in the mean.
      * re-admissions for the same family are dropped.  With h = 0 a live
        program never breaks, so a second entry buys nothing and costs both
        C_eff and a second permanent tax stream; dropping it only lowers the
        value.

    Where it is NOT valid: the legacy drift channel (h > 0).  Then a program
    dies, re-compiles are genuinely useful, and the per-family choice is no
    longer a single index.  In that case this falls back to the tax-blind DP,
    which is still a lower bound because m * sum_t n_t >= 0.

    ROUTER (2026-09-09).  The externality term m*(T-1-k) is the marginal tax
    of full listing.  Under retrieval or a hierarchy the tax stops growing
    with n, so the marginal is charged at its lower bound of zero and every
    arrival is charged tau_R(0) = tau0.  That stays a valid lower bound and
    is a looser one; it is not the exact optimum of those objectives.

    EVICTION (2026-09-09).  Pass evict_cfg to bound the objective an evicting
    policy faces.  Delisting is free and relisting costs no compile, so a
    clairvoyant with a residency bound T serves exactly the arrivals it would
    have served anyway and pays tax only for min(gap, T) arrivals after each
    use, plus min(T, rest) after the last one.  The scan below is the same
    one with (T-1-k) replaced by that suffix sum, still exact, still one pass
    per family.  It ignores the evidence floor, which can only extend
    residency, so the value can only be lowered by ignoring it.
    """
    tau_cfg = params.get("tau") or {}
    router = params.get("router")
    tau0 = tau_of(0, tau_cfg, router)
    m = tau_marginal_lower_bound(tau_cfg, router)
    m_tax = tau_cfg.get("m", 0.0)
    fam_cfgs = params["families"]
    if any(env_fragility_on(fam_cfgs[nm]) for nm in set(stream)):
        # E11: programs really die, so the single-admission scan below no
        # longer applies.  The two-state DP keeps the tax term.
        return _offline_optimum_tax_env(stream, params, evict_cfg)
    if any(fam_cfgs[nm].get("h", 0.0) > 0.0 for nm in set(stream)):
        return offline_optimum(stream, params)

    T = len(stream)
    positions: dict[str, list[int]] = {}
    for k, name in enumerate(stream):
        positions.setdefault(name, []).append(k)

    total = T * tau0
    for name, pos in positions.items():
        fam = fam_cfgs[name]
        c, d = fam["c"], fam["d"]
        q0 = fam.get("q0", 0.0)
        s = use_saving_fam(fam, q0)
        c_eff = fam["C"] / fam["p"] if fam.get("p", 1.0) > 0 else math.inf
        n_f = len(pos)
        best_gain = 0.0                      # the "never compile" option
        if s > 0 and c_eff < math.inf:
            if evict_cfg is None:
                taxed = [T - 1 - k for k in pos]
            else:
                # the clairvoyant sizes residency from the family's true rate
                T_res = eviction_T(evict_cfg, c_eff, n_f / T, m_tax)
                taxed = [0] * n_f
                acc = min(T_res, T - 1 - pos[-1])
                taxed[n_f - 1] = acc
                for i in range(n_f - 2, -1, -1):
                    acc += min(pos[i + 1] - pos[i], T_res)
                    taxed[i] = acc
            for j, k in enumerate(pos):
                gain = (n_f - j) * s - c_eff - m * taxed[j]
                if gain > best_gain:
                    best_gain = gain
        total += n_f * c - best_gain
    return total


# ---------------------------------------------------------------------------
# derived quantities
# ---------------------------------------------------------------------------

def n_star(params: dict) -> float:
    """Break-even uses N* = C_eff / ((1-q) c - d), averaged over families.

    Uses q0 only (eps off): the headline N* of methods-v2 section 6.
    """
    vals = []
    for fam in params["families"].values():
        s = use_saving_fam(fam, fam.get("q0", 0.0))
        c_eff = fam["C"] / fam["p"] if fam.get("p", 1.0) > 0 else math.inf
        vals.append(c_eff / s if s > 0 else math.inf)
    return sum(vals) / len(vals)


def hottest(stream: list[str]) -> str:
    """The most frequent family, ties broken by name (reproducible)."""
    counts: dict[str, int] = {}
    for name in stream:
        counts[name] = counts.get(name, 0) + 1
    return min(counts, key=lambda k: (-counts[k], k))
