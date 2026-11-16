"""Is this configuration -- and this run -- trustworthy enough to look at?

Two kinds of checks:

1. BEFORE the run (`offered_load_checks`): simple arithmetic on the
   assumptions. "Load" = work arriving / work capacity.
     * load >= 1 on an average day: the queue grows without limit. Any
       "average wait" from such a run depends only on how long you ran it,
       so it is meaningless and flagged CRITICAL.
     * load >= 0.85, or peak-hour load >= 1: waits are long, highly
       variable, and very sensitive to small changes. Flagged WARNING.
   These use daily averages and are rough. They are NOT steady-state
   queueing formulas; they only screen for obvious overload.

2. AFTER the run (`run_checks`): hard stop reached, unfinished visits,
   waits that keep growing from one day to the next, too few measured days,
   and a standing reminder that one seed is one random draw.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

import numpy as np

from beyond_the_wait.config import SiteProfile
from beyond_the_wait.config.models import ESI_LEVELS
from beyond_the_wait.simulation.engine import SimulationResult

Level = Literal["info", "warning", "critical"]
NEAR_SATURATION = 0.85


@dataclass(frozen=True)
class Flag:
    level: Level
    code: str
    message: str


def offered_loads(site: SiteProfile) -> dict[str, float]:
    """Average-day and peak-hour load for physicians, triage, and inpatient beds."""
    mix, st, ops = site.patient_mix, site.service_times, site.operations
    rates = np.asarray(site.arrivals.hourly_rates)
    peak_multiplier = max(site.arrivals.multiplier_for(d) for d in range(7))
    shift_of_hour = {h: s.name for s in site.staffing.shifts for h in s.hours_covered()}

    eval_mean = sum(
        mix.acuity_distribution[a] * st.provider_evaluation_by_acuity[a].mean for a in ESI_LEVELS
    )
    slots = np.array(
        [
            site.staffing.physicians_on_duty[shift_of_hour[h]] * ops.physician_concurrent_patients
            for h in range(24)
        ]
    )
    triaged_share = 1 - (mix.acuity_distribution[1] if ops.esi1_bypasses_triage else 0)
    nurses = np.array([ops.triage_nurses_on_duty[shift_of_hour[h]] for h in range(24)])

    def loads(
        work_per_patient: float, servers: np.ndarray, share: float = 1.0
    ) -> tuple[float, float]:
        demand = rates * share * work_per_patient  # minutes of work arriving per hour
        average = demand.sum() / (servers.sum() * 60)
        peak = float(np.max(demand * peak_multiplier / (servers * 60)))
        return float(average), peak

    phys_avg, phys_peak = loads(eval_mean, slots)
    tri_avg, tri_peak = loads(st.triage.mean, nurses, triaged_share)
    admits_per_day = site.arrivals.expected_daily_arrivals * mix.implied_admission_fraction
    bed_load = admits_per_day / ops.inpatient_bed_availability.releases_per_day
    return {
        "physician_average": phys_avg,
        "physician_peak_hour": phys_peak,
        "triage_average": tri_avg,
        "triage_peak_hour": tri_peak,
        "inpatient_bed_average": bed_load,
    }


def offered_load_checks(site: SiteProfile) -> list[Flag]:
    loads = offered_loads(site)
    flags: list[Flag] = []
    names = {
        "physician": "Physicians",
        "triage": "Triage nurses",
        "inpatient_bed": "Inpatient beds",
    }
    for key, label in names.items():
        avg = loads[f"{key}_average"]
        peak = loads.get(f"{key}_peak_hour")
        if avg >= 1.0:
            flags.append(
                Flag(
                    "critical",
                    f"{key}_saturated",
                    f"{label}: average load {avg:.2f} >= 1. Work arrives faster than it can be "
                    "done; queues grow without limit and waits are not meaningful.",
                )
            )
        elif avg >= NEAR_SATURATION:
            flags.append(
                Flag(
                    "warning",
                    f"{key}_near_saturation",
                    f"{label}: average load {avg:.2f} (>= {NEAR_SATURATION}). Expect long, highly "
                    "variable waits that are sensitive to small changes.",
                )
            )
        if peak is not None and peak >= 1.0:
            flags.append(
                Flag(
                    "warning",
                    f"{key}_peak_overload",
                    f"{label}: busiest-hour load {peak:.2f} >= 1. Queues build during peak "
                    "hours and must clear later.",
                )
            )
    return flags


def run_checks(result: SimulationResult) -> list[Flag]:
    s = result.settings
    p = result.patients
    flags: list[Flag] = []
    if result.stop_reason == "hard_stop":
        flags.append(
            Flag(
                "critical",
                "hard_stop_reached",
                f"{int((p['status'] == 'unfinished').sum())} patient(s) were still in the ED "
                f"{s.drain_hours} h after arrivals stopped. Results are incomplete; the system "
                "may be overloaded.",
            )
        )
    measured = p[p["cohort"] == "measured"]
    unfinished = int((measured["status"] == "unfinished").sum())
    if unfinished:
        flags.append(
            Flag(
                "warning",
                "unfinished_visits",
                f"{unfinished} measured visit(s) were unfinished; their LOS is excluded from "
                "medians and reported separately as time-in-ED-at-stop.",
            )
        )
    # Growing queue: compare the average number of patients IN THE ED on the
    # first vs. last measured day. (An earlier version compared median LOS by
    # arrival day; under strict priority the backlog falls on low-acuity
    # patients and early arrivals wait longest, so LOS by arrival day can even
    # FALL while the queue explodes. The census does not have that blind spot.)
    first_day, last_day = s.warmup_days, s.arrival_days - 1
    if last_day - first_day >= 2:
        census_first = result.census.integral(first_day * 1440, (first_day + 1) * 1440) / 1440
        census_last = result.census.integral(last_day * 1440, (last_day + 1) * 1440) / 1440
        if census_last > 1.5 * census_first and census_last - census_first >= 5:
            flags.append(
                Flag(
                    "warning",
                    "growing_queue",
                    f"Average patients in the ED rose from {census_first:.0f} on the first "
                    f"measured day to {census_last:.0f} on the last. The queue may not be stable.",
                )
            )
    # Extreme stays: completed LOS, or time so far for unfinished visits.
    time_in_ed = measured["los_min"].fillna(measured["time_in_ed_at_stop_min"])
    extreme = int((time_in_ed > 24 * 60).sum())
    if len(measured) and extreme / len(measured) > 0.01:
        flags.append(
            Flag(
                "warning",
                "extreme_stays",
                f"{extreme} measured patient(s) ({extreme / len(measured):.1%}) spent over 24 h "
                "in the ED. Under strict priority, lower-acuity patients may be starved.",
            )
        )
    measured_days = s.arrival_days - s.warmup_days
    if measured_days < 3:
        flags.append(
            Flag(
                "warning",
                "short_run",
                f"Only {measured_days} measured day(s). Day-to-day variation is large; use more "
                "days and seeds before drawing any conclusion.",
            )
        )
    end = s.start_date + timedelta(days=s.arrival_days - 1)
    flags.append(
        Flag(
            "info",
            "single_seed",
            f"One run (seed {s.seed}, {s.start_date}..{end}) is one random draw. Comparing "
            "policies needs many seeds and confidence intervals (Milestone 6).",
        )
    )
    return flags
