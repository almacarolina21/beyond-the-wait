"""Tests for site-profile validation.

Pattern used throughout: start from the valid example (`site_dict`), break
ONE thing, and assert that validation rejects it with a relevant message.
If the valid example ever stops validating, `test_example_site_is_valid`
fails first and tells you the baseline itself is broken.
"""

from __future__ import annotations

import math
from typing import Any

import pytest
from pydantic import ValidationError

from beyond_the_wait.config import SiteProfile
from beyond_the_wait.data.provenance import Provenance


def _set(data: dict[str, Any], dotted_path: str, value: Any) -> None:
    """Set a nested value, e.g. _set(d, "staffing.nurses_on_duty.day", -1)."""
    *parents, last = dotted_path.split(".")
    for key in parents:
        data = data[key]
    data[last] = value


def _delete(data: dict[str, Any], dotted_path: str) -> None:
    *parents, last = dotted_path.split(".")
    for key in parents:
        data = data[key]
    del data[last]


def _assert_invalid(data: dict[str, Any], expected_message: str) -> None:
    with pytest.raises(ValidationError) as excinfo:
        SiteProfile.model_validate(data)
    assert expected_message in str(excinfo.value)


# --- The valid baseline ------------------------------------------------------


def test_example_site_is_valid(site: SiteProfile) -> None:
    assert site.site.site_id == "riverside_commons_ed"
    assert site.site.fictional is True
    assert site.provenance.label is Provenance.SYNTHETIC


def test_derived_values(site: SiteProfile) -> None:
    assert site.arrivals.expected_daily_arrivals == pytest.approx(190.5)
    assert site.treatment_spaces.total == 36 + 3 + 8 + 6


def test_validated_site_is_immutable(site: SiteProfile) -> None:
    with pytest.raises(ValidationError):
        site.treatment_spaces.acute_beds = 999  # type: ignore[misc]


# --- Negative staffing or bed capacity ---------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "staffing.physicians_on_duty.day",
        "staffing.nurses_on_duty.night",
        "treatment_spaces.acute_beds",
        "treatment_spaces.resuscitation_bays",
        "treatment_spaces.fast_track_chairs",
        "treatment_spaces.hallway_spaces",
        "inpatient.staffed_beds",
    ],
)
def test_negative_capacity_rejected(site_dict: dict[str, Any], path: str) -> None:
    _set(site_dict, path, -1)
    _assert_invalid(site_dict, "greater than or equal to 0")


@pytest.mark.parametrize("bad", [2.5, "4", True])
def test_headcount_must_be_whole_number(site_dict: dict[str, Any], bad: object) -> None:
    _set(site_dict, "staffing.physicians_on_duty.day", bad)
    _assert_invalid(site_dict, "staffing.physicians_on_duty.day")


@pytest.mark.parametrize("role", ["physicians_on_duty", "nurses_on_duty"])
def test_shift_with_zero_staff_rejected(site_dict: dict[str, Any], role: str) -> None:
    _set(site_dict, f"staffing.{role}.night", 0)
    _assert_invalid(site_dict, "every shift needs at least one")


def test_staffing_missing_a_shift_rejected(site_dict: dict[str, Any]) -> None:
    _delete(site_dict, "staffing.nurses_on_duty.evening")
    _assert_invalid(site_dict, "nurses_on_duty is missing shift(s): ['evening']")


def test_staffing_for_unknown_shift_rejected(site_dict: dict[str, Any]) -> None:
    _set(site_dict, "staffing.physicians_on_duty.swing", 1)
    _assert_invalid(site_dict, "lists unknown shift(s): ['swing']")


def test_shift_gap_rejected(site_dict: dict[str, Any]) -> None:
    site_dict["staffing"]["shifts"][2]["duration_hours"] = 6  # night ends 05:00
    _assert_invalid(site_dict, "uncovered hours: [5, 6]")


def test_shift_overlap_rejected(site_dict: dict[str, Any]) -> None:
    site_dict["staffing"]["shifts"][0]["duration_hours"] = 10  # day runs into evening
    _assert_invalid(site_dict, "overlapping hours: [15, 16]")


def test_duplicate_shift_names_rejected(site_dict: dict[str, Any]) -> None:
    site_dict["staffing"]["shifts"][1]["name"] = "day"
    _assert_invalid(site_dict, "shift names must be unique")


@pytest.mark.parametrize("hour", [-1, 24])
def test_shift_start_hour_out_of_range(site_dict: dict[str, Any], hour: int) -> None:
    site_dict["staffing"]["shifts"][0]["start_hour"] = hour
    _assert_invalid(site_dict, "start_hour")


def test_zero_total_treatment_spaces_rejected(site_dict: dict[str, Any]) -> None:
    for space in ("acute_beds", "resuscitation_bays", "fast_track_chairs", "hallway_spaces"):
        _set(site_dict, f"treatment_spaces.{space}", 0)
    _assert_invalid(site_dict, "at least one treatment space")


def test_admissions_without_inpatient_beds_rejected(site_dict: dict[str, Any]) -> None:
    _set(site_dict, "inpatient.staffed_beds", 0)
    _assert_invalid(site_dict, "staffed_beds is 0")


@pytest.mark.parametrize("field", ["typical_occupancy", "ed_admission_fraction"])
@pytest.mark.parametrize("bad", [-0.01, 1.01, math.nan])
def test_fractions_must_be_between_0_and_1(
    site_dict: dict[str, Any], field: str, bad: float
) -> None:
    _set(site_dict, f"inpatient.{field}", bad)
    _assert_invalid(site_dict, f"inpatient.{field}")


# --- Invalid arrival rates ---------------------------------------------------


@pytest.mark.parametrize(
    ("bad_rate", "message"),
    [
        (-1.0, "greater than or equal to 0"),
        (math.nan, "finite number"),
        (math.inf, "finite number"),
        (51.0, "less than or equal to 50"),
        ("12", "must be a number"),
        (None, "must be a number"),
        (True, "must be a number"),
    ],
)
def test_invalid_hourly_rate_rejected(
    site_dict: dict[str, Any], bad_rate: object, message: str
) -> None:
    site_dict["arrivals"]["hourly_rates"][10] = bad_rate
    _assert_invalid(site_dict, message)


@pytest.mark.parametrize("length", [0, 23, 25])
def test_arrivals_need_exactly_24_hours(site_dict: dict[str, Any], length: int) -> None:
    site_dict["arrivals"]["hourly_rates"] = [5.0] * length
    _assert_invalid(site_dict, "arrivals.hourly_rates")


def test_all_zero_arrivals_rejected(site_dict: dict[str, Any]) -> None:
    site_dict["arrivals"]["hourly_rates"] = [0.0] * 24
    _assert_invalid(site_dict, "all zero")


def test_integer_rates_are_accepted(site_dict: dict[str, Any]) -> None:
    site_dict["arrivals"]["hourly_rates"] = [8] * 24  # whole numbers are fine
    assert SiteProfile.model_validate(site_dict).arrivals.expected_daily_arrivals == 192


# --- Missing required fields -------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "schema_version",
        "site",
        "provenance",
        "staffing",
        "treatment_spaces",
        "arrivals",
        "inpatient",
        "site.fictional",
        "site.disclaimer",
        "provenance.label",
        "staffing.shifts",
        "staffing.physicians_on_duty",
        "staffing.source_note",
        "treatment_spaces.acute_beds",
        "arrivals.hourly_rates",
        "inpatient.ed_admission_fraction",
    ],
)
def test_missing_required_field_rejected(site_dict: dict[str, Any], path: str) -> None:
    _delete(site_dict, path)
    with pytest.raises(ValidationError) as excinfo:
        SiteProfile.model_validate(site_dict)
    errors = excinfo.value.errors()
    assert any(e["type"] == "missing" and ".".join(map(str, e["loc"])) == path for e in errors)


def test_optional_spaces_default_to_zero(site_dict: dict[str, Any]) -> None:
    _delete(site_dict, "treatment_spaces.hallway_spaces")
    assert SiteProfile.model_validate(site_dict).treatment_spaces.hallway_spaces == 0


def test_misspelled_field_rejected(site_dict: dict[str, Any]) -> None:
    site_dict["treatment_spaces"]["acute_bedz"] = 40
    _assert_invalid(site_dict, "Extra inputs are not permitted")


@pytest.mark.parametrize("version", [1, 3])
def test_unsupported_schema_version_rejected(site_dict: dict[str, Any], version: int) -> None:
    # Version 1 predates patient_mix/service_times; 3 doesn't exist yet.
    site_dict["schema_version"] = version
    _assert_invalid(site_dict, "schema_version")


# --- Provenance and fictional-site safeguards --------------------------------


@pytest.mark.parametrize("label", ["public", "REAL", "Synthetic", ""])
def test_unsupported_provenance_label_rejected(site_dict: dict[str, Any], label: str) -> None:
    _set(site_dict, "provenance.label", label)
    _assert_invalid(site_dict, "Unsupported provenance label")


def test_site_profile_cannot_be_labeled_simulated(site_dict: dict[str, Any]) -> None:
    _set(site_dict, "provenance.label", "SIMULATED")
    _assert_invalid(site_dict, "must have provenance label SYNTHETIC")


def test_site_profile_cannot_be_labeled_public(site_dict: dict[str, Any]) -> None:
    _set(site_dict, "provenance.label", "PUBLIC")
    _set(site_dict, "provenance.retrieved_on", "2026-11-15")
    _assert_invalid(site_dict, "must have provenance label SYNTHETIC")


@pytest.mark.parametrize("value", [False, "yes", None])
def test_site_must_be_marked_fictional(site_dict: dict[str, Any], value: object) -> None:
    _set(site_dict, "site.fictional", value)
    _assert_invalid(site_dict, "site.fictional")


@pytest.mark.parametrize(
    ("field", "name"),
    [
        ("display_name", "Lincoln Medical Center ED"),
        ("display_name", "St. Barnabas Emergency"),
        ("display_name", "JACOBI test site"),
        ("site_id", "montefiore_copy"),
        ("site_id", "north_central_bronx_ed"),
    ],
)
def test_real_hospital_names_rejected(site_dict: dict[str, Any], field: str, name: str) -> None:
    _set(site_dict, f"site.{field}", name)
    _assert_invalid(site_dict, "resembles a real hospital name")


def test_source_notes_are_required_to_be_meaningful(site_dict: dict[str, Any]) -> None:
    _set(site_dict, "arrivals.source_note", "tbd")
    _assert_invalid(site_dict, "arrivals.source_note")
