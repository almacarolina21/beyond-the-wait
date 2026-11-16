"""Paired comparison statistics for simulation experiments.

The method: paired t confidence interval for a MEAN DIFFERENCE
    For each seed i we run the baseline and the scenario with the same random
    inputs, compute a metric for each (e.g., median length of stay), and take
    the difference d_i = scenario_i - baseline_i. Pairing removes most of the
    seed-to-seed luck, because both runs saw the same patients.

        mean  = average of d_i
        sd    = sample standard deviation of d_i
        se    = sd / sqrt(n)
        95% CI = mean +/- t(0.975, n-1) x se

Assumptions (and why they're reasonable here)
    * Seeds are independent replications, so the d_i are independent.
    * The AVERAGE of the d_i is approximately normal. With ~30 replications
      this holds for moderately skewed differences (central limit theorem);
      we report the skewness of the d_i so strongly skewed cases can be
      flagged.
    * The metric is defined the same way in every replication.

What the CI is -- and is not
    The 95% CI describes uncertainty about the AVERAGE effect over many runs
    of this fictional model. It is NOT a range for a single future run: one
    future run's difference varies much more. That range is the PREDICTION
    INTERVAL, mean +/- t x sd x sqrt(1 + 1/n), which we report separately so
    the two are never confused. Neither one says anything about a real
    hospital.

t critical values are computed without SciPy: exact table values for small
degrees of freedom, and a Cornish-Fisher expansion of the normal quantile
for df >= 8 (accurate to better than 1e-4; checked against published tables
in the tests).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

Z_975 = 1.959963984540054

# Two-sided 95% (upper 0.975) Student t quantiles for small df (standard tables).
_T975_TABLE = {
    1: 12.706204736,
    2: 4.302652730,
    3: 3.182446305,
    4: 2.776445105,
    5: 2.570581836,
    6: 2.446911851,
    7: 2.364624252,
}


def t_critical_975(df: int) -> float:
    """Upper 0.975 quantile of Student's t with `df` degrees of freedom."""
    if df < 1:
        raise ValueError("degrees of freedom must be >= 1")
    if df in _T975_TABLE:
        return _T975_TABLE[df]
    z, v = Z_975, float(df)
    return (
        z
        + (z**3 + z) / (4 * v)
        + (5 * z**5 + 16 * z**3 + 3 * z) / (96 * v**2)
        + (3 * z**7 + 19 * z**5 + 17 * z**3 - 15 * z) / (384 * v**3)
        + (79 * z**9 + 776 * z**7 + 1482 * z**5 - 1920 * z**3 - 945 * z) / (92160 * v**4)
    )


@dataclass(frozen=True)
class PairedEstimate:
    """Summary of paired differences d_i = scenario_i - baseline_i."""

    n: int
    baseline_mean: float
    scenario_mean: float
    mean_difference: float
    sd_difference: float
    ci_low: float
    ci_high: float
    prediction_low: float
    prediction_high: float
    skewness: float

    @property
    def ci_half_width(self) -> float:
        return (self.ci_high - self.ci_low) / 2

    @property
    def ci_excludes_zero(self) -> bool:
        return self.ci_low > 0 or self.ci_high < 0


def paired_estimate(baseline: Sequence[float], scenario: Sequence[float]) -> PairedEstimate:
    """Paired t estimate. Inputs must be the same length, aligned by seed."""
    b = np.asarray(baseline, dtype=float)
    s = np.asarray(scenario, dtype=float)
    if b.shape != s.shape:
        raise ValueError("baseline and scenario must have the same number of paired values")
    if b.size < 2:
        raise ValueError("need at least 2 paired replications for a confidence interval")
    if not (np.isfinite(b).all() and np.isfinite(s).all()):
        raise ValueError("paired values must be finite; handle missing values explicitly")
    d = s - b
    n = d.size
    mean = float(d.mean())
    sd = float(d.std(ddof=1))
    t = t_critical_975(n - 1)
    half = t * sd / math.sqrt(n)
    pred_half = t * sd * math.sqrt(1 + 1 / n)
    skew = float(((d - mean) ** 3).mean() / sd**3) if sd > 0 else 0.0
    return PairedEstimate(
        n=n,
        baseline_mean=float(b.mean()),
        scenario_mean=float(s.mean()),
        mean_difference=mean,
        sd_difference=sd,
        ci_low=mean - half,
        ci_high=mean + half,
        prediction_low=mean - pred_half,
        prediction_high=mean + pred_half,
        skewness=skew,
    )
