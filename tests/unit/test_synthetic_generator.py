"""Tests for the synthetic patient generator.

Statistical tests use FIXED seeds, so they give the same answer every run.
Tolerances come from probability theory (e.g. "within 4 standard errors"),
so a correct generator passes them with overwhelming probability, and a
broken one (wrong rate, wrong proportions) fails clearly.
"""

from __future__ import annotations

import copy
import math
from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd
import pytest

from beyond_the_wait.config import SiteProfile
from beyond_the_wait.config.models import ESI_LEVELS
from beyond_the_wait.data.errors import SyntheticDataError
from beyond_the_wait.data.provenance import Provenance, require_provenance
from beyond_the_wait.data.synthetic_generator import (
    GENERATED_COLUMNS,
    expected_arrivals,
    generate_patients,
    hourly_rate_schedule,
    main,
    summarize,
    validate_synthetic_patients,
)

WEDNESDAY = date(2026, 11, 18)  # multiplier 1.00
SUNDAY = date(2026, 11, 15)
MONDAY = date(2026, 11, 16)


def _site(site_dict: dict[str, Any], **arrivals: Any) -> SiteProfile:
    data = copy.deepcopy(site_dict)
    data["arrivals"].update(arrivals)
    return SiteProfile.model_validate(data)


@pytest.fixture(scope="module")
def large_sample(request: pytest.FixtureRequest) -> tuple[pd.DataFrame, SiteProfile]:
    """400 fictional days (~76,000 patients) for proportion tests."""
    from beyond_the_wait.config import load_site_profile
    from tests.paths import EXAMPLE_SITE_PATH

    site = load_site_profile(EXAMPLE_SITE_PATH)
    return generate_patients(site, start=WEDNESDAY, days=400, seed=2026), site


# --- Reproducibility ------------------------------------------------------------------


def test_same_seed_gives_identical_patients(site: SiteProfile) -> None:
    a = generate_patients(site, start=MONDAY, days=2, seed=7)
    b = generate_patients(site, start=MONDAY, days=2, seed=7)
    pd.testing.assert_frame_equal(a, b)


def test_different_seeds_give_different_patients(site: SiteProfile) -> None:
    a = generate_patients(site, start=MONDAY, seed=7)
    b = generate_patients(site, start=MONDAY, seed=8)
    assert (
        not a["arrival_time"]
        .reset_index(drop=True)
        .equals(b["arrival_time"].reset_index(drop=True))
    )
    assert set(a["synthetic_patient_id"]).isdisjoint(b["synthetic_patient_id"])


def test_attribute_changes_do_not_move_arrival_times(site_dict: dict[str, Any]) -> None:
    # Separate random streams: changing lab probabilities leaves arrivals untouched.
    base = SiteProfile.model_validate(site_dict)
    changed = copy.deepcopy(site_dict)
    changed["patient_mix"]["lab_probability_by_acuity"] = dict.fromkeys(ESI_LEVELS, 0.5)
    other = SiteProfile.model_validate(changed)
    a = generate_patients(base, start=MONDAY, seed=11)
    b = generate_patients(other, start=MONDAY, seed=11)
    pd.testing.assert_series_equal(a["arrival_time"], b["arrival_time"])
    pd.testing.assert_series_equal(a["acuity_esi"], b["acuity_esi"])
    assert not a["needs_lab"].equals(b["needs_lab"])


# --- Arrival-count distribution --------------------------------------------------------


def test_daily_counts_follow_poisson_over_many_runs(site: SiteProfile) -> None:
    counts = np.array([len(generate_patients(site, start=WEDNESDAY, seed=s)) for s in range(300)])
    expected = expected_arrivals(site, WEDNESDAY, 1)
    assert expected == pytest.approx(190.5)
    # Mean within 4 standard errors of the expected value.
    assert abs(counts.mean() - expected) < 4 * math.sqrt(expected / len(counts))
    # Poisson signature: variance is close to the mean (dispersion index ~1).
    assert 0.7 < counts.var(ddof=1) / counts.mean() < 1.3


def test_hourly_profile_matches_configured_rates(large_sample) -> None:
    df, site = large_sample
    expected = hourly_rate_schedule(site, WEDNESDAY, 400).reshape(400, 24).sum(axis=0)
    observed = df.groupby("arrival_hour").size().reindex(range(24), fill_value=0).to_numpy()
    z = (observed - expected) / np.sqrt(expected)
    assert np.abs(z).max() < 5


def test_weekday_multipliers_change_volume(large_sample) -> None:
    df, site = large_sample
    per_day = df.groupby(["arrival_date", "day_of_week"]).size().groupby("day_of_week").mean()
    assert per_day["monday"] > per_day["saturday"]
    assert per_day["monday"] == pytest.approx(190.5 * 1.10, rel=0.03)
    assert per_day["saturday"] == pytest.approx(190.5 * 0.95, rel=0.03)


# --- Proportions over a large sample ---------------------------------------------------


def _within(observed: float, p: float, n: int, k: float = 4.0) -> bool:
    return abs(observed - p) <= k * math.sqrt(p * (1 - p) / n)


def test_acuity_proportions(large_sample) -> None:
    df, site = large_sample
    n = len(df)
    assert n > 70_000
    for level in ESI_LEVELS:
        p = site.patient_mix.acuity_distribution[level]
        assert _within(float((df["acuity_esi"] == level).mean()), p, n), level


@pytest.mark.parametrize(
    ("column", "table"),
    [
        ("needs_lab", "lab_probability_by_acuity"),
        ("needs_imaging", "imaging_probability_by_acuity"),
        ("hypothetical_admission", "admission_probability_by_acuity"),
    ],
)
def test_conditional_probabilities_by_acuity(large_sample, column: str, table: str) -> None:
    df, site = large_sample
    for level in ESI_LEVELS:
        group = df[df["acuity_esi"] == level]
        p = getattr(site.patient_mix, table)[level]
        assert _within(float(group[column].mean()), p, len(group)), (column, level)


def test_overall_admission_matches_law_of_total_probability(large_sample) -> None:
    df, site = large_sample
    implied = site.patient_mix.implied_admission_fraction
    assert _within(float(df["hypothetical_admission"].mean()), implied, len(df))


# --- Valid attributes and provenance ---------------------------------------------------


def test_every_row_valid_and_synthetic(large_sample) -> None:
    df, _ = large_sample
    validate_synthetic_patients(df, start=WEDNESDAY, days=400)
    require_provenance(df, Provenance.SYNTHETIC)
    assert (df["provenance"] == "SYNTHETIC").all()
    assert list(df.columns) == GENERATED_COLUMNS
    assert not df[GENERATED_COLUMNS[:12]].isna().any().any()


def test_no_outcome_columns_are_generated(site: SiteProfile) -> None:
    df = generate_patients(site, start=MONDAY, seed=1)
    outcome_words = (
        "wait",
        "los",
        "length_of_stay",
        "departure",
        "boarding",
        "outcome",
        "discharge",
    )
    assert not [c for c in df.columns if any(w in c for w in outcome_words)]


def test_ids_are_anonymous_and_sequential(site: SiteProfile) -> None:
    df = generate_patients(site, start=MONDAY, seed=5)
    assert df["synthetic_patient_id"].iloc[0] == "SYN-5-000001"
    assert df["synthetic_patient_id"].str.fullmatch(r"SYN-5-\d{6}").all()


# --- Zero, low, high volume, and midnight --------------------------------------------------


def test_zero_arrivals_gives_empty_valid_table(site_dict: dict[str, Any]) -> None:
    rates = [0.0] * 24
    rates[3] = 1e-9  # config requires *some* expected arrivals
    site = _site(site_dict, hourly_rates=rates, day_of_week_multipliers=None)
    df = generate_patients(site, start=MONDAY, seed=0)
    assert df.empty
    assert list(df.columns) == GENERATED_COLUMNS
    validate_synthetic_patients(df, start=MONDAY, days=1)
    summary = summarize(df, site, start=MONDAY, days=1)
    assert summary["patients"] == 0 and math.isnan(summary["needs_lab"][0])


def test_zero_rate_hours_have_no_arrivals(site_dict: dict[str, Any]) -> None:
    rates = [0.0] * 24
    rates[14] = 5.0
    site = _site(site_dict, hourly_rates=rates, day_of_week_multipliers=None)
    df = generate_patients(site, start=MONDAY, days=30, seed=3)
    assert set(df["arrival_hour"]) == {14}


def test_low_volume(site_dict: dict[str, Any]) -> None:
    site = _site(site_dict, hourly_rates=[0.1] * 24, day_of_week_multipliers=None)
    counts = [len(generate_patients(site, start=MONDAY, seed=s)) for s in range(200)]
    assert np.mean(counts) == pytest.approx(2.4, abs=4 * math.sqrt(2.4 / 200))
    assert 0 in counts  # with ~2.4/day, some days have nobody


def test_high_volume(site_dict: dict[str, Any]) -> None:
    site = _site(site_dict, hourly_rates=[50.0] * 24, day_of_week_multipliers=None)
    df = generate_patients(site, start=MONDAY, seed=9)
    assert abs(len(df) - 1200) < 5 * math.sqrt(1200)
    validate_synthetic_patients(df, start=MONDAY, days=1)


def test_midnight_boundary_uses_each_days_own_multiplier(site_dict: dict[str, Any]) -> None:
    rates = [0.0] * 24
    rates[0] = rates[23] = 10.0
    weekdays = dict.fromkeys(["tuesday", "wednesday", "thursday", "friday", "saturday"], 1.0)
    site = _site(
        site_dict,
        hourly_rates=rates,
        day_of_week_multipliers={**weekdays, "sunday": 0.4, "monday": 1.6},
    )
    schedule = hourly_rate_schedule(site, SUNDAY, 2)
    assert schedule[23] == pytest.approx(4.0)  # Sunday 23:00 -> Sunday multiplier
    assert schedule[24] == pytest.approx(16.0)  # Monday 00:00 -> Monday multiplier

    late_sunday, early_monday = [], []
    for seed in range(300):
        df = generate_patients(site, start=SUNDAY, days=2, seed=seed)
        validate_synthetic_patients(df, start=SUNDAY, days=2)
        late_sunday.append(((df["day_of_week"] == "sunday") & (df["arrival_hour"] == 23)).sum())
        early_monday.append(((df["day_of_week"] == "monday") & (df["arrival_hour"] == 0)).sum())
    assert np.mean(late_sunday) == pytest.approx(4.0, abs=4 * math.sqrt(4 / 300))
    assert np.mean(early_monday) == pytest.approx(16.0, abs=4 * math.sqrt(16 / 300))


def test_arrivals_just_before_midnight_stay_on_their_date(site_dict: dict[str, Any]) -> None:
    rates = [0.0] * 24
    rates[23] = 40.0
    site = _site(site_dict, hourly_rates=rates, day_of_week_multipliers=None)
    df = generate_patients(site, start=MONDAY, days=1, seed=4)
    assert len(df) > 0
    assert (df["arrival_date"] == MONDAY).all()
    assert pd.to_datetime(df["arrival_time"]).max() < pd.Timestamp(2026, 11, 17)


# --- Invalid requests and tampered tables -------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"seed": -1}, "seed must be"),
        ({"seed": 1.5}, "seed must be"),
        ({"seed": True}, "seed must be"),
        ({"days": 0}, "days must be"),
        ({"days": 10_000}, "days must be"),
        ({"start": datetime(2026, 11, 16, 8)}, "calendar date"),
        ({"start": "2026-11-16"}, "calendar date"),
    ],
)
def test_invalid_requests(site: SiteProfile, kwargs: dict[str, Any], message: str) -> None:
    args = {"start": MONDAY, "days": 1, "seed": 1} | kwargs
    with pytest.raises(SyntheticDataError, match=message):
        generate_patients(site, **args)


def _tamper(df: pd.DataFrame, column: str, value: Any) -> pd.DataFrame:
    broken = df.copy()
    broken[column] = broken[column].astype(object)
    broken.loc[broken.index[0], column] = value
    return broken


@pytest.mark.parametrize(
    ("column", "value", "message"),
    [
        ("acuity_esi", 6, "acuity_esi must be one of"),
        ("acuity_esi", None, "required attribute values are missing"),
        ("needs_lab", "yes", "must be true/false"),
        ("provenance", "PUBLIC", "expected every row to be SYNTHETIC"),
        ("synthetic_patient_id", "MRN-123", "must start with 'SYN-'"),
        ("arrival_hour", 99, "disagree with arrival_time"),
    ],
)
def test_validation_catches_bad_rows(
    site: SiteProfile, column: str, value: Any, message: str
) -> None:
    df = generate_patients(site, start=MONDAY, seed=1)
    with pytest.raises(SyntheticDataError, match=message):
        validate_synthetic_patients(_tamper(df, column, value), start=MONDAY, days=1)


def test_validation_catches_structure_problems(site: SiteProfile) -> None:
    df = generate_patients(site, start=MONDAY, seed=1)
    with pytest.raises(SyntheticDataError, match="missing columns"):
        validate_synthetic_patients(df.drop(columns="needs_imaging"), start=MONDAY, days=1)
    dup = df.copy()
    dup.loc[dup.index[1], "synthetic_patient_id"] = dup["synthetic_patient_id"].iloc[0]
    with pytest.raises(SyntheticDataError, match="unique"):
        validate_synthetic_patients(dup, start=MONDAY, days=1)
    with pytest.raises(SyntheticDataError, match="outside the requested dates"):
        validate_synthetic_patients(df, start=date(2026, 11, 17), days=1)
    with pytest.raises(SyntheticDataError, match="sorted by arrival time"):
        validate_synthetic_patients(df.iloc[::-1], start=MONDAY, days=1)


# --- Command-line demonstration ------------------------------------------------------------


def test_cli_demo_prints_labeled_sample_and_summary(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--seed", "42", "--date", "2026-11-16", "--sample", "3"]) == 0
    out = capsys.readouterr().out
    assert "FICTIONAL / SYNTHETIC DATA" in out
    assert "SYN-42-000003" in out and "SYN-42-000004" not in out
    assert "expected on average: 209.6" in out
    assert "No waits or lengths of stay are generated" in out


def test_cli_writes_csv(tmp_path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "synthetic" / "day.csv"
    assert main(["--seed", "1", "--out", str(out)]) == 0
    written = pd.read_csv(out)
    assert (written["provenance"] == "SYNTHETIC").all()
    assert "Wrote" in capsys.readouterr().out


def test_cli_reports_config_errors(tmp_path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--site", str(tmp_path / "missing.yaml")]) == 1
    assert "ERROR" in capsys.readouterr().err
