"""Versioned, serve-before-compile simulation for the September revision.

Costs/hazard are supplied scenario inputs. Only arrival and admission rates
are estimated. This module makes no fully-online or competitive guarantee.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import heapq
import math

POLICIES = ('reactive', 'earliest', 'earliest_cap', 'fixed10_cap',
            'success10_cap', 'breakeven_cap', 'projected', 'projected_cap')


def coin(seed, family, index, channel):
    key = f'{seed}|{family}|{index}|{channel}'.encode()
    return int.from_bytes(hashlib.blake2b(key, digest_size=8).digest(), 'big') / 2**64


def expected_buy(C, C_fail, p):
    return C + (1 / p - 1) * C_fail if p > 0 else math.inf


@dataclass
class State:
    first: int
    arrivals: int = 0
    demos: int = 0
    successes_since_admission: int = 0
    attempts: int = 0
    admits: int = 0
    alive: bool = False
    valid: bool = False
    listed: bool = False
    last_use: int = -1
    failed_spend: float = 0.0
    saving: float = 0.0
    excess: float = 0.0


def run(stream, profiles, mapping, policy, *, seed=7, ttl=100, k_min=3,
        h=0.02, m=91.0, tau0=368.0, silent=0.0, penalty=3.0,
        fallback_mult=1.0, trace=False):
    """All policies share outcomes indexed by family arrival/attempt.

    Family IDs and free archived-artifact lookup are supplied. Expiration
    removes a manifest entry, not the artifact. A recurring valid artifact
    is relisted before routing, for all policies. Breaks occur per active use.
    Silent failures incur an explicit harm penalty and no free fallback.
    """
    if policy not in POLICIES:
        raise ValueError(policy)
    if k_min < 1 or ttl < 1 or not 0 <= h <= 1 or not 0 <= silent <= 1:
        raise ValueError('invalid deployment parameters')
    states = {}
    heap = []
    library = 0
    metrics = dict(reactive_tokens=0.0, extraction_tokens=0.0,
                   compile_tokens=0.0, router_tokens=0.0, harm_tokens=0.0,
                   failed_compile_tokens=0.0, attempts=0, admissions=0,
                   reactive_uses=0, program_uses=0, successes=0,
                   silent_failures=0, breaks=0, evictions=0, relists=0)
    events = []
    for t, family in enumerate(stream):
        while heap and heap[0][0] < t:
            deadline, expired = heapq.heappop(heap)
            st = states[expired]
            if st.listed and st.last_use + ttl == deadline:
                st.listed = False
                library -= 1
                metrics['evictions'] += 1
        f = states.setdefault(family, State(t))
        f.arrivals += 1
        cfg = profiles[mapping[family]]
        c, d, C, Cf = (cfg[k] for k in ('c', 'd', 'C', 'C_fail'))
        q, pi = cfg['q'], cfg['pi']
        fail_rate = h + (1-h)*q
        saving = c - d - fail_rate*c*(silent*penalty + (1-silent)*fallback_mult)
        if f.alive and not f.listed and policy != 'reactive':
            f.listed = True
            library += 1
            metrics['relists'] += 1
        metrics['router_tokens'] += tau0 + m*library
        program = policy != 'reactive' and f.alive and f.listed
        reactive = not program
        success = False
        service_cost = 0.0
        if program:
            metrics['program_uses'] += 1
            metrics['extraction_tokens'] += d
            service_cost += d
            died = (not f.valid) or coin(seed,family,f.arrivals,'drift') < h
            if died:
                f.valid = False
            failed = died or coin(seed,family,f.arrivals,'program') < q
            is_silent = failed and coin(seed,family,f.arrivals,'silent') < silent
            if is_silent:
                metrics['silent_failures'] += 1
                metrics['harm_tokens'] += penalty*c
                service_cost += penalty*c
            elif failed:
                reactive = True
                if died:
                    f.alive = False
                    f.listed = False
                    library -= 1
                    metrics['breaks'] += 1
                    f.successes_since_admission = 0
                    f.excess = 0.0
            else:
                success = True
            if f.listed:
                f.last_use = t
                heapq.heappush(heap,(t+ttl,family))
        if reactive:
            charge = c * (fallback_mult if program else 1)
            metrics['reactive_tokens'] += charge
            service_cost += charge
            metrics['reactive_uses'] += 1
            f.demos += 1
            success = coin(seed,family,f.arrivals,'reactive') < pi
            f.successes_since_admission += int(success)
            if not program:
                f.excess += max(0.0,saving)
        if program:
            f.saving += c-service_cost
        metrics['successes'] += int(success)
        # Only observations through this arrival inform the compile decision.
        age = t-f.first
        lam = (f.arrivals-1+1.0)/(age+5.0)
        uses = lam*age
        if h>0:
            uses = min(uses,1/h)
        p_est = (f.admits+1.0)/(f.attempts+2.0)
        buy = expected_buy(C,Cf,p_est)
        allowance = max(0.0,f.saving)+max(0.0,uses*saving)
        act = False
        if policy != 'reactive' and not f.alive and f.demos >= k_min:
            if policy.startswith('earliest'):
                act = True
            elif policy=='fixed10_cap':
                act = f.arrivals>=10
            elif policy=='success10_cap':
                act = f.successes_since_admission>=10
            elif policy=='breakeven_cap':
                act = saving>0 and f.excess>=buy
            elif policy.startswith('projected'):
                # A heuristic allowance for one idle residency interval.
                act = saving>0 and uses*saving>buy+m*min(ttl,age)
            if policy.endswith('_cap'):
                # Reserve this attempt's failed cost before permitting it.
                act = act and f.failed_spend+Cf<=allowance
        admitted = None
        if act:
            admitted = coin(seed,family,f.attempts,'compile') < cfg['p']
            f.attempts += 1
            metrics['attempts'] += 1
            charge = C if admitted else Cf
            metrics['compile_tokens'] += charge
            if admitted:
                f.admits += 1
                metrics['admissions'] += 1
                f.alive = True
                f.valid = True
                f.listed = True
                f.last_use = t
                f.excess = 0.0
                f.successes_since_admission = 0
                library += 1
                heapq.heappush(heap,(t+ttl,family))
            else:
                f.failed_spend += charge
                metrics['failed_compile_tokens'] += charge
        if trace:
            events.append(dict(t=t,family=family,program=program,reactive=reactive,
                               success=success,demos=f.demos,attempt=act,
                               admitted=admitted,service_cost=service_cost,
                               p_est=p_est,expected_buy=buy,projected_uses=uses))
    metrics['tokens'] = sum(metrics[k] for k in ('reactive_tokens','extraction_tokens',
                            'compile_tokens','router_tokens','harm_tokens'))
    metrics['success_rate'] = metrics['successes']/len(stream) if stream else 0.0
    metrics['arrivals'] = len(stream)
    metrics['final_library'] = library
    if trace:
        metrics['events']=events
    return metrics
