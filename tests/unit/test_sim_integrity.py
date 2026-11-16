"""Integrity checks on full, randomly generated runs of the fictional baseline."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from beyond_the_wait.config import SiteProfile, apply_scenario, load_scenario
from beyond_the_wait.data.provenance import Provenance, require_provenance
from beyond_the_wait.simulation.engine import SimulationSettings, run_simulation
from tests.paths import EXAMPLE_SCENARIO_PATH

SETTINGS = SimulationSettings(start_date=date(2026, 11, 16), arrival_days=3, warmup_days=1, seed=7)
ORDER = [
    "arrival_min", "triage_start_min", "triage_end_min", "space_start_min",
    "provider_start_min", "provider_end_min", "disposition_min", "departure_min",
]  # fmt: skip


@pytest.fixture(scope="module")
def run(request: pytest.FixtureRequest):  # noqa: ANN201
    from beyond_the_wait.config import load_site_profile
    from tests.paths import EXAMPLE_SITE_PATH

    site = load_site_profile(EXAMPLE_SITE_PATH)
    return site, run_simulation(site, SETTINGS)


def test_every_row_is_simulated(run) -> None:  # noqa: ANN001
    _, r = run
    require_provenance(r.patients, Provenance.SIMULATED)
    assert set(r.patients["provenance"]) == {"SIMULATED"}


def test_event_order_and_nonnegative_durations(run) -> None:  # noqa: ANN001
    _, r = run
    done = r.patients[r.patients["status"] == "completed"]
    assert len(done) > 400
    times = done[ORDER].to_numpy()
    assert (np.diff(times, axis=1) >= -1e-9).all()
    for col in (
        "triage_wait_min",
        "space_wait_min",
        "provider_wait_min",
        "arrival_to_provider_min",
        "los_min",
    ):
        assert (done[col] >= -1e-9).all(), col


def test_admission_and_discharge_paths_are_consistent(run) -> None:  # noqa: ANN001
    _, r = run
    done = r.patients[r.patients["status"] == "completed"]
    admitted = done[done["disposition"] == "admit"]
    discharged = done[done["disposition"] == "discharge"]
    assert len(admitted) > 50 and len(discharged) > 200
    assert admitted["boarding_min"].notna().all() and (admitted["boarding_min"] >= 0).all()
    assert (admitted["departure_min"] == admitted["bed_assigned_min"]).all()
    assert discharged["boarding_min"].isna().all()


def test_no_double_booking_anywhere(run) -> None:  # noqa: ANN001
    _, r = run
    assert all(busy <= cap for _, busy, cap in r.physicians.assignments)
    assert all(busy <= cap for _, busy, cap in r.triage_nurses.assignments)
    assert all(occ <= cap for _, _, occ, cap in r.spaces.assignments)
    for space_type, series in r.spaces.occupied_series.items():
        assert max(series.values) <= r.spaces.capacity[space_type]
        assert min(series.values) >= 0


def test_resources_conserved_after_everyone_leaves(run) -> None:  # noqa: ANN001
    _, r = run
    assert r.stop_reason == "all_departed"
    assert r.physicians.busy == 0 and r.triage_nurses.busy == 0
    assert r.spaces.free == r.spaces.capacity
    assert r.census.current == 0 and min(r.census.values) >= 0
    # Each patient was placed in exactly one space.
    assert len(r.spaces.assignments) == len(r.patients)
    # Every bed claim went to an admitted patient.
    assert r.beds.claimed == int((r.patients["disposition"] == "admit").sum())


def test_littles_law_holds_on_the_sample_path(run) -> None:  # noqa: ANN001
    # Exact identity (no steady-state assumption): area under "patients in ED"
    # over time equals the sum of every patient's time in the ED.
    _, r = run
    p = r.patients
    total_time = (p["departure_min"].fillna(r.stop_time) - p["arrival_min"]).sum()
    assert r.census.integral(0, r.stop_time) == pytest.approx(total_time)


def test_reproducible_with_same_seed(run) -> None:  # noqa: ANN001
    site, r = run
    again = run_simulation(site, SETTINGS)
    pd.testing.assert_frame_equal(r.patients, again.patients)
    np.testing.assert_array_equal(r.bed_release_times, again.bed_release_times)


def test_different_seed_gives_different_run(run) -> None:  # noqa: ANN001
    site, r = run
    other = run_simulation(site, SETTINGS.model_copy(update={"seed": 8}))
    assert (
        not r.patients["los_min"]
        .reset_index(drop=True)
        .equals(other.patients["los_min"].reset_index(drop=True))
    )


def test_common_random_numbers_across_a_staffing_scenario(run) -> None:  # noqa: ANN001
    # Adding an evening physician must not change who arrives or how long each
    # task takes -- only the waiting. That's what makes comparisons fair.
    site, r = run
    more = apply_scenario(site, load_scenario(EXAMPLE_SCENARIO_PATH))
    alt = run_simulation(more, SETTINGS)
    pd.testing.assert_series_equal(r.patients["arrival_min"], alt.patients["arrival_min"])
    exam = r.patients["provider_end_min"] - r.patients["provider_start_min"]
    alt_exam = alt.patients["provider_end_min"] - alt.patients["provider_start_min"]
    np.testing.assert_allclose(exam, alt_exam)
    np.testing.assert_array_equal(r.bed_release_times, alt.bed_release_times)
    # One seed, directional sanity only (not a policy conclusion):
    assert alt.patients["provider_wait_min"].sum() <= r.patients["provider_wait_min"].sum()


def test_warmup_cohort_is_labeled_not_dropped(run) -> None:  # noqa: ANN001
    _, r = run
    cohorts = r.patients.groupby("cohort")["arrival_min"].agg(["min", "max"])
    assert cohorts.loc["warm_up", "max"] < 1440 <= cohorts.loc["measured", "min"]


def test_saturated_configuration_runs_and_is_detectable(site_dict) -> None:  # noqa: ANN001
    # One patient at a time per physician: demand ~1.4x physician supply.
    site_dict["operations"]["physician_concurrent_patients"] = 1
    site = SiteProfile.model_validate(site_dict)
    s = SimulationSettings(start_date=date(2026, 11, 16), arrival_days=4, warmup_days=1,
                           drain_hours=12, seed=3)  # fmt: skip
    r = run_simulation(site, s)
    assert r.stop_reason == "hard_stop"
    assert (r.patients["status"] == "unfinished").sum() > 50
