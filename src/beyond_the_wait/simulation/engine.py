"""Discrete-event simulation of patient flow in the FICTIONAL Riverside Commons ED.

Every outcome produced here is labeled SIMULATED. It describes a model of a
fictional ED under stated assumptions -- never a real hospital -- and is
never mixed with public CMS/NYS values.

Clock
    Minutes since 00:00 on `settings.start_date`. Minute 1440 is midnight of
    day 2; shift schedules repeat every 1440 minutes.

Patient journey (see docs/SIMULATION_MODEL.md for the full assumption list)
    1. Arrive (from the Milestone 3 synthetic generator).
    2. Triage by a triage nurse, first come first served (acuity is assigned
       AT triage, so it can't set priority before it). ESI-1 may bypass.
    3. Wait for a treatment space allowed for this ESI level (priority queue).
    4. Provider evaluation by a physician slot (priority queue).
    5. Lab and/or imaging, in parallel, if needed (delay only; no capacity).
    6. Disposition (instantaneous):
         discharge -> discharge process -> leave
         admit     -> BOARD in the ED space until an inpatient bed is released
    7. Leave the ED; the treatment space is freed.

Stopping rule
    Arrivals happen only within the arrival window
    [start, start + arrival_days). The simulation then keeps running until
    every patient has left OR `drain_hours` after the window closes (the
    hard stop), whichever comes first. Patients still in the ED at a hard
    stop are kept and labeled `unfinished`, with the stage they reached.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Annotated

import numpy as np
import pandas as pd
import simpy
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from beyond_the_wait.config import SiteProfile
from beyond_the_wait.config.models import SPACE_TYPES
from beyond_the_wait.data.provenance import Provenance, tag_dataframe
from beyond_the_wait.data.synthetic_generator import generate_patients
from beyond_the_wait.simulation.randomness import (
    BED_RELEASE_STREAM,
    DURATION_STREAM,
    PatientDurations,
    child_rng,
    sample_bed_release_times,
    sample_durations,
)
from beyond_the_wait.simulation.resources import (
    InpatientBedQueue,
    ScheduledPool,
    StepSeries,
    TreatmentSpaceManager,
)

ENGINE_VERSION = "1"
ENGINE_ID = f"beyond_the_wait.simulation.engine@v{ENGINE_VERSION}"
MINUTES_PER_DAY = 1440
REQUIRED_PATIENT_COLUMNS = (
    "synthetic_patient_id",
    "arrival_time",
    "acuity_esi",
    "needs_lab",
    "needs_imaging",
    "hypothetical_admission",
)
STAGE_ORDER = (
    "arrival",
    "triage_start",
    "triage_end",
    "space_start",
    "provider_start",
    "provider_end",
    "disposition",
    "departure",
)


class SimulationError(ValueError):
    """Invalid simulation settings or inputs."""


class SimulationSettings(BaseModel):
    """How long to simulate, what to measure, and the seed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    start_date: date
    arrival_days: Annotated[StrictInt, Field(ge=1, le=365)]
    warmup_days: Annotated[StrictInt, Field(ge=0)] = 1
    drain_hours: Annotated[StrictInt, Field(ge=0, le=24 * 14)] = 72
    seed: Annotated[StrictInt, Field(ge=0)]

    @model_validator(mode="after")
    def _warmup_shorter_than_run(self) -> SimulationSettings:
        if self.warmup_days >= self.arrival_days:
            raise ValueError(
                "warmup_days must be smaller than arrival_days, or nothing would be measured"
            )
        return self

    @property
    def arrival_end(self) -> float:
        return self.arrival_days * MINUTES_PER_DAY

    @property
    def warmup_end(self) -> float:
        return self.warmup_days * MINUTES_PER_DAY

    @property
    def hard_stop(self) -> float:
        return self.arrival_end + self.drain_hours * 60


@dataclass
class _Visit:
    index: int
    patient_id: str
    esi: int
    needs_lab: bool
    needs_imaging: bool
    admit: bool
    durations: PatientDurations
    arrival: float
    bypassed_triage: bool = False
    times: dict[str, float] = field(default_factory=dict)
    space_type: str | None = None


@dataclass
class SimulationResult:
    """Everything produced by one run. `patients` is labeled SIMULATED."""

    settings: SimulationSettings
    site_id: str
    patients: pd.DataFrame
    physicians: ScheduledPool
    triage_nurses: ScheduledPool
    spaces: TreatmentSpaceManager
    beds: InpatientBedQueue
    census: StepSeries
    stop_time: float
    stop_reason: str
    bed_release_times: np.ndarray
    site: SiteProfile | None = None  # the profile that was simulated (for shift metrics)
    n_input_patients: int | None = None  # how many patients were fed in (conservation check)


def _capacity_by_hour(site: SiteProfile, counts: dict[str, int], factor: int = 1) -> list[int]:
    hourly = [0] * 24
    for shift in site.staffing.shifts:
        for hour in shift.hours_covered():
            hourly[hour] = counts[shift.name] * factor
    return hourly


def _prepare_patients(patients: pd.DataFrame, settings: SimulationSettings) -> pd.DataFrame:
    missing = [c for c in REQUIRED_PATIENT_COLUMNS if c not in patients.columns]
    if missing:
        raise SimulationError(f"patient table is missing columns {missing}")
    df = patients.reset_index(drop=True).copy()
    origin = pd.Timestamp(settings.start_date)
    minutes = (pd.to_datetime(df["arrival_time"]) - origin).dt.total_seconds() / 60.0
    if len(df) and (minutes.min() < 0 or minutes.max() >= settings.arrival_end):
        raise SimulationError("patient arrivals fall outside the arrival window")
    if not minutes.is_monotonic_increasing:
        raise SimulationError("patients must be sorted by arrival time")
    df["arrival_min"] = minutes.astype(float)
    return df


def run_simulation(
    site: SiteProfile,
    settings: SimulationSettings,
    *,
    patients: pd.DataFrame | None = None,
    durations: list[PatientDurations] | None = None,
    bed_release_times: np.ndarray | list[float] | None = None,
) -> SimulationResult:
    """Run one simulation. Defaults draw everything from `settings.seed`.

    `patients`, `durations`, and `bed_release_times` can be supplied to build
    small, fully deterministic test cases.
    """
    if patients is None:
        patients = generate_patients(
            site, start=settings.start_date, days=settings.arrival_days, seed=settings.seed
        )
    df = _prepare_patients(patients, settings)
    n = len(df)
    acuity = df["acuity_esi"].to_numpy(dtype=int)
    if durations is None:
        durations = sample_durations(site, acuity, child_rng(settings.seed, DURATION_STREAM))
    if len(durations) != n:
        raise SimulationError(f"got {len(durations)} duration sets for {n} patients")
    ops = site.operations
    beds_cfg = ops.inpatient_bed_availability
    if bed_release_times is None:
        bed_release_times = sample_bed_release_times(
            beds_cfg, settings.hard_stop, child_rng(settings.seed, BED_RELEASE_STREAM)
        )
    bed_release_times = np.asarray(bed_release_times, dtype=float)

    env = simpy.Environment()
    physicians = ScheduledPool(
        env,
        "physician_slots",
        _capacity_by_hour(
            site, site.staffing.physicians_on_duty, ops.physician_concurrent_patients
        ),
    )
    triage = ScheduledPool(env, "triage_nurses", _capacity_by_hour(site, ops.triage_nurses_on_duty))
    spaces = TreatmentSpaceManager(
        env,
        {t: getattr(site.treatment_spaces, t) for t in SPACE_TYPES},
        ops.treatment_space_eligibility,
    )
    beds = InpatientBedQueue(
        env,
        bed_release_times,
        initial_available=beds_cfg.initial_available_beds,
        max_unclaimed=beds_cfg.max_unclaimed_beds,
    )
    census = StepSeries(0.0, 0)
    all_departed = env.event()
    departed = {"count": 0}
    if n == 0:
        all_departed.succeed()

    visits = [
        _Visit(
            index=i,
            patient_id=str(row.synthetic_patient_id),
            esi=int(row.acuity_esi),
            needs_lab=bool(row.needs_lab),
            needs_imaging=bool(row.needs_imaging),
            admit=bool(row.hypothetical_admission),
            durations=durations[i],
            arrival=float(row.arrival_min),
        )
        for i, row in enumerate(df.itertuples(index=False))
    ]

    def journey(v: _Visit):  # noqa: ANN202 - SimPy process generator
        yield env.timeout(v.arrival - env.now)
        v.times["arrival"] = env.now
        census.set(env.now, census.current + 1)

        # Triage: FIFO (acuity is not known until triage is done).
        if ops.esi1_bypasses_triage and v.esi == 1:
            v.bypassed_triage = True
            v.times["triage_start"] = v.times["triage_end"] = env.now
        else:
            yield triage.request((v.arrival, v.index))
            v.times["triage_start"] = env.now
            yield env.timeout(v.durations.triage)
            triage.release()
            v.times["triage_end"] = env.now

        # Treatment space: priority by ESI, then time ready.
        v.space_type = yield spaces.request((v.esi, env.now, v.index), v.esi)
        v.times["space_start"] = env.now

        # Provider evaluation: priority by ESI, then time ready.
        yield physicians.request((v.esi, env.now, v.index))
        v.times["provider_start"] = env.now
        yield env.timeout(v.durations.provider)
        physicians.release()
        v.times["provider_end"] = env.now

        # Diagnostics run in parallel; the patient waits for the slower one.
        diagnostics = max(
            v.durations.lab if v.needs_lab else 0.0,
            v.durations.imaging if v.needs_imaging else 0.0,
        )
        if diagnostics > 0:
            yield env.timeout(diagnostics)
        v.times["disposition"] = env.now

        if v.admit:
            v.times["boarding_start"] = env.now
            yield beds.request()
            v.times["bed_assigned"] = env.now
        else:
            yield env.timeout(v.durations.discharge)

        v.times["departure"] = env.now
        spaces.release(v.space_type)
        census.set(env.now, census.current - 1)
        departed["count"] += 1
        if departed["count"] == n:
            all_departed.succeed()

    for v in visits:
        env.process(journey(v))

    env.run(until=env.any_of([all_departed, env.timeout(settings.hard_stop)]))
    stop_time = float(env.now)
    stop_reason = "all_departed" if all_departed.triggered else "hard_stop"
    table = _patient_table(visits, df, settings, stop_time, site.site.site_id)
    return SimulationResult(
        settings=settings,
        site_id=site.site.site_id,
        patients=table,
        physicians=physicians,
        triage_nurses=triage,
        spaces=spaces,
        beds=beds,
        census=census,
        stop_time=stop_time,
        stop_reason=stop_reason,
        bed_release_times=bed_release_times,
        site=site,
        n_input_patients=n,
    )


def _stage_at_stop(t: dict[str, float]) -> str:
    if "departure" in t:
        return "departed"
    if "boarding_start" in t:
        return "boarding"
    if "disposition" in t:
        return "discharge_process"
    if "provider_end" in t:
        return "awaiting_results"
    if "provider_start" in t:
        return "in_evaluation"
    if "space_start" in t:
        return "waiting_for_provider"
    if "triage_end" in t:
        return "waiting_for_space"
    if "triage_start" in t:
        return "in_triage"
    if "arrival" in t:
        return "waiting_for_triage"
    return "not_arrived"  # pragma: no cover - every arrival is inside the window


def _patient_table(
    visits: list[_Visit], df: pd.DataFrame, settings: SimulationSettings, stop: float, site_id: str
) -> pd.DataFrame:
    def get(t: dict[str, float], key: str) -> float:
        return t.get(key, np.nan)

    rows = []
    for v in visits:
        t = v.times
        done = "departure" in t
        rows.append(
            {
                "synthetic_patient_id": v.patient_id,
                "acuity_esi": v.esi,
                "disposition": "admit" if v.admit else "discharge",
                "needs_lab": v.needs_lab,
                "needs_imaging": v.needs_imaging,
                "cohort": "warm_up" if v.arrival < settings.warmup_end else "measured",
                "status": "completed" if done else "unfinished",
                "stage_at_stop": _stage_at_stop(t),
                "bypassed_triage": v.bypassed_triage,
                "space_type": v.space_type or "",
                **{f"{k}_min": get(t, k) for k in (*STAGE_ORDER, "boarding_start", "bed_assigned")},
            }
        )
    out = pd.DataFrame(rows)
    if out.empty:
        out = pd.DataFrame(columns=[*rows[0].keys()] if rows else _EMPTY_COLUMNS)
    else:
        out["arrival_time"] = pd.Timestamp(settings.start_date) + pd.to_timedelta(
            out["arrival_min"], unit="min"
        )
        out["triage_wait_min"] = out["triage_start_min"] - out["arrival_min"]
        out["space_wait_min"] = out["space_start_min"] - out["triage_end_min"]
        out["provider_wait_min"] = out["provider_start_min"] - out["space_start_min"]
        out["arrival_to_provider_min"] = out["provider_start_min"] - out["arrival_min"]
        out["boarding_min"] = out["bed_assigned_min"] - out["boarding_start_min"]
        out["los_min"] = out["departure_min"] - out["arrival_min"]
        # Unfinished visits: time in the ED so far (a lower bound on their LOS).
        out["time_in_ed_at_stop_min"] = np.where(
            out["status"] == "unfinished", stop - out["arrival_min"], np.nan
        )
    out = out.reindex(columns=_EMPTY_COLUMNS)
    return tag_dataframe(
        out,
        label=Provenance.SIMULATED,
        dataset_id=f"simulation:{site_id}:seed{settings.seed}",
        source_url=ENGINE_ID,
        retrieved_on=None,
    )


_EMPTY_COLUMNS = [
    "synthetic_patient_id", "acuity_esi", "disposition", "needs_lab", "needs_imaging",
    "cohort", "status", "stage_at_stop", "bypassed_triage", "space_type", "arrival_time",
    *(f"{k}_min" for k in (*STAGE_ORDER, "boarding_start", "bed_assigned")),
    "triage_wait_min", "space_wait_min", "provider_wait_min", "arrival_to_provider_min",
    "boarding_min", "los_min", "time_in_ed_at_stop_min",
]  # fmt: skip
