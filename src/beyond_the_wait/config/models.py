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
    (Milestone 3) will add realistic random variation around them.
    """

    hourly_rates: Annotated[
        list[HourlyRate], Field(min_length=HOURS_PER_DAY, max_length=HOURS_PER_DAY)
    ]
    source_note: SourceNote

    @property
    def expected_daily_arrivals(self) -> float:
        """Sum of the 24 hourly averages = expected arrivals per day."""
        return sum(self.hourly_rates)

    @model_validator(mode="after")
    def _some_arrivals(self) -> ArrivalAssumptions:
        if self.expected_daily_arrivals <= 0:
            raise ValueError("hourly_rates are all zero; an ED must expect some arrivals")
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
    """Complete configuration for one fictional emergency department."""

    schema_version: Literal[1]
    site: SiteInfo
    provenance: ProvenanceRecord
    staffing: StaffingPlan
    treatment_spaces: TreatmentSpaces
    arrivals: ArrivalAssumptions
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
