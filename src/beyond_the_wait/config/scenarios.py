"""Apply a what-if scenario to a site profile, producing a new validated profile.

The original profile is never modified (models are frozen). We copy its data,
apply the changes, and run the FULL SiteProfile validation again. That second
validation catches combinations such as "remove 3 triage nurses from a shift
that only has 2."

Where each lever lands in the model
    physicians      staffing.physicians_on_duty[shift]           (simulated)
    triage_nurses   operations.triage_nurses_on_duty[shift]      (simulated)
    nurses          staffing.nurses_on_duty[shift]               (NOT simulated)
    spaces          treatment_spaces.<type>                       (simulated)
    bed releases    operations.inpatient_bed_availability.releases_per_day
    diagnostics     service_times.lab_turnaround / imaging_turnaround (median and p90
                    scaled by the multiplier, so every duration scales exactly)
    demand          arrivals.hourly_rates x arrival_multiplier      (assumption)
    concurrency     operations.physician_concurrent_patients       (assumption)
"""

from __future__ import annotations

from pydantic import ValidationError

from beyond_the_wait.config.errors import ScenarioError
from beyond_the_wait.config.models import Scenario, SiteProfile


def scenario_effects(scenario: Scenario) -> dict[str, list[str]]:
    """Describe each change: does it alter simulated behavior or not?

    Returns {"simulated": [...], "not_simulated": [...], "assumptions": [...]}.
    A change listed as "simulated" can still have no visible effect if the
    resource it adds is never the binding constraint (e.g., extra beds that
    were never full).
    """
    simulated, not_simulated, assumptions = [], [], []
    for c in scenario.staffing_changes:
        label = f"{c.role} {c.change:+d} on {c.shift} shift"
        (not_simulated if c.role == "nurses" else simulated).append(label)
    for c in scenario.treatment_space_changes:
        simulated.append(f"{c.space} {c.change:+d}")
    if scenario.bed_release_change_per_day:
        simulated.append(f"inpatient bed releases {scenario.bed_release_change_per_day:+g}/day")
    if scenario.diagnostic_turnaround_multiplier != 1.0:
        simulated.append(f"lab/imaging turnaround x{scenario.diagnostic_turnaround_multiplier:g}")
    if scenario.arrival_multiplier != 1.0:
        assumptions.append(f"demand x{scenario.arrival_multiplier:g}")
    if scenario.physician_concurrency_override is not None:
        assumptions.append(
            f"physician concurrency = {scenario.physician_concurrency_override} (hypothetical)"
        )
    return {"simulated": simulated, "not_simulated": not_simulated, "assumptions": assumptions}


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

    data = site.model_dump()  # a plain, independent deep copy we are free to edit
    shift_names = {shift.name for shift in site.staffing.shifts}

    for change in scenario.staffing_changes:
        if change.shift not in shift_names:
            raise ScenarioError(
                f"scenario {scenario.scenario_id!r} changes unknown shift {change.shift!r}; "
                f"site shifts are {sorted(shift_names)}"
            )
        if change.role == "triage_nurses":
            data["operations"]["triage_nurses_on_duty"][change.shift] += change.change
        else:
            data["staffing"][f"{change.role}_on_duty"][change.shift] += change.change

    for change in scenario.treatment_space_changes:
        data["treatment_spaces"][change.space] += change.change

    beds = data["operations"]["inpatient_bed_availability"]
    beds["releases_per_day"] += scenario.bed_release_change_per_day

    m = scenario.diagnostic_turnaround_multiplier
    for task in ("lab_turnaround", "imaging_turnaround"):
        spec = data["service_times"][task]
        spec["median"] *= m
        spec["p90"] *= m

    data["arrivals"]["hourly_rates"] = [
        rate * scenario.arrival_multiplier for rate in data["arrivals"]["hourly_rates"]
    ]
    if scenario.physician_concurrency_override is not None:
        data["operations"]["physician_concurrent_patients"] = (
            scenario.physician_concurrency_override
        )

    try:
        return SiteProfile.model_validate(data)
    except ValidationError as exc:
        raise ScenarioError(
            f"applying scenario {scenario.scenario_id!r} to {site.site.site_id!r} "
            f"produces an invalid configuration:\n{exc}"
        ) from exc
