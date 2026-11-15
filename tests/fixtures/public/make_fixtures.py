"""Regenerate the public-data test fixtures from the full official raw files.

Run from the repo root after `public_pipeline download` (or `register`):
    python tests/fixtures/public/make_fixtures.py

Each fixture is a small EXCERPT: the header line plus selected data lines
copied byte-for-byte from the official file (no values are edited). The
fixture manifest records the official download URL and the excerpt's own
SHA-256 fingerprint.
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
RAW = REPO / "data" / "public" / "raw"
OUT = Path(__file__).resolve().parent / "raw"

BRONX_CCNS = {"330009", "330059", "330080", "330127", "330399"}
ED_IDS = {"EDV", "OP_18a", "OP_18b", "OP_18c", "OP_18d", "OP_22"}
NON_BRONX_CCN = "010001"  # leading zero: tests that IDs stay text


def excerpt(name: str, keep) -> None:  # noqa: ANN001 - small script
    """Copy the header and every record where keep(record) is true, byte-for-byte.

    Some records span several physical lines (a quoted field containing a
    line break), so we use the CSV reader's line counter to find each
    record's exact line range instead of assuming one record per line.
    """
    lines = (RAW / name).read_text(encoding="utf-8").splitlines(keepends=True)
    reader = csv.reader(iter(lines))
    header = next(reader)
    kept = ["".join(lines[: reader.line_num])]
    start = reader.line_num
    for fields in reader:
        end = reader.line_num
        if len(fields) != len(header):
            raise SystemExit(f"{name}: record at line {start + 1} has wrong field count")
        if keep(dict(zip(header, fields, strict=True))):
            kept.append("".join(lines[start:end]))
        start = end
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text("".join(kept), encoding="utf-8")
    print(f"{name}: {len(kept) - 1} data records")


def main() -> None:
    lincoln_other = {"done": False}

    def tec(r: dict) -> bool:
        if r["Facility ID"] in BRONX_CCNS and r["Measure ID"] in ED_IDS | {"OP_23"}:
            return True
        if r["Facility ID"] == NON_BRONX_CCN and r["Measure ID"] in ED_IDS:
            return True
        # One non-ED measure row, to test that non-ED measures are filtered out.
        if r["Facility ID"] == "330080" and r["Condition"] != "Emergency Department":
            if not lincoln_other["done"]:
                lincoln_other["done"] = True
                return True
        return False

    excerpt("cms_timely_effective_care_hospital.csv", tec)
    excerpt("cms_footnote_crosswalk.csv", lambda r: True)
    excerpt(
        "cms_hospital_general_information.csv",
        lambda r: r["County/Parish"] == "BRONX" or r["Facility ID"] == NON_BRONX_CCN,
    )
    excerpt(
        "nys_health_facility_general_information.csv",
        lambda r: (
            (r["Facility County"] == "Bronx" and r["Short Description"] in {"HOSP"})
            or r["Facility ID"] in {"9750", "10006"}
        ),  # extension clinic; multi-operator PFI
    )
    excerpt(
        "nys_ed_encounters_by_facility.csv",
        lambda r: (
            (r["Facility County"] == "Bronx" and r["Year"] in {"2024", "2025"})
            or (r["Facility ID"] == "001172" and r["Year"] == "2023")
            or (r["Facility County"] == "Albany" and r["Year"] == "2025")
        ),
    )

    source = json.loads((RAW / "manifest.json").read_text(encoding="utf-8"))
    for entry in source["files"].values():
        data = (OUT / entry["raw_filename"]).read_bytes()
        entry["sha256"] = hashlib.sha256(data).hexdigest()
        entry["bytes"] = len(data)
    (OUT / "manifest.json").write_text(json.dumps(source, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
