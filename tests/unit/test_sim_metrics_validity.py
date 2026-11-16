"""Tests for outcome metrics, validity flags, operations config, and the demo CLI."""

from __future__ import annotations

import copy
from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError

from beyond_the_wait.analysis.metrics import (
    patient_summary,
    resource_summary,
    throughput_summary,
)
from beyond_the_wait.config import SiteProfile
from beyond_the_wait.simulation.demo import main
from beyond_the_wait.simulation.engine import SimulationSettings, run_simulation
from beyond_the_wait.simulation.validity import offered_load_checks, offered_loads, run_checks
from tests.sim_helpers import durations, patients, settings, tiny_site


@pytest.fixture(scope="module")
def baseline():  # noqa: ANN201
    from beyond_the_wait.config import load_site_profile
    from tests.paths import EXAMPLE_SITE_PATH

    site = load_site_profile(EXAMPLE_SITE_PATH)
    s = SimulationSettings(start_date=date(2026, 11, 16), arrival_days=4, warmup_days=1, seed=11)
    return site, run_simulation(site, s)


# --- Metrics -------------------------------------------------------------------------------


def test_patient_summary_is_simulated_and_grouped(baseline) -> None:  # noqa: ANN001
    _, r = baseline
    s = patient_summary(r)
    assert set(s["provenance"]) == {"SIMULATED"}
    los = s[s["column"] == "los_min"].set_index("group")
    assert set(los.index) == {"all", "discharged", "admitted"}
    assert (
        los.loc["all", "patients"]
        == los.loc["discharged", "patients"] + los.loc["admitted", "patients"]
    )
    assert los.loc["admitted", "median_min"] > los.loc["discharged", "median_min"]
    boarding = s[s["column"] == "boarding_min"]
    assert list(boarding["group"]) == ["admitted"]


def test_patient_summary_uses_only_measured_completed_visits(baseline) -> None:  # noqa: ANN001
    _, r = baseline
    s = patient_summary(r)
    p = r.patients
    expected = int(((p["cohort"] == "measured") & (p["status"] == "completed")).sum())
    n = s[(s["column"] == "los_min") & (s["group"] == "all")]["patients"].iloc[0]
    assert n == expected < len(p)  # warm-up patients excluded


def test_resource_summary(baseline) -> None:  # noqa: ANN001
    _, r = baseline
    rs = resource_summary(r).set_index("resource")
    assert set(rs["provenance"]) == {"SIMULATED"}
    for name in ("physician_slots", "triage_nurses", "acute_beds", "fast_track_chairs"):
        assert 0 <= rs.loc[name, "utilization"] <= 1.05, name
    # 4/4/2 physicians x 2 concurrent slots -> (8*8 + 8*8 + 4*8)/24 = 6.67 average slots.
    assert rs.loc["physician_slots", "average_capacity"] == pytest.approx(160 / 24)
    assert rs.loc["boarders_waiting_for_inpatient_bed", "mean_queue"] >= 0


def test_throughput_summary(baseline) -> None:  # noqa: ANN001
    _, r = baseline
    t = throughput_summary(r)
    assert t["measured_days"] == 3
    assert t["arrivals_measured"] == t["completed_measured"] + t["unfinished_measured"]
    assert 150 < t["arrivals_per_day"] < 240
    assert (
        t["beds_released"]
        == t["beds_claimed"] + t["beds_lost_to_other_demand"] + r.beds.available - 2
    )


def test_metrics_with_zero_patients(site_dict: dict[str, Any]) -> None:
    r = run_simulation(
        tiny_site(site_dict),
        settings(days=2, warmup=1),
        patients=patients(),
        durations=[],
        bed_release_times=[],
    )
    s = patient_summary(r)
    assert (s["patients"] == 0).all()
    rs = resource_summary(r).set_index("resource")
    assert rs.loc["physician_slots", "utilization"] == 0
    assert throughput_summary(r)["arrivals_measured"] == 0


# --- Validity flags -------------------------------------------------------------------------


def test_baseline_offered_loads(baseline) -> None:  # noqa: ANN001
    site, _ = baseline
    loads = offered_loads(site)
    assert loads["physician_average"] == pytest.approx(0.70, abs=0.01)
    assert loads["inpatient_bed_average"] == pytest.approx(37.7 / 42, abs=0.01)
    codes = {f.code for f in offered_load_checks(site)}
    assert "inpatient_bed_near_saturation" in codes
    assert not any(c.endswith("_saturated") for c in codes)


def test_one_patient_per_physician_is_flagged_saturated(site_dict: dict[str, Any]) -> None:
    site_dict["operations"]["physician_concurrent_patients"] = 1
    site = SiteProfile.model_validate(site_dict)
    flags = {f.code: f for f in offered_load_checks(site)}
    assert flags["physician_saturated"].level == "critical"


def test_bed_releases_below_admissions_flagged(site_dict: dict[str, Any]) -> None:
    site_dict["operations"]["inpatient_bed_availability"]["releases_per_day"] = 30
    flags = {f.code for f in offered_load_checks(SiteProfile.model_validate(site_dict))}
    assert "inpatient_bed_saturated" in flags


def test_growing_queue_detected(site_dict: dict[str, Any]) -> None:
    site_dict["operations"]["inpatient_bed_availability"]["releases_per_day"] = 25
    site = SiteProfile.model_validate(site_dict)
    s = SimulationSettings(start_date=date(2026, 11, 16), arrival_days=5, warmup_days=1, seed=2)
    codes = {f.code for f in run_checks(run_simulation(site, s))}
    assert "growing_queue" in codes


def test_priority_overload_is_detected_even_when_median_los_looks_flat(
    site_dict: dict[str, Any],
) -> None:
    # Regression: with physicians limited to one patient at a time, strict
    # priority pushes the backlog onto ESI 4-5 patients. Median LOS by arrival
    # day stays flat (it even falls), which fooled an earlier LOS-based check.
    site_dict["operations"]["physician_concurrent_patients"] = 1
    site = SiteProfile.model_validate(site_dict)
    s = SimulationSettings(start_date=date(2026, 11, 16), arrival_days=8, warmup_days=1, seed=42)
    r = run_simulation(site, s)
    codes = {f.code for f in run_checks(r)}
    assert {"growing_queue", "extreme_stays"} <= codes
    low = r.patients[(r.patients["cohort"] == "measured") & (r.patients["acuity_esi"] >= 4)]
    assert low["los_min"].median() > 24 * 60  # low-acuity patients starved


def test_baseline_has_no_growth_or_extreme_stay_flags(baseline) -> None:  # noqa: ANN001
    _, r = baseline
    codes = {f.code for f in run_checks(r)}
    assert not {"growing_queue", "extreme_stays"} & codes


def test_every_run_carries_single_seed_reminder(baseline) -> None:  # noqa: ANN001
    _, r = baseline
    flags = run_checks(r)
    assert flags[-1].code == "single_seed" and flags[-1].level == "info"
    assert "hard_stop_reached" not in {f.code for f in flags}


def test_short_run_flagged(site_dict: dict[str, Any]) -> None:
    r = run_simulation(
        tiny_site(site_dict),
        settings(days=2, warmup=1),
        patients=patients((1500, 3, False, False, False)),
        durations=durations(1),
        bed_release_times=[],
    )
    assert "short_run" in {f.code for f in run_checks(r)}


# --- Settings and operations config --------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"arrival_days": 0}, "greater than or equal to 1"),
        ({"warmup_days": 3}, "warmup_days must be smaller"),
        ({"seed": -1}, "greater than or equal to 0"),
        ({"drain_hours": 10_000}, "less than or equal to 336"),
    ],
)
def test_settings_validation(kwargs: dict[str, Any], message: str) -> None:
    base = {"start_date": date(2026, 11, 16), "arrival_days": 3, "warmup_days": 1, "seed": 1}
    with pytest.raises(ValidationError, match=message):
        SimulationSettings(**(base | kwargs))


def _ops_invalid(site_dict: dict[str, Any], edit, message: str) -> None:  # noqa: ANN001
    d = copy.deepcopy(site_dict)
    edit(d["operations"], d)
    with pytest.raises(ValidationError, match=message):
        SiteProfile.model_validate(d)


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (lambda o, d: o["triage_nurses_on_duty"].update(night=11), "exceeds the 10 nurses"),
        (lambda o, d: o["triage_nurses_on_duty"].update(night=0), "at least one triage nurse"),
        (lambda o, d: o["triage_nurses_on_duty"].pop("day"), "must list exactly the shifts"),
        (lambda o, d: o.update(physician_concurrent_patients=0), "greater than or equal to 1"),
        (lambda o, d: o.update(physician_concurrent_patients=5), "less than or equal to 4"),
        (lambda o, d: o["treatment_space_eligibility"].pop(3), "missing: \\[3\\]"),
        (lambda o, d: o["treatment_space_eligibility"].update({3: []}), "at least one space type"),
        (
            lambda o, d: o["treatment_space_eligibility"].update({3: ["acute_beds", "acute_beds"]}),
            "more than once",
        ),
        (
            lambda o, d: o["treatment_space_eligibility"].update({3: ["helipad"]}),
            "treatment_space_eligibility",
        ),
        (
            lambda o, d: d["treatment_spaces"].update(
                resuscitation_bays=0, acute_beds=0, hallway_spaces=0
            ),
            "zero capacity",
        ),
        (
            lambda o, d: o["inpatient_bed_availability"]["hourly_release_profile"].__setitem__(
                0, 0.5
            ),
            "must sum to 1.0",
        ),
        (
            lambda o, d: o["inpatient_bed_availability"].update(initial_available_beds=9),
            "cannot exceed max_unclaimed_beds",
        ),
        (lambda o, d: o["inpatient_bed_availability"].update(model="beds_x_occupancy"), "model"),
        (lambda o, d: o["inpatient_bed_availability"].update(releases_per_day=0), "greater than 0"),
        (lambda o, d: o.pop("source_note"), "source_note"),
    ],
)
def test_operations_validation(site_dict: dict[str, Any], edit, message: str) -> None:  # noqa: ANN001
    _ops_invalid(site_dict, edit, message)


# --- Demo CLI ---------------------------------------------------------------------------------


def test_demo_prints_labeled_results_and_flags(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--days", "3", "--warmup-days", "1", "--seed", "5", "--sample", "2"]) == 0
    out = capsys.readouterr().out
    assert "SIMULATED OUTCOMES" in out and "Not comparable to CMS/NYS" in out
    assert "Pre-run capacity check" in out and "Validity flags" in out
    assert "single_seed" in out and "ED length of stay" in out


def test_demo_rejects_bad_settings(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--days", "1", "--warmup-days", "1"]) == 1
    assert "ERROR" in capsys.readouterr().err


def test_demo_clock_handles_missing_times() -> None:
    from beyond_the_wait.simulation.demo import _clock

    assert _clock(float("nan"), date(2026, 11, 16)).strip() == "--"
    assert _clock(1450, date(2026, 11, 16)) == "Tue 00:10"
