"""Tests for the Milestone 3 config sections: weekday pattern, patient mix, service times."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pytest
from pydantic import ValidationError

from beyond_the_wait.config import SiteProfile
from beyond_the_wait.config.models import LognormalMinutes


def _invalid(data: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError) as excinfo:
        SiteProfile.model_validate(data)
    assert message in str(excinfo.value)


# --- Day-of-week multipliers ------------------------------------------------------


def test_weekday_multiplier_lookup(site: SiteProfile) -> None:
    assert site.arrivals.multiplier_for(0) == 1.10  # Monday
    assert site.arrivals.multiplier_for(6) == 0.96  # Sunday


def test_weekday_multipliers_are_optional(site_dict: dict[str, Any]) -> None:
    del site_dict["arrivals"]["day_of_week_multipliers"]
    site = SiteProfile.model_validate(site_dict)
    assert all(site.arrivals.multiplier_for(d) == 1.0 for d in range(7))


def test_missing_weekday_rejected(site_dict: dict[str, Any]) -> None:
    del site_dict["arrivals"]["day_of_week_multipliers"]["friday"]
    _invalid(site_dict, "missing: ['friday']")


def test_unknown_weekday_rejected(site_dict: dict[str, Any]) -> None:
    site_dict["arrivals"]["day_of_week_multipliers"]["funday"] = 1.0
    _invalid(site_dict, "day_of_week_multipliers")


def test_weekday_multipliers_must_average_one(site_dict: dict[str, Any]) -> None:
    site_dict["arrivals"]["day_of_week_multipliers"]["monday"] = 1.50
    _invalid(site_dict, "must average 1.0")


@pytest.mark.parametrize("bad", [0, -0.5, math.inf, "high"])
def test_weekday_multiplier_values(site_dict: dict[str, Any], bad: object) -> None:
    site_dict["arrivals"]["day_of_week_multipliers"]["monday"] = bad
    _invalid(site_dict, "day_of_week_multipliers.monday")


def test_busiest_hour_times_busiest_day_has_ceiling(site_dict: dict[str, Any]) -> None:
    site_dict["arrivals"]["hourly_rates"][11] = 48.0  # x 1.10 on Monday = 52.8
    _invalid(site_dict, "above the plausibility ceiling")


# --- Patient mix --------------------------------------------------------------------


def test_implied_overall_shares(site: SiteProfile) -> None:
    mix = site.patient_mix
    # Law of total probability: sum of P(acuity) x P(event | acuity).
    assert mix.implied_admission_fraction == pytest.approx(0.1979)
    assert mix.implied_lab_fraction == pytest.approx(0.554)
    assert mix.implied_imaging_fraction == pytest.approx(0.448)


def test_acuity_distribution_must_sum_to_one(site_dict: dict[str, Any]) -> None:
    site_dict["patient_mix"]["acuity_distribution"][3] = 0.50  # total 1.05
    _invalid(site_dict, "must sum to 1.0")


@pytest.mark.parametrize(
    "field",
    [
        "acuity_distribution",
        "lab_probability_by_acuity",
        "imaging_probability_by_acuity",
        "admission_probability_by_acuity",
    ],
)
def test_every_esi_level_required(site_dict: dict[str, Any], field: str) -> None:
    del site_dict["patient_mix"][field][5]
    _invalid(site_dict, "missing: [5]")


def test_unsupported_esi_level_rejected(site_dict: dict[str, Any]) -> None:
    site_dict["patient_mix"]["lab_probability_by_acuity"][6] = 0.1
    _invalid(site_dict, "unsupported: [6]")


@pytest.mark.parametrize("bad", [-0.1, 1.1, math.nan, True, "0.5"])
def test_probabilities_must_be_valid(site_dict: dict[str, Any], bad: object) -> None:
    site_dict["patient_mix"]["imaging_probability_by_acuity"][2] = bad
    _invalid(site_dict, "imaging_probability_by_acuity.2")


def test_string_esi_keys_rejected(site_dict: dict[str, Any]) -> None:
    lab = site_dict["patient_mix"]["lab_probability_by_acuity"]
    lab["1"] = lab.pop(1)
    _invalid(site_dict, "lab_probability_by_acuity")


def test_admission_assumptions_must_agree(site_dict: dict[str, Any]) -> None:
    site_dict["inpatient"]["ed_admission_fraction"] = 0.30
    _invalid(site_dict, "admission assumptions disagree")


def test_patient_mix_needs_source_note(site_dict: dict[str, Any]) -> None:
    del site_dict["patient_mix"]["source_note"]
    _invalid(site_dict, "patient_mix.source_note")


# --- Service times --------------------------------------------------------------------


def test_lognormal_parameters_reproduce_median_and_p90() -> None:
    t = LognormalMinutes(median=35, p90=70)
    assert math.exp(t.mu) == pytest.approx(35)
    assert math.exp(t.mu + 1.2815515655446004 * t.sigma) == pytest.approx(70)
    assert t.mean > t.median  # long right tail pulls the mean up


def test_lognormal_parameters_match_numpy_samples() -> None:
    t = LognormalMinutes(median=35, p90=70)
    samples = np.random.default_rng(0).lognormal(t.mu, t.sigma, size=200_000)
    assert np.median(samples) == pytest.approx(35, rel=0.01)
    assert np.quantile(samples, 0.9) == pytest.approx(70, rel=0.01)


@pytest.mark.parametrize(
    ("median", "p90", "message"),
    [
        (35, 35, "must be greater than median"),
        (35, 20, "must be greater than median"),
        (0, 10, "greater than 0"),
        (-5, 10, "greater than 0"),
        (35, 2000, "less than or equal to 1440"),
    ],
)
def test_lognormal_validation(median: float, p90: float, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        LognormalMinutes(median=median, p90=p90)


def test_provider_evaluation_needs_every_esi_level(site_dict: dict[str, Any]) -> None:
    del site_dict["service_times"]["provider_evaluation_by_acuity"][1]
    _invalid(site_dict, "provider_evaluation_by_acuity must have exactly ESI levels")


@pytest.mark.parametrize(
    "field", ["triage", "lab_turnaround", "imaging_turnaround", "discharge_process"]
)
def test_service_time_fields_required(site_dict: dict[str, Any], field: str) -> None:
    del site_dict["service_times"][field]
    _invalid(site_dict, f"service_times.{field}")
