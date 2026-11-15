# Public Data Methodology — Bronx ED Reference Data

**Milestone 2 · Data retrieved 2026-11-15 · Provenance: `PUBLIC`**

This document explains which official ED measures we use for Bronx
hospitals, exactly what each one measures, what it does **not** measure, and
why these measures **cannot by themselves identify a hospital's operational
bottlenecks**.

> **Bottom line.** Public measures describe *how long* some patients spent in
> an ED, *what share* left before being seen, and *how many* encounters
> occurred. They say nothing about *why*. This project uses them as
> context, never as evidence of the cause of delays at any real hospital.

---

## 1. Sources

Five official datasets. Details, URLs, and fingerprints are in
[`data/public/SOURCES.md`](../data/public/SOURCES.md) and
`data/public/processed/build_manifest.json`.

| Publisher | Dataset | What we take from it |
|---|---|---|
| CMS | Timely and Effective Care – Hospital | OP-18a/b/c/d, OP-22, EDV |
| CMS | Footnote Crosswalk | Official text explaining missing or qualified scores |
| CMS | Hospital General Information | County, hospital type, emergency services |
| NYS DOH | Health Facility General Information | Independent facility registry |
| NYS DOH | ED Encounters by Facility (SPARCS, aggregate) | Encounter counts by quarter and year |

## 2. Three measures that are easy to confuse

| Question | Measure | Available? |
|---|---|---|
| How long did patients who went **home** spend in the ED (arrival → departure)? | **OP-18** family (median minutes) | ✅ CMS, calendar 2025 |
| What share of patients **left before** a physician, APN, or PA evaluated them? | **OP-22** (percent) | ✅ CMS, calendar 2024 |
| How long until a patient was **first seen** by a provider? | Door-to-provider | ❌ **Not reported** in any source reviewed |

These measures are **not interchangeable**. A short OP-18 does not mean a
short door-to-provider time, and a low OP-22 does not mean short waits. The
code enforces this: each measure has a `MeasureKind`, and asking for
door-to-provider raises `MeasureNotAvailableError` instead of quietly
substituting another measure (`src/beyond_the_wait/data/ed_measures.py`).

## 3. Measure definitions

### OP-18: median time from ED arrival to ED departure (minutes)

| ID | Population |
|---|---|
| OP-18a | All included patients, including psychiatric/mental health and transfers |
| OP-18b | Excluding psychiatric/mental health patients and transfers (the headline measure) |
| OP-18c | Psychiatric/mental health patients only (downloadable database only) |
| OP-18d | Patients transferred to another facility only |

- **Who's counted:** ED visits that did **not** end in inpatient admission at
  the same facility. The eCQM specification (CMS32v7) excludes "ED visits
  followed within an hour by an inpatient encounter at the same physical
  facility" and patients who died in the ED.
- **Statistic:** median, not mean (CMS labels it "Average (median)").
- **Period:** 12 months, refreshed quarterly. Current file: 2025-01-01 to 2025-12-31.
- **Does not measure:** time spent by **admitted** patients, so it cannot
  show **boarding**. It also doesn't show door-to-provider time, or why the
  visit took as long as it did.
- **`Sample` column:** the number of cases behind the median (a few hundred
  for Bronx hospitals). OP-18 is chart-abstracted from samples, so this is
  not the hospital's ED visit volume.

### OP-22: left without being seen (percent)

- **Definition:** percentage of ED patients who left before being evaluated
  by a physician, advanced practice nurse, or physician assistant (CMS HOQR
  web-based measure guidance).
- **Period:** 12 months, refreshed annually. Current file: 2024-01-01 to 2024-12-31.
- **Reporting:** submitted annually by each hospital as an aggregate.
- **Does not measure:** how long people waited before leaving, door-to-provider
  time for those who stayed, or people who left *after* being seen.
- **`Sample` column:** far larger than OP-18's (e.g., 125,885). It appears to
  be the number of ED patients the percentage is based on, but the data
  dictionary doesn't define it per measure, so we keep it as published and
  don't call it "ED visits."
- **Status note:** CMS proposed removing the LWBS measure in the CY 2024
  OPPS proposed rule (ACEP commented against it). Secondary summaries report
  that removal was **not finalized** (see §10; Federal Register text not
  checked). OP-22 is still published in the current file (2024 period).
  Confirm its status before relying on future releases.

### EDV: ED volume (category)

- Reported as `low`, `medium`, `high`, or `very high`. It is **not** a number,
  so we store it as `value_category` with no numeric value. Current period: 2024.
- We have not documented CMS's category thresholds here, so EDV shouldn't
  be converted to visit counts.

### NYS ED encounter counts

From SPARCS, summarized by facility and quarter. Four counts per facility
per period:

| Measure ID | Meaning |
|---|---|
| `NYS_ED_TOTAL` | All ED encounters = the three below |
| `NYS_ED_TREAT_AND_RELEASE` | Released without ambulatory surgery or admission |
| `NYS_ED_AMBULATORY_SURGERY` | Required an operating room for ambulatory surgery |
| `NYS_ED_ADMITTED` | Admitted to the facility as an inpatient |

- These are **counts**, with no times. `NYS_ED_ADMITTED` shows how many
  ED patients were admitted, not how long they waited for a bed.
- Each year has **quarter rows (Q1–Q4)** and a **calendar-year total (CY)**.
  We label every row's `period_type`. Adding quarter and CY rows together
  would double-count.
- We keep 2024 and 2025, the years overlapping the current CMS periods.
- The pipeline checks Total = Treat-and-release + Ambulatory surgery + Admitted
  whenever all four are reported. This held for every row in the 2026-10-22 file.

## 4. Why public measures can't identify a hospital's bottleneck

This project rests on one observation: **the same wait time can have very
different causes**. Public measures can't distinguish between them:

1. **They're outcomes, not mechanisms.** A 279-minute median says time was
   long. It doesn't say whether patients waited for a bed, a clinician, a lab
   result, or a ride home.
2. **The most important constraint is invisible.** OP-18 excludes admitted
   patients, so inpatient **boarding**, the constraint most often linked
   to ED crowding, is not measured at all. NYS admission counts have no times.
3. **Medians hide distributions.** Two EDs with the same median can have very
   different worst-case waits, hours of the day, or patient mixes.
4. **Periods don't line up.** OP-18 covers 2025; OP-22 and EDV cover 2024.
   Combining them mixes years.
5. **Reporting units don't line up with buildings.** CMS reports one result
   per CCN, and a CCN can cover more than one site (see §5). We can't tell
   which sites contribute.
6. **Case mix and community context differ.** Acuity, behavioral-health
   volume, and social needs affect times. None of these are adjusted for
   in OP-18.
7. **Small numbers and suppression.** Several values are unavailable because
   the case count was too small (CMS footnote 1) or redacted by NYS ('S').
8. **Hospital-submitted and sampled.** OP-22 is self-reported in aggregate;
   OP-18 is based on chart-abstracted samples.

**What we do with these data:** show them as clearly labeled `PUBLIC` context,
and later use them as plausibility ranges for the **fictional** simulation.
**What we never do:** attribute a cause of delay to a real hospital, rank
hospitals as if the measures were complete, or let Claude claim otherwise.

## 5. Facility matching: how "Lincoln", "St. Barnabas", and "BronxCare" were identified

CMS identifies hospitals by **CCN**; NYS DOH identifies each physical site by
**PFI**. Neither file contains the other's ID, so we matched them with
independent evidence and a human review:

1. **CMS location filter:** CMS lists `County/Parish = BRONX`, `State = NY`,
   `Hospital Type = Acute Care Hospitals`, and `Emergency Services = Yes`.
   Five hospitals qualify.
2. **Address matching against the NYS registry:** addresses are normalized
   (`EAST`→`E`, `AVENUE`→`AVE`, `THIRD`→`3RD`, …) and compared with ZIP codes.
3. **Human review:** accepted matches are recorded in
   [`data/public/reference/bronx_facility_crosswalk.csv`](../data/public/reference/bronx_facility_crosswalk.csv).
   Every build re-checks the crosswalk against the current official records
   (names, ZIPs, street numbers, method) and stops if anything changed.

| CMS CCN | CMS name | NYS PFI | NYS name | Match |
|---|---|---|---|---|
| 330009 | BRONXCARE HOSPITAL CENTER | 001164 | BronxCare Hospital Center | exact address |
| 330059 | MONTEFIORE MEDICAL CENTER | 001169 | Montefiore Medical Center – Henry & Lucy Moses Div | exact address |
| 330080 | LINCOLN MEDICAL & MENTAL HEALTH CENTER | 001172 | Lincoln Medical & Mental Health Center | exact address |
| 330127 | JACOBI MEDICAL CENTER | 001165 | Jacobi Medical Center | partial address (CMS adds "SOUTH") — reviewed |
| 330399 | ST BARNABAS HOSPITAL | 001176 | SBH Health System | exact address |

**Excluded:** Bronx VA Medical Center (`33016F`; VA hospitals are reported in a
separate CMS VA table) and Bronx Psychiatric Center (`334053`; psychiatric,
no emergency services).

**NYS sites with no separate CMS record:** Montefiore Wakefield, Westchester
Square, Weiler, Hutchinson Campus (extension clinic), the Montefiore children's
mental health center, BronxCare's Grand Concourse site (PFI 001178), North
Central Bronx Hospital, and Calvary Hospital. The NYS registry's official
**"Main Site Facility ID"** links several of them to a matched hospital
(e.g., North Central Bronx → Jacobi; BronxCare 001178 → 001164).

> ⚠️ **A matched address doesn't mean a matched scope.** BronxCare's CMS record
> matches the Fulton Avenue site (5,118 NYS ED encounters in 2025), while
> BronxCare's Grand Concourse site recorded 113,343. CMS's OP-22 figure for CCN
> 330009 is based on 125,885 patients (2024), far more than the Fulton Avenue
> site alone. That suggests the CMS figure covers more than one site, but
> these public files can't confirm which. The same uncertainty applies to
> Montefiore and to Jacobi / North Central Bronx. **Don't divide CMS figures by
> NYS site-level counts.**

## 6. Missing, suppressed, and qualified values

| Situation | How we store it |
|---|---|
| CMS score `Not Available` | `status = not_available`, empty `value`, `missing_reason` = official footnote text (e.g., "[1] The number of cases/patients is too few to report.") |
| CMS `Not Available` with no footnote | Same, with reason "CMS published 'Not Available' without a footnote explaining why." |
| CMS number with a footnote (e.g., "[3] shorter time period") | `status = reported`, footnote kept in `footnote_text` as a caveat |
| NYS `S` | `status = suppressed`, empty `value`, reason "Redacted by NYS DOH (published as 'S')." |
| Anything unrecognized | The build **stops** with an error |

**Missing is never converted to zero.** Tests assert this for every row.

## 7. Units and conversions

| Measure | Published unit | Stored | Conversion |
|---|---|---|---|
| OP-18 | minutes | `value` (minutes) + `value_hours` | hours = minutes ÷ 60 |
| OP-22 | percent (e.g., `9` = 9%) | `value` (percent) + `value_fraction` | fraction = percent ÷ 100 |
| EDV | category | `value_category` | none |
| NYS counts | encounters | `value` | none (commas removed if present) |

The original published text is always kept in `score_raw`.

## 8. Reproducibility

- `download` records each file's official URL, SHA-256, size, download date,
  and publisher last-modified date (`data/public/raw/manifest.json`).
- `build` refuses to run if any raw file no longer matches its fingerprint.
- Outputs are sorted and use fixed formatting with **no build timestamp**, so
  the same inputs always produce byte-identical files. Tests rebuild twice and
  compare fingerprints.
- Tests run offline against byte-for-byte excerpts of the official files
  (`tests/fixtures/public/`). A build from those excerpts reproduces the
  committed `bronx_ed_measures.csv` exactly.

## 9. Known limitations and open questions

- OP-18 population rules were checked against eCQM CMS32 **version 7**. The
  current chart-abstracted specification may differ in detail.
- The meaning of `Sample` for OP-22 isn't defined per measure in the
  dictionary.
- OP-22's future status depends on CMS rulemaking.
- EDV category thresholds aren't documented here.
- CMS → NYS matching relies on addresses plus human review. No public file
  provides an official CCN↔PFI key.
- On 2026-11-15, the CMS catalog API refused Python's standard HTTP client
  (HTTP 403), so files were registered through the manual path.

## 10. Verification log (Milestone 3 review)

Reviewed 2026-11-15. **No official measure definition was changed.**

| Item | What we checked | Result | Status |
|---|---|---|---|
| OP-18 population and exclusions | Searched for the current Hospital OQR Specifications Manual | The current manual wasn't located. Secondary sources point to it, and CMS materials list OP-18a/c/d strata, consistent with the current CMS file. A Telligen deadline notice cites "specifications version 18.0c" (unconfirmed). | **Partly verified.** Definitions still rest on eCQM CMS32v7 plus the October 2026 data dictionary. |
| OP-22 status | Searched for the CY 2024 OPPS final rule outcome | AAMC and America's Essential Hospitals report CMS **did not finalize** removal of the ED LWBS measure. Leapfrog's analysis describes a modified approach (suspension pending future rulemaking). The Federal Register text wasn't checked. The current CMS file still publishes OP-22 for 2024. | **Partly verified.** Measure kept as published; status to recheck on future CMS releases. |
| Door-to-provider availability | Re-checked the current CMS file | Still no door-to-provider measure. | Unchanged |

Sources consulted: [AAMC summary of the CY 2024 OPPS final rule](https://www.aamc.org/advocacy-policy/washington-highlights/cms-releases-cy-2024-opps-final-rule);
[America's Essential Hospitals](https://essentialhospitals.org/cms-issues-cy-2024-opps-final-rule/);
[Leapfrog OPPS 2024 final rule analysis](https://prod-next.leapfroggroup.org/sites/default/files/Files/OPPS%20FY%2024%20Final%20Rule%20Analysis_final.pdf);
[Telligen MBQIP submission deadlines (May 2026)](https://www.telligen.com/wp-content/uploads/2026/05/MonthlyMBQIPDataSubmissionDeadlines_202605_508.pdf).
