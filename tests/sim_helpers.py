"""Helpers for deterministic simulation tests: tiny fictional EDs and hand-placed patients."""

from __future__ import annotations

import copy
from datetime import date
from typing import Any

import pandas as pd

from beyond_the_wait.config import SiteProfile
from beyond_the_wait.simulation.engine import SimulationSettings
from beyond_the_wait.simulation.randomness import PatientDurations

START = date(2026, 11, 16)  # a Monday; minute 0 = 00:00


def tiny_site(
    site_dict: dict[str, Any],
    *,
    physicians: dict[str, int] | None = None,
    concurrency: int = 1,
    triage_nurses: dict[str, int] | None = None,
    spaces: dict[str, int] | None = None,
    eligibility: dict[int, list[str]] | None = None,
    bypass: bool = True,
    initial_beds: int = 0,
    max_unclaimed: int = 0,
) -> SiteProfile:
    """A small, easy-to-reason-about version of the fictional site."""
    d = copy.deepcopy(site_dict)
    d["staffing"]["physicians_on_duty"] = physicians or {"day": 1, "evening": 1, "night": 1}
    ops = d["operations"]
    ops["physician_concurrent_patients"] = concurrency
    ops["triage_nurses_on_duty"] = triage_nurses or {"day": 1, "evening": 1, "night": 1}
    ops["esi1_bypasses_triage"] = bypass
    d["treatment_spaces"].update(
        spaces
        or {"acute_beds": 5, "resuscitation_bays": 1, "fast_track_chairs": 0, "hallway_spaces": 0}
    )
    ops["treatment_space_eligibility"] = eligibility or {
        1: ["resuscitation_bays", "acute_beds"],
        2: ["acute_beds"],
        3: ["acute_beds"],
        4: ["acute_beds"],
        5: ["acute_beds"],
    }
    beds = ops["inpatient_bed_availability"]
    beds["initial_available_beds"] = initial_beds
    beds["max_unclaimed_beds"] = max_unclaimed
    return SiteProfile.model_validate(d)


def patients(*rows: tuple[float, int, bool, bool, bool]) -> pd.DataFrame:
    """Rows of (arrival minute, ESI, needs_lab, needs_imaging, admit)."""
    origin = pd.Timestamp(START)
    return pd.DataFrame(
        {
            "synthetic_patient_id": [f"SYN-T-{i:06d}" for i in range(1, len(rows) + 1)],
            "arrival_time": [origin + pd.Timedelta(minutes=r[0]) for r in rows],
            "acuity_esi": [r[1] for r in rows],
            "needs_lab": [r[2] for r in rows],
            "needs_imaging": [r[3] for r in rows],
            "hypothetical_admission": [r[4] for r in rows],
        },
        columns=[
            "synthetic_patient_id",
            "arrival_time",
            "acuity_esi",
            "needs_lab",
            "needs_imaging",
            "hypothetical_admission",
        ],
    )


def durations(n: int, **kw: float) -> list[PatientDurations]:
    base = {"triage": 5.0, "provider": 30.0, "lab": 60.0, "imaging": 90.0, "discharge": 10.0}
    return [PatientDurations(**(base | kw)) for _ in range(n)]


def settings(
    days: int = 1, warmup: int = 0, drain_hours: int = 72, seed: int = 0
) -> SimulationSettings:
    return SimulationSettings(
        start_date=START, arrival_days=days, warmup_days=warmup, drain_hours=drain_hours, seed=seed
    )


def row(result, patient_number: int) -> pd.Series:  # noqa: ANN001
    p = result.patients
    return p[p["synthetic_patient_id"] == f"SYN-T-{patient_number:06d}"].iloc[0]
