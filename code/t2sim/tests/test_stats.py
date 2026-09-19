"""Unit tests of the bootstrap CI machinery in t2sim/stats.py."""
from __future__ import annotations

import random

import pytest

import stats


def test_mean():
    assert stats.mean([1.0, 2.0, 3.0]) == 2.0
    assert stats.mean([5.0]) == 5.0


def test_mean_empty_raises():
    with pytest.raises(ZeroDivisionError):
        stats.mean([])


def test_percentile_linear_interpolation():
    v = [0.0, 1.0, 2.0, 3.0]
    assert stats._percentile(v, 0.0) == 0.0
    assert stats._percentile(v, 1.0) == 3.0
    assert stats._percentile(v, 0.5) == 1.5
    assert stats._percentile([4.0], 0.25) == 4.0


def test_bootstrap_mean_ci_constant_sample():
    lo, hi = stats.bootstrap_mean_ci([7.0] * 10, B=50, seed=1)
    assert (lo, hi) == (7.0, 7.0)


def test_bootstrap_mean_ci_brackets_mean_and_ordered():
    xs = [random.Random(i).random() * 100 for i in range(40)]
    m = stats.mean(xs)
    lo, hi = stats.bootstrap_mean_ci(xs, B=500, seed=3)
    assert lo <= m <= hi
    assert lo < hi                       # a spread sample has spread


def test_bootstrap_mean_ci_deterministic():
    xs = [1.0, 5.0, 9.0, 2.0, 7.5, 3.3, 8.1, 4.4]
    a = stats.bootstrap_mean_ci(xs, B=200, seed=42)
    b = stats.bootstrap_mean_ci(xs, B=200, seed=42)
    c = stats.bootstrap_mean_ci(xs, B=200, seed=43)
    assert a == b
    assert a == c or True   # different seed may move it; not asserted


def test_bootstrap_ratio_ci_identical_rows():
    xs = list(range(1, 11))
    lo, hi = stats.bootstrap_ratio_ci(xs, list(xs), B=100, seed=0)
    assert (lo, hi) == (1.0, 1.0)


def test_bootstrap_ratio_ci_proportional_rows():
    xs = [10.0, 20.0, 30.0, 40.0]
    lo, hi = stats.bootstrap_ratio_ci([2 * x for x in xs], xs, B=100, seed=0)
    assert (lo, hi) == (2.0, 2.0)        # every resample keeps the 2:1 pairing


def test_bootstrap_ratio_ci_tight_when_paired():
    """joint resampling: a small constant offset on top of a noisy
    denominator gives a near-degenerate ratio CI; the naive unpaired
    interval (independent CIs divided through) is orders of magnitude
    wider, which is the point of sharing resample indices."""
    rng = random.Random(0)
    ys = [800.0 + 400.0 * rng.random() for _ in range(30)]
    xs = [y + 1.0 for y in ys]        # every rep pair: x = y + 1
    lo, hi = stats.bootstrap_ratio_ci(xs, ys, B=300, seed=5)
    # the statistic is the ratio of means: 1 + 1/mean(ys_resampled)
    assert 1.0008 <= lo <= hi <= 1.0013
    assert hi - lo < 0.0003
    ci_x = stats.bootstrap_mean_ci(xs, B=300, seed=6)
    ci_y = stats.bootstrap_mean_ci(ys, B=300, seed=7)
    naive_width = ci_x[1] / ci_y[0] - ci_x[0] / ci_y[1]
    assert (hi - lo) * 20.0 < naive_width


def test_bootstrap_empty_and_zero_inputs():
    lo, hi = stats.bootstrap_mean_ci([], B=10)
    assert lo != lo and hi != hi          # NaN
    lo, hi = stats.bootstrap_ratio_ci([1.0, 2.0], [0.0, 0.0], B=10)
    assert lo != lo and hi != hi          # NaN


def test_paired_diff_summary():
    d = stats.paired_diff_summary([1.0, 2.0, 3.0], [1.0, 2.0, 3.0])
    assert d["mean_diff"] == 0.0
    assert d["se"] == 0.0
    d2 = stats.paired_diff_summary([6.0, 7.0, 8.0, 9.0],
                                   [4.0, 5.0, 6.0, 7.0])
    assert d2["mean_diff"] == 2.0          # constant difference of 2
    assert d2["se"] == 0.0                 # ... so no paired noise
    d3 = stats.paired_diff_summary([3.0, 5.0], [1.0, 1.0])
    assert d3["mean_diff"] == 3.0
    assert d3["se"] == pytest.approx(1.0)  # sd of (2,4) = sqrt(2); /sqrt(2)
