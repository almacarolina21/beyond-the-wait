"""Validated configuration models for a FICTIONAL emergency department.

A *site profile* describes the operating assumptions of one ED: staffing,
treatment spaces, hourly arrivals, and inpatient capacity. A *scenario*
describes a what-if change to that profile (e.g., "+1 evening physician").

Why Pydantic?
    Pydantic turns a plain dictionary (loaded from YAML) into typed Python
    objects and checks every value on the way in. If a config says a site has
    -3 beds, or forgets the arrivals section, we find out immediately with a
    clear message -- not hours later as a confusing simulation result.

Model-wide rules (see StrictModel):
    * extra="forbid"  -- unknown keys are errors, so a typo like
                         "acute_bedz" is caught instead of silently ignored.
    * frozen=True     -- models can't be changed after validation, so a
                         validated config can't drift into an invalid state.
                         Scenarios produce a *new* validated profile instead.

Every section requires a `source_note` explaining where its numbers came
from, so assumptions are always documented next to the values.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)

from beyond_the_wait.data.provenance import Provenance, ProvenanceRecord

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HOURS_PER_DAY = 24

# Plausibility ceiling for arrivals in any single hour. Even very large EDs
# average well under this; a higher number almost certainly signals a typo
# (e.g., a daily total entered as an hourly rate).
MAX_PLAUSIBLE_HOURLY_ARRIVALS = 50.0

# Name fragments of real Bronx hospitals. Fictional site names must not use
# them, so a configuration can't accidentally appear to describe a real
# institution. This is a guard against honest mistakes, not a guarantee --
# it can't recognize every real hospital name.
REAL_HOSPITAL_NAME_FRAGMENTS: tuple[str, ...] = (
    "lincoln",
    "barnabas",
    "jacobi",
    "north central bronx",
    "montefiore",
    "bronxcare",
)

# ---------------------------------------------------------------------------
# Reusable field types
# ---------------------------------------------------------------------------


def _require_real_number(value: object) -> object:
    """Reject booleans, strings, NaN, and infinity before range checks run.

    By default Pydantic would turn the string "12" into 12.0, and Python
    treats True as 1. In a config file both are almost always mistakes.
    NaN ("not a number") and infinity are checked here explicitly so the user
    sees a clear message instead of a confusing range error.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"must be a number, got {type(value).__name__} {value!r}")
    if not math.isfinite(value):
        raise ValueError(f"must be a finite number, got {value!r}")
    return value


Number = Annotated[float, BeforeValidator(_require_real_number)]

# Headcount = a whole number >= 0. StrictInt rejects 2.5, "2", and True.
Headcount = Annotated[StrictInt, Field(ge=0)]

# A fraction between 0 and 1 inclusive (e.g., 0.93 = 93% occupancy).
Fraction = Annotated[Number, Field(ge=0.0, le=1.0, allow_inf_nan=False)]

# Patients arriving per hour: >= 0, finite (no NaN/infinity), plausible.
HourlyRate = Annotated[Number, Field(ge=0.0, le=MAX_PLAUSIBLE_HOURLY_ARRIVALS, allow_inf_nan=False)]

# Machine-friendly identifier: lowercase words joined by underscores.
Slug = Annotated[str, Field(pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$", min_length=3, max_length=64)]

# Required explanation of where a section's numbers came from.
SourceNote = Annotated[str, Field(min_length=10)]

# ESI-like triage levels: 1 = most urgent, 5 = least urgent.
ESI_LEVELS: tuple[int, ...] = (1, 2, 3, 4, 5)

Weekday = Literal["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
WEEKDAYS: tuple[str, ...] = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)  # fmt: skip  -- index matches Python's date.weekday() (Monday = 0)

# A day-of-week multiplier scales every hourly rate on that day (1.10 = 10% busier).
DayMultiplier = Annotated[Number, Field(gt=0.0, le=3.0, allow_inf_nan=False)]

# Probabilities must sum to 1 within this tolerance (allows for decimal rounding).
PROBABILITY_SUM_TOLERANCE = 1e-6
# Allowed gap between the per-acuity admission probabilities and the overall
# ed_admission_fraction (see SiteProfile validation).
ADMISSION_CONSISTENCY_TOLERANCE = 0.005
# Ceiling for any single service-time assumption: 24 hours, in minutes.
MAX_SERVICE_MINUTES = 24 * 60.0
# z-score of the 90th percentile of a standard normal distribution.
Z_90 = 1.2815515655446004


def _check_esi_keys(mapping: dict[int, object], name: str) -> None:
    """Every ESI level 1-5 must appear exactly once, and nothing else."""
    missing = sorted(set(ESI_LEVELS) - set(mapping))
    unknown = sorted(set(mapping) - set(ESI_LEVELS))
    if missing or unknown:
        raise ValueError(
            f"{name} must have exactly ESI levels {list(ESI_LEVELS)}; "
            f"missing: {missing}, unsupported: {unknown}"
        )


class StrictModel(BaseModel):
    """Base class: forbid unknown keys, freeze after validation, trim strings."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


# ---------------------------------------------------------------------------
# Staffing (physicians and nurses)
# ---------------------------------------------------------------------------


class Shift(StrictModel):
    """A named block of hours, e.g. day shift 07:00 for 8 hours.

    Shifts may wrap past midnight (night: start 23, duration 8 -> 23:00-07:00).
    """

    name: Slug
    start_hour: Annotated[StrictInt, Field(ge=0, le=23)]
    duration_hours: Annotated[StrictInt, Field(ge=1, le=HOURS_PER_DAY)]

    def hours_covered(self) -> list[int]:
        """Clock hours (0-23) this shift covers, wrapping past midnight."""
        return [(self.start_hour + i) % HOURS_PER_DAY for i in range(self.duration_hours)]


class StaffingPlan(StrictModel):
    """How many physicians and nurses are on duty during each shift.

    Rules enforced:
      * Shifts together cover all 24 hours exactly once (no gaps, no overlaps).
      * Physician and nurse headcounts are given for every shift -- no more,
        no less -- and are never negative.
      * Every shift has at least one physician and one nurse (a 24/7 ED is
        never unstaffed).

    Simplification: physicians and nurses share one shift pattern. Real EDs
    often use different patterns per role; we can extend this later.
    """

    shifts: Annotated[list[Shift], Field(min_length=1)]
    physicians_on_duty: dict[str, Headcount]
    nurses_on_duty: dict[str, Headcount]
    source_note: SourceNote

    @model_validator(mode="after")
    def _check_shifts_and_headcounts(self) -> StaffingPlan:
        names = [shift.name for shift in self.shifts]

        duplicates = sorted(name for name, n in Counter(names).items() if n > 1)
        if duplicates:
            raise ValueError(f"shift names must be unique; duplicated: {duplicates}")

        coverage = Counter(hour for shift in self.shifts for hour in shift.hours_covered())
        uncovered = [h for h in range(HOURS_PER_DAY) if coverage[h] == 0]
        overlapping = [h for h in range(HOURS_PER_DAY) if coverage[h] > 1]
        if uncovered or overlapping:
            raise ValueError(
                "shifts must cover each hour of the day exactly once; "
                f"uncovered hours: {uncovered}, overlapping hours: {overlapping}"
            )

        for field_name, role in (("physicians_on_duty", "physician"), ("nurses_on_duty", "nurse")):
            counts: dict[str, int] = getattr(self, field_name)
            missing = sorted(set(names) - set(counts))
            unknown = sorted(set(counts) - set(names))
            if missing:
                raise ValueError(f"{field_name} is missing shift(s): {missing}")
            if unknown:
                raise ValueError(f"{field_name} lists unknown shift(s): {unknown}")
            unstaffed = [name for name in names if counts[name] == 0]
            if unstaffed:
                raise ValueError(f"every shift needs at least one {role}; zero on: {unstaffed}")
        return self


# ---------------------------------------------------------------------------
# ED treatment spaces
# ---------------------------------------------------------------------------


class TreatmentSpaces(StrictModel):
    """Physical places a patient can be treated in the ED."""

    acute_beds: Headcount
    resuscitation_bays: Headcount
    fast_track_chairs: Headcount = 0
    hallway_spaces: Headcount = 0
    source_note: SourceNote

    @property
    def total(self) -> int:
        """Total treatment spaces of all types."""
        return (
            self.acute_beds + self.resuscitation_bays + self.fast_track_chairs + self.hallway_spaces
        )

    @model_validator(mode="after")
    def _at_least_one_space(self) -> TreatmentSpaces:
        if self.total < 1:
            raise ValueError("an ED needs at least one treatment space")
        return self


# ---------------------------------------------------------------------------
# Hourly patient arrival assumptions
# ---------------------------------------------------------------------------


class ArrivalAssumptions(StrictModel):
    """Average number of patients arriving in each clock hour (00:00 to 23:00).

    `hourly_rates[0]` is the average for 00:00-00:59, `hourly_rates[13]` for
    13:00-13:59, and so on. These are *averages*; the synthetic generator
    adds random (Poisson) variation around them.

    Optional `day_of_week_multipliers` scale every hour of a given weekday
    (e.g., monday: 1.10 = 10% busier than average). They must average exactly
    1.0 across the week, so `hourly_rates` still describe an average day.
    """

    hourly_rates: Annotated[
        list[HourlyRate], Field(min_length=HOURS_PER_DAY, max_length=HOURS_PER_DAY)
    ]
    day_of_week_multipliers: dict[Weekday, DayMultiplier] | None = None
    source_note: SourceNote

    @property
    def expected_daily_arrivals(self) -> float:
        """Sum of the 24 hourly averages = expected arrivals on an average day."""
        return sum(self.hourly_rates)

    def multiplier_for(self, weekday_index: int) -> float:
        """Multiplier for a weekday (0 = Monday, as in date.weekday())."""
        if self.day_of_week_multipliers is None:
            return 1.0
        return self.day_of_week_multipliers[WEEKDAYS[weekday_index]]  # type: ignore[index]

    @model_validator(mode="after")
    def _check_arrivals(self) -> ArrivalAssumptions:
        if self.expected_daily_arrivals <= 0:
            raise ValueError("hourly_rates are all zero; an ED must expect some arrivals")
        multipliers = self.day_of_week_multipliers
        if multipliers is not None:
            missing = [d for d in WEEKDAYS if d not in multipliers]
            if missing:
                raise ValueError(f"day_of_week_multipliers is missing: {missing}")
            mean = sum(multipliers.values()) / len(WEEKDAYS)
            if abs(mean - 1.0) > 0.001:
                raise ValueError(
                    f"day_of_week_multipliers must average 1.0 across the week (got {mean:.4f}) "
                    "so that hourly_rates keep meaning 'an average day'"
                )
            peak = max(self.hourly_rates) * max(multipliers.values())
            if peak > MAX_PLAUSIBLE_HOURLY_ARRIVALS:
                raise ValueError(
                    f"busiest hour x busiest day gives {peak:.1f} arrivals/hour, above the "
                    f"plausibility ceiling of {MAX_PLAUSIBLE_HOURLY_ARRIVALS}"
                )
        return self


# ---------------------------------------------------------------------------
# Patient mix: acuity and conditional probabilities
# ---------------------------------------------------------------------------


class PatientMix(StrictModel):
    """Who arrives: acuity mix and, FOR EACH ACUITY LEVEL, the chance of needing
    a lab test, imaging, or (hypothetically) admission.

    These are *conditional* probabilities: lab_probability_by_acuity[2] = 0.85
    means "of ESI-2 patients, 85% need a lab test" -- not "85% of all
    patients". The overall share follows from the law of total probability:

        P(lab) = sum over levels a of  P(acuity = a) x P(lab | acuity = a)

    Simplification: given acuity, lab, imaging, and admission are drawn
    independently. In reality they're correlated (e.g., imaging often
    precedes admission). See docs/SYNTHETIC_DATA_ASSUMPTIONS.md.
    """

    acuity_distribution: dict[StrictInt, Fraction]
    lab_probability_by_acuity: dict[StrictInt, Fraction]
    imaging_probability_by_acuity: dict[StrictInt, Fraction]
    admission_probability_by_acuity: dict[StrictInt, Fraction]
    source_note: SourceNote

    @model_validator(mode="after")
    def _check_mix(self) -> PatientMix:
        for name in (
            "acuity_distribution",
            "lab_probability_by_acuity",
            "imaging_probability_by_acuity",
            "admission_probability_by_acuity",
        ):
            _check_esi_keys(getattr(self, name), name)
        total = sum(self.acuity_distribution.values())
        if abs(total - 1.0) > PROBABILITY_SUM_TOLERANCE:
            raise ValueError(f"acuity_distribution must sum to 1.0 (got {total:.6f})")
        return self

    def _overall(self, conditional: dict[int, float]) -> float:
        return sum(self.acuity_distribution[a] * conditional[a] for a in ESI_LEVELS)

    @property
    def implied_admission_fraction(self) -> float:
        """Overall admission share implied by the acuity mix (law of total probability)."""
        return self._overall(self.admission_probability_by_acuity)

    @property
    def implied_lab_fraction(self) -> float:
        return self._overall(self.lab_probability_by_acuity)

    @property
    def implied_imaging_fraction(self) -> float:
        return self._overall(self.imaging_probability_by_acuity)


# ---------------------------------------------------------------------------
# Service-time assumptions (inputs for the Milestone 4 simulation)
# ---------------------------------------------------------------------------

ServiceMinutes = Annotated[Number, Field(gt=0.0, le=MAX_SERVICE_MINUTES, allow_inf_nan=False)]


class LognormalMinutes(StrictModel):
    """How long a task takes, described by its median and 90th percentile.

    Why lognormal? Task durations are always positive and "right-skewed":
    most are near the typical time, a few take much longer. A lognormal
    distribution has exactly that shape. We describe it with two numbers an
    administrator can reason about -- "typically 35 minutes; 9 in 10 finish
    within 70" -- and convert them to the distribution's internal parameters.

    These are durations of WORK (e.g., a lab result's turnaround), not waits
    or outcomes. Waiting emerges later, in the simulation, when demand
    exceeds capacity.
    """

    median: ServiceMinutes
    p90: ServiceMinutes

    @model_validator(mode="after")
    def _p90_above_median(self) -> LognormalMinutes:
        if self.p90 <= self.median:
            raise ValueError(
                f"p90 ({self.p90}) must be greater than median ({self.median}); "
                "durations vary, so the 90th percentile is always above the median"
            )
        return self

    @property
    def mu(self) -> float:
        """Mean of log(duration)."""
        return math.log(self.median)

    @property
    def sigma(self) -> float:
        """Standard deviation of log(duration), chosen so the 90th percentile = p90."""
        return (math.log(self.p90) - math.log(self.median)) / Z_90

    @property
    def mean(self) -> float:
        """Average duration in minutes (above the median because of the long tail)."""
        return math.exp(self.mu + self.sigma**2 / 2)


class ServiceTimeAssumptions(StrictModel):
    """Assumed durations of ED tasks, in minutes. Used by the Milestone 4 simulation.

    Not used by the synthetic patient generator, which produces patient
    attributes only.
    """

    triage: LognormalMinutes
    provider_evaluation_by_acuity: dict[StrictInt, LognormalMinutes]
    lab_turnaround: LognormalMinutes
    imaging_turnaround: LognormalMinutes
    discharge_process: LognormalMinutes
    source_note: SourceNote

    @model_validator(mode="after")
    def _check_levels(self) -> ServiceTimeAssumptions:
        _check_esi_keys(self.provider_evaluation_by_acuity, "provider_evaluation_by_acuity")
        return self


# ---------------------------------------------------------------------------
# Inpatient capacity
# ---------------------------------------------------------------------------


class InpatientCapacity(StrictModel):
    """Hospital beds available to patients admitted from the ED.

    When these beds are full, admitted patients *board* in the ED -- one of
    the four constraint types this project investigates.
    """

    staffed_beds: Headcount
    typical_occupancy: Fraction = Field(description="Share of staffed beds usually occupied.")
    ed_admission_fraction: Fraction = Field(description="Share of ED patients who are admitted.")
    source_note: SourceNote

    @model_validator(mode="after")
    def _admissions_need_beds(self) -> InpatientCapacity:
        if self.ed_admission_fraction > 0 and self.staffed_beds == 0:
            raise ValueError(
                "patients are admitted (ed_admission_fraction > 0) but staffed_beds is 0"
            )
        return self


# ---------------------------------------------------------------------------
# Hospital / site configuration
# ---------------------------------------------------------------------------


def _reject_real_hospital_names(value: str) -> str:
    normalized = re.sub(r"[^a-z ]+", " ", value.lower().replace("_", " "))
    normalized = " ".join(normalized.split())
    for fragment in REAL_HOSPITAL_NAME_FRAGMENTS:
        if fragment in normalized:
            raise ValueError(
                f"{value!r} resembles a real hospital name ({fragment!r}). "
                "Site profiles must be fictional; choose an invented name."
            )
    return value


class SiteInfo(StrictModel):
    """Identity of a fictional site. `fictional: true` is mandatory."""

    site_id: Slug
    display_name: Annotated[str, Field(min_length=3, max_length=120)]
    fictional: Literal[True] = Field(
        description="Must be true. This project models fictional sites only."
    )
    disclaimer: Annotated[str, Field(min_length=20)]

    _no_real_names = field_validator("site_id", "display_name")(_reject_real_hospital_names)


class SiteProfile(StrictModel):
    """Complete configuration for one fictional emergency department.

    schema_version 2 (Milestone 3) added `patient_mix`, `service_times`, and
    optional day-of-week arrival multipliers.
    """

    schema_version: Literal[2]
    site: SiteInfo
    provenance: ProvenanceRecord
    staffing: StaffingPlan
    treatment_spaces: TreatmentSpaces
    arrivals: ArrivalAssumptions
    patient_mix: PatientMix
    service_times: ServiceTimeAssumptions
    inpatient: InpatientCapacity

    @model_validator(mode="after")
    def _site_profiles_are_synthetic(self) -> SiteProfile:
        # A site profile is a set of *assumptions we wrote*, so it is SYNTHETIC
        # by definition. PUBLIC is reserved for published measurements and
        # SIMULATED for model outputs; allowing either here would mislabel it.
        if self.provenance.label is not Provenance.SYNTHETIC:
            raise ValueError(
                f"site profiles must have provenance label SYNTHETIC, got {self.provenance.label}"
            )
        return self

    @model_validator(mode="after")
    def _admission_assumptions_agree(self) -> SiteProfile:
        # The same assumption is stated twice: once overall (inpatient section)
        # and once per acuity level (patient_mix). They must agree, or the
        # simulation would use two contradictory admission rates.
        implied = self.patient_mix.implied_admission_fraction
        stated = self.inpatient.ed_admission_fraction
        if abs(implied - stated) > ADMISSION_CONSISTENCY_TOLERANCE:
            raise ValueError(
                f"admission assumptions disagree: patient_mix implies {implied:.4f} of patients "
                f"are admitted, but inpatient.ed_admission_fraction is {stated:.4f}"
            )
        return self


# ---------------------------------------------------------------------------
# Intervention scenarios
# ---------------------------------------------------------------------------

StaffRole = Literal["physicians", "nurses"]
TreatmentSpaceName = Literal[
    "acute_beds", "resuscitation_bays", "fast_track_chairs", "hallway_spaces"
]
NonZeroChange = Annotated[StrictInt, Field(description="Positive adds, negative removes.")]


def _nonzero(value: int) -> int:
    if value == 0:
        raise ValueError("a change of 0 has no effect; remove it or use a non-zero number")
    return value


class StaffingChange(StrictModel):
    """Add or remove staff of one role on one shift."""

    role: StaffRole
    shift: Slug
    change: NonZeroChange

    _check_nonzero = field_validator("change")(_nonzero)


class TreatmentSpaceChange(StrictModel):
    """Add or remove treatment spaces of one type."""

    space: TreatmentSpaceName
    change: NonZeroChange

    _check_nonzero = field_validator("change")(_nonzero)


class Scenario(StrictModel):
    """A named what-if change applied on top of one site profile.

    Changes are *relative* (e.g., +1 physician), so the same scenario reads
    naturally against its baseline. A scenario is validated twice: once on
    its own (here), and again after it is applied, because a change that is
    valid in isolation (-3 nurses) can still produce an invalid site.
    """

    schema_version: Literal[1]
    scenario_id: Slug
    name: Annotated[str, Field(min_length=3, max_length=120)]
    description: SourceNote
    applies_to_site: Slug
    staffing_changes: list[StaffingChange] = Field(default_factory=list)
    treatment_space_changes: list[TreatmentSpaceChange] = Field(default_factory=list)
    inpatient_bed_change: StrictInt = 0
    arrival_multiplier: Annotated[Number, Field(gt=0.0, le=3.0, allow_inf_nan=False)] = 1.0

    @model_validator(mode="after")
    def _check_changes(self) -> Scenario:
        has_change = (
            self.staffing_changes
            or self.treatment_space_changes
            or self.inpatient_bed_change != 0
            or self.arrival_multiplier != 1.0
        )
        if not has_change:
            raise ValueError("scenario makes no changes; the baseline is the site profile itself")

        staff_keys = Counter((c.role, c.shift) for c in self.staffing_changes)
        space_keys = Counter(c.space for c in self.treatment_space_changes)
        dup_staff = sorted(k for k, n in staff_keys.items() if n > 1)
        dup_space = sorted(k for k, n in space_keys.items() if n > 1)
        if dup_staff or dup_space:
            raise ValueError(
                "each role/shift and space type may appear only once; "
                f"duplicated staffing: {dup_staff}, duplicated spaces: {dup_space}"
            )
        return self
