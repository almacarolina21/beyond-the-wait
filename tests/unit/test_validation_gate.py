"""Adversarial and hand-checkable tests for the validation gate.

Corrupted outputs must be BLOCKED and impossible to release. Valid but
alarming results (overload stress tests, extreme waits, censoring) must be
CAUTION -- flagged, explained, and still available for diagnosis.
"""

from __future__ import annotations

import dataclasses
from datetime import date
from typing import Any

import numpy as np
import pytest

from beyond_the_wait.analysis import detailed_metrics as dm
from beyond_the_wait.analysis.validation import (
    BlockedResultError,
    GateStatus,
    release_for_interpretation,
    validate_run,
)
from beyond_the_wait.config import SiteProfile
from beyond_the_wait.simulation.engine import SimulationSettings, run_simulation
from tests.sim_helpers import durations, patients, row, settings, tiny_site


def _check(report, name: str):  # noqa: ANN001, ANN202
    return next(c for c in report.checks if c.check == name)


@pytest.fixture
def small_run(site: SiteProfile):  # noqa: ANN201
    """A fresh 3-day baseline run (fresh per test, so tests can corrupt it)."""
    s = SimulationSettings(start_date=date(2026, 11, 16), arrival_days=3, warmup_days=1, seed=7)
    return run_simulation(site, s)


def _with_patients(result, edit):  # noqa: ANN001, ANN202
    """Copy of `result` whose patient table is `edit(copy of table)`."""
    return dataclasses.replace(result, patients=edit(result.patients.copy()))


# Corruptions of the event log. Each returns a NEW, broken table.
def _set_cell(column: str, position: int, value):  # noqa: ANN001, ANN202
    def edit(p):  # noqa: ANN001, ANN202
        p.loc[p.index[position], column] = value
        return p

    return edit


def _provider_before_space(p):  # noqa: ANN001, ANN202
    i = p.index[5]
    p.loc[i, "provider_start_min"] = p.loc[i, "space_start_min"] - 30
    return p


def _set_column(column: str, value):  # noqa: ANN001, ANN202
    def edit(p):  # noqa: ANN001, ANN202
        p[column] = value
        return p

    return edit


# --- Valid baseline -----------------------------------------------------------------------


def test_valid_baseline_is_caution_not_blocked(small_run) -> None:  # noqa: ANN001
    report = validate_run(small_run)
    assert report.status is GateStatus.CAUTION
    assert all(c.status is GateStatus.PASS for c in report.checks if c.category == "integrity")
    assert _check(report, "single_seed").status is GateStatus.CAUTION
    assert not report.overloaded
    released = release_for_interpretation(small_run, report)
    assert set(released.tables) >= {"acuity_metrics", "shift_utilization", "stage_decomposition"}


def test_every_check_explains_its_limits(small_run) -> None:  # noqa: ANN001
    for c in validate_run(small_run).checks:
        assert c.protects_against and c.cannot_prove, c.check


def test_report_carries_safety_limitations_and_unavailable_lwbs(small_run) -> None:  # noqa: ANN001
    report = validate_run(small_run)
    text = " ".join(report.limitations)
    for phrase in (
        "ESI-1 PRIORITY LIMITATION",
        "NOT A STAFFING RECOMMENDATION",
        "PHYSICIAN CONCURRENCY IS FICTIONAL",
        "BED RELEASES ARE A SIMPLIFIED",
        "not clinically validated",
    ):
        assert phrase in text
    assert "left_without_being_seen_rate" in report.unavailable_metrics
    assert "Unavailable" in report.unavailable_metrics["left_without_being_seen_rate"]


# --- Adversarial: corrupted outputs are BLOCKED ---------------------------------------------


@pytest.mark.parametrize(
    ("edit", "check", "message"),
    [
        # Invalid event timestamp: seen by a physician before being placed in a space.
        (_provider_before_space, "event_order_and_units", "out of order"),
        # A later stage recorded while an earlier one is missing.
        (
            _set_cell("space_start_min", 3, np.nan),
            "event_order_and_units",
            "without its predecessor",
        ),
        # Unit error: LOS stored in seconds instead of minutes.
        (
            lambda p: p.assign(los_min=p["los_min"] * 60),
            "event_order_and_units",
            "los_min disagrees",
        ),
        # Negative time.
        (_set_cell("arrival_min", 0, -5.0), "event_order_and_units", "negative timestamp"),
        # Patient silently lost.
        (lambda p: p.drop(index=p.index[10]), "patient_conservation", "rows for"),
        # Wrong provenance label.
        (_set_column("provenance", "PUBLIC"), "provenance", "SIMULATED"),
        # Rows from a different run.
        (
            _set_column("source_dataset_id", "simulation:other:seed1"),
            "provenance",
            "does not match",
        ),
        # Warm-up patients leaking into the measured cohort.
        (_set_column("cohort", "measured"), "cohort_denominators", "disagree"),
        # Missing column: the check cannot even run, so it is BLOCKED (never a crash).
        (lambda p: p.drop(columns="disposition"), "event_order_and_units", "could not run"),
    ],
    ids=[
        "provider_before_space",
        "missing_stage",
        "unit_error",
        "negative_time",
        "lost_patient",
        "wrong_provenance",
        "foreign_rows",
        "warmup_leak",
        "missing_column",
    ],
)
def test_corrupted_event_logs_are_blocked(small_run, edit, check: str, message: str) -> None:  # noqa: ANN001
    bad = _with_patients(small_run, edit)
    report = validate_run(bad)
    assert report.status is GateStatus.BLOCKED
    assert message in _check(report, check).detail
    with pytest.raises(BlockedResultError, match="BLOCKED"):
        release_for_interpretation(bad, report)


def test_blocked_runs_skip_interpretation_checks(small_run) -> None:  # noqa: ANN001
    bad = _with_patients(small_run, _set_column("provenance", "SYNTHETIC"))
    report = validate_run(bad)
    assert {c.category for c in report.checks} == {"integrity"}


@pytest.mark.parametrize(
    "corrupt",
    [
        lambda r: r.physicians.assignments.append((100.0, 99, 8)),  # double-booked physician
        lambda r: r.spaces.assignments.append((100.0, "acute_beds", 37, 36)),  # double-booked bed
        lambda r: setattr(r.beds, "lost_to_other_demand", r.beds.lost_to_other_demand + 1),
        lambda r: r.spaces.assignments.pop(),  # assignment log lost a record
    ],
)
def test_resource_accounting_inconsistencies_are_blocked(small_run, corrupt) -> None:  # noqa: ANN001
    corrupt(small_run)
    report = validate_run(small_run)
    assert report.status is GateStatus.BLOCKED
    assert _check(report, "resource_conservation").status is GateStatus.BLOCKED


def test_census_bookkeeping_error_is_blocked(small_run) -> None:  # noqa: ANN001
    small_run.census.values[len(small_run.census.values) // 2] += 3
    report = validate_run(small_run)
    assert _check(report, "littles_law_sample_path").status is GateStatus.BLOCKED


def test_report_cannot_be_reused_for_an_altered_result(small_run) -> None:  # noqa: ANN001
    report = validate_run(small_run)
    altered = _with_patients(small_run, lambda p: p.assign(needs_lab=~p["needs_lab"]))
    with pytest.raises(BlockedResultError, match="fingerprint"):
        release_for_interpretation(altered, report)


# --- Empty and incomplete cohorts ------------------------------------------------------------


def test_zero_arrivals_blocked(site_dict: dict[str, Any]) -> None:
    r = run_simulation(
        tiny_site(site_dict), settings(), patients=patients(), durations=[], bed_release_times=[]
    )
    report = validate_run(r)
    assert report.status is GateStatus.BLOCKED
    assert "measured cohort is empty" in _check(report, "cohort_denominators").detail


def test_everyone_in_warmup_blocked(site_dict: dict[str, Any]) -> None:
    r = run_simulation(
        tiny_site(site_dict),
        settings(days=2, warmup=1),
        patients=patients((60, 3, False, False, False)),
        durations=durations(1),
        bed_release_times=[],
    )
    assert validate_run(r).status is GateStatus.BLOCKED


# --- Adversarial: misleading-but-valid results are CAUTION, not hidden ------------------------


def test_reassuring_median_masks_starved_low_acuity_patient(site_dict: dict[str, Any]) -> None:
    """Hand-checkable: one physician, exams take exactly 30 min, triage 5 min.
    P1 (ESI 2) is examined 5-35. The ESI-5 patient (arrives minute 1) is
    ready at 10. ESI-2 patients arrive at 25, 55, ..., 1435; each is ready
    5 min BEFORE the physician frees up (35, 65, ...), so ESI 2 always wins.
    The 48th ESI-2 exam ends at 1475; only then is the ESI-5 patient seen.
    ESI-2 LOS = 5 triage + 5 wait + 30 exam + 10 discharge = 50 min.
    ESI-5 LOS = (1475 - 1) + 30 + 10 = 1514 min, while the median stays 50."""
    stream = [(0, 2, False, False, False), (1, 5, False, False, False)]
    stream += [(t, 2, False, False, False) for t in range(25, 1440, 30)]
    r = run_simulation(
        tiny_site(site_dict),
        settings(),
        patients=patients(*stream),
        durations=durations(len(stream), provider=30),
        bed_release_times=[],
    )
    starved = row(r, 2)
    assert starved.provider_start_min == 1475
    assert starved.los_min == 1514
    assert r.patients["los_min"].median() == 50  # falsely reassuring
    extreme = dm.extreme_waits(r)
    esi5 = extreme[
        (extreme["acuity_esi"] == "5")
        & (extreme["measure"] == "time_in_ed")
        & (extreme["threshold_min"] == 1440)
    ].iloc[0]
    assert esi5["patients_over"] == 1 and esi5["share_over"] == 1.0
    report = validate_run(r)
    assert report.status is GateStatus.CAUTION
    assert _check(report, "extreme_waits").status is GateStatus.CAUTION
    assert "ESI 5: 1514" in _check(report, "extreme_waits").detail


def test_completed_only_los_is_biased_low_and_flagged(site_dict: dict[str, Any]) -> None:
    site = tiny_site(
        site_dict,
        spaces={
            "acute_beds": 20,
            "resuscitation_bays": 1,
            "fast_track_chairs": 0,
            "hallway_spaces": 0,
        },
    )
    discharged = [(t, 3, False, False, False) for t in range(0, 6)]
    admitted = [(t, 3, False, False, True) for t in range(10, 16)]  # no beds ever released
    r = run_simulation(
        site,
        settings(drain_hours=0),
        patients=patients(*discharged, *admitted),
        durations=durations(12, triage=1, provider=10),
        bed_release_times=[],
    )
    cs = dm.censoring_summary(r).iloc[0]
    assert cs["unfinished"] == 6 and cs["completed"] == 6
    assert cs["los_median_completed_only"] < 100 < cs["los_median_lower_bound_all"]
    report = validate_run(r)
    assert report.status is GateStatus.CAUTION
    censor = _check(report, "censoring")
    assert censor.status is GateStatus.CAUTION and "biased low" in censor.detail
    assert report.overloaded  # the run hit its hard stop
    release_for_interpretation(r, report)  # still releasable, with the caveat


def test_esi1_delay_is_flagged(site_dict: dict[str, Any]) -> None:
    # ESI-1 arrives while the only physician is mid-exam -> waits 55 min.
    r = run_simulation(
        tiny_site(site_dict),
        settings(),
        patients=patients((0, 3, False, False, False), (10, 1, False, False, False)),
        durations=durations(2, provider=60),
        bed_release_times=[],
    )
    check = _check(validate_run(r), "esi1_timeliness")
    assert check.status is GateStatus.CAUTION and "1 of 1 ESI-1" in check.detail


@pytest.mark.parametrize("intent", ["stress_test", "baseline"])
def test_overload_stress_test_is_flagged_not_discarded(
    site_dict: dict[str, Any], intent: str
) -> None:
    site_dict["operations"]["physician_concurrent_patients"] = 1  # demand ~1.4x supply
    site = SiteProfile.model_validate(site_dict)
    s = SimulationSettings(start_date=date(2026, 11, 16), arrival_days=4, warmup_days=1, seed=3)
    r = run_simulation(site, s)
    report = validate_run(r, intent=intent)
    assert report.status is GateStatus.CAUTION and report.overloaded
    assert all(c.status is GateStatus.PASS for c in report.checks if c.category == "integrity")
    capacity = _check(report, "capacity_and_stability").detail
    expected = "Intentional stress test" if intent == "stress_test" else "OVERLOADED"
    assert expected in capacity
    released = release_for_interpretation(r, report)  # diagnostic value preserved
    assert released.tables["extreme_waits"]["patients_over"].sum() > 0


def test_lightly_loaded_stress_test_says_it_did_not_overload(site_dict: dict[str, Any]) -> None:
    site_dict["arrivals"]["hourly_rates"] = [2.0] * 24  # ~48 patients/day: no capacity flags
    site = SiteProfile.model_validate(site_dict)
    s = SimulationSettings(start_date=date(2026, 11, 16), arrival_days=4, warmup_days=1, seed=1)
    report = validate_run(run_simulation(site, s), intent="stress_test")
    capacity = _check(report, "capacity_and_stability")
    assert capacity.status is GateStatus.PASS
    assert "did not overload" in capacity.detail and not report.overloaded
