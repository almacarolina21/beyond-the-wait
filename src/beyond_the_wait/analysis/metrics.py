"""Outcome metrics for one simulation run. All values are SIMULATED.

Measurement rules
    * Only the MEASURED cohort counts: patients who arrived after the
      warm-up period and inside the arrival window. Warm-up patients are
      simulated (they occupy resources) but excluded from statistics,
      because the ED starts unrealistically empty.
    * Patient waits and lengths of stay use COMPLETED visits. Unfinished
      visits are counted and reported separately (their final LOS is
      unknown), never silently dropped.
    * Resource statistics are time-weighted over the measurement window
      [end of warm-up, end of arrivals).
"""

from __future__ import annotations

import pandas as pd

from beyond_the_wait.data.provenance import Provenance, tag_dataframe
from beyond_the_wait.simulation.engine import ENGINE_ID, SimulationResult
from beyond_the_wait.simulation.resources import StepSeries

PATIENT_MEASURES: dict[str, str] = {
    "triage_wait_min": "Waiting for triage",
    "space_wait_min": "Waiting for a treatment space (after triage)",
    "provider_wait_min": "Waiting for a physician (after placement)",
    "arrival_to_provider_min": "Arrival to provider",
    "los_min": "ED length of stay",
    "boarding_min": "Boarding (admitted patients)",
}


def _tag(df: pd.DataFrame, result: SimulationResult) -> pd.DataFrame:
    return tag_dataframe(
        df,
        label=Provenance.SIMULATED,
        dataset_id=f"simulation:{result.site_id}:seed{result.settings.seed}",
        source_url=ENGINE_ID,
        retrieved_on=None,
    )


def measured(result: SimulationResult) -> pd.DataFrame:
    p = result.patients
    return p[p["cohort"] == "measured"]


def patient_summary(result: SimulationResult) -> pd.DataFrame:
    """Median, 90th percentile, and mean (minutes) by disposition group."""
    m = measured(result)
    done = m[m["status"] == "completed"]
    rows = []
    for column, label in PATIENT_MEASURES.items():
        for group, subset in (
            ("all", done),
            ("discharged", done[done["disposition"] == "discharge"]),
            ("admitted", done[done["disposition"] == "admit"]),
        ):
            if column == "boarding_min" and group != "admitted":
                continue
            values = subset[column].dropna()
            rows.append(
                {
                    "measure": label,
                    "column": column,
                    "group": group,
                    "patients": len(values),
                    "median_min": values.median() if len(values) else float("nan"),
                    "p90_min": values.quantile(0.9) if len(values) else float("nan"),
                    "mean_min": values.mean() if len(values) else float("nan"),
                }
            )
    return _tag(pd.DataFrame(rows), result)


def _time_average(series: StepSeries, start: float, end: float) -> float:
    return series.integral(start, end) / (end - start) if end > start else float("nan")


def resource_summary(result: SimulationResult) -> pd.DataFrame:
    """Time-weighted utilization and queue lengths over the measurement window.

    Utilization = (busy unit-minutes) / (available unit-minutes). Around shift
    changes, a clinician finishing a patient after their shift ends can make
    momentary busy > capacity, so utilization can slightly exceed what the
    schedule alone would allow.
    """
    start, end = result.settings.warmup_end, result.settings.arrival_end
    rows = []
    for pool in (result.physicians, result.triage_nurses):
        capacity_minutes = pool.capacity_series.integral(start, end)
        rows.append(
            {
                "resource": pool.name,
                "average_capacity": _time_average(pool.capacity_series, start, end),
                "utilization": pool.busy_series.integral(start, end) / capacity_minutes
                if capacity_minutes
                else float("nan"),
                "mean_queue": _time_average(pool.queue_series, start, end),
                "max_queue": pool.queue_series.maximum(start, end),
            }
        )
    for space_type, capacity in result.spaces.capacity.items():
        occupied = result.spaces.occupied_series[space_type]
        rows.append(
            {
                "resource": space_type,
                "average_capacity": float(capacity),
                "utilization": _time_average(occupied, start, end) / capacity
                if capacity
                else float("nan"),
                "mean_queue": float("nan"),
                "max_queue": float("nan"),
            }
        )
    rows.append(
        {
            "resource": "treatment_space_queue (all types)",
            "average_capacity": float(sum(result.spaces.capacity.values())),
            "utilization": float("nan"),
            "mean_queue": _time_average(result.spaces.queue_series, start, end),
            "max_queue": result.spaces.queue_series.maximum(start, end),
        }
    )
    rows.append(
        {
            "resource": "boarders_waiting_for_inpatient_bed",
            "average_capacity": float("nan"),
            "utilization": float("nan"),
            "mean_queue": _time_average(result.beds.queue_series, start, end),
            "max_queue": result.beds.queue_series.maximum(start, end),
        }
    )
    return _tag(pd.DataFrame(rows), result)


def throughput_summary(result: SimulationResult) -> dict[str, float]:
    """Counts of arrivals, departures, and unfinished visits."""
    s = result.settings
    p = result.patients
    m = measured(result)
    measured_days = s.arrival_days - s.warmup_days
    departures_in_window = int(
        ((p["departure_min"] >= s.warmup_end) & (p["departure_min"] < s.arrival_end)).sum()
    )
    return {
        "measured_days": measured_days,
        "arrivals_measured": len(m),
        "arrivals_per_day": len(m) / measured_days,
        "departures_per_day_in_window": departures_in_window / measured_days,
        "completed_measured": int((m["status"] == "completed").sum()),
        "unfinished_measured": int((m["status"] == "unfinished").sum()),
        "unfinished_all": int((p["status"] == "unfinished").sum()),
        "mean_census_in_window": _time_average(result.census, s.warmup_end, s.arrival_end),
        "beds_released": result.beds.released,
        "beds_claimed": result.beds.claimed,
        "beds_lost_to_other_demand": result.beds.lost_to_other_demand,
    }
