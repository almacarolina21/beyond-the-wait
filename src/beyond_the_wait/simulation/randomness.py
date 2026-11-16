"""Random inputs for the simulation: task durations and inpatient bed releases.

Seeds and fair comparisons
    One seed drives everything. NumPy's SeedSequence splits it into child
    streams that never overlap:
        child 0: arrival times        (synthetic generator)
        child 1: patient attributes   (synthetic generator)
        child 2: task durations       (this module)
        child 3: inpatient bed releases (this module)
    Every patient gets ALL five durations drawn up front, in arrival order,
    whether or not they need that task. So a scenario that changes, say,
    staffing leaves each patient's task durations unchanged -- differences
    between scenarios then come from the scenario, not from luck
    ("common random numbers").
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from beyond_the_wait.config import SiteProfile
from beyond_the_wait.config.models import ESI_LEVELS, InpatientBedAvailability

DURATION_STREAM = 2
BED_RELEASE_STREAM = 3


def child_rng(seed: int, stream: int) -> np.random.Generator:
    """The `stream`-th independent random generator derived from `seed`."""
    children = np.random.SeedSequence(seed).spawn(stream + 1)
    return np.random.default_rng(children[stream])


@dataclass(frozen=True)
class PatientDurations:
    """Minutes each task WOULD take for one patient (work time, not waiting)."""

    triage: float
    provider: float
    lab: float
    imaging: float
    discharge: float

    def __post_init__(self) -> None:
        for name in ("triage", "provider", "lab", "imaging", "discharge"):
            value = getattr(self, name)
            if not value >= 0:  # also rejects NaN
                raise ValueError(f"{name} duration must be >= 0, got {value!r}")


def sample_durations(
    site: SiteProfile, acuity: np.ndarray, rng: np.random.Generator
) -> list[PatientDurations]:
    """Draw lognormal task durations for each patient (see LognormalMinutes)."""
    st = site.service_times
    n = len(acuity)

    def draw(spec) -> np.ndarray:  # noqa: ANN001
        return rng.lognormal(spec.mu, spec.sigma, size=n)

    triage = draw(st.triage)
    mus = np.array([st.provider_evaluation_by_acuity[a].mu for a in ESI_LEVELS])
    sigmas = np.array([st.provider_evaluation_by_acuity[a].sigma for a in ESI_LEVELS])
    idx = np.asarray(acuity, dtype=int) - 1
    provider = rng.lognormal(mus[idx], sigmas[idx]) if n else np.empty(0)
    lab = draw(st.lab_turnaround)
    imaging = draw(st.imaging_turnaround)
    discharge = draw(st.discharge_process)
    return [
        PatientDurations(float(t), float(p), float(lb), float(im), float(d))
        for t, p, lb, im, d in zip(triage, provider, lab, imaging, discharge, strict=True)
    ]


def sample_bed_release_times(
    availability: InpatientBedAvailability, horizon_minutes: float, rng: np.random.Generator
) -> np.ndarray:
    """Moments (minutes from start) when an inpatient bed becomes available to the ED.

    Time-varying Poisson stream, drawn hour by hour exactly like arrivals:
    rate in hour h = releases_per_day x hourly_release_profile[h].
    """
    hours = int(np.ceil(horizon_minutes / 60))
    profile = np.asarray(availability.hourly_release_profile, dtype=float)
    rates = availability.releases_per_day * profile[np.arange(hours) % 24]
    counts = rng.poisson(rates)
    hour_index = np.repeat(np.arange(hours), counts)
    times = (hour_index + rng.random(hour_index.size)) * 60.0
    times.sort()
    return times[times < horizon_minutes]
