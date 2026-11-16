"""Tests for intervention scenarios and applying them to a site profile."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from beyond_the_wait.config import Scenario, ScenarioError, SiteProfile, apply_scenario

# --- Scenario validation on its own -------------------------------------------


def test_example_scenario_is_valid(scenario: Scenario) -> None:
    assert scenario.applies_to_site == "riverside_commons_ed"
    assert scenario.arrival_multiplier == 1.0


def test_scenario_with_no_changes_rejected(scenario_dict: dict[str, Any]) -> None:
    scenario_dict["staffing_changes"] = []
    with pytest.raises(ValidationError, match="makes no changes"):
        Scenario.model_validate(scenario_dict)


def test_zero_change_rejected(scenario_dict: dict[str, Any]) -> None:
    scenario_dict["staffing_changes"][0]["change"] = 0
    with pytest.raises(ValidationError, match="change of 0 has no effect"):
        Scenario.model_validate(scenario_dict)


def test_duplicate_staffing_change_rejected(scenario_dict: dict[str, Any]) -> None:
    duplicate = {"role": "physicians", "shift": "evening", "change": 2}
    scenario_dict["staffing_changes"].append(duplicate)
    with pytest.raises(ValidationError, match="may appear only once"):
        Scenario.model_validate(scenario_dict)


@pytest.mark.parametrize("role", ["doctors", "techs", "PHYSICIANS"])
def test_unsupported_role_rejected(scenario_dict: dict[str, Any], role: str) -> None:
    scenario_dict["staffing_changes"][0]["role"] = role
    with pytest.raises(ValidationError, match="role"):
        Scenario.model_validate(scenario_dict)


def test_unknown_space_type_rejected(scenario_dict: dict[str, Any]) -> None:
    scenario_dict["treatment_space_changes"] = [{"space": "helipad", "change": 1}]
    with pytest.raises(ValidationError, match="space"):
        Scenario.model_validate(scenario_dict)


@pytest.mark.parametrize("multiplier", [0, -1.0, 3.5])
def test_arrival_multiplier_bounds(scenario_dict: dict[str, Any], multiplier: float) -> None:
    scenario_dict["arrival_multiplier"] = multiplier
    with pytest.raises(ValidationError, match="arrival_multiplier"):
        Scenario.model_validate(scenario_dict)


def test_missing_target_site_rejected(scenario_dict: dict[str, Any]) -> None:
    del scenario_dict["applies_to_site"]
    with pytest.raises(ValidationError, match="applies_to_site"):
        Scenario.model_validate(scenario_dict)


# --- Applying a scenario --------------------------------------------------------


def test_apply_adds_evening_physician(site: SiteProfile, scenario: Scenario) -> None:
    changed = apply_scenario(site, scenario)
    assert changed.staffing.physicians_on_duty["evening"] == 5
    # Everything else is unchanged.
    assert changed.staffing.physicians_on_duty["day"] == 4
    assert changed.staffing.nurses_on_duty == site.staffing.nurses_on_duty
    assert changed.treatment_spaces == site.treatment_spaces


def test_apply_does_not_modify_original(site: SiteProfile, scenario: Scenario) -> None:
    apply_scenario(site, scenario)
    assert site.staffing.physicians_on_duty["evening"] == 4


def test_apply_combined_changes(site: SiteProfile, scenario_dict: dict[str, Any]) -> None:
    scenario_dict.update(
        kind="sensitivity",  # arrival_multiplier is an assumption, not an intervention
        treatment_space_changes=[{"space": "hallway_spaces", "change": -6}],
        bed_release_change_per_day=6,
        diagnostic_turnaround_multiplier=0.75,
        arrival_multiplier=1.1,
    )
    changed = apply_scenario(site, Scenario.model_validate(scenario_dict))
    assert changed.treatment_spaces.hallway_spaces == 0
    assert changed.operations.inpatient_bed_availability.releases_per_day == 48
    assert changed.service_times.lab_turnaround.median == pytest.approx(45)
    assert changed.service_times.imaging_turnaround.p90 == pytest.approx(112.5)
    assert changed.arrivals.expected_daily_arrivals == pytest.approx(190.5 * 1.1)
    assert changed.inpatient.staffed_beds == site.inpatient.staffed_beds  # untouched


def test_apply_to_wrong_site_rejected(site: SiteProfile, scenario_dict: dict[str, Any]) -> None:
    scenario_dict["applies_to_site"] = "some_other_ed"
    with pytest.raises(ScenarioError, match="applies to site 'some_other_ed'"):
        apply_scenario(site, Scenario.model_validate(scenario_dict))


def test_apply_unknown_shift_rejected(site: SiteProfile, scenario_dict: dict[str, Any]) -> None:
    scenario_dict["staffing_changes"][0]["shift"] = "swing"
    with pytest.raises(ScenarioError, match="unknown shift 'swing'"):
        apply_scenario(site, Scenario.model_validate(scenario_dict))


def test_apply_cannot_make_staffing_negative(
    site: SiteProfile, scenario_dict: dict[str, Any]
) -> None:
    scenario_dict["staffing_changes"] = [{"role": "nurses", "shift": "night", "change": -11}]
    with pytest.raises(ScenarioError, match="greater than or equal to 0"):
        apply_scenario(site, Scenario.model_validate(scenario_dict))


def test_apply_cannot_remove_all_physicians_from_a_shift(
    site: SiteProfile, scenario_dict: dict[str, Any]
) -> None:
    scenario_dict["staffing_changes"] = [{"role": "physicians", "shift": "night", "change": -2}]
    with pytest.raises(ScenarioError, match="at least one physician"):
        apply_scenario(site, Scenario.model_validate(scenario_dict))


def test_apply_cannot_make_beds_negative(site: SiteProfile, scenario_dict: dict[str, Any]) -> None:
    scenario_dict["staffing_changes"] = []
    scenario_dict["treatment_space_changes"] = [{"space": "acute_beds", "change": -37}]
    with pytest.raises(ScenarioError, match="greater than or equal to 0"):
        apply_scenario(site, Scenario.model_validate(scenario_dict))


def test_apply_cannot_push_arrivals_past_plausible_ceiling(
    site: SiteProfile, scenario_dict: dict[str, Any]
) -> None:
    scenario_dict["kind"] = "stress_test"
    scenario_dict["arrival_multiplier"] = 3.0  # 12/hr peak -> 36/hr is fine...
    apply_scenario(site, Scenario.model_validate(scenario_dict))
    tight = site.model_dump()
    tight["arrivals"]["hourly_rates"][11] = 20.0  # ...but 20/hr x 3 = 60/hr is not
    with pytest.raises(ScenarioError, match="less than or equal to 50"):
        apply_scenario(SiteProfile.model_validate(tight), Scenario.model_validate(scenario_dict))
