"""Literature decision rules adapted to the shared PACE token-cost model.

AutoRPA is a one-time build followed by execution or agent fallback. Its
bounded repair is already part of one measured compilation attempt.
ToolPro compares one-use savings with build cost. The original paper uses
latency and within-task endpoint counts; this declared adaptation uses the
same token charges, verification estimator, and reusable artifacts as PACE.

The serving environment follows revision_sim.py. Only the environment reads
the hidden verification probability. The proposal function accepts neither
that probability nor any future stream information.
"""
from __future__ import annotations

import heapq
import importlib.util
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import revision_sim as reference


NAMED_POLICIES = ("autorpa_once", "toolpro_cost")
POLICIES = NAMED_POLICIES + ("pace_narrow_price",)
COMPONENTS = ("reactive_tokens", "extraction_tokens", "compile_tokens", "router_tokens")


def _load_safety_engine(name):
    path = Path(__file__).resolve().parents[1] / "protocol_explore/engine.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_full_safety = _load_safety_engine("pace_unmodified_safety")
_narrow_safety = _load_safety_engine("pace_narrow_safety")
_original_narrow_propose = _narrow_safety.propose


def _narrow_propose(kind, **observed):
    # Only the proposal's price calculation changes. The unchanged serving
    # engine still reserves and charges the real C_fail from the profile.
    observed["Cf"] = observed["C"]
    return _original_narrow_propose(kind, **observed)


_narrow_safety.propose = _narrow_propose


def full_run(*args, **kwargs):
    """Crosscheck the unchanged PACE engine against the retained outcomes."""
    return _full_safety.run(*args, **kwargs)


def propose(policy, *, demos, alive, attempts, admits, c, d, C, C_fail,
            q, h, fallback_mult, k_min):
    """Choose using only past outcomes and supplied cost/hazard parameters."""
    if policy not in NAMED_POLICIES:
        raise ValueError(policy)
    if alive or demos < k_min:
        return False
    if policy == "autorpa_once":
        return attempts == 0
    saving = c - d - (h + (1 - h) * q) * fallback_mult * c
    p_est = (admits + 1.0) / (attempts + 2.0)
    return saving > reference.expected_buy(C, C_fail, p_est)


def run(stream, profiles, mapping, policy, *, seed=7, ttl=100, k_min=3,
        h=0.02, m=91.0, tau0=368.0, silent=0.0, penalty=3.0,
        fallback_mult=1.0, trace=False):
    """Serve the current task, then consider compilation for later tasks.

These adapters are evaluated only in the detected-failure setting used by
the retained main conditions. Hidden failure penalties are not token cost.
"""
    if policy not in POLICIES:
        raise ValueError(policy)
    if policy == "pace_narrow_price":
        return _narrow_safety.run(stream, profiles, mapping,
                                  _narrow_safety.Policy(policy, "projected", .25),
                                  seed=seed, ttl=ttl, k_min=k_min, h=h, m=m,
                                  tau0=tau0, silent=silent, penalty=penalty,
                                  fallback_mult=fallback_mult, trace=trace)
    if k_min < 1 or ttl < 1 or not 0 <= h <= 1 or min(m, tau0, fallback_mult) < 0:
        raise ValueError("invalid deployment parameters")
    if silent != 0:
        raise ValueError("the frozen adapters use detected failures only")
    states, heap, library = {}, [], 0
    baseline, paid, worst_ratio = 0.0, 0.0, 0.0
    metrics = dict(reactive_tokens=0.0, extraction_tokens=0.0,
                   compile_tokens=0.0, router_tokens=0.0, harm_tokens=0.0,
                   failed_compile_tokens=0.0, attempts=0, admissions=0,
                   reactive_uses=0, program_uses=0, successes=0,
                   silent_failures=0, breaks=0, evictions=0, relists=0)
    events = []
    for t, family in enumerate(stream):
        while heap and heap[0][0] < t:
            deadline, expired = heapq.heappop(heap)
            expired_state = states[expired]
            if expired_state.listed and expired_state.last_use + ttl == deadline:
                expired_state.listed = False
                library -= 1
                metrics["evictions"] += 1
        state = states.setdefault(family, reference.State(t))
        state.arrivals += 1
        cfg = profiles[mapping[family]]
        c, d, C, C_fail = (cfg[k] for k in ("c", "d", "C", "C_fail"))
        q, pi = cfg["q"], cfg["pi"]
        baseline += c + tau0
        if state.alive and not state.listed:
            state.listed = True
            library += 1
            metrics["relists"] += 1
        route = tau0 + m * library
        metrics["router_tokens"] += route
        paid += route
        program = state.alive and state.listed
        reactive = not program
        success = False
        observed_cost = 0.0
        if program:
            metrics["program_uses"] += 1
            metrics["extraction_tokens"] += d
            observed_cost += d
            paid += d
            died = (not state.valid) or reference.coin(seed, family, state.arrivals, "drift") < h
            if died:
                state.valid = False
            failed = died or reference.coin(seed, family, state.arrivals, "program") < q
            if failed:
                reactive = True
                if died:
                    state.alive = False
                    state.listed = False
                    library -= 1
                    metrics["breaks"] += 1
                    state.successes_since_admission = 0
                    state.excess = 0.0
            else:
                success = True
            if state.listed:
                state.last_use = t
                heapq.heappush(heap, (t + ttl, family))
        if reactive:
            charge = c * (fallback_mult if program else 1)
            metrics["reactive_tokens"] += charge
            observed_cost += charge
            paid += charge
            metrics["reactive_uses"] += 1
            state.demos += 1
            success = reference.coin(seed, family, state.arrivals, "reactive") < pi
            state.successes_since_admission += int(success)
        if program:
            state.saving += c - observed_cost
        metrics["successes"] += int(success)
        act = propose(policy, demos=state.demos, alive=state.alive,
                      attempts=state.attempts, admits=state.admits,
                      c=c, d=d, C=C, C_fail=C_fail, q=q, h=h,
                      fallback_mult=fallback_mult, k_min=k_min)
        p_est = (state.admits + 1.0) / (state.attempts + 2.0)
        admitted = None
        if act:
            admitted = reference.coin(seed, family, state.attempts, "compile") < cfg["p"]
            state.attempts += 1
            metrics["attempts"] += 1
            charge = C if admitted else C_fail
            metrics["compile_tokens"] += charge
            paid += charge
            if admitted:
                state.admits += 1
                metrics["admissions"] += 1
                state.alive = True
                state.valid = True
                state.listed = True
                state.last_use = t
                state.excess = 0.0
                state.successes_since_admission = 0
                library += 1
                heapq.heappush(heap, (t + ttl, family))
            else:
                state.failed_spend += charge
                metrics["failed_compile_tokens"] += charge
        worst_ratio = max(worst_ratio, paid / baseline if baseline else 1.0)
        if trace:
            events.append(dict(t=t, family=family, program=program, reactive=reactive,
                               success=success, demos=state.demos, attempt=act,
                               admitted=admitted, service_cost=observed_cost,
                               p_est=p_est, paid=paid, baseline=baseline,
                               library=library, controller_saving=state.saving))
    metrics["token_cost"] = paid
    metrics["tokens"] = paid
    metrics["baseline_cost"] = baseline
    metrics["max_prefix_ratio"] = worst_ratio
    metrics["success_rate"] = metrics["successes"] / len(stream) if stream else 0.0
    metrics["arrivals"] = len(stream)
    metrics["final_library"] = library
    if trace:
        metrics["events"] = events
    return metrics
