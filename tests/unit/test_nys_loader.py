"""Tests for the NYS DOH registry and ED encounter-count loaders."""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from beyond_the_wait.data.errors import PublicDataError
from beyond_the_wait.data.nys_loader import (
    ED_COLUMNS,
    load_nys_ed_encounters,
    load_nys_facilities,
    normalize_pfi,
    parse_count,
    period_bounds,
)
from tests.paths import PUBLIC_FIXTURE_RAW

ED = PUBLIC_FIXTURE_RAW / "nys_ed_encounters_by_facility.csv"
REGISTRY = PUBLIC_FIXTURE_RAW / "nys_health_facility_general_information.csv"


@pytest.fixture(scope="module")
def bronx() -> pd.DataFrame:
    table, _ = load_nys_ed_encounters(ED, county="Bronx", years=(2024, 2025))
    return table


def _value(table: pd.DataFrame, pfi: str, measure: str, start: date, period_type: str) -> pd.Series:
    m = table[
        (table["facility_id"] == pfi)
        & (table["measure_id"] == measure)
        & (table["period_start"] == start)
        & (table["period_type"] == period_type)
    ]
    assert len(m) == 1
    return m.iloc[0]


@pytest.mark.parametrize(
    ("raw", "expected"), [("1164", "001164"), ("001164", "001164"), (9750, "009750")]
)
def test_normalize_pfi(raw: object, expected: str) -> None:
    assert normalize_pfi(raw) == expected


@pytest.mark.parametrize("bad", ["", "11A4", "1234567", "-1"])
def test_invalid_pfi_rejected(bad: str) -> None:
    with pytest.raises(PublicDataError, match="invalid NYS facility ID"):
        normalize_pfi(bad)


def test_period_bounds() -> None:
    assert period_bounds("2025", "Q4") == (date(2025, 10, 1), date(2025, 12, 31), "quarter")
    assert period_bounds("2025", "Q2") == (date(2025, 4, 1), date(2025, 6, 30), "quarter")
    assert period_bounds("2025", "CY") == (date(2025, 1, 1), date(2025, 12, 31), "calendar_year")
    with pytest.raises(PublicDataError, match="invalid quarter"):
        period_bounds("2025", "Q5")
    with pytest.raises(PublicDataError, match="invalid year"):
        period_bounds("25x", "Q1")


@pytest.mark.parametrize(
    ("raw", "value", "status"),
    [("1215", 1215, "reported"), ("1,215", 1215, "reported"), ("0", 0, "reported")],
)
def test_parse_reported_counts(raw: str, value: int, status: str) -> None:
    assert parse_count(raw)[:2] == (value, status)


def test_suppressed_and_blank_counts_are_missing_not_zero() -> None:
    value, status, reason = parse_count("S")
    assert pd.isna(value) and status == "suppressed" and "'S'" in reason
    value, status, _ = parse_count("")
    assert pd.isna(value) and status == "not_available"
    with pytest.raises(PublicDataError, match="unexpected NYS count"):
        parse_count("12.5")


def test_county_and_year_filters(bronx: pd.DataFrame) -> None:
    assert set(bronx["county"]) == {"Bronx"}
    assert {d.year for d in bronx["period_start"]} == {2024, 2025}
    everything, _ = load_nys_ed_encounters(ED, county="Bronx")
    assert 2023 in {d.year for d in everything["period_start"]}


def test_quarter_and_calendar_year_rows_are_labeled_separately(bronx: pd.DataFrame) -> None:
    lincoln = bronx[(bronx["facility_id"] == "001172") & (bronx["measure_id"] == "NYS_ED_TOTAL")]
    by_type = lincoln.groupby("period_type").size().to_dict()
    assert by_type == {"calendar_year": 2, "quarter": 8}
    q2025 = lincoln[
        (lincoln["period_type"] == "quarter")
        & (lincoln["period_start"].map(lambda d: d.year) == 2025)
    ]
    cy2025 = _value(bronx, "001172", "NYS_ED_TOTAL", date(2025, 1, 1), "calendar_year")
    # Quarters sum to the published calendar-year total, so adding both would double-count.
    assert q2025["value"].sum() == cy2025["value"] == 134810


def test_suppressed_quarter_with_reported_annual_total(bronx: pd.DataFrame) -> None:
    q1 = _value(bronx, "001178", "NYS_ED_ADMITTED", date(2025, 1, 1), "quarter")
    assert q1["status"] == "suppressed" and pd.isna(q1["value"]) and q1["score_raw"] == "S"
    cy = _value(bronx, "001178", "NYS_ED_ADMITTED", date(2025, 1, 1), "calendar_year")
    assert cy["status"] == "reported" and cy["value"] == 19556


def test_ids_are_six_digit_text(bronx: pd.DataFrame) -> None:
    assert bronx["facility_id"].str.fullmatch(r"\d{6}").all()


def test_reproducible(bronx: pd.DataFrame) -> None:
    again, _ = load_nys_ed_encounters(ED, county="Bronx", years=(2024, 2025))
    pd.testing.assert_frame_equal(bronx, again)


# --- Registry -----------------------------------------------------------------


def test_registry_collapses_multi_operator_rows() -> None:
    registry = load_nys_facilities(REGISTRY)
    assert registry["pfi"].is_unique
    multi = registry[registry["pfi"] == "010006"].iloc[0]
    assert multi["registry_rows"] == 2


def test_registry_main_site_links() -> None:
    registry = load_nys_facilities(REGISTRY).set_index("pfi")
    assert registry.loc["001186", "main_site_pfi"] == "001165"  # NCB -> Jacobi
    assert registry.loc["001172", "main_site_pfi"] == ""


def test_registry_conflicting_facility_details_raise(tmp_path: Path) -> None:
    lines = REGISTRY.read_text(encoding="utf-8").splitlines(keepends=True)
    multi = [ln for ln in lines if ln.startswith("10006,")]
    assert len(multi) == 2
    conflicting = multi[1].replace("39 Brentwood Road", "40 Brentwood Road")
    path = tmp_path / "registry.csv"
    path.write_text(lines[0] + multi[0] + conflicting, encoding="utf-8")
    with pytest.raises(PublicDataError, match="conflicting facility details"):
        load_nys_facilities(path)


# --- Fictional edge cases ---------------------------------------------------------

FICTIONAL = {
    "Year": "2025",
    "Quarter": "Q1",
    "Facility ID": "999001",
    "Facility Name": "FICTIONAL TEST HOSPITAL",
    "Facility Type Description": "Hospital",
    "Facility County": "Bronx",
    "Operating Certificate Number": "0000000H",
    "Operator Name": "Fictional Operator",
    "Total ED Encounters": "100",
    "Treat and Release ED Encounters": "80",
    "ED Encounters Requiring Ambulatory Surgery": "0",
    "ED Encounters Admitted as Inpatient": "20",
}


def _write(tmp_path: Path, *overrides: dict[str, str]) -> Path:
    path = tmp_path / "ed.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=ED_COLUMNS)
        writer.writeheader()
        for change in overrides:
            writer.writerow(FICTIONAL | change)
    return path


def test_exact_duplicates_dropped(tmp_path: Path) -> None:
    table, report = load_nys_ed_encounters(_write(tmp_path, {}, {}))
    assert report["exact_duplicates_dropped"] == 1
    assert len(table) == 4  # one row per measure


def test_conflicting_duplicates_raise(tmp_path: Path) -> None:
    path = _write(
        tmp_path, {}, {"Total ED Encounters": "101", "Treat and Release ED Encounters": "81"}
    )
    with pytest.raises(PublicDataError, match="conflicting duplicate"):
        load_nys_ed_encounters(path)


def test_total_must_equal_sum_of_parts(tmp_path: Path) -> None:
    with pytest.raises(PublicDataError, match="total does not equal"):
        load_nys_ed_encounters(_write(tmp_path, {"Total ED Encounters": "999"}))


def test_sum_check_skipped_when_a_part_is_suppressed(tmp_path: Path) -> None:
    table, _ = load_nys_ed_encounters(
        _write(tmp_path, {"Total ED Encounters": "999", "ED Encounters Admitted as Inpatient": "S"})
    )
    assert (table["status"] == "suppressed").sum() == 1
