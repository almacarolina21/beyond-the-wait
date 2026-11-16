"""Tests for the detailed metrics, with hand-checked shift-staffing examples."""

from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
import pandas as pd
import pytest

from beyond_the_wait.analysis import detailed_metrics as dm
from beyond_the_wait.config import SiteProfile
from beyond_the_wait.data.provenance import Provenance, require_provenance
from beyond_the_wait.simulation.engine import SimulationSettings, run_simulation
from beyond_the_wait.simulation.resources import StepSeries
from tests.sim_helpers import durations, patients, settings, tiny_site


@pytest.fixture(scope="module")
def baseline():  # noqa: ANN201
    from beyond_the_wait.config import load_site_profile
    from tests.paths import EXAMPLE_SITE_PATH

    site = load_site_profile(EXAMPLE_SITE_PATH)
    s = SimulationSettings(start_date=date(2026, 11, 16), arrival_days=4, warmup_days=1, seed=11)
    return run_simulation(site, s)


ALL_TABLES = (
    dm.acuity_metrics,
    dm.censoring_summary,
    dm.extreme_waits,
    dm.boarding_distribution,
    dm.stage_decomposition,
    dm.shift_utilization,
    dm.hourly_profile,
)


@pytest.mark.parametrize("fn", ALL_TABLES, ids=lambda f: f.__name__)
def test_every_table_is_simulated_with_units(baseline, fn) -> None:  # noqa: ANN001
    table = fn(baseline)
    require_provenance(table, Provenance.SIMULATED)
    assert "unit" in table.columns and table["unit"].notna().all()


def test_acuity_counts_add_up(baseline) -> None:  # noqa: ANN001
    am = dm.acuity_metrics(baseline)
    los = am[(am["measure"] == "los_min") & (am["disposition"] == "all")].set_index("acuity_esi")
    assert los.loc[["1", "2", "3", "4", "5"], "patients"].sum() == los.loc["all", "patients"]
    assert (los["p95"] >= los["p90"]).all() and (los["max"] >= los["p95"]).all()
    boarding = am[am["measure"] == "boarding_min"]
    assert set(boarding["disposition"]) == {"admit"}


def test_stage_decomposition_adds_up_to_los(baseline) -> None:  # noqa: ANN001
    sd = dm.stage_decomposition(baseline)
    p = baseline.patients
    done = p[(p["cohort"] == "measured") & (p["status"] == "completed")]
    for disposition in ("discharge", "admit"):
        g = sd[sd["disposition"] == disposition]
        mean_los = done.loc[done["disposition"] == disposition, "los_min"].mean()
        assert g["mean_min"].sum() == pytest.approx(mean_los)
        assert g["share_of_mean_los"].sum() == pytest.approx(1.0)
    assert set(sd["stage"]) >= {"boarding", "discharge process"}
    assert (sd["interpretation"].str.contains("not a bottleneck attribution")).all()


def test_boarding_distribution_by_decision_shift(baseline) -> None:  # noqa: ANN001
    bd = dm.boarding_distribution(baseline).set_index("group")
    shifts = [g for g in bd.index if g.startswith("decision during")]
    assert len(shifts) == 3
    assert bd.loc[shifts, "admitted_patients"].sum() == bd.loc["all", "admitted_patients"]
    assert 0 <= bd.loc["all", "share_over_2h"] <= 1


def test_hourly_profile_has_24_hours(baseline) -> None:  # noqa: ANN001
    hp = dm.hourly_profile(baseline)
    assert list(hp["hour"]) == list(range(24))
    assert hp["arrivals_per_day"].sum() == pytest.approx(
        (baseline.patients["cohort"] == "measured").sum() / 3
    )


def test_lwbs_marked_unavailable() -> None:
    assert "left_without_being_seen_rate" in dm.UNAVAILABLE_METRICS
    assert "invented" in dm.UNAVAILABLE_METRICS["left_without_being_seen_rate"]


def test_segments_split_on_changes_and_hours() -> None:
    a = StepSeries(0, 1)
    a.set(90, 2)
    segs = list(dm.segments([a], 30, 150))
    assert [(s, e, v) for s, e, v in segs] == [
        (30, 60, [1]),
        (60, 90, [1]),
        (90, 120, [2]),
        (120, 150, [2]),
    ]
    assert list(dm.segments([a], 0, 100, step=None)) == [(0, 90, [1]), (90, 100, [2])]


# --- Hand-checked shift staffing --------------------------------------------------------------


def test_shift_utilization_hand_checked(site_dict: dict[str, Any]) -> None:
    """Day shift has 2 physicians (1 slot each). Four patients arrive at 07:00,
    triage takes 5 min each (one nurse), exams take 60 min.

      triage ends   425, 430, 435, 440
      exams        425-485, 430-490, 485-545, 490-550
      busy = 2 from 430 to 545 (115 min), so time at >= 90% utilization = 115 min
      busy slot-minutes = 4 x 60 = 240; day capacity = 2 x 480 = 960 -> 0.25
      evaluations started in day shift = 4; physician-hours = 2 x 8 = 16 -> 0.25/hour
    """
    site = tiny_site(site_dict, physicians={"day": 2, "evening": 1, "night": 1})
    pts = patients(*[(420, 3, False, False, False)] * 4)
    r = run_simulation(
        site, settings(), patients=pts, durations=durations(4, provider=60), bed_release_times=[]
    )
    su = dm.shift_utilization(r).set_index(["resource", "shift"])
    day = su.loc[("physician_slots", "day")]
    assert day["people_on_duty"] == 2 and day["hours_observed"] == 8
    assert day["utilization"] == pytest.approx(0.25)
    assert day["share_time_at_or_above_90pct"] == pytest.approx(115 / 480)
    assert day["patients_started"] == 4 and day["patients_per_staffed_hour"] == pytest.approx(0.25)
    evening = su.loc[("physician_slots", "evening")]
    assert evening["utilization"] == 0 and evening["people_on_duty"] == 1
    triage = su.loc[("triage_nurses", "day")]
    assert triage["patients_started"] == 4 and triage["utilization"] == pytest.approx(20 / 480)
    nurses = su.loc[("all_nurses_on_duty (descriptive)", "day")]
    assert np.isnan(nurses["utilization"]) and nurses["patients_started"] == 4


def test_different_staffing_changes_per_hour_denominator(site_dict: dict[str, Any]) -> None:
    pts = patients(*[(420, 3, False, False, False)] * 4)
    results = {}
    for n in (1, 2, 4):
        site = tiny_site(site_dict, physicians={"day": n, "evening": 1, "night": 1})
        r = run_simulation(
            site,
            settings(),
            patients=pts,
            durations=durations(4, provider=60),
            bed_release_times=[],
        )
        day = (
            dm.shift_utilization(r).set_index(["resource", "shift"]).loc[("physician_slots", "day")]
        )
        results[n] = day["patients_per_staffed_hour"]
    assert results[1] == pytest.approx(4 / 8)
    assert results[2] == pytest.approx(4 / 16)
    assert results[4] == pytest.approx(4 / 32)


def test_unfinished_boarders_counted_in_boarding_distribution(site_dict: dict[str, Any]) -> None:
    site = tiny_site(site_dict)
    r = run_simulation(
        site,
        settings(drain_hours=0),
        patients=patients((0, 3, False, False, True)),
        durations=durations(1),
        bed_release_times=[],
    )
    bd = dm.boarding_distribution(r).set_index("group").loc["all"]
    assert bd["still_boarding_at_stop"] == 1
    assert bd["max"] == pytest.approx(1440 - 35)  # lower bound: boarding since minute 35


def test_result_without_site_cannot_compute_shift_metrics(baseline) -> None:  # noqa: ANN001
    import dataclasses

    with pytest.raises(ValueError, match="no site profile"):
        dm.shift_utilization(dataclasses.replace(baseline, site=None))


def test_empty_cohort_tables_do_not_crash(site_dict: dict[str, Any]) -> None:
    r = run_simulation(
        tiny_site(site_dict), settings(), patients=patients(), durations=[], bed_release_times=[]
    )
    for fn in ALL_TABLES:
        assert isinstance(fn(r), pd.DataFrame)


def test_site_profile_retained_on_result(baseline) -> None:  # noqa: ANN001
    assert isinstance(baseline.site, SiteProfile)
    assert baseline.n_input_patients == len(baseline.patients)
