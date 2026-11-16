"""Validation gate between the simulation and the (future) Claude interpretation layer.

Every run gets a typed ValidationReport with one of three statuses:

    PASS     All checks mathematically consistent and within declared scope.
    CAUTION  Numbers are internally consistent, but can only be interpreted
             with stated limitations (one seed, near saturation, overload
             stress test, unfinished visits, model simplifications...).
    BLOCKED  Outputs are corrupted, incomplete, or mathematically
             inconsistent. NO number from a BLOCKED run may be packaged for
             interpretation: `release_for_interpretation()` refuses it.

Two kinds of checks
    * INTEGRITY checks look for implementation failures (impossible event
      order, lost patients, double-booked resources, wrong labels, metrics
      that disagree with the event log). Failure => BLOCKED.
    * INTERPRETATION checks look at what the numbers can mean (overload,
      extreme waits, censoring, observation length, scope). They never
      block; they produce CAUTION with an explanation.

An intentionally overloaded "stress test" is therefore NOT blocked: if its
event log is internally consistent, it's a valid description of how the
fictional system fails, and it is labeled as such.

What the gate cannot prove: that the model's assumptions are realistic,
that it represents any real ED, or that any staffing change would be safe.
Passing integrity checks means "internally consistent", not "true".
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict

from beyond_the_wait.analysis import detailed_metrics as dm
from beyond_the_wait.analysis.metrics import patient_summary, resource_summary
from beyond_the_wait.data.provenance import PROVENANCE_COLUMNS, Provenance, require_provenance
from beyond_the_wait.simulation.engine import STAGE_ORDER, SimulationResult
from beyond_the_wait.simulation.validity import offered_load_checks, run_checks

ESI1_REVIEW_THRESHOLD_MIN = 10.0  # illustrative review threshold, NOT a clinical standard
TOLERANCE = 1e-6


class GateStatus(StrEnum):
    PASS = "PASS"
    CAUTION = "CAUTION"
    BLOCKED = "BLOCKED"


_RANK = {GateStatus.PASS: 0, GateStatus.CAUTION: 1, GateStatus.BLOCKED: 2}


class CheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    check: str
    category: Literal["integrity", "interpretation", "scope"]
    status: GateStatus
    detail: str
    protects_against: str
    cannot_prove: str


SAFETY_LIMITATIONS: tuple[str, ...] = (
    "FICTIONAL MODEL: results describe the fictional Riverside Commons ED under illustrative "
    "assumptions. They are not clinically validated and do not describe any real hospital.",
    "NOT A STAFFING RECOMMENDATION: no simulated result establishes that a staffing or "
    "capacity change would be safe in a real ED. Decisions require qualified human review.",
    "ESI-1 PRIORITY LIMITATION: priority is non-preemptive. A critically ill (ESI 1) "
    "patient waits for the next free physician or space instead of receiving immediate "
    "resuscitation, so the model can understate how fast critical patients are seen.",
    "TRIAGE IS FIRST COME, FIRST SERVED: acuity priority applies only after triage, so "
    "higher-acuity patients wait in the same triage line as everyone else.",
    "PHYSICIAN CONCURRENCY IS FICTIONAL: each physician is assumed to evaluate up to 2 "
    "patients at once. Results are highly sensitive to this assumption; the Milestone 6 "
    "sensitivity analysis varies it (1-3) and flags conclusions that change with it.",
    "BED RELEASES ARE A SIMPLIFIED ABSTRACTION: inpatient beds open for the ED at an "
    "assumed rate (42/day, afternoon-weighted). It is not derived from bed counts or "
    "occupancy, and boarding results depend heavily on it.",
    "LEFT WITHOUT BEING SEEN IS NOT MODELED: every simulated patient waits to be seen, so "
    "no LWBS rate is reported and waits for the most crowded periods may be overstated "
    "relative to a real ED where some patients leave.",
)


class ValidationReport(BaseModel):
    """Typed result of the gate. `status` is the worst status of any check."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: GateStatus
    run_intent: Literal["baseline", "stress_test"]
    overloaded: bool
    site_id: str
    seed: int
    result_fingerprint: str
    checks: tuple[CheckResult, ...]
    limitations: tuple[str, ...]
    unavailable_metrics: dict[str, str]

    @property
    def reasons(self) -> list[str]:
        return [f"[{c.status}] {c.check}: {c.detail}" for c in self.checks if c.status != "PASS"]


class BlockedResultError(RuntimeError):
    """Raised when someone tries to package results that must not be interpreted."""


def fingerprint(result: SimulationResult) -> str:
    """SHA-256 of the patient event log plus run identity, so a report can't be
    reused for a different (or later altered) result."""
    p = result.patients
    payload = p.reindex(sorted(p.columns), axis=1).to_csv(index=False)
    meta = json.dumps(
        {
            "settings": result.settings.model_dump(mode="json"),
            "site_id": result.site_id,
            "stop_time": result.stop_time,
            "n_input": result.n_input_patients,
        },
        sort_keys=True,
    )
    return hashlib.sha256((payload + meta).encode()).hexdigest()


# ---------------------------------------------------------------------------
# Integrity checks (failure => BLOCKED)
# ---------------------------------------------------------------------------


class _Fail(Exception):
    """A check found a problem; the message explains it."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise _Fail(message)


def _check_provenance(r: SimulationResult) -> str:
    p = r.patients
    missing = [c for c in PROVENANCE_COLUMNS if c not in p.columns]
    _require(not missing, f"missing provenance columns {missing}")
    try:
        require_provenance(p, Provenance.SIMULATED)
    except ValueError as exc:
        raise _Fail(str(exc)) from exc
    expected_id = f"simulation:{r.site_id}:seed{r.settings.seed}"
    _require(
        bool((p["source_dataset_id"] == expected_id).all()) if len(p) else True,
        f"source_dataset_id does not match this run ({expected_id})",
    )
    return "every row labeled SIMULATED with this run's site and seed"


def _check_conservation(r: SimulationResult) -> str:
    p = r.patients
    _require(r.n_input_patients is not None, "result does not record its input patient count")
    _require(len(p) == r.n_input_patients, f"{len(p)} rows for {r.n_input_patients} input patients")
    _require(p["synthetic_patient_id"].is_unique, "duplicate patient IDs")
    _require(set(p["status"]) <= {"completed", "unfinished"}, "unknown status values")
    completed = int((p["status"] == "completed").sum())
    _require(completed == int(p["departure_min"].notna().sum()), "completed != departures logged")
    unfinished = len(p) - completed
    if r.stop_reason == "all_departed":
        _require(unfinished == 0, "stop reason says all departed, but visits are unfinished")
    _require(
        r.census.current == unfinished,
        f"census at stop {r.census.current} != unfinished {unfinished}",
    )
    _require(min(r.census.values) >= 0, "census went negative")
    return f"{len(p)} in = {completed} completed + {unfinished} unfinished; census consistent"


def _check_event_order(r: SimulationResult) -> str:
    p = r.patients
    s = r.settings
    cols = [f"{k}_min" for k in STAGE_ORDER]
    t = p[cols].to_numpy(dtype=float)
    present = ~np.isnan(t)
    # A later stage may only be recorded if every earlier stage was.
    _require(
        bool((present[:, 1:] <= present[:, :-1]).all()),
        "a stage is recorded without its predecessor",
    )
    filled = np.where(present, t, np.nan)
    diffs = np.diff(filled, axis=1)
    _require(
        bool((np.nan_to_num(diffs, nan=0.0) >= -TOLERANCE).all()), "event timestamps out of order"
    )
    finite = filled[present]
    _require(bool(np.isfinite(finite).all()), "non-finite timestamp")
    _require(bool((finite >= -TOLERANCE).all()), "negative timestamp")
    _require(
        bool((finite <= r.stop_time + TOLERANCE).all()), "timestamp after the simulation stopped"
    )
    _require(bool((p["arrival_min"] < s.arrival_end).all()), "arrival outside the arrival window")
    admit = p["disposition"] == "admit"
    _require(
        bool(p.loc[~admit, "boarding_start_min"].isna().all()),
        "discharged patient has boarding times",
    )
    done_admit = admit & (p["status"] == "completed")
    _require(
        bool(
            np.allclose(p.loc[done_admit, "departure_min"], p.loc[done_admit, "bed_assigned_min"])
        ),
        "admitted patient left at a different time than the bed was assigned",
    )
    derived = {
        "triage_wait_min": ("triage_start_min", "arrival_min"),
        "space_wait_min": ("space_start_min", "triage_end_min"),
        "provider_wait_min": ("provider_start_min", "space_start_min"),
        "arrival_to_provider_min": ("provider_start_min", "arrival_min"),
        "los_min": ("departure_min", "arrival_min"),
        "boarding_min": ("bed_assigned_min", "boarding_start_min"),
    }
    for col, (b, a) in derived.items():
        recomputed = p[b] - p[a]
        same = np.isclose(p[col], recomputed, atol=TOLERANCE) | (p[col].isna() & recomputed.isna())
        _require(
            bool(same.all()), f"{col} disagrees with its timestamps (unit or calculation error)"
        )
    unfinished = p["status"] == "unfinished"
    expected = r.stop_time - p.loc[unfinished, "arrival_min"]
    _require(
        bool(np.allclose(p.loc[unfinished, "time_in_ed_at_stop_min"], expected)),
        "time_in_ed_at_stop_min is wrong for unfinished visits",
    )
    return "all timestamps ordered, non-negative, within the run; derived minutes recompute exactly"


def _check_cohorts(r: SimulationResult) -> str:
    p = r.patients
    expected = np.where(p["arrival_min"] < r.settings.warmup_end, "warm_up", "measured")
    _require(
        bool((p["cohort"].to_numpy() == expected).all()),
        "cohort labels disagree with arrival times",
    )
    n = int((p["cohort"] == "measured").sum())
    _require(n > 0, "the measured cohort is empty; there is nothing to interpret")
    return f"{n} measured patients; cohort labels match arrival times"


def _check_resources(r: SimulationResult) -> str:
    p = r.patients
    for pool in (r.physicians, r.triage_nurses):
        _require(
            all(b <= c for _, b, c in pool.assignments), f"{pool.name} assigned above capacity"
        )
    _require(all(o <= c for _, _, o, c in r.spaces.assignments), "treatment space double-booked")
    for space, series in r.spaces.occupied_series.items():
        cap = r.spaces.capacity[space]
        _require(
            0 <= min(series.values) and max(series.values) <= cap, f"{space} occupancy out of range"
        )
    placed = int(p["space_start_min"].notna().sum())
    _require(len(r.spaces.assignments) == placed, "space assignments do not match placed patients")
    stage = p["stage_at_stop"]
    _require(
        r.physicians.busy == int((stage == "in_evaluation").sum()),
        "busy physicians != patients in evaluation",
    )
    _require(
        r.triage_nurses.busy == int((stage == "in_triage").sum()),
        "busy triage nurses != patients in triage",
    )
    occupied = sum(r.spaces.occupied(t) for t in r.spaces.capacity)
    in_space = int((p["space_start_min"].notna() & p["departure_min"].isna()).sum())
    _require(occupied == in_space, f"{occupied} spaces occupied but {in_space} patients hold one")
    _require(
        r.beds.queue_length == int((stage == "boarding").sum()),
        "boarding queue != boarding patients",
    )
    _require(
        r.beds.claimed == int(p["bed_assigned_min"].notna().sum()), "beds claimed != beds assigned"
    )
    initial = r.site.operations.inpatient_bed_availability.initial_available_beds if r.site else 0
    _require(
        r.beds.claimed + r.beds.available + r.beds.lost_to_other_demand
        == initial + r.beds.released,
        "inpatient bed accounting does not balance",
    )
    return "no assignment above capacity; staff, spaces, and beds balance against the event log"


def _check_summary_consistency(r: SimulationResult) -> str:
    m = r.patients[r.patients["cohort"] == "measured"]
    done = m[m["status"] == "completed"]
    ps = patient_summary(r)
    los = ps[(ps["column"] == "los_min") & (ps["group"] == "all")].iloc[0]
    _require(int(los["patients"]) == len(done), "summary patient count != event log")
    if len(done):
        _require(
            np.isclose(los["median_min"], done["los_min"].median()),
            "summary median LOS != event log",
        )
    am = dm.acuity_metrics(r)
    by_esi = am[
        (am["disposition"] == "all") & (am["measure"] == "los_min") & (am["acuity_esi"] != "all")
    ]
    _require(
        int(by_esi["patients"].sum()) == len(done), "per-ESI counts do not add up to the total"
    )
    stages = (
        sum(done[b] - done[a] for _, a, b in dm.STAGES) if len(done) else pd.Series(dtype=float)
    )
    _require(
        bool(np.allclose(stages, done["los_min"])) if len(done) else True,
        "stage times do not add up to LOS",
    )
    su = dm.shift_utilization(r)
    phys = su[su["resource"] == r.physicians.name]
    busy_total = (phys["utilization"] * phys["average_units"] * phys["hours_observed"] * 60).sum()
    window = r.physicians.busy_series.integral(r.settings.warmup_end, r.settings.arrival_end)
    _require(
        np.isclose(busy_total, window, rtol=1e-9),
        "shift utilization does not add up to the window total",
    )
    for name, table in (("patient_summary", ps), ("acuity_metrics", am), ("shift_utilization", su)):
        require_provenance(table, Provenance.SIMULATED)
        _require(
            "unit" in table.columns or name == "patient_summary", f"{name} lacks a unit column"
        )
    return (
        "summary tables recompute from the event log; stages sum to LOS; units and labels present"
    )


def _check_littles_law(r: SimulationResult) -> str:
    p = r.patients
    total = float((p["departure_min"].fillna(r.stop_time) - p["arrival_min"]).sum())
    area = r.census.integral(0, r.stop_time)
    _require(
        np.isclose(area, total, rtol=1e-9, atol=1e-6),
        f"census area {area:.3f} != time in ED {total:.3f}",
    )
    return "area under the census curve equals total patient time in the ED"


INTEGRITY_CHECKS: tuple[tuple[str, Callable[[SimulationResult], str], str, str], ...] = (
    (
        "provenance",
        _check_provenance,
        "outputs mislabeled as PUBLIC/SYNTHETIC, or mixed with another run's rows",
        "that the simulated numbers are realistic",
    ),
    (
        "patient_conservation",
        _check_conservation,
        "patients silently lost, duplicated, or miscounted at the stop",
        "that patients were routed sensibly",
    ),
    (
        "event_order_and_units",
        _check_event_order,
        "impossible timestamps, negative durations, unit or arithmetic errors in derived columns",
        "that durations are clinically plausible",
    ),
    (
        "cohort_denominators",
        _check_cohorts,
        "warm-up patients leaking into statistics, or an empty measured cohort",
        "that the warm-up period is long enough",
    ),
    (
        "resource_conservation",
        _check_resources,
        "double-booked staff or spaces, and staff/space/bed counts that do not balance",
        "that the capacity assumptions are right",
    ),
    (
        "summary_consistency",
        _check_summary_consistency,
        "summary metrics that disagree with the event log they claim to summarize",
        "that the chosen metrics answer the right question",
    ),
    (
        "littles_law_sample_path",
        _check_littles_law,
        "census and length-of-stay bookkeeping drifting apart",
        "steady-state behavior (this is an exact identity, not a queueing formula)",
    ),
)


# ---------------------------------------------------------------------------
# Interpretation and scope checks (CAUTION, never BLOCKED)
# ---------------------------------------------------------------------------


def _interpretation_checks(r: SimulationResult, intent: str) -> tuple[list[CheckResult], bool]:
    out: list[CheckResult] = []
    m = r.patients[r.patients["cohort"] == "measured"]

    load_flags = offered_load_checks(r.site) if r.site else []
    run_flags = run_checks(r)
    run_codes = {f.code for f in run_flags}
    overloaded = any(f.level == "critical" for f in load_flags) or bool(
        {"hard_stop_reached", "growing_queue"} & run_codes
    )
    if overloaded:
        lead = (
            "Intentional stress test: overload is expected. Use these numbers to see HOW and "
            "WHERE the fictional system fails, not as typical performance."
            if intent == "stress_test"
            else "The configuration is OVERLOADED: queues grow with run length, so averages "
            "are not meaningful as typical performance."
        )
        detail = (
            lead + " " + " ".join(f.message for f in load_flags + run_flags if f.level != "info")
        )
    elif load_flags:
        detail = "Near saturation or peak-hour overload: " + " ".join(f.message for f in load_flags)
    else:
        detail = "No capacity flags." + (
            " Stress test did not overload the system." if intent == "stress_test" else ""
        )
    out.append(
        CheckResult(
            check="capacity_and_stability",
            category="interpretation",
            status=GateStatus.CAUTION if (overloaded or load_flags) else GateStatus.PASS,
            detail=detail,
            protects_against=(
                "presenting results from an overloaded or near-saturated system as typical"
            ),
            cannot_prove="that real-world demand or capacity match these assumptions",
        )
    )

    ted = dm.time_in_ed_lower_bound(r).loc[m.index]
    over24 = int((ted > 1440).sum())
    worst = m.assign(t=ted).groupby("acuity_esi")["t"].max().to_dict() if len(m) else {}
    out.append(
        CheckResult(
            check="extreme_waits",
            category="interpretation",
            status=GateStatus.CAUTION if over24 else GateStatus.PASS,
            detail=(
                f"{over24} measured patient(s) spent over 24 h in the ED. Longest time by ESI "
                f"(min): {', '.join(f'ESI {k}: {v:.0f}' for k, v in sorted(worst.items()))}. "
                "Medians can hide these; check the by-ESI and extreme-wait tables."
            ),
            protects_against="a reassuring median that masks extreme waits for some patients",
            cannot_prove="what an acceptable wait is (that is a human, clinical judgment)",
        )
    )

    esi1 = m[m["acuity_esi"] == 1]
    atp = esi1["arrival_to_provider_min"].fillna(r.stop_time - esi1["arrival_min"])
    slow = int((atp > ESI1_REVIEW_THRESHOLD_MIN).sum())
    out.append(
        CheckResult(
            check="esi1_timeliness",
            category="interpretation",
            status=GateStatus.CAUTION if slow else GateStatus.PASS,
            detail=(
                f"{slow} of {len(esi1)} ESI-1 patients waited more than "
                f"{ESI1_REVIEW_THRESHOLD_MIN:.0f} min for a physician (longest "
                f"{atp.max() if len(atp) else float('nan'):.1f} min). The threshold is an "
                "illustrative review trigger, not a clinical standard; see the ESI-1 limitation."
            ),
            protects_against="overlooking delays to the most critical patients",
            cannot_prove="that real critical patients would be treated this way",
        )
    )

    unfinished = int((m["status"] == "unfinished").sum())
    cs = dm.censoring_summary(r).iloc[0]
    out.append(
        CheckResult(
            check="censoring",
            category="interpretation",
            status=GateStatus.CAUTION if unfinished else GateStatus.PASS,
            detail=(
                f"{unfinished} measured visit(s) unfinished at the stop. Completed-only median LOS "
                f"{cs['los_median_completed_only']:.0f} min vs. lower bound including unfinished "
                f"{cs['los_median_lower_bound_all']:.0f} min; "
                "completed-only figures are biased low."
                if unfinished
                else "All measured visits completed; LOS is not censored."
            ),
            protects_against="selection bias from computing LOS on completed visits only",
            cannot_prove="the true LOS of unfinished visits (only a lower bound)",
        )
    )

    measured_days = r.settings.arrival_days - r.settings.warmup_days
    out.append(
        CheckResult(
            check="observation_length",
            category="scope",
            status=GateStatus.CAUTION if measured_days < 3 else GateStatus.PASS,
            detail=(
                f"{measured_days} measured day(s) after {r.settings.warmup_days} warm-up day(s)."
            ),
            protects_against="conclusions from too short an observation window",
            cannot_prove="that the warm-up removed all start-up bias",
        )
    )
    for name, detail, protects in (
        (
            "single_seed",
            f"One random seed ({r.settings.seed}). Another seed gives different numbers; no "
            "confidence intervals exist yet (Milestone 6).",
            "treating one random draw as a stable estimate",
        ),
        (
            "no_bottleneck_claims",
            "The stage decomposition is descriptive for this run. No 'dominant bottleneck' may be "
            "claimed without multiple seeds and scenario comparisons.",
            "causal bottleneck claims from a single run",
        ),
        (
            "model_assumptions",
            "Non-preemptive ESI-1 handling, FIFO triage, physician concurrency of 2, a simplified "
            "bed-release stream, and no left-without-being-seen behavior. See limitations.",
            "forgetting that results are conditional on unvalidated assumptions",
        ),
    ):
        out.append(
            CheckResult(
                check=name,
                category="scope",
                status=GateStatus.CAUTION,
                detail=detail,
                protects_against=protects,
                cannot_prove="anything about a real hospital",
            )
        )
    return out, overloaded


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------


def validate_run(
    result: SimulationResult, *, intent: Literal["baseline", "stress_test"] = "baseline"
) -> ValidationReport:
    """Run every check. Integrity failures BLOCK; nothing here raises on bad data."""
    checks: list[CheckResult] = []
    for name, fn, protects, cannot in INTEGRITY_CHECKS:
        try:
            detail, status = fn(result), GateStatus.PASS
        except _Fail as exc:
            detail, status = str(exc), GateStatus.BLOCKED
        except Exception as exc:  # noqa: BLE001 - any crash on bad data must block, not escape
            detail, status = f"check could not run: {type(exc).__name__}: {exc}", GateStatus.BLOCKED
        checks.append(
            CheckResult(
                check=name,
                category="integrity",
                status=status,
                detail=detail,
                protects_against=protects,
                cannot_prove=cannot,
            )
        )

    blocked = any(c.status is GateStatus.BLOCKED for c in checks)
    overloaded = False
    if not blocked:  # interpretation only makes sense for internally consistent outputs
        interp, overloaded = _interpretation_checks(result, intent)
        checks.extend(interp)
    status = max((c.status for c in checks), key=_RANK.__getitem__)
    return ValidationReport(
        status=status,
        run_intent=intent,
        overloaded=overloaded,
        site_id=result.site_id,
        seed=result.settings.seed,
        result_fingerprint=fingerprint(result),
        checks=tuple(checks),
        limitations=SAFETY_LIMITATIONS,
        unavailable_metrics=dict(dm.UNAVAILABLE_METRICS),
    )


@dataclass(frozen=True)
class ReleasedResults:
    """The ONLY object a future interpretation layer should accept."""

    report: ValidationReport
    tables: dict[str, pd.DataFrame]


def release_for_interpretation(
    result: SimulationResult, report: ValidationReport
) -> ReleasedResults:
    """Package metrics for interpretation -- refused for BLOCKED or mismatched reports."""
    if report.status is GateStatus.BLOCKED:
        raise BlockedResultError(
            "validation status is BLOCKED; no numbers from this run may be released:\n"
            + "\n".join(report.reasons)
        )
    if fingerprint(result) != report.result_fingerprint:
        raise BlockedResultError(
            "the validation report does not match this result (fingerprint differs)"
        )
    tables = {
        "patient_summary": patient_summary(result),
        "resource_summary": resource_summary(result),
        "acuity_metrics": dm.acuity_metrics(result),
        "censoring_summary": dm.censoring_summary(result),
        "extreme_waits": dm.extreme_waits(result),
        "boarding_distribution": dm.boarding_distribution(result),
        "stage_decomposition": dm.stage_decomposition(result),
        "shift_utilization": dm.shift_utilization(result),
        "hourly_profile": dm.hourly_profile(result),
    }
    for name, table in tables.items():
        try:
            require_provenance(table, Provenance.SIMULATED)
        except ValueError as exc:  # pragma: no cover - every table is tagged by construction
            raise BlockedResultError(f"{name}: {exc}") from exc
    return ReleasedResults(report=report, tables=tables)
