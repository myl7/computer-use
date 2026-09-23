"""Exploratory policies with a realized-cost safety constraint.

The reference simulator remains unchanged. Proposals use observable histories
and supplied costs/hazard, never the true admission probability or horizon.
"""
from __future__ import annotations
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import heapq
import math
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import revision_sim as reference


@dataclass(frozen=True)
class Policy:
    name: str
    proposer: str
    epsilon: float | None


POLICIES = (
    Policy('safe_history_025', 'history', .25),
    Policy('safe_history_100', 'history', 1.0),
    Policy('safe_once_025', 'once', .25),
    Policy('safe_once_100', 'once', 1.0),
)
POLICY_BY_NAME = {p.name: p for p in POLICIES}

def _beta_fraction(a, b, x):
    qab, qap, qam = a+b, a+1, a-1
    tiny = 1e-300
    c, d = 1., 1-qab*x/qap
    if abs(d)<tiny:d=tiny
    d=1/d
    value=d
    for k in range(1,301):
        twice=2*k
        aa=k*(b-k)*x/((qam+twice)*(a+twice))
        d=1+aa*d;c=1+aa/c
        if abs(d)<tiny:d=tiny
        if abs(c)<tiny:c=tiny
        d=1/d;value*=d*c
        aa=-(a+k)*(qab+k)*x/((a+twice)*(qap+twice))
        d=1+aa*d;c=1+aa/c
        if abs(d)<tiny:d=tiny
        if abs(c)<tiny:c=tiny
        d=1/d;step=d*c;value*=step
        if abs(step-1)<3e-14:return value
    raise ArithmeticError('incomplete beta did not converge')


def _beta_regularized(a,b,x):
    if x<=0:return 0.
    if x>=1:return 1.
    front=math.exp(math.lgamma(a+b)-math.lgamma(a)-math.lgamma(b)
                   +a*math.log(x)+b*math.log1p(-x))
    if x<(a+1)/(a+b+2):return front*_beta_fraction(a,b,x)/a
    return 1-front*_beta_fraction(b,a,1-x)/b


@lru_cache(maxsize=65536)
def upper_probability(successes: int, attempts: int, delta: float = .1) -> float:
    """One-sided 90% Clopper-Pearson upper bound, at an observed count.

    This is only an optimistic proposal. No simultaneous statistical guarantee
    is assigned to its repeated use; the cost guarantee comes from the ledger.
    """
    if attempts == 0 or successes == attempts:
        return 1.0
    if successes == 0:
        return 1.0 - delta ** (1.0 / attempts)
    low, high = successes / attempts, 1.0
    def cdf(p):
        return _beta_regularized(attempts-successes, successes+1, 1-p)
    for _ in range(42):
        mid = (low+high)/2
        if cdf(mid) > delta:
            low = mid
        else:
            high = mid
    return (low+high)/2


def propose(kind, *, demos, alive, arrivals, successes_since_admission,
            attempts, admits, age, c, d, C, Cf, q, h, m, ttl,
            silent, penalty, fallback_mult, k_min):
    """The entire policy-information boundary is explicit in this signature."""
    if alive or demos < k_min:
        return False
    if kind == 'earliest':
        return True
    if kind == 'success10':
        return successes_since_admission >= 10
    saving = c-d-(h+(1-h)*q)*c*(silent*penalty+(1-silent)*fallback_mult)
    if saving <= 0:
        return False
    if kind == 'projected':
        rate = arrivals/(age+5.0)
        uses = rate*age
        if h > 0:
            uses = min(uses,1/h)
        p = (admits+1)/(attempts+2)
        return uses*saving > reference.expected_buy(C,Cf,p)+m*min(ttl,age)
    if kind in ('optimistic','history','once'):
        if kind == 'once' and admits == 0 and attempts > 0:
            return False
        p = upper_probability(admits, attempts)
        lifetime = 1/h if h > 0 else math.inf
        if kind == 'optimistic':
            uses=lifetime
            overhead=m*ttl
        else:
            # Evidence-scaled opportunity estimate, still not known future use.
            uses=min(arrivals*age/(age+5.0),lifetime)
            overhead=m*min(ttl,age)
        return uses*saving > reference.expected_buy(C,Cf,p)+overhead
    raise ValueError(kind)


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
