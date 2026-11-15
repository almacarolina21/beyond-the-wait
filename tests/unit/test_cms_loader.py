"""Tests for the CMS Timely and Effective Care loader.

Real-data tests use byte-for-byte excerpts of the official file. Edge cases
use an obviously FICTIONAL facility (ID 999001) written to a temp file, so no
invented value is ever attributed to a real hospital.
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from beyond_the_wait.data.cms_loader import (
    TEC_COLUMNS,
    load_cms_ed_measures,
    load_footnote_crosswalk,
    parse_cms_date,
    parse_footnote_codes,
)
from beyond_the_wait.data.errors import PublicDataError
from tests.paths import PUBLIC_FIXTURE_RAW

TEC = PUBLIC_FIXTURE_RAW / "cms_timely_effective_care_hospital.csv"
FOOTNOTES = PUBLIC_FIXTURE_RAW / "cms_footnote_crosswalk.csv"
BRONX = ["330009", "330059", "330080", "330127", "330399"]


@pytest.fixture(scope="module")
def bronx() -> pd.DataFrame:
    table, _ = load_cms_ed_measures(TEC, FOOTNOTES, facility_ids=BRONX)
    return table


def _row(table: pd.DataFrame, facility: str, measure: str) -> pd.Series:
    match = table[(table["facility_id"] == facility) & (table["measure_id"] == measure)]
    assert len(match) == 1
    return match.iloc[0]


# --- Real excerpt -------------------------------------------------------------


def test_loads_six_ed_measures_for_each_bronx_hospital(bronx: pd.DataFrame) -> None:
    assert len(bronx) == 30
    assert bronx.groupby("facility_id").size().to_dict() == dict.fromkeys(BRONX, 6)


def test_non_ed_measures_are_filtered_out(bronx: pd.DataFrame) -> None:
    assert "OP_23" not in set(bronx["measure_id"])
    assert set(bronx["measure_id"]) == {"EDV", "OP_18a", "OP_18b", "OP_18c", "OP_18d", "OP_22"}


def test_facility_ids_stay_text_with_leading_zeros() -> None:
    table, _ = load_cms_ed_measures(TEC, FOOTNOTES)
    assert "010001" in set(table["facility_id"])


def test_reported_minutes_and_unit_conversion(bronx: pd.DataFrame) -> None:
    row = _row(bronx, "330080", "OP_18b")
    assert row["status"] == "reported"
    assert row["value"] == 228.0
    assert row["value_hours"] == pytest.approx(3.8)
    assert row["unit"] == "minutes"


def test_reported_percent_and_unit_conversion(bronx: pd.DataFrame) -> None:
    row = _row(bronx, "330080", "OP_22")
    assert row["value"] == 9.0
    assert row["value_fraction"] == pytest.approx(0.09)
    assert pd.isna(row["value_hours"])


def test_category_measure_has_no_numeric_value(bronx: pd.DataFrame) -> None:
    row = _row(bronx, "330080", "EDV")
    assert row["value_category"] == "medium"
    assert pd.isna(row["value"])


def test_not_available_stays_missing_with_official_reason(bronx: pd.DataFrame) -> None:
    row = _row(bronx, "330127", "OP_18c")
    assert row["status"] == "not_available"
    assert pd.isna(row["value"]) and pd.isna(row["value_hours"])
    assert row["score_raw"] == "Not Available"
    assert "[1] The number of cases/patients is too few to report." in row["missing_reason"]


def test_missing_values_are_never_zero(bronx: pd.DataFrame) -> None:
    missing = bronx[bronx["status"] != "reported"]
    assert len(missing) == 6
    assert missing["value"].isna().all()
    assert not (bronx["value"] == 0).any()


def test_sample_kept_raw_and_parsed_when_numeric(bronx: pd.DataFrame) -> None:
    assert _row(bronx, "330009", "OP_22")["sample_size"] == 125885
    na = _row(bronx, "330009", "OP_18d")
    assert na["sample_raw"] == "Not Available" and pd.isna(na["sample_size"])


def test_reporting_periods_preserved_per_measure(bronx: pd.DataFrame) -> None:
    periods = bronx.groupby("measure_id")[["period_start", "period_end"]].first()
    assert periods.loc["OP_18b"].tolist() == [date(2025, 1, 1), date(2025, 12, 31)]
    assert periods.loc["OP_22"].tolist() == [date(2024, 1, 1), date(2024, 12, 31)]


def test_reproducible(bronx: pd.DataFrame) -> None:
    again, _ = load_cms_ed_measures(TEC, FOOTNOTES, facility_ids=BRONX)
    pd.testing.assert_frame_equal(bronx, again)


def test_footnote_crosswalk() -> None:
    footnotes = load_footnote_crosswalk(FOOTNOTES)
    assert footnotes["5"] == "Results are not available for this reporting period."


def test_parse_helpers() -> None:
    assert parse_footnote_codes("1, 3") == ("1", "3")
    assert parse_footnote_codes("") == ()
    assert parse_cms_date("12/31/2025") == date(2025, 12, 31)
    with pytest.raises(PublicDataError, match="MM/DD/YYYY"):
        parse_cms_date("2025-12-31")


# --- Fictional edge cases ---------------------------------------------------------

FICTIONAL = {
    "Facility ID": "999001",
    "Facility Name": "FICTIONAL TEST HOSPITAL",
    "Address": "1 TEST WAY",
    "City/Town": "TESTVILLE",
    "State": "NY",
    "ZIP Code": "00000",
    "County/Parish": "TEST",
    "Telephone Number": "",
    "Condition": "Emergency Department",
    "Measure ID": "OP_18b",
    "Measure Name": "Fictional test row",
    "Score": "100",
    "Sample": "50",
    "Footnote": "",
    "Start Date": "01/01/2025",
    "End Date": "12/31/2025",
}


def _write(tmp_path: Path, *overrides: dict[str, str]) -> Path:
    path = tmp_path / "tec.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=TEC_COLUMNS, quoting=csv.QUOTE_ALL)
        writer.writeheader()
        for change in overrides:
            writer.writerow(FICTIONAL | change)
    return path


def test_exact_duplicates_are_dropped_and_counted(tmp_path: Path) -> None:
    table, report = load_cms_ed_measures(_write(tmp_path, {}, {}), FOOTNOTES)
    assert len(table) == 1
    assert report["exact_duplicates_dropped"] == 1


def test_conflicting_duplicates_raise(tmp_path: Path) -> None:
    path = _write(tmp_path, {"Score": "100"}, {"Score": "140"})
    with pytest.raises(PublicDataError, match="conflicting duplicate"):
        load_cms_ed_measures(path, FOOTNOTES)


def test_mixed_reporting_periods_within_a_measure_raise(tmp_path: Path) -> None:
    other = {"Facility ID": "999002", "Start Date": "01/01/2024", "End Date": "12/31/2024"}
    with pytest.raises(PublicDataError, match="reporting periods differ"):
        load_cms_ed_measures(_write(tmp_path, {}, other), FOOTNOTES)


def test_different_measures_may_have_different_periods(tmp_path: Path) -> None:
    lwbs = {
        "Measure ID": "OP_22",
        "Score": "4",
        "Start Date": "01/01/2024",
        "End Date": "12/31/2024",
    }
    table, _ = load_cms_ed_measures(_write(tmp_path, {}, lwbs), FOOTNOTES)
    assert len(table) == 2


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"Score": "lots"}, "non-numeric score"),
        ({"Score": ""}, "non-numeric score"),
        ({"Score": "-5"}, "negative minutes"),
        ({"Measure ID": "OP_22", "Score": "101"}, "outside 0-100"),
        ({"Measure ID": "EDV", "Score": "enormous"}, "unexpected category"),
        ({"Score": "Not Available", "Footnote": "99"}, "not in the CMS footnote crosswalk"),
        ({"Start Date": "2025-01-01"}, "MM/DD/YYYY"),
    ],
)
def test_unexpected_values_raise(tmp_path: Path, change: dict[str, str], message: str) -> None:
    with pytest.raises(PublicDataError, match=message):
        load_cms_ed_measures(_write(tmp_path, change), FOOTNOTES)


def test_not_available_without_footnote_is_explained(tmp_path: Path) -> None:
    table, _ = load_cms_ed_measures(_write(tmp_path, {"Score": "Not Available"}), FOOTNOTES)
    assert table.iloc[0]["status"] == "not_available"
    assert "without a footnote" in table.iloc[0]["missing_reason"]


def test_reported_value_keeps_qualifying_footnote(tmp_path: Path) -> None:
    table, _ = load_cms_ed_measures(_write(tmp_path, {"Footnote": "3"}), FOOTNOTES)
    row = table.iloc[0]
    assert row["status"] == "reported" and row["missing_reason"] == ""
    assert "shorter time period" in row["footnote_text"]


def test_missing_column_raises(tmp_path: Path) -> None:
    path = tmp_path / "bad.csv"
    path.write_text('"Facility ID","Score"\n"999001","1"\n', encoding="utf-8")
    with pytest.raises(PublicDataError, match="missing expected column"):
        load_cms_ed_measures(path, FOOTNOTES)


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(PublicDataError, match="file not found"):
        load_cms_ed_measures(tmp_path / "nope.csv", FOOTNOTES)


def test_no_matching_rows_returns_empty(tmp_path: Path) -> None:
    table, report = load_cms_ed_measures(_write(tmp_path, {}), FOOTNOTES, facility_ids=["1"])
    assert table.empty and report["rows_kept"] == 0


def test_duplicate_footnote_codes_raise(tmp_path: Path) -> None:
    path = tmp_path / "footnotes.csv"
    path.write_text("Footnote,Footnote Text\n1,One\n1,Uno\n", encoding="utf-8")
    with pytest.raises(PublicDataError, match="duplicate codes"):
        load_footnote_crosswalk(path)


def test_unparseable_csv_raises(tmp_path: Path) -> None:
    path = tmp_path / "binary.csv"
    path.write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(PublicDataError, match="could not parse"):
        load_footnote_crosswalk(path)
