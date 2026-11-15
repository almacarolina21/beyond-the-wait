"""Catalog of the public ED measures we ingest -- and what each one does NOT mean.

Different ED measures answer different questions. They are easy to confuse:

    ED length of stay (OP-18)   How long did patients who went HOME spend in
                                the ED, from arrival to departure? (minutes)
    Left without being seen     What share of patients LEFT before a physician,
    (OP-22)                     APN, or PA evaluated them? (percent)
    Door-to-provider            How long until a patient was first seen by a
                                provider? -> NOT AVAILABLE in any source we use.

Each measure gets a `MeasureKind`, and code that needs a specific kind must
ask for it by kind. Asking for door-to-provider raises an error that explains
why it's unavailable, rather than silently substituting OP-18 or OP-22.

Definitions quote or paraphrase the publishers' own documentation; the
source URL for each is stored alongside it. See
docs/PUBLIC_DATA_METHODOLOGY.md for the full discussion.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from beyond_the_wait.data.public_sources import (
    CMS_DICTIONARY,
    CMS_TIMELY_EFFECTIVE_CARE,
    NYS_ED_ENCOUNTERS_BY_FACILITY,
)

OP18_SPEC_URL = "https://ecqi.healthit.gov/system/files/ecqm/measures/CMS32v7.html"
OP22_GUIDE_URL = (
    "https://www.qualityreportingcenter.com/globalassets/2020/01/oqr/"
    "qrc-hoqr-wbm-submission-guidelines-2020--final.pdf"
)


class MeasureKind(StrEnum):
    """What concept a measure captures. Kinds are NOT interchangeable."""

    ED_LENGTH_OF_STAY_DISCHARGED = "ed_length_of_stay_discharged"
    LEFT_WITHOUT_BEING_SEEN = "left_without_being_seen"
    ED_VOLUME_CATEGORY = "ed_volume_category"
    ED_ENCOUNTER_COUNT = "ed_encounter_count"
    # Listed so code can ask for it explicitly and get a clear "not available".
    DOOR_TO_PROVIDER = "door_to_provider"
    ADMITTED_PATIENT_BOARDING_TIME = "admitted_patient_boarding_time"


class Unit(StrEnum):
    MINUTES = "minutes"
    PERCENT = "percent"
    CATEGORY = "category"
    ENCOUNTERS = "encounters"


class MeasureDefinition(BaseModel):
    """Plain-language definition of one published measure."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    measure_id: str
    source_key: str
    kind: MeasureKind
    unit: Unit
    short_name: str
    definition: str
    population: str
    does_not_measure: tuple[str, ...]
    lower_is_better: bool | None
    definition_source: str


_OP18_POPULATION = (
    "ED visits that did NOT end in inpatient admission at the same facility; "
    "patients who died in the ED are excluded (OP-18 measure family; eCQM CMS32 "
    "specification, version 7 reviewed)."
)
_OP18_NOT = (
    "Time spent by patients admitted to the hospital (so it does not capture boarding)",
    "Time from arrival to first provider (door-to-provider)",
    "Why the time was long (staffing, beds, tests, or coordination)",
)


def _op18(measure_id: str, short_name: str, definition: str, population: str) -> MeasureDefinition:
    return MeasureDefinition(
        measure_id=measure_id,
        source_key=CMS_TIMELY_EFFECTIVE_CARE.key,
        kind=MeasureKind.ED_LENGTH_OF_STAY_DISCHARGED,
        unit=Unit.MINUTES,
        short_name=short_name,
        definition=definition,
        population=population,
        does_not_measure=_OP18_NOT,
        lower_is_better=True,
        definition_source=OP18_SPEC_URL,
    )


_NYS_COUNT_NOT = (
    "How long patients waited or stayed",
    "Whether patients left without being seen",
    "Visit-level detail (these are facility totals per period)",
)


def _nys_count(measure_id: str, short_name: str, definition: str) -> MeasureDefinition:
    return MeasureDefinition(
        measure_id=measure_id,
        source_key=NYS_ED_ENCOUNTERS_BY_FACILITY.key,
        kind=MeasureKind.ED_ENCOUNTER_COUNT,
        unit=Unit.ENCOUNTERS,
        short_name=short_name,
        definition=definition,
        population="ED encounters reported to SPARCS by the facility in the period.",
        does_not_measure=_NYS_COUNT_NOT,
        lower_is_better=None,
        definition_source=NYS_ED_ENCOUNTERS_BY_FACILITY.landing_page,
    )


MEASURES: dict[str, MeasureDefinition] = {
    m.measure_id: m
    for m in (
        _op18(
            "OP_18a",
            "Median ED time, all discharged patients",
            "Median minutes from ED arrival to ED departure, including psychiatric/mental "
            "health patients and patients transferred to another facility.",
            _OP18_POPULATION,
        ),
        _op18(
            "OP_18b",
            "Median ED time, discharged (excl. psych & transfers)",
            "Median minutes from ED arrival to ED departure, excluding patients transferred "
            "to another facility and psychiatric/mental health patients.",
            _OP18_POPULATION,
        ),
        _op18(
            "OP_18c",
            "Median ED time, psychiatric/mental health patients",
            "Median minutes from ED arrival to ED departure for psychiatric/mental health "
            "patients. CMS notes it appears only in the downloadable database.",
            _OP18_POPULATION,
        ),
        _op18(
            "OP_18d",
            "Median ED time, patients transferred out",
            "Median minutes from ED arrival to ED departure for patients transferred to "
            "another facility.",
            _OP18_POPULATION,
        ),
        MeasureDefinition(
            measure_id="OP_22",
            source_key=CMS_TIMELY_EFFECTIVE_CARE.key,
            kind=MeasureKind.LEFT_WITHOUT_BEING_SEEN,
            unit=Unit.PERCENT,
            short_name="Left without being seen",
            definition=(
                "Percentage of ED patients who left before being evaluated by a physician, "
                "advanced practice nurse, or physician assistant. Submitted annually by the "
                "hospital as an aggregate."
            ),
            population="All ED patients in the reporting period (hospital-submitted aggregate).",
            does_not_measure=(
                "How long patients waited before leaving",
                "Door-to-provider time for patients who stayed",
                "Patients who left after being seen (e.g., against medical advice)",
            ),
            lower_is_better=True,
            definition_source=OP22_GUIDE_URL,
        ),
        MeasureDefinition(
            measure_id="EDV",
            source_key=CMS_TIMELY_EFFECTIVE_CARE.key,
            kind=MeasureKind.ED_VOLUME_CATEGORY,
            unit=Unit.CATEGORY,
            short_name="ED volume category",
            definition="Emergency department volume, reported as a category (low to very high).",
            population="All ED visits in the reporting period.",
            does_not_measure=("An exact visit count", "Crowding, timeliness, or acuity"),
            lower_is_better=None,
            definition_source=CMS_DICTIONARY,
        ),
        _nys_count(
            "NYS_ED_TOTAL",
            "Total ED encounters",
            "Total ED encounters: treat-and-release + requiring ambulatory surgery + "
            "admitted as inpatient.",
        ),
        _nys_count(
            "NYS_ED_TREAT_AND_RELEASE",
            "Treat-and-release ED encounters",
            "ED encounters where the patient was released without ambulatory surgery or "
            "inpatient admission.",
        ),
        _nys_count(
            "NYS_ED_AMBULATORY_SURGERY",
            "ED encounters requiring ambulatory surgery",
            "ED encounters that required use of an operating room for ambulatory surgery.",
        ),
        _nys_count(
            "NYS_ED_ADMITTED",
            "ED encounters admitted as inpatient",
            "ED encounters that resulted in the patient being admitted to the facility. "
            "A count only: it says nothing about how long admitted patients waited.",
        ),
    )
}

CMS_ED_MEASURE_IDS: tuple[str, ...] = tuple(
    m.measure_id for m in MEASURES.values() if m.source_key == CMS_TIMELY_EFFECTIVE_CARE.key
)

_UNAVAILABLE_EXPLANATIONS: dict[MeasureKind, str] = {
    MeasureKind.DOOR_TO_PROVIDER: (
        "Door-to-provider time is not reported in the current CMS Timely and Effective Care "
        "file or the NYS DOH datasets reviewed in Milestone 2. OP-18 (time to departure for "
        "discharged patients) and OP-22 (left without being seen) are different measures "
        "and must not be used as substitutes."
    ),
    MeasureKind.ADMITTED_PATIENT_BOARDING_TIME: (
        "No reviewed public dataset reports how long admitted patients waited in the ED. "
        "OP-18 excludes admitted patients, and NYS admission counts contain no times."
    ),
}


class MeasureNotAvailableError(LookupError):
    """Raised when a requested kind of measure has no public source."""


def measures_of_kind(kind: MeasureKind) -> list[MeasureDefinition]:
    """Return every cataloged measure of a kind, or raise if none exists."""
    found = [m for m in MEASURES.values() if m.kind is kind]
    if not found:
        reason = _UNAVAILABLE_EXPLANATIONS.get(kind, "No public source is cataloged.")
        raise MeasureNotAvailableError(f"No public measure of kind {kind.value!r}. {reason}")
    return found
