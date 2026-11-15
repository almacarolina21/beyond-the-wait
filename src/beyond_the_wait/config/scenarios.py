"""Apply a what-if scenario to a site profile, producing a new validated profile.

The original profile is never modified (models are frozen). Instead we copy
its data, apply the changes, and run the *full* SiteProfile validation again.
That second validation is what catches combinations such as "remove 3 nurses
from a shift that only has 2."
"""

from __future__ import annotations

from pydantic import ValidationError

from beyond_the_wait.config.errors import ScenarioError
from beyond_the_wait.config.models import Scenario, SiteProfile


def apply_scenario(site: SiteProfile, scenario: Scenario) -> SiteProfile:
    """Return a new SiteProfile with the scenario's changes applied.

    Raises:
        ScenarioError: if the scenario targets a different site, names a shift
            the site doesn't have, or produces an invalid configuration.
    """
    if scenario.applies_to_site != site.site.site_id:
        raise ScenarioError(
            f"scenario {scenario.scenario_id!r} applies to site {scenario.applies_to_site!r}, "
            f"not {site.site.site_id!r}"
        )

    data = site.model_dump()  # a plain, independent copy we are free to edit
    shift_names = {shift.name for shift in site.staffing.shifts}

    for change in scenario.staffing_changes:
        if change.shift not in shift_names:
            raise ScenarioError(
                f"scenario {scenario.scenario_id!r} changes unknown shift {change.shift!r}; "
                f"site shifts are {sorted(shift_names)}"
            )
        data["staffing"][f"{change.role}_on_duty"][change.shift] += change.change

    for change in scenario.treatment_space_changes:
        data["treatment_spaces"][change.space] += change.change

    data["inpatient"]["staffed_beds"] += scenario.inpatient_bed_change
    data["arrivals"]["hourly_rates"] = [
        rate * scenario.arrival_multiplier for rate in data["arrivals"]["hourly_rates"]
    ]

    try:
        return SiteProfile.model_validate(data)
    except ValidationError as exc:
        raise ScenarioError(
            f"applying scenario {scenario.scenario_id!r} to {site.site.site_id!r} "
            f"produces an invalid configuration:\n{exc}"
        ) from exc
