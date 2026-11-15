"""Load CMS Timely and Effective Care ED measures into a tidy, validated table.

Output: one row per (facility, measure, reporting period), with
    * the raw published score kept verbatim (`score_raw`)
    * a numeric `value` ONLY when CMS published a number
    * explicit unit conversions (`value_hours`, `value_fraction`)
    * `status` = reported | not_available, plus a `missing_reason` built from
      CMS's own footnote text. Missing values are NEVER filled with 0.

Validation (each raises PublicDataError):
    * expected columns present
    * scores are numbers, "Not Available", or (for EDV) a known category
    * percentages lie in 0-100; minutes are >= 0
    * every footnote code exists in the official crosswalk
    * no conflicting duplicate rows (exact duplicates are dropped and counted)
    * within a measure, every facility shares one reporting period
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from beyond_the_wait.data._csv import read_text_csv
from beyond_the_wait.data.ed_measures import CMS_ED_MEASURE_IDS, MEASURES, Unit
from beyond_the_wait.data.errors import PublicDataError

TEC_COLUMNS: tuple[str, ...] = (
    "Facility ID", "Facility Name", "Address", "City/Town", "State", "ZIP Code",
    "County/Parish", "Telephone Number", "Condition", "Measure ID", "Measure Name",
    "Score", "Sample", "Footnote", "Start Date", "End Date",
)  # fmt: skip
HGI_COLUMNS: tuple[str, ...] = (
    "Facility ID", "Facility Name", "Address", "City/Town", "State", "ZIP Code",
    "County/Parish", "Hospital Type", "Emergency Services",
)  # fmt: skip
FOOTNOTE_COLUMNS: tuple[str, ...] = ("Footnote", "Footnote Text")

CMS_NOT_AVAILABLE = "Not Available"
EDV_CATEGORIES: tuple[str, ...] = ("low", "medium", "high", "very high")
_NUMBER = re.compile(r"^-?\d+(\.\d+)?$")
_DIGITS = re.compile(r"^\d+$")
_KEY = ["facility_id", "measure_id", "period_start", "period_end"]


def load_footnote_crosswalk(path: str | Path) -> dict[str, str]:
    """Map CMS footnote code -> official explanation text."""
    df = read_text_csv(path, FOOTNOTE_COLUMNS, "CMS footnote crosswalk")
    if df["Footnote"].duplicated().any():
        raise PublicDataError("CMS footnote crosswalk has duplicate codes")
    return dict(zip(df["Footnote"], df["Footnote Text"], strict=True))


def load_cms_hospital_info(path: str | Path) -> pd.DataFrame:
    """CMS facility directory, with facility IDs kept as text."""
    return read_text_csv(path, HGI_COLUMNS, "CMS Hospital General Information")


def parse_footnote_codes(raw: str) -> tuple[str, ...]:
    """'1, 3' -> ('1', '3'); '' -> ()."""
    return tuple(code.strip() for code in raw.split(",") if code.strip())


def parse_cms_date(raw: str) -> date:
    """CMS dates are MM/DD/YYYY. Anything else is an error, not a guess."""
    try:
        return datetime.strptime(raw, "%m/%d/%Y").date()
    except ValueError as exc:
        raise PublicDataError(f"unexpected CMS date format {raw!r} (expected MM/DD/YYYY)") from exc


def _interpret_score(measure_id: str, raw: str) -> dict[str, object]:
    """Turn one published score into typed fields, or raise if unrecognized."""
    unit = MEASURES[measure_id].unit
    out: dict[str, object] = {
        "value": pd.NA,
        "value_hours": pd.NA,
        "value_fraction": pd.NA,
        "value_category": pd.NA,
    }
    if raw == CMS_NOT_AVAILABLE:
        return out | {"status": "not_available"}

    if unit is Unit.CATEGORY:
        if raw not in EDV_CATEGORIES:
            raise PublicDataError(f"{measure_id}: unexpected category {raw!r}")
        return out | {"status": "reported", "value_category": raw}

    if not _NUMBER.match(raw):
        raise PublicDataError(f"{measure_id}: unexpected non-numeric score {raw!r}")
    number = float(raw)
    if unit is Unit.MINUTES:
        if number < 0:
            raise PublicDataError(f"{measure_id}: negative minutes {raw!r}")
        # Unit conversion: minutes -> hours (the simulation will work in hours).
        return out | {"status": "reported", "value": number, "value_hours": number / 60.0}
    if unit is Unit.PERCENT:
        if not 0 <= number <= 100:
            raise PublicDataError(f"{measure_id}: percentage outside 0-100: {raw!r}")
        # Unit conversion: CMS publishes 3 meaning 3%; fraction form is 0.03.
        return out | {"status": "reported", "value": number, "value_fraction": number / 100.0}
    raise PublicDataError(f"{measure_id}: no score rule for unit {unit}")  # pragma: no cover


def _missing_reason(status: str, codes: tuple[str, ...], footnotes: dict[str, str]) -> str:
    if status == "reported":
        return ""
    if not codes:
        return "CMS published 'Not Available' without a footnote explaining why."
    return " ".join(f"[{c}] {footnotes[c]}" for c in codes)


def load_cms_ed_measures(
    tec_path: str | Path,
    footnotes_path: str | Path,
    *,
    facility_ids: Iterable[str] | None = None,
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Load ED measures from the Timely and Effective Care file.

    Args:
        facility_ids: if given, keep only these CMS facility IDs (CCNs).

    Returns:
        (table, report) where report counts rows read, kept, and exact
        duplicates dropped.
    """
    footnotes = load_footnote_crosswalk(footnotes_path)
    raw = read_text_csv(tec_path, TEC_COLUMNS, "CMS Timely and Effective Care")
    report = {"rows_read": len(raw)}

    ed = raw[raw["Measure ID"].isin(CMS_ED_MEASURE_IDS)]
    if facility_ids is not None:
        ed = ed[ed["Facility ID"].isin(set(facility_ids))]

    before = len(ed)
    ed = ed.drop_duplicates()
    report["exact_duplicates_dropped"] = before - len(ed)

    records = []
    for row in ed.itertuples(index=False):
        r = dict(zip(TEC_COLUMNS, row, strict=True))
        codes = parse_footnote_codes(r["Footnote"])
        unknown = [c for c in codes if c not in footnotes]
        if unknown:
            raise PublicDataError(
                f"facility {r['Facility ID']} {r['Measure ID']}: footnote code(s) {unknown} "
                "are not in the CMS footnote crosswalk"
            )
        scored = _interpret_score(r["Measure ID"], r["Score"])
        sample = r["Sample"]
        records.append(
            {
                "facility_id": r["Facility ID"],
                "facility_name": r["Facility Name"],
                "address": r["Address"],
                "city": r["City/Town"],
                "state": r["State"],
                "zip_code": r["ZIP Code"],
                "county": r["County/Parish"],
                "measure_id": r["Measure ID"],
                "measure_name": r["Measure Name"],
                "measure_kind": MEASURES[r["Measure ID"]].kind.value,
                "unit": MEASURES[r["Measure ID"]].unit.value,
                "period_start": parse_cms_date(r["Start Date"]),
                "period_end": parse_cms_date(r["End Date"]),
                "period_type": "reporting_period",
                "score_raw": r["Score"],
                **scored,
                "sample_raw": sample,
                "sample_size": int(sample) if _DIGITS.match(sample) else pd.NA,
                "footnote_codes": ";".join(codes),
                "footnote_text": " ".join(f"[{c}] {footnotes[c]}" for c in codes),
                "missing_reason": _missing_reason(str(scored["status"]), codes, footnotes),
            }
        )

    table = pd.DataFrame.from_records(records)
    if table.empty:
        report["rows_kept"] = 0
        return table, report

    conflicts = table[table.duplicated(_KEY, keep=False)]
    if not conflicts.empty:
        keys = conflicts[_KEY].drop_duplicates().to_dict("records")
        raise PublicDataError(f"conflicting duplicate CMS rows for {keys}")

    periods = table.groupby("measure_id")[["period_start", "period_end"]].nunique()
    inconsistent = periods[(periods > 1).any(axis=1)].index.tolist()
    if inconsistent:
        raise PublicDataError(
            f"reporting periods differ between facilities for {inconsistent}; "
            "values from different periods must not be compared as if equivalent"
        )

    report["rows_kept"] = len(table)
    return table, report
