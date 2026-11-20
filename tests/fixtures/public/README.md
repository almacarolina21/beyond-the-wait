# Public-data test fixtures

`raw/` holds **small excerpts of real, official, public aggregate files**, so
the tests run offline in under a second.

- Each CSV is the official header plus selected records **copied byte-for-byte**
  from the files downloaded on 2026-11-15 (see `raw/manifest.json` for the
  official download URLs). No values were edited.
- `raw/manifest.json` keeps the official URLs and dates, but its fingerprints
  (`sha256`, `bytes`) describe these excerpts, not the full files.
- Regenerate with `python tests/fixtures/public/make_fixtures.py` after
  downloading the full files.

What's included:

| File | Records | Why |
|---|---|---|
| `cms_timely_effective_care_hospital.csv` | 42 | ED measures for the 5 matched Bronx hospitals, OP_23 rows (to be filtered out), one non-ED measure, and facility `010001` (leading-zero ID) |
| `cms_footnote_crosswalk.csv` | 32 | Complete official footnote list |
| `cms_hospital_general_information.csv` | 8 | All 7 CMS Bronx facilities + `010001` |
| `nys_health_facility_general_information.csv` | 15 | All 12 Bronx general hospitals, an extension clinic, and a non-Bronx PFI listed twice (multiple operators) |
| `nys_ed_encounters_by_facility.csv` | 140 | Bronx 2024–2025, Lincoln 2023 (year filter), one Albany facility (county filter) |

**Edge cases** (conflicting duplicates, invalid scores, mixed reporting
periods, etc.) are built inside the tests using obviously fictional
facilities such as `FICTIONAL TEST HOSPITAL` (ID `999001`). We never attach
made-up values to a real hospital, even in a test.

**Licensing.** These excerpts keep their publishers' terms, not the project's
MIT license. The NYS DOH excerpts are provided under the Open Database License
(ODbL) 1.0. See
[`data/public/SOURCES.md`](../../../data/public/SOURCES.md#reuse-terms-and-attribution)
for the CMS and NYS terms and the required attribution.
