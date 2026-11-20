# Public Data Sources

Every dataset below was verified on **2026-11-15** against the publisher's own
catalog API. Exact download URLs, file fingerprints (SHA-256), and publisher
"last modified" dates for the files actually used are recorded in
[`processed/build_manifest.json`](processed/build_manifest.json).

All sources are **official, public, aggregate** data (hospital-level or
facility-level totals). No record-level or patient-level data is used.

| Key | Dataset | Publisher | ID | Publisher last modified | Used for |
|---|---|---|---|---|---|
| `cms_timely_effective_care` | [Timely and Effective Care – Hospital](https://data.cms.gov/provider-data/dataset/yv7e-xc69) | CMS | `yv7e-xc69` | 2026-10-19 (released 2026-11-12) | ED measures OP-18a/b/c/d, OP-22, EDV |
| `cms_footnote_crosswalk` | [Footnote Crosswalk](https://data.cms.gov/provider-data/dataset/y9us-9xdf) | CMS | `y9us-9xdf` | 2025-09-18 | Official meaning of footnote codes |
| `cms_hospital_general_information` | [Hospital General Information](https://data.cms.gov/provider-data/dataset/xubh-q36u) | CMS | `xubh-q36u` | 2026-10-26 | County, hospital type, emergency services |
| `nys_health_facility_general_information` | [Health Facility General Information](https://health.data.ny.gov/d/vn5v-hh5r) | NYS Department of Health | `vn5v-hh5r` | 2026-10-23 | Independent facility registry (PFI, address, main site) |
| `nys_sparcs_ed_encounters_by_facility` | [ED Encounters by Facility: Beginning 2018 (SPARCS De-Identified)](https://health.data.ny.gov/d/5gzv-zv2z) | NYS Department of Health | `5gzv-zv2z` | 2026-10-22 | Facility-level ED encounter **counts** by quarter/year |

## Reuse terms and attribution

*Checked 2026-11-20. This is not legal advice. The project's MIT license
covers its source code only, not these data.*

**What this repository redistributes.**
- Small *derived* tables: `processed/bronx_ed_measures.csv`,
  `processed/bronx_facilities.csv` and `processed/build_manifest.json`.
- The curated crosswalk in `reference/`.
- Byte-for-byte excerpts of the raw files used as test fixtures, in
  `tests/fixtures/public/raw/`. They contain under 150 rows per file.

All of these hold facility-level aggregates only. The full raw publisher files
(`raw/`, about 42 MB) are **not** committed. `download` fetches them again from
the official URLs.

**What the publishers' own metadata says.**

| Datasets | Publisher metadata (checked 2026-11-20) | Catalog listing | Terms applied in this repository |
|---|---|---|---|
| CMS `yv7e-xc69`, `y9us-9xdf`, `xubh-q36u` | The data.cms.gov metastore lists `accessLevel: public` and **no license field**. CMS's API guide says works of the U.S. Government are in the public domain and reuse needs no permission. | n/a | Treated as U.S. government works, with attribution to CMS |
| NYS DOH `vn5v-hh5r`, `5gzv-zv2z` | The health.data.ny.gov dataset metadata and its `data.json` catalog list **no license field** (`attribution: New York State Department of Health`, `accessLevel: public`). The portal's general terms are the Open NY Terms of Use ([PDF](https://data.ny.gov/download/77gx-ii52/application/pdf)). | HealthData.gov harvests the NYS catalog (mirrors `j5hh-ai98` and `gdcn-zk7j`) and lists the license as **ODbL** (`http://opendefinition.org/licenses/odc-odbl/`) | **ODbL 1.0**, the stricter reading, plus the Open NY terms |

The NYS publisher pages do not themselves state ODbL. The repository follows
ODbL anyway as the conservative choice. Concretely:
- **License.** The NYS-derived content is offered under the
  [Open Database License (ODbL) 1.0](https://opendatacommons.org/licenses/odbl/1-0/).
  This covers the NYS columns and rows in `processed/`, the crosswalk, and the
  NYS test-fixture excerpts.
- **Attribution.** Every copy keeps the attribution below.
- **Share-alike.** Anyone who publicly uses an adapted version of these tables
  must keep them under ODbL.
- **No implied endorsement.** The project does not imply endorsement by
  NYS DOH. The data is used "as is".

**Attribution.**
- Contains data from the Centers for Medicare & Medicaid Services, Provider
  Data Catalog (datasets `yv7e-xc69`, `y9us-9xdf`, `xubh-q36u`).
- Contains information from New York State Department of Health, Health Data
  NY (datasets `vn5v-hh5r`, `5gzv-zv2z`), which is made available under the
  Open Database License (ODbL) as listed in the HealthData.gov catalog.
- This project is not affiliated with or endorsed by CMS, NYS DOH, or any
  hospital. The processed tables and the crosswalk are this project's
  transformations, and the publishers are not responsible for them.

Measure definitions were checked against:

- CMS Hospital Downloadable Database **Data Dictionary**, October 2026
  ([PDF](https://data.cms.gov/provider-data/sites/default/files/data_dictionaries/hospital/HOSPITAL_Data_Dictionary.pdf)):
  measure names, column definitions, and refresh cadence (OP-18: 12 months,
  refreshed quarterly; OP-22 and EDV: 12 months, refreshed annually).
- eCQM **CMS32v7**, "Median Time from ED Arrival to ED Departure for
  Discharged ED Patients" ([eCQI](https://ecqi.healthit.gov/system/files/ecqm/measures/CMS32v7.html)):
  OP-18 population and exclusions. *Version 7 was reviewed; check the current
  CMS Hospital OQR Specifications Manual for later changes.*
- CMS HOQR **Web-Based Measures Submission Guidelines** (2020)
  ([PDF](https://www.qualityreportingcenter.com/globalassets/2020/01/oqr/qrc-hoqr-wbm-submission-guidelines-2020--final.pdf)):
  OP-22 meaning of "seen" (evaluated by a physician, APN, or PA).

Reviewed but **not ingested**:

- NYS SPARCS de-identified *record-level* inpatient and ED files: outside this
  project's data scope (no record-level data, even de-identified).
- NYS Potentially Preventable ED Visit rates (`f8ue-xzy3`, `gu5u-nze7`): by
  patient county/ZIP, not by facility.
- CMS OP-23 (head CT for stroke within 45 minutes): an ED quality measure, but
  not a patient-flow measure.

## How to obtain the files

```bash
python -m beyond_the_wait.data.public_pipeline download   # automated
python -m beyond_the_wait.data.public_pipeline build      # offline
```

**If the automated download fails** (on 2026-11-15, the CMS catalog API
returned HTTP 403 to Python's standard HTTP client, though the same URL
worked from `curl` and a browser), download each file manually and register it:

1. Open the dataset page linked in the table above.
2. Download the CSV (CMS: "Download full dataset"; NYS: Export → CSV).
3. Register it, giving the CSV's download URL:
   ```bash
   python -m beyond_the_wait.data.public_pipeline register cms_timely_effective_care \
       ~/Downloads/Timely_and_Effective_Care-Hospital.csv \
       --url "https://data.cms.gov/provider-data/sites/default/files/resources/<...>/Timely_and_Effective_Care-Hospital.csv" \
       --source-modified 2026-10-19
   ```
4. Repeat for each key, then run `build`.

The pipeline never substitutes a different or invented file.

The 2026-11-15 files were obtained this way: downloaded with `curl` from the
official URLs recorded in the manifest, then registered.
