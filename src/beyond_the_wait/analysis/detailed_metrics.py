"""Detailed, censoring-aware outcome metrics for one simulation run (SIMULATED).

Why these exist (Milestone 5 audit)
    * Medians can look reassuring while some patients wait for days, so we
      report high percentiles, maxima, and counts over 4 h / 8 h / 24 h, by
      ESI level.
    * LOS from COMPLETED visits only is biased low when a run ends with
      patients still in the ED (they are the longest stays). We add a
      "lower-bound" version that counts unfinished visits at their time so
      far. The true value is at least that large.
    * Daily averages hide hourly and shift peaks, so we report utilization by
      shift, by hour, and the share of time at >= 90% utilization.
    * The stage decomposition is DESCRIPTIVE for one run. It is not evidence
      of a "dominant bottleneck"; that needs many seeds and scenario
      comparisons (Milestone 6).

Every table carries a `unit` column and SIMULATED provenance columns.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterator, Sequence

import numpy as np
import pandas as pd

from beyond_the_wait.analysis.metrics import _tag, measured
from beyond_the_wait.simulation.engine import SimulationResult
from beyond_the_wait.simulation.resources import StepSeries

HIGH_UTILIZATION = 0.90
EXTREME_THRESHOLDS_MIN: tuple[int, ...] = (240, 480, 1440)
BOARDING_THRESHOLDS_MIN: tuple[int, ...] = (120, 240, 480)

UNAVAILABLE_METRICS: dict[str, str] = {
    "left_without_being_seen_rate": (
        "Unavailable: the simulation has no mechanism for patients to leave before "
        "evaluation, so every simulated patient is eventually seen. Reporting a rate "
        "(even 0%) would be invented. Requires a departure (balking/reneging) model."
    ),
}

STAGES: tuple[tuple[str, str, str], ...] = (
    ("waiting for triage", "arrival_min", "triage_start_min"),
    ("triage", "triage_start_min", "triage_end_min"),
    ("waiting for a space", "triage_end_min", "space_start_min"),
    ("waiting for a physician", "space_start_min", "provider_start_min"),
    ("provider evaluation", "provider_start_min", "provider_end_min"),
    ("lab/imaging results", "provider_end_min", "disposition_min"),
    ("discharge process or boarding", "disposition_min", "departure_min"),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _stats(values: pd.Series) -> dict[str, float]:
    v = values.dropna()
    if v.empty:
        return dict.fromkeys(("median", "p90", "p95", "max", "mean"), float("nan"))
    return {
        "median": float(v.median()),
        "p90": float(v.quantile(0.90)),
        "p95": float(v.quantile(0.95)),
        "max": float(v.max()),
        "mean": float(v.mean()),
    }


def time_in_ed_lower_bound(result: SimulationResult) -> pd.Series:
    """LOS for completed visits; time-so-far for unfinished ones (a lower bound)."""
    p = result.patients
    return p["los_min"].fillna(p["time_in_ed_at_stop_min"])


def _arrival_to_provider_lower_bound(result: SimulationResult) -> pd.Series:
    p = result.patients
    not_seen = result.stop_time - p["arrival_min"]
    return p["arrival_to_provider_min"].fillna(not_seen)


def _value_at(series: StepSeries, t: float) -> float:
    i = bisect_right(series.times, t) - 1
    return series.values[max(i, 0)]


def segments(
    series: Sequence[StepSeries], start: float, end: float, step: float | None = 60.0
) -> Iterator[tuple[float, float, list[float]]]:
    """Split [start, end) wherever any series changes (and on every hour, if
    `step` is set). Yields (segment start, segment end, values in segment)."""
    points = {start, end}
    for s in series:
        points.update(t for t in s.times if start < t < end)
    if step:
        first = np.ceil(start / step) * step
        points.update(float(t) for t in np.arange(first, end, step) if start < t < end)
    ordered = sorted(points)
    for a, b in zip(ordered, ordered[1:], strict=False):
        yield a, b, [_value_at(s, a) for s in series]


def _shift_of_hour(result: SimulationResult) -> dict[int, str]:
    if result.site is None:
        raise ValueError("result has no site profile; re-run with the current engine")
    return {h: s.name for s in result.site.staffing.shifts for h in s.hours_covered()}


# ---------------------------------------------------------------------------
# Patient metrics
# ---------------------------------------------------------------------------


def acuity_metrics(result: SimulationResult) -> pd.DataFrame:
    """Waits, LOS, and boarding by ESI level and disposition (completed visits)."""
    m = measured(result)
    done = m[m["status"] == "completed"]
    rows = []
    groups = [
        ("all", done),
        ("discharge", done[done["disposition"] == "discharge"]),
        ("admit", done[done["disposition"] == "admit"]),
    ]
    for disposition, subset in groups:
        for esi in ["all", 1, 2, 3, 4, 5]:
            g = subset if esi == "all" else subset[subset["acuity_esi"] == esi]
            for column in ("arrival_to_provider_min", "los_min", "boarding_min"):
                if column == "boarding_min" and disposition != "admit":
                    continue
                values = g[column].dropna()
                rows.append(
                    {
                        "disposition": disposition,
                        "acuity_esi": str(esi),
                        "measure": column,
                        "patients": len(values),
                        **_stats(values),
                        "over_4h": int((values > 240).sum()),
                        "over_24h": int((values > 1440).sum()),
                        "unit": "minutes",
                    }
                )
    return _tag(pd.DataFrame(rows), result)


def censoring_summary(result: SimulationResult) -> pd.DataFrame:
    """Completed-only LOS vs. a lower bound that includes unfinished visits."""
    m = measured(result)
    lower = time_in_ed_lower_bound(result).loc[m.index]
    completed = m.loc[m["status"] == "completed", "los_min"]
    row = {
        "measured_patients": len(m),
        "completed": int((m["status"] == "completed").sum()),
        "unfinished": int((m["status"] == "unfinished").sum()),
        "los_median_completed_only": _stats(completed)["median"],
        "los_median_lower_bound_all": _stats(lower)["median"],
        "los_p90_completed_only": _stats(completed)["p90"],
        "los_p90_lower_bound_all": _stats(lower)["p90"],
        "unit": "minutes",
        "note": (
            "Lower bound counts unfinished visits at their time in the ED when the run "
            "stopped; their true LOS is at least that long."
        ),
    }
    return _tag(pd.DataFrame([row]), result)


def extreme_waits(result: SimulationResult) -> pd.DataFrame:
    """Counts and shares of very long stays and waits, including unfinished visits."""
    m = measured(result)
    ted = time_in_ed_lower_bound(result).loc[m.index]
    atp = _arrival_to_provider_lower_bound(result).loc[m.index]
    rows = []
    for esi in ["all", 1, 2, 3, 4, 5]:
        mask = m.index if esi == "all" else m.index[m["acuity_esi"] == esi]
        n = len(mask)
        for measure, values, thresholds in (
            ("time_in_ed", ted, EXTREME_THRESHOLDS_MIN),
            ("arrival_to_provider", atp, (60, 240)),
        ):
            for threshold in thresholds:
                over = int((values.loc[mask] > threshold).sum())
                rows.append(
                    {
                        "acuity_esi": str(esi),
                        "measure": measure,
                        "threshold_min": threshold,
                        "patients_over": over,
                        "denominator": n,
                        "share_over": over / n if n else float("nan"),
                        "max_min": float(values.loc[mask].max()) if n else float("nan"),
                        "unit": "minutes / patients",
                    }
                )
    return _tag(pd.DataFrame(rows), result)


def boarding_distribution(result: SimulationResult) -> pd.DataFrame:
    """Boarding for admitted measured patients, overall and by the shift in
    which the admission decision was made. Unfinished boarders are included
    at their time so far (lower bound)."""
    m = measured(result)
    adm = m[m["disposition"] == "admit"]
    boarding = adm["boarding_min"].fillna(result.stop_time - adm["boarding_start_min"])
    started = adm["boarding_start_min"].notna()
    adm, boarding = adm[started], boarding[started]
    shift_of_hour = _shift_of_hour(result)
    decision_shift = ((adm["boarding_start_min"] // 60) % 24).astype(int).map(shift_of_hour)
    rows = []
    for label, mask in [
        ("all", adm.index),
        *[
            (f"decision during {s} shift", adm.index[decision_shift == s])
            for s in sorted(set(shift_of_hour.values()))
        ],
    ]:
        values = boarding.loc[mask]
        rows.append(
            {
                "group": label,
                "admitted_patients": len(values),
                "still_boarding_at_stop": int(adm.loc[mask, "bed_assigned_min"].isna().sum()),
                "p25": float(values.quantile(0.25)) if len(values) else float("nan"),
                **_stats(values),
                **{
                    f"share_over_{t // 60}h": float((values > t).mean())
                    if len(values)
                    else float("nan")
                    for t in BOARDING_THRESHOLDS_MIN
                },
                "unit": "minutes",
            }
        )
    return _tag(pd.DataFrame(rows), result)


def stage_decomposition(result: SimulationResult) -> pd.DataFrame:
    """Average minutes in each stage for completed measured visits.

    Stages add up exactly to LOS for every patient, so mean stage times add up
    to mean LOS. DESCRIPTIVE ONLY: a large share in one run is not proof of a
    dominant bottleneck.
    """
    m = measured(result)
    done = m[m["status"] == "completed"]
    rows = []
    for disposition in ("discharge", "admit"):
        g = done[done["disposition"] == disposition]
        mean_los = float(g["los_min"].mean()) if len(g) else float("nan")
        for stage, a, b in STAGES:
            name = stage
            if stage == "discharge process or boarding":
                name = "boarding" if disposition == "admit" else "discharge process"
            mean = float((g[b] - g[a]).mean()) if len(g) else float("nan")
            rows.append(
                {
                    "disposition": disposition,
                    "stage": name,
                    "patients": len(g),
                    "mean_min": mean,
                    "share_of_mean_los": mean / mean_los if mean_los else float("nan"),
                    "unit": "minutes",
                    "interpretation": "descriptive, one run; not a bottleneck attribution",
                }
            )
    return _tag(pd.DataFrame(rows), result)


# ---------------------------------------------------------------------------
# Staffing metrics
# ---------------------------------------------------------------------------


def shift_utilization(result: SimulationResult) -> pd.DataFrame:
    """Utilization, time at >= 90% utilization, and patients per staffed
    person-hour, by shift, over the measured window.

    Denominators:
      * physicians: physician-hours (people, not concurrency slots)
      * triage: triage-nurse-hours
      * all nurses on duty: nurse-hours, with ARRIVALS as the numerator --
        descriptive only, because non-triage nursing work isn't modeled.
    """
    site = result.site
    shift_of_hour = _shift_of_hour(result)
    start, end = result.settings.warmup_end, result.settings.arrival_end
    p = result.patients
    rows = []
    pools = (
        (result.physicians, "provider_start_min", site.staffing.physicians_on_duty, "physician"),
        (
            result.triage_nurses,
            "triage_start_min",
            site.operations.triage_nurses_on_duty,
            "triage nurse",
        ),
    )
    for pool, start_col, people, role in pools:
        acc = {s: {"busy": 0.0, "cap": 0.0, "high": 0.0, "minutes": 0.0} for s in people}
        for a, b, (busy, cap) in segments([pool.busy_series, pool.capacity_series], start, end):
            shift = shift_of_hour[int(a // 60) % 24]
            d = b - a
            acc[shift]["busy"] += busy * d
            acc[shift]["cap"] += cap * d
            acc[shift]["minutes"] += d
            if cap > 0 and busy / cap >= HIGH_UTILIZATION:
                acc[shift]["high"] += d
        started = p[start_col].dropna()
        started = started[(started >= start) & (started < end)]
        started_shift = ((started // 60) % 24).astype(int).map(shift_of_hour)
        for shift, v in acc.items():
            person_hours = people[shift] * v["minutes"] / 60
            n = int((started_shift == shift).sum())
            rows.append(
                {
                    "resource": pool.name,
                    "shift": shift,
                    "hours_observed": v["minutes"] / 60,
                    "people_on_duty": people[shift],
                    "average_units": v["cap"] / v["minutes"] if v["minutes"] else float("nan"),
                    "utilization": v["busy"] / v["cap"] if v["cap"] else float("nan"),
                    "share_time_at_or_above_90pct": v["high"] / v["minutes"]
                    if v["minutes"]
                    else float("nan"),
                    "staffed_person_hours": person_hours,
                    "patients_started": n,
                    "patients_per_staffed_hour": n / person_hours if person_hours else float("nan"),
                    "denominator": f"{role}-hours",
                    "unit": "fraction / patients per person-hour",
                }
            )
    arrivals = p["arrival_min"][(p["arrival_min"] >= start) & (p["arrival_min"] < end)]
    arrival_shift = ((arrivals // 60) % 24).astype(int).map(shift_of_hour)
    for shift in site.staffing.nurses_on_duty:
        hours = sum(
            1 for t in np.arange(start, end, 60) if shift_of_hour[int(t // 60) % 24] == shift
        )
        nurse_hours = site.staffing.nurses_on_duty[shift] * hours
        n = int((arrival_shift == shift).sum())
        rows.append(
            {
                "resource": "all_nurses_on_duty (descriptive)",
                "shift": shift,
                "hours_observed": float(hours),
                "people_on_duty": site.staffing.nurses_on_duty[shift],
                "average_units": float("nan"),
                "utilization": float("nan"),
                "share_time_at_or_above_90pct": float("nan"),
                "staffed_person_hours": float(nurse_hours),
                "patients_started": n,
                "patients_per_staffed_hour": n / nurse_hours if nurse_hours else float("nan"),
                "denominator": "nurse-hours (arrivals per nurse-hour; non-triage work not modeled)",
                "unit": "patients per person-hour",
            }
        )
    return _tag(pd.DataFrame(rows), result)


def hourly_profile(result: SimulationResult) -> pd.DataFrame:
    """Clock-hour averages over measured days: physician utilization and queue,
    triage queue, arrivals, and median arrival-to-provider by arrival hour."""
    start, end = result.settings.warmup_end, result.settings.arrival_end
    days = int((end - start) // 1440)
    m = measured(result)
    rows = []
    for hour in range(24):
        util, pq, tq = [], [], []
        for d in range(days):
            a = start + d * 1440 + hour * 60
            b = a + 60
            cap = result.physicians.capacity_series.integral(a, b)
            util.append(result.physicians.busy_series.integral(a, b) / cap if cap else np.nan)
            pq.append(result.physicians.queue_series.integral(a, b) / 60)
            tq.append(result.triage_nurses.queue_series.integral(a, b) / 60)
        at_hour = m[((m["arrival_min"] // 60) % 24).astype(int) == hour]
        rows.append(
            {
                "hour": hour,
                "arrivals_per_day": len(at_hour) / days if days else float("nan"),
                "physician_utilization": float(np.nanmean(util)) if util else float("nan"),
                "physician_queue_mean": float(np.mean(pq)) if pq else float("nan"),
                "triage_queue_mean": float(np.mean(tq)) if tq else float("nan"),
                "median_arrival_to_provider_min": float(at_hour["arrival_to_provider_min"].median())
                if len(at_hour)
                else float("nan"),
                "unit": "fraction / patients / minutes",
            }
        )
    return _tag(pd.DataFrame(rows), result)
