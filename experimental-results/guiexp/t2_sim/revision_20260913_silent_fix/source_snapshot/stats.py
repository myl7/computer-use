"""Bootstrap statistics for the T2 tables.

Every cell of E3/E4 reports a mean plus a 95% bootstrap confidence interval
over its reps; ratio cells (everything relative to "ours") resample the rep
indices jointly for the policy and the reference so the pairing that the
paired seeding creates is not thrown away.

All functions are seeded and deterministic.
"""
from __future__ import annotations

import random


def mean(xs: list[float]) -> float:
    return sum(xs) / len(xs)


def _percentile(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return float("nan")
    i = q * (len(sorted_vals) - 1)
    lo = int(i)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = i - lo
    return sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac


def bootstrap_mean_ci(xs: list[float], B: int = 2000, alpha: float = 0.05,
                      seed: int = 0) -> tuple[float, float]:
    """Percentile bootstrap CI of the mean of xs."""
    n = len(xs)
    if n == 0:
        return float("nan"), float("nan")
    m = mean(xs)
    if all(x == xs[0] for x in xs):
        return m, m
    rng = random.Random(seed)
    stats_ = []
    for _ in range(B):
        s = sum(xs[rng.randrange(n)] for _ in range(n))
        stats_.append(s / n)
    stats_.sort()
    return (_percentile(stats_, alpha / 2),
            _percentile(stats_, 1.0 - alpha / 2))


def bootstrap_ratio_ci(xs: list[float], ys: list[float], B: int = 2000,
                       alpha: float = 0.05,
                       seed: int = 0) -> tuple[float, float]:
    """Percentile bootstrap CI of mean(xs) / mean(ys).

    The resample indices are shared between numerator and denominator: rep i
    of the policy and rep i of "ours" saw the same stream, and under paired
    seeding they started from the same RNG seed.  Note that a shared seed is
    not common random numbers -- policies consume different numbers of draws,
    so their coin sequences diverge after the first differing decision -- so
    the pairing removes stream variance, not coin variance.
    """
    n = len(xs)
    if n == 0 or mean(ys) == 0:
        return float("nan"), float("nan")
    rng = random.Random(seed)
    ratios = []
    for _ in range(B):
        num = den = 0.0
        for _ in range(n):
            i = rng.randrange(n)
            num += xs[i]
            den += ys[i]
        if den:
            ratios.append(num / den)
    if not ratios:
        r = mean(xs) / mean(ys)
        return r, r
    ratios.sort()
    return (_percentile(ratios, alpha / 2),
            _percentile(ratios, 1.0 - alpha / 2))


def paired_diff_summary(xs: list[float], ys: list[float]) -> dict:
    """Mean and standard error of the paired differences xs - ys."""
    n = len(xs)
    diffs = [x - y for x, y in zip(xs, ys)]
    m = mean(diffs) if diffs else float("nan")
    if n > 1:
        var = sum((d - m) ** 2 for d in diffs) / (n - 1)
        se = (var / n) ** 0.5
    else:
        se = float("nan")
    return {"mean_diff": m, "se": se}
