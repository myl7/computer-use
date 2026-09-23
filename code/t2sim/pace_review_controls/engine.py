"""Matched-protection economic controls for the independent PACE review.

The serving loop is copied from protocol_explore/engine.py. The sole loop
change passes two additional observed fields to the proposal. All charges,
reservations, outcomes, and manifest actions remain identical.
"""
from __future__ import annotations
from dataclasses import dataclass
import heapq
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import revision_sim as reference


@dataclass(frozen=True)
class Policy:
    name: str
    proposer: str
    epsilon: float | None


POLICIES = (
    Policy("safe_earliest_allowance_025", "earliest_cap", .25),
    Policy("safe_arrival10_allowance_025", "fixed10_cap", .25),
    Policy("safe_projected_allowance_025", "projected_cap", .25),
    Policy("safe_count_025", "count", .25),
)
POLICY_BY_NAME = {p.name: p for p in POLICIES}
POLICY_BY_NAME["safe_projected_025"] = Policy("safe_projected_025", "projected", .25)


def propose(kind, *, demos, alive, arrivals, successes_since_admission,
            attempts, admits, age, failed_spend, realized_saving,
            c, d, C, Cf, q, h, m, ttl, silent, penalty, fallback_mult, k_min):
    """Use observed state and supplied parameters, without hidden outcomes."""
    if alive or demos < k_min:
        return False
    saving = c-d-(h+(1-h)*q)*c*(silent*penalty+(1-silent)*fallback_mult)
    rate = arrivals/(age+5.0)
    uses = float(arrivals) if kind == "count" else rate*age
    if h > 0:
        uses = min(uses, 1/h)
    p = (admits+1)/(attempts+2)
    buy = reference.expected_buy(C, Cf, p)
    if kind == "earliest_cap":
        act = True
    elif kind == "fixed10_cap":
        act = arrivals >= 10
    elif kind in ("projected", "projected_cap", "count"):
        act = saving > 0 and uses*saving > buy+m*min(ttl, age)
    else:
        raise ValueError(kind)
    if kind.endswith("_cap"):
        allowance = max(0.0, realized_saving)+max(0.0, uses*saving)
        act = act and failed_spend+Cf <= allowance
    return act


def run(stream, profiles, mapping, policy, *, seed=7, ttl=100, k_min=3,
        h=.02, m=91.0, tau0=368.0, silent=0.0, penalty=3.0,
        fallback_mult=1.0, trace=False):
    if isinstance(policy,str):
        policy=POLICY_BY_NAME[policy]
    if policy.epsilon is not None and policy.epsilon < 0:
        raise ValueError('epsilon must be nonnegative')
    if not 0<=h<=1 or not 0<=silent<=1 or min(m,tau0,fallback_mult)<0:
        raise ValueError('invalid world parameters')
    states, heap, library = {}, [], 0
    baseline, paid, worst_ratio = 0.0, 0.0, 0.0
    metrics=dict(reactive_tokens=0.0,extraction_tokens=0.0,compile_tokens=0.0,
                 router_tokens=0.0,harm_tokens=0.0,failed_compile_tokens=0.0,
                 attempts=0,admissions=0,reactive_uses=0,program_uses=0,
                 successes=0,silent_failures=0,breaks=0,evictions=0,relists=0,
                 safety_service_fallbacks=0,safety_compile_rejections=0,
                 proposed_attempts=0)
    events=[]
    for t,family in enumerate(stream):
        while heap and heap[0][0] < t:
            deadline, expired=heapq.heappop(heap)
            st=states[expired]
            if st.listed and st.last_use+ttl==deadline:
                st.listed=False;library-=1;metrics['evictions']+=1
        f=states.setdefault(family,reference.State(t))
        f.arrivals+=1
        cfg=profiles[mapping[family]]
        c,d,C,Cf=(cfg[k] for k in ('c','d','C','C_fail'))
        q,pi=cfg['q'],cfg['pi']
        baseline+=c+tau0
        limit=math.inf if policy.epsilon is None else (1+policy.epsilon)*baseline
        if f.alive and not f.listed:
            f.listed=True;library+=1;metrics['relists']+=1
        program=f.alive and f.listed
        reserve=tau0+m*library+(d+fallback_mult*c if program else c)
        if paid+reserve > limit+1e-8*max(1,limit):
            # Release all active entries before charging routing. Artifacts
            # remain stored and can be relisted on a later arrival.
            for st in states.values():
                st.listed=False
            library=0;heap.clear();program=False
            metrics['safety_service_fallbacks']+=1
        route=tau0+m*library
        metrics['router_tokens']+=route;paid+=route
        reactive=not program;success=False;observed=0.0
        if program:
            metrics['program_uses']+=1
            metrics['extraction_tokens']+=d;observed+=d;paid+=d
            died=(not f.valid) or reference.coin(seed,family,f.arrivals,'drift')<h
            if died:f.valid=False
            failed=died or reference.coin(seed,family,f.arrivals,'program')<q
            is_silent=failed and reference.coin(seed,family,f.arrivals,'silent')<silent
            if is_silent:
                metrics['silent_failures']+=1
                metrics['harm_tokens']+=penalty*c
            elif failed:
                reactive=True
                if died:
                    f.alive=False;f.listed=False;library-=1
                    metrics['breaks']+=1;f.successes_since_admission=0;f.excess=0.0
            else:success=True
            if f.listed:
                f.last_use=t;heapq.heappush(heap,(t+ttl,family))
        if reactive:
            charge=c*(fallback_mult if program else 1)
            metrics['reactive_tokens']+=charge;observed+=charge;paid+=charge
            metrics['reactive_uses']+=1;f.demos+=1
            success=reference.coin(seed,family,f.arrivals,'reactive')<pi
            f.successes_since_admission+=int(success)
        if program:f.saving+=c-observed
        metrics['successes']+=int(success)
        act=propose(policy.proposer,demos=f.demos,alive=f.alive,
                    arrivals=f.arrivals,successes_since_admission=f.successes_since_admission,
                    attempts=f.attempts,admits=f.admits,age=t-f.first,
                    failed_spend=f.failed_spend,realized_saving=f.saving,
                    c=c,d=d,C=C,Cf=Cf,q=q,h=h,m=m,ttl=ttl,
                    silent=silent,penalty=penalty,fallback_mult=fallback_mult,k_min=k_min)
        if act:metrics['proposed_attempts']+=1
        if act and paid+max(C,Cf)>limit+1e-8*max(1,limit):
            act=False;metrics['safety_compile_rejections']+=1
        admitted=None
        if act:
            # Only the environment reads the hidden probability.
            admitted=reference.coin(seed,family,f.attempts,'compile')<cfg['p']
            f.attempts+=1;metrics['attempts']+=1
            charge=C if admitted else Cf
            metrics['compile_tokens']+=charge;paid+=charge
            if admitted:
                f.admits+=1;metrics['admissions']+=1
                f.alive=True;f.valid=True;f.listed=True;f.last_use=t
                f.excess=0.0;f.successes_since_admission=0
                library+=1;heapq.heappush(heap,(t+ttl,family))
            else:
                f.failed_spend+=charge;metrics['failed_compile_tokens']+=charge
        ratio=paid/baseline if baseline else 1.0
        worst_ratio=max(worst_ratio,ratio)
        if policy.epsilon is not None and paid>limit+1e-8*max(1,limit):
            raise AssertionError(('prefix budget violated',t,paid,limit))
        if trace:
            events.append(dict(t=t,family=family,program=program,reactive=reactive,
                               success=success,demos=f.demos,attempt=act,admitted=admitted,
                               service_cost=observed,controller_saving=f.saving,
                               paid=paid,baseline=baseline,limit=limit,library=library))
    metrics['token_cost']=paid
    metrics['tokens']=paid+metrics['harm_tokens']
    metrics['baseline_cost']=baseline
    metrics['max_prefix_ratio']=worst_ratio
    metrics['success_rate']=metrics['successes']/len(stream) if stream else 0.0
    metrics['arrivals']=len(stream);metrics['final_library']=library
    if trace:metrics['events']=events
    return metrics
