"""Milestone 6 scenario levers: each one must change real simulated capacity or
behavior (or say honestly that it doesn't), and the baseline must never change."""

from __future__ import annotations

import copy
from typing import Any

import numpy as np
import pytest
from pydantic import ValidationError

from beyond_the_wait.config import Scenario, SiteProfile, apply_scenario, scenario_effects
from beyond_the_wait.simulation.engine import run_simulation
from beyond_the_wait.simulation.randomness import (
    BED_RELEASE_STREAM,
    child_rng,
    sample_bed_release_times,
)
from tests.sim_helpers import settings

BASE = {
    "schema_version": 2,
    "scenario_id": "lever_test",
    "kind": "intervention",
    "name": "lever test",
    "description": "fictional lever test scenario",
    "applies_to_site": "riverside_commons_ed",
}


def _sc(**changes: Any) -> Scenario:
    return Scenario.model_validate(BASE | changes)


# --- Schema rules --------------------------------------------------------------------------


def test_retired_inpatient_bed_change_is_rejected_with_guidance() -> None:
    with pytest.raises(ValidationError, match="bed_release_change_per_day"):
        Scenario.model_validate(BASE | {"inpatient_bed_change": 20})


@pytest.mark.parametrize(
    "changes",
    [{"arrival_multiplier": 1.1}, {"physician_concurrency_override": 3}],
)
@pytest.mark.parametrize("kind", ["intervention", "archetype"])
def test_assumption_changes_not_allowed_in_interventions(changes: dict, kind: str) -> None:
    with pytest.raises(ValidationError, match="may not change uncertain assumptions"):
        Scenario.model_validate(BASE | {"kind": kind} | changes)


@pytest.mark.parametrize("kind", ["sensitivity", "stress_test"])
def test_assumption_changes_allowed_in_sensitivity(kind: str) -> None:
    assert _sc(kind=kind, physician_concurrency_override=3).changes_assumptions


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"physician_concurrency_override": 4, "kind": "sensitivity"}, "less than or equal to 3"),
        ({"diagnostic_turnaround_multiplier": 0.1}, "greater than or equal to 0.25"),
        ({"bed_release_change_per_day": float("inf")}, "finite"),
        ({"kind": "experiment", "bed_release_change_per_day": 1}, "kind"),
    ],
)
def test_lever_bounds(changes: dict, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        Scenario.model_validate(BASE | changes)


def test_scenario_effects_are_honest() -> None:
    effects = scenario_effects(
        _sc(
            staffing_changes=[
                {"role": "nurses", "shift": "day", "change": 2},
                {"role": "triage_nurses", "shift": "day", "change": 1},
            ],
            bed_release_change_per_day=6,
            diagnostic_turnaround_multiplier=0.75,
        )
    )
    assert effects["not_simulated"] == ["nurses +2 on day shift"]
    assert "triage_nurses +1 on day shift" in effects["simulated"]
    assert "inpatient bed releases +6/day" in effects["simulated"]
    sens = scenario_effects(_sc(kind="sensitivity", physician_concurrency_override=3))
    assert sens["assumptions"] == ["physician concurrency = 3 (hypothetical)"]


# --- Baseline immutability ------------------------------------------------------------------


def test_baseline_is_never_mutated(site: SiteProfile) -> None:
    before = copy.deepcopy(site.model_dump())
    apply_scenario(
        site,
        _sc(
            staffing_changes=[
                {"role": "physicians", "shift": "night", "change": 1},
                {"role": "triage_nurses", "shift": "night", "change": 1},
            ],
            bed_release_change_per_day=-4,
            diagnostic_turnaround_multiplier=1.5,
        ),
    )
    apply_scenario(site, _sc(kind="sensitivity", physician_concurrency_override=1))
    assert site.model_dump() == before


def test_scenarios_are_reversible(site: SiteProfile) -> None:
    forward = apply_scenario(
        site,
        _sc(
            staffing_changes=[{"role": "triage_nurses", "shift": "day", "change": 1}],
            bed_release_change_per_day=6,
            diagnostic_turnaround_multiplier=2.0,
        ),
    )
    back = apply_scenario(
        forward,
        _sc(
            staffing_changes=[{"role": "triage_nurses", "shift": "day", "change": -1}],
            bed_release_change_per_day=-6,
            diagnostic_turnaround_multiplier=0.5,
        ),
    )
    assert back.model_dump() == site.model_dump()


# --- Each lever changes the configuration it claims to change -------------------------------


def test_levers_change_the_right_settings(site: SiteProfile) -> None:
    s = apply_scenario(
        site,
        _sc(
            staffing_changes=[
                {"role": "triage_nurses", "shift": "night", "change": 1},
                {"role": "physicians", "shift": "evening", "change": 1},
            ],
            bed_release_change_per_day=6,
            diagnostic_turnaround_multiplier=0.75,
        ),
    )
    assert s.operations.triage_nurses_on_duty["night"] == 2
    assert s.staffing.physicians_on_duty["evening"] == 5
    assert s.operations.inpatient_bed_availability.releases_per_day == 48
    assert s.service_times.lab_turnaround.median == pytest.approx(45)
    c = apply_scenario(site, _sc(kind="sensitivity", physician_concurrency_override=3))
    assert c.operations.physician_concurrent_patients == 3


def test_triage_nurses_cannot_exceed_nurses_on_duty(site: SiteProfile) -> None:
    from beyond_the_wait.config import ScenarioError

    with pytest.raises(ScenarioError, match="exceeds"):
        apply_scenario(
            site, _sc(staffing_changes=[{"role": "triage_nurses", "shift": "night", "change": 10}])
        )


def test_bed_releases_cannot_go_to_zero(site: SiteProfile) -> None:
    from beyond_the_wait.config import ScenarioError

    with pytest.raises(ScenarioError, match="releases_per_day"):
        apply_scenario(site, _sc(bed_release_change_per_day=-42))


# --- ...and each simulated lever changes simulated behavior ---------------------------------


@pytest.fixture(scope="module")
def baseline_run(request: pytest.FixtureRequest):  # noqa: ANN201
    from beyond_the_wait.config import load_site_profile
    from tests.paths import EXAMPLE_SITE_PATH

    site = load_site_profile(EXAMPLE_SITE_PATH)
    return site, run_simulation(site, settings(days=3, warmup=1, seed=4))


@pytest.mark.parametrize(
    ("changes", "capacity_check"),
    [
        (
            {"staffing_changes": [{"role": "physicians", "shift": "night", "change": 1}]},
            lambda r: r.physicians.capacity_at(60) == 6,
        ),
        (
            {"staffing_changes": [{"role": "triage_nurses", "shift": "night", "change": 1}]},
            lambda r: r.triage_nurses.capacity_at(60) == 2,
        ),
        ({"bed_release_change_per_day": 10}, lambda r: True),
        ({"diagnostic_turnaround_multiplier": 0.5}, lambda r: True),
    ],
)
def test_simulated_levers_change_behavior(baseline_run, changes: dict, capacity_check) -> None:  # noqa: ANN001
    site, base = baseline_run
    r = run_simulation(apply_scenario(site, _sc(**changes)), settings(days=3, warmup=1, seed=4))
    assert capacity_check(r)
    assert not r.patients["los_min"].equals(base.patients["los_min"])


def test_nurses_lever_has_no_simulated_effect(baseline_run) -> None:  # noqa: ANN001
    site, base = baseline_run
    r = run_simulation(
        apply_scenario(
            site, _sc(staffing_changes=[{"role": "nurses", "shift": "day", "change": 2}])
        ),
        settings(days=3, warmup=1, seed=4),
    )
    assert r.patients["los_min"].equals(base.patients["los_min"])


# --- Common random numbers for the new levers -----------------------------------------------


def test_more_bed_releases_keep_every_baseline_release(site: SiteProfile) -> None:
    """Thinning coupling: a higher rate is a SUPERSET of the lower rate's releases."""
    cfg = site.operations.inpatient_bed_availability
    more = cfg.model_copy(update={"releases_per_day": 48.0})
    low = sample_bed_release_times(cfg, 20 * 1440, child_rng(9, BED_RELEASE_STREAM))
    high = sample_bed_release_times(more, 20 * 1440, child_rng(9, BED_RELEASE_STREAM))
    assert set(low) <= set(high)
    assert len(high) > len(low)


def test_diagnostic_multiplier_scales_each_patients_draw_exactly(baseline_run) -> None:  # noqa: ANN001
    site, base = baseline_run
    r = run_simulation(
        apply_scenario(site, _sc(diagnostic_turnaround_multiplier=0.5)),
        settings(days=3, warmup=1, seed=4),
    )
    np.testing.assert_array_equal(r.patients["arrival_min"], base.patients["arrival_min"])
    # Same patients, same evaluation lengths; only the diagnostic stage is scaled.
    exam = lambda p: p["provider_end_min"] - p["provider_start_min"]  # noqa: E731
    np.testing.assert_allclose(exam(r.patients), exam(base.patients))


def test_scenario_effects_for_spaces_and_demand() -> None:
    effects = scenario_effects(
        _sc(
            kind="sensitivity",
            arrival_multiplier=1.1,
            treatment_space_changes=[{"space": "acute_beds", "change": 5}],
        )
    )
    assert effects["simulated"] == ["acute_beds +5"]
    assert effects["assumptions"] == ["demand x1.1"]
