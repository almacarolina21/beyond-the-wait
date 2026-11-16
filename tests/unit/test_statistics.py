"""Paired-difference statistics, checked against hand calculations and published tables."""

from __future__ import annotations

import math

import numpy as np
import pytest

from beyond_the_wait.analysis.statistics import paired_estimate, t_critical_975

# Published two-sided 95% Student t critical values.
PUBLISHED = {
    1: 12.706205,
    2: 4.302653,
    4: 2.776445,
    7: 2.364624,
    8: 2.306004,
    9: 2.262157,
    14: 2.144787,
    19: 2.093024,
    29: 2.045230,
    49: 2.009575,
    99: 1.984217,
}


@pytest.mark.parametrize(("df", "value"), PUBLISHED.items())
def test_t_critical_matches_published_tables(df: int, value: float) -> None:
    assert t_critical_975(df) == pytest.approx(value, abs=5e-5)


def test_t_critical_approaches_normal_and_rejects_bad_df() -> None:
    # t(0.975, 100000) is about 1.959988 (not yet exactly the normal 1.959964).
    assert 0 < t_critical_975(100_000) - 1.959964 < 3e-5
    with pytest.raises(ValueError):
        t_critical_975(0)


def test_hand_calculated_paired_interval() -> None:
    # d = 1,2,3,4,5: mean 3, sd sqrt(2.5)=1.5811, se 0.7071, t(4)=2.776445
    e = paired_estimate([0, 0, 0, 0, 0], [1, 2, 3, 4, 5])
    assert e.n == 5 and e.mean_difference == 3
    assert e.sd_difference == pytest.approx(math.sqrt(2.5))
    assert e.ci_low == pytest.approx(3 - 2.776445 * math.sqrt(2.5) / math.sqrt(5), abs=1e-5)
    assert e.ci_high == pytest.approx(4.963243, abs=1e-5)
    assert e.ci_excludes_zero


def test_pairing_removes_shared_noise() -> None:
    # Large seed-to-seed variation shared by both arms; constant true effect of -2.
    rng = np.random.default_rng(0)
    base = 100 + rng.normal(0, 30, 30)
    scen = base - 2 + rng.normal(0, 0.5, 30)
    e = paired_estimate(base, scen)
    assert e.ci_low < -2 < e.ci_high
    assert e.ci_half_width < 0.5  # an unpaired comparison would have half-width ~15


def test_prediction_interval_is_wider_than_confidence_interval() -> None:
    e = paired_estimate([10, 12, 9, 11, 10, 13], [8, 11, 9, 9, 7, 12])
    assert e.prediction_low < e.ci_low and e.prediction_high > e.ci_high
    ratio = (e.prediction_high - e.prediction_low) / (e.ci_high - e.ci_low)
    assert ratio == pytest.approx(math.sqrt(e.n + 1))  # sqrt(1 + 1/n) / sqrt(1/n)


def test_interval_including_zero() -> None:
    e = paired_estimate([1, 2, 3, 4], [2, 1, 4, 3])
    assert e.mean_difference == 0 and not e.ci_excludes_zero


def test_skewness_reported() -> None:
    e = paired_estimate([0] * 10, [0, 0, 0, 0, 0, 0, 0, 0, 0, 10])
    assert e.skewness > 2


@pytest.mark.parametrize(
    ("b", "s", "message"),
    [([1], [2], "at least 2"), ([1, 2], [1], "same number"), ([1, float("nan")], [1, 2], "finite")],
)
def test_invalid_inputs(b: list, s: list, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        paired_estimate(b, s)
