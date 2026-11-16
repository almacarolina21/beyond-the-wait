"""Unit tests for resource allocators, random inputs, and one queueing-theory check."""

from __future__ import annotations

import math

import numpy as np
import pytest
import simpy

from beyond_the_wait.config import SiteProfile
from beyond_the_wait.config.models import LognormalMinutes
from beyond_the_wait.simulation.randomness import (
    PatientDurations,
    child_rng,
    sample_bed_release_times,
    sample_durations,
)
from beyond_the_wait.simulation.resources import (
    InpatientBedQueue,
    ResourceIntegrityError,
    ScheduledPool,
    StepSeries,
    TreatmentSpaceManager,
)

# --- StepSeries -----------------------------------------------------------------------


def test_step_series_integral_and_maximum() -> None:
    s = StepSeries(0, 0)
    s.set(10, 2)
    s.set(10, 3)  # same moment: overwrite
    s.set(20, 1)
    assert s.integral(0, 30) == 3 * 10 + 1 * 10
    assert s.integral(15, 25) == 3 * 5 + 1 * 5
    assert s.maximum(0, 30) == 3 and s.maximum(21, 30) == 1
    with pytest.raises(ResourceIntegrityError, match="backwards"):
        s.set(5, 1)


# --- ScheduledPool --------------------------------------------------------------------


def test_pool_rejects_bad_schedule_and_bad_release() -> None:
    env = simpy.Environment()
    with pytest.raises(ValueError, match="24 non-negative"):
        ScheduledPool(env, "x", [1] * 23)
    pool = ScheduledPool(env, "x", [1] * 24)
    with pytest.raises(ResourceIntegrityError, match="nobody busy"):
        pool.release()


def test_pool_priority_and_capacity() -> None:
    env = simpy.Environment()
    pool = ScheduledPool(env, "doc", [1] * 24)
    order: list[str] = []

    def patient(name: str, prio: tuple, arrive: float, work: float):  # noqa: ANN202
        yield env.timeout(arrive)
        yield pool.request(prio)
        order.append(name)
        yield env.timeout(work)
        pool.release()

    env.process(patient("first", (3, 0), 0, 10))
    env.process(patient("low", (5, 1), 1, 10))
    env.process(patient("high", (1, 2), 2, 10))
    env.run(until=100)
    assert order == ["first", "high", "low"]
    assert all(b <= c for _, b, c in pool.assignments)


def test_pool_capacity_follows_schedule_at_hour_boundaries() -> None:
    env = simpy.Environment()
    hours = [1] * 24
    hours[1] = 3  # 01:00-01:59
    pool = ScheduledPool(env, "doc", hours)
    starts: list[float] = []

    def patient():  # noqa: ANN202
        yield pool.request((0,))
        starts.append(env.now)
        yield env.timeout(200)
        pool.release()

    for _ in range(3):
        env.process(patient())
    env.run(until=300)
    assert starts == [0, 60, 60]
    assert pool.capacity_series.times[:3] == [0, 60, 120]


# --- TreatmentSpaceManager ----------------------------------------------------------------


def test_space_manager_preference_release_and_integrity() -> None:
    env = simpy.Environment()
    mgr = TreatmentSpaceManager(env, {"a": 1, "b": 1}, {1: ["a", "b"], 2: ["b"]})
    got: list[str] = []

    def patient(prio: tuple, esi: int):  # noqa: ANN202
        space = yield mgr.request(prio, esi)
        got.append(space)

    for prio, esi in [((1, 0), 1), ((1, 1), 1), ((2, 2), 2)]:
        env.process(patient(prio, esi))
    env.run(until=1)
    assert got == ["a", "b"]  # first takes preferred "a", second falls back to "b"
    assert mgr.queue_length == 1 and mgr.occupied("b") == 1
    mgr.release("b")
    env.run(until=2)
    assert got == ["a", "b", "b"]
    with pytest.raises(ResourceIntegrityError, match="nobody occupies"):
        TreatmentSpaceManager(env, {"a": 1}, {1: ["a"]}).release("a")


# --- InpatientBedQueue ---------------------------------------------------------------------


def test_bed_queue_banks_up_to_cap_and_loses_the_rest() -> None:
    env = simpy.Environment()
    beds = InpatientBedQueue(env, [10, 20, 30], initial_available=0, max_unclaimed=2)
    env.run(until=40)
    assert (beds.released, beds.available, beds.lost_to_other_demand) == (3, 2, 1)
    got = []

    def boarder():  # noqa: ANN202
        yield beds.request()
        got.append(env.now)

    env.process(boarder())
    env.run(until=41)
    assert got == [40] and beds.available == 1 and beds.claimed == 1


def test_bed_queue_validation_and_past_releases() -> None:
    env = simpy.Environment()
    with pytest.raises(ValueError, match="cannot exceed"):
        InpatientBedQueue(env, [], initial_available=3, max_unclaimed=1)
    env.run(until=50)
    beds = InpatientBedQueue(env, [10, 60], initial_available=0, max_unclaimed=5)
    env.run(until=100)
    assert beds.released == 1  # the release at t=10 is already in the past


# --- Random inputs ----------------------------------------------------------------------------


def test_patient_durations_must_be_nonnegative() -> None:
    with pytest.raises(ValueError, match="provider duration"):
        PatientDurations(triage=1, provider=-1, lab=0, imaging=0, discharge=0)
    with pytest.raises(ValueError, match="lab duration"):
        PatientDurations(triage=1, provider=1, lab=float("nan"), imaging=0, discharge=0)


def test_streams_are_independent_and_reproducible() -> None:
    a = child_rng(42, 2).random(5)
    np.testing.assert_array_equal(a, child_rng(42, 2).random(5))
    assert not np.allclose(a, child_rng(42, 3).random(5))
    # Stream 0 here is the same stream the synthetic generator uses for arrivals.
    arrivals_stream = np.random.default_rng(np.random.SeedSequence(42).spawn(2)[0])
    np.testing.assert_array_equal(child_rng(42, 0).random(3), arrivals_stream.random(3))


def test_sampled_durations_match_configured_medians(site: SiteProfile) -> None:
    acuity = np.full(20_000, 3)
    d = sample_durations(site, acuity, np.random.default_rng(0))
    provider = np.array([x.provider for x in d])
    triage = np.array([x.triage for x in d])
    assert np.median(provider) == pytest.approx(35, rel=0.03)
    assert np.quantile(provider, 0.9) == pytest.approx(70, rel=0.03)
    assert np.median(triage) == pytest.approx(8, rel=0.03)
    assert sample_durations(site, np.array([], dtype=int), np.random.default_rng(0)) == []


def test_bed_release_times_follow_daily_rate_and_profile(site: SiteProfile) -> None:
    cfg = site.operations.inpatient_bed_availability
    times = sample_bed_release_times(cfg, 200 * 1440, np.random.default_rng(1))
    assert len(times) / 200 == pytest.approx(42, abs=4 * math.sqrt(42 / 200))
    hours = (times // 60 % 24).astype(int)
    afternoon = np.isin(hours, [12, 13, 14, 15]).mean()
    assert afternoon == pytest.approx(0.38, abs=0.02)  # 0.09+0.10+0.10+0.09
    assert (np.diff(times) >= 0).all() and times.max() < 200 * 1440


# --- Queueing theory, ONLY where its assumptions hold --------------------------------------


def test_single_server_matches_pollaczek_khinchine() -> None:
    """M/G/1 check: Poisson arrivals, one server, constant capacity, FIFO,
    independent lognormal service times, long run with warm-up discarded.
    These are exactly the formula's assumptions, so the comparison is valid.
    (It would NOT be valid for the full ED: shifts, priorities, and finite
    days break them.)"""
    service = LognormalMinutes(median=8, p90=16)
    mean_s = service.mean
    second_moment = math.exp(2 * service.mu + 2 * service.sigma**2)
    rho = 0.6
    lam = rho / mean_s
    expected_wait = lam * second_moment / (2 * (1 - rho))  # Pollaczek-Khinchine

    rng = np.random.default_rng(2026)
    env = simpy.Environment()
    pool = ScheduledPool(env, "server", [1] * 24)
    waits: list[float] = []
    n = 40_000
    finished = env.event()

    def customer(arrival: float, work: float):  # noqa: ANN202
        yield env.timeout(arrival - env.now)
        yield pool.request((arrival,))
        waits.append(env.now - arrival)
        yield env.timeout(work)
        pool.release()
        if len(waits) == n and pool.busy == 0:
            finished.succeed()

    arrivals = np.cumsum(rng.exponential(1 / lam, n))
    works = rng.lognormal(service.mu, service.sigma, n)
    for t, w in zip(arrivals, works, strict=True):
        env.process(customer(t, w))
    # The pool's shift-schedule process never ends, so stop when the last
    # customer finishes (with a generous time limit as a safety net).
    env.run(until=env.any_of([finished, env.timeout(arrivals[-1] + 10_000)]))
    assert finished.triggered
    observed = float(np.mean(waits[n // 10 :]))
    assert observed == pytest.approx(expected_wait, rel=0.10)


def test_queue_length_properties() -> None:
    env = simpy.Environment()
    pool = ScheduledPool(env, "doc", [0] * 24)  # nobody on duty
    pool.request((1,))
    beds = InpatientBedQueue(env, [], initial_available=0, max_unclaimed=0)
    beds.request()
    assert pool.queue_length == 1 and beds.queue_length == 1
