"""Load NYS DOH facility registry and ED encounter counts.

Key facts about these files (verified 2026-11-15):
    * Facilities are identified by a PFI (Permanent Facility Identifier).
      The registry writes it as "1164"; the ED file writes "001164". We
      normalize both to a 6-digit string so they can be matched exactly.
    * The ED file mixes QUARTER rows (Q1-Q4) with CALENDAR-YEAR total rows
      (CY). Summing all rows for a year would double-count; we label each
      row's `period_type` so they are never mixed.
    * Suppressed counts appear as "S". They become status="suppressed" with
      an empty value -- never 0.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date
from pathlib import Path

import pandas as pd

from beyond_the_wait.data._csv import read_text_csv
from beyond_the_wait.data.ed_measures import MEASURES
from beyond_the_wait.data.errors import PublicDataError

REGISTRY_COLUMNS: tuple[str, ...] = (
    "Facility ID", "Facility Name", "Short Description", "Facility Address 1",
    "Facility City", "Facility State", "Facility Zip Code", "Facility County",
    "Operating Certificate Number", "Main Site Facility ID", "Operator Name",
)  # fmt: skip

ED_COLUMNS: tuple[str, ...] = (
    "Year", "Quarter", "Facility ID", "Facility Name", "Facility Type Description",
    "Facility County", "Operating Certificate Number", "Operator Name",
    "Total ED Encounters", "Treat and Release ED Encounters",
    "ED Encounters Requiring Ambulatory Surgery", "ED Encounters Admitted as Inpatient",
)  # fmt: skip

# Source column -> our measure ID.
ED_MEASURE_COLUMNS: dict[str, str] = {
    "Total ED Encounters": "NYS_ED_TOTAL",
    "Treat and Release ED Encounters": "NYS_ED_TREAT_AND_RELEASE",
    "ED Encounters Requiring Ambulatory Surgery": "NYS_ED_AMBULATORY_SURGERY",
    "ED Encounters Admitted as Inpatient": "NYS_ED_ADMITTED",
}
_PARTS = (
    "Treat and Release ED Encounters",
    "ED Encounters Requiring Ambulatory Surgery",
    "ED Encounters Admitted as Inpatient",
)
SUPPRESSED = "S"
_COUNT = re.compile(r"^\d{1,3}(,\d{3})*$|^\d+$")
_QUARTERS = {"Q1": (1, 3, 31), "Q2": (4, 6, 30), "Q3": (7, 9, 30), "Q4": (10, 12, 31)}


def normalize_pfi(raw: object) -> str:
    """Normalize a NYS PFI to 6 digits: '1164' and '001164' -> '001164'."""
    text = str(raw).strip()
    if not text.isdigit() or len(text) > 6:
        raise PublicDataError(f"invalid NYS facility ID (PFI) {raw!r}")
    return text.zfill(6)


def period_bounds(year: str, quarter: str) -> tuple[date, date, str]:
    """('2025', 'Q4') -> (2025-10-01, 2025-12-31, 'quarter'); 'CY' -> calendar year."""
    if not year.isdigit():
        raise PublicDataError(f"invalid year {year!r}")
    y = int(year)
    if quarter == "CY":
        return date(y, 1, 1), date(y, 12, 31), "calendar_year"
    if quarter in _QUARTERS:
        start_month, end_month, end_day = _QUARTERS[quarter]
        return date(y, start_month, 1), date(y, end_month, end_day), "quarter"
    raise PublicDataError(f"invalid quarter {quarter!r} (expected Q1-Q4 or CY)")


def parse_count(raw: str) -> tuple[object, str, str]:
    """Return (value, status, missing_reason) for one published count."""
    if raw == SUPPRESSED:
        return pd.NA, "suppressed", "Redacted by NYS DOH (published as 'S')."
    if raw == "":
        return pd.NA, "not_available", "Blank in the NYS DOH source file."
    if not _COUNT.match(raw):
        raise PublicDataError(f"unexpected NYS count value {raw!r}")
    return int(raw.replace(",", "")), "reported", ""


# Attributes of the physical facility. These must agree across all rows that
# share a PFI. (Verified 2026-11-15: some PFIs appear on several rows because
# they have more than one operator/co-operator; facility attributes match.)
FACILITY_LEVEL_COLUMNS: tuple[str, ...] = (
    "Facility Name", "Short Description", "Facility Address 1", "Facility City",
    "Facility Zip Code", "Facility County", "Operating Certificate Number",
    "Main Site Facility ID",
)  # fmt: skip


def load_nys_facilities(path: str | Path) -> pd.DataFrame:
    """NYS facility registry: one row per PFI, normalized IDs.

    A PFI listed on several rows (multiple operators) is collapsed to one row
    with operator names joined by "; ". If facility-level attributes conflict
    between those rows, we stop -- that would mean we can't tell where the
    facility is or what it is.
    """
    df = read_text_csv(path, REGISTRY_COLUMNS, "NYS Health Facility General Information")
    df["pfi"] = df["Facility ID"].map(normalize_pfi)
    df["main_site_pfi"] = df["Main Site Facility ID"].map(lambda v: normalize_pfi(v) if v else "")
    grouped = df.groupby("pfi", sort=True)
    conflicts = grouped[list(FACILITY_LEVEL_COLUMNS)].nunique()
    conflicting = conflicts[(conflicts > 1).any(axis=1)].index.tolist()
    if conflicting:
        raise PublicDataError(
            f"NYS registry lists conflicting facility details for PFI(s) {conflicting[:10]}"
        )
    operators = grouped["Operator Name"].agg(lambda s: "; ".join(sorted(set(s))))
    collapsed = grouped.first().reset_index()
    collapsed["Operator Name"] = collapsed["pfi"].map(operators)
    collapsed["registry_rows"] = collapsed["pfi"].map(grouped.size())
    return collapsed


def load_nys_ed_encounters(
    path: str | Path,
    *,
    county: str = "Bronx",
    years: Iterable[int] | None = None,
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Load NYS ED encounter counts for one county as a long table.

    Returns (table, report). One output row per facility x period x measure.
    """
    raw = read_text_csv(path, ED_COLUMNS, "NYS ED Encounters by Facility")
    report = {"rows_read": len(raw)}
    df = raw[raw["Facility County"].str.casefold() == county.casefold()]
    if years is not None:
        df = df[df["Year"].isin({str(y) for y in years})]

    before = len(df)
    df = df.drop_duplicates()
    report["exact_duplicates_dropped"] = before - len(df)
    key = ["Facility ID", "Year", "Quarter"]
    if df.duplicated(key).any():
        dupes = df[df.duplicated(key, keep=False)][key].drop_duplicates().to_dict("records")
        raise PublicDataError(f"conflicting duplicate NYS rows for {dupes}")

    records = []
    for _, r in df.iterrows():
        pfi = normalize_pfi(r["Facility ID"])
        start, end, period_type = period_bounds(r["Year"], r["Quarter"])
        parsed = {col: parse_count(r[col]) for col in ED_MEASURE_COLUMNS}

        total = parsed["Total ED Encounters"]
        parts = [parsed[c] for c in _PARTS]
        if total[1] == "reported" and all(p[1] == "reported" for p in parts):
            if total[0] != sum(int(p[0]) for p in parts):  # type: ignore[arg-type]
                raise PublicDataError(
                    f"PFI {pfi} {r['Year']} {r['Quarter']}: total does not equal the sum "
                    "of treat-and-release + ambulatory surgery + admitted"
                )

        for col, measure_id in ED_MEASURE_COLUMNS.items():
            value, status, reason = parsed[col]
            records.append(
                {
                    "facility_id": pfi,
                    "facility_name": r["Facility Name"],
                    "facility_type": r["Facility Type Description"],
                    "county": r["Facility County"],
                    "operating_certificate": r["Operating Certificate Number"],
                    "measure_id": measure_id,
                    "measure_name": MEASURES[measure_id].short_name,
                    "measure_kind": MEASURES[measure_id].kind.value,
                    "unit": MEASURES[measure_id].unit.value,
                    "period_start": start,
                    "period_end": end,
                    "period_type": period_type,
                    "score_raw": r[col],
                    "value": value,
                    "status": status,
                    "missing_reason": reason,
                }
            )

    table = pd.DataFrame.from_records(records)
    report["rows_kept"] = len(table)
    return table, report
