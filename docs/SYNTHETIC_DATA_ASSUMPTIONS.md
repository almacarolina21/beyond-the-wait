# Synthetic Data Assumptions — Riverside Commons Community ED (fictional)

**Milestone 3 · Provenance: `SYNTHETIC`**

This document lists every assumption the synthetic patient generator uses,
explains the modeling choices, and states plainly what the synthetic data
is **not**.

> **The generator describes a fictional ED.** Every number below is an
> illustrative assumption written for this project. None of them were measured
> at, derived from, or calibrated to any real hospital, including the Bronx
> hospitals in our public reference data. **No assumption below has been
> verified against real-world data.**

---

## 1. Synthetic data vs. public data

| | Public data (`PUBLIC`) | Synthetic data (`SYNTHETIC`) |
|---|---|---|
| Where it comes from | Official CMS and NYS DOH releases | Our code + assumptions in `config/sites/*.yaml` |
| Unit | Hospital-level aggregates (medians, percentages, counts) | Individual fictional patients |
| Describes | Real hospitals, real past periods | An invented ED on invented dates |
| Can show | What a real hospital reported | How a model ED *could* behave under stated assumptions |
| Cannot show | Why delays happened | Anything about a real hospital |
| Files | `data/public/processed/` (committed) | Regenerated from a seed; `data/synthetic/` is git-ignored |

The two never mix: the generator reads only the fictional site profile, and
every row it produces is labeled `SYNTHETIC`. Public measures may later be
shown **next to** simulated results as context, but never as proof that
the simulation matches a real hospital.

## 2. What the generator produces — and what it doesn't

| Generated patient **attributes** (Milestone 3) | Simulated **outcomes** (Milestone 4 — not generated yet) |
|---|---|
| `synthetic_patient_id` (e.g. `SYN-42-000001`) | Time to triage, bed, provider |
| `arrival_time`, `arrival_date`, `arrival_hour`, `day_of_week` | Waits in each queue |
| `acuity_esi` (1–5) | Length of stay |
| `needs_lab`, `needs_imaging` | Boarding hours for admitted patients |
| `hypothetical_admission` | Departure time, left-without-being-seen |

`hypothetical_admission` means "this fictional patient's case would call for
admission". It's an input to the simulation, not a result. Whether a bed is
available, and how long the patient waits for it, is decided later by
simulated capacity.

## 3. Modeling choices, in plain language

### Poisson arrivals with an hourly rate

Patients arrive independently, but on average more come at noon than at 4 a.m.
The standard model for this is a **Poisson process**:

- Each clock hour has an average rate, e.g. 12 patients/hour at noon,
  multiplied by that weekday's multiplier.
- The **count** in that hour is random, following a Poisson distribution with
  that average. A 12/hour hour might bring 9, 12, or 16 patients.
- Given the count, the **arrival moments** are spread uniformly at random
  across the hour.

Because our rate only changes on the hour, generating one hour at a time is
*exact* for this model, so no approximation is needed. A test checks the
Poisson signature: across 300 simulated days, the variance of daily counts is
close to their mean.

**Midnight and dates.** Each hour looks up its own calendar date. 23:00 on
Sunday uses Sunday's multiplier; 00:00 the next morning uses Monday's. An
arrival at 23:59:59 stays on Sunday. Tests cover this boundary.

### Random seeds

Computers produce "random" numbers from a starting value called a **seed**.
The same seed always produces the same sequence, so:

- `seed=42` gives exactly the same fictional patients every time. That's what
  makes results reproducible and testable.
- A different seed gives a different but equally valid sample. Later,
  running many seeds shows how much results vary by chance alone.

We use NumPy's `default_rng`, seeded explicitly. The seed is split into **two
independent streams**, one for arrivals and one for attributes. Changing an
attribute assumption (say, lab use) therefore doesn't change *who arrives
when*, which keeps later scenario comparisons fair.

### Conditional probabilities

Acuity is drawn first, then everything else depends on it:

- `P(ESI 2) = 0.15`: 15% of arrivals are ESI 2.
- `P(lab | ESI 2) = 0.85`: *of ESI-2 patients*, 85% need a lab test.

The overall share follows from the **law of total probability**:

```
P(admission) = Σ  P(ESI = a) × P(admission | ESI = a)
             = 0.01×0.80 + 0.15×0.45 + 0.45×0.25 + 0.30×0.03 + 0.09×0.01
             = 0.1979
```

The site profile also states the overall admission share
(`inpatient.ed_admission_fraction = 0.20`). Validation requires the two to
agree within 0.005, so the configuration can't contradict itself.

**Simplification:** given acuity, lab, imaging, and admission are drawn
*independently*. Real patients who get imaging are probably more likely to be
admitted. This is a known limitation (see §6).

### Lognormal service times

Task durations are positive and "right-skewed": most are near typical, a few
take much longer. A **lognormal** distribution has that shape. Each assumption
is written as two numbers an administrator can reason about, `median`
(typical) and `p90` (9 in 10 finish within this). The code converts them
into the distribution's parameters. These are durations of **work**, not
waits. They are configured now and used in Milestone 4.

## 4. Assumption register

Status for every row: **illustrative assumption, unverified**.

### Arrivals

| Assumption | Value | Note |
|---|---|---|
| Hourly average arrivals (00:00 → 23:00) | 5.5, 4.5, 4.0, 3.5, 3.0, 3.0, 3.5, 5.0, 7.5, 10.0, 11.5, 12.0, 12.0, 11.5, 11.0, 11.0, 10.5, 10.5, 10.0, 9.5, 9.0, 8.5, 7.5, 6.5 | 190.5 patients on an average day. Overnight-low, late-morning-peak shape. |
| Day-of-week multipliers | Mon 1.10, Tue 1.03, Wed 1.00, Thu 0.98, Fri 0.98, Sat 0.95, Sun 0.96 | Average exactly 1.0. Busier early in the week. |
| Arrivals independent of each other | — | Ignores clusters (e.g., multi-patient incidents). |

### Patient mix (per ESI level 1 / 2 / 3 / 4 / 5)

| Assumption | Values | Implied overall |
|---|---|---|
| Acuity distribution | 0.01 / 0.15 / 0.45 / 0.30 / 0.09 | sums to 1.00 |
| P(lab \| ESI) | 0.95 / 0.85 / 0.75 / 0.25 / 0.05 | 55.4% |
| P(imaging \| ESI) | 0.85 / 0.65 / 0.55 / 0.30 / 0.05 | 44.8% |
| P(admission \| ESI) | 0.80 / 0.45 / 0.25 / 0.03 / 0.01 | 19.79% (overall stated: 20%) |

"ESI" here means **ESI-like 1–5 levels** (1 = most urgent). The generator
doesn't apply the actual ESI triage algorithm; it assigns a level at random
from the distribution above.

### Service times (minutes; median / p90)

| Task | Median | p90 |
|---|---|---|
| Triage | 8 | 15 |
| Provider evaluation, ESI 1 / 2 / 3 / 4 / 5 | 60 / 45 / 35 / 20 / 15 | 120 / 90 / 70 / 40 / 30 |
| Lab turnaround | 60 | 120 |
| Imaging turnaround | 75 | 150 |
| Discharge process | 30 | 75 |

## 5. Relationship to the Milestone 2 public measures

- **Not used as inputs.** No CMS or NYS value feeds the generator.
- **No calibration claim.** We don't claim the fictional ED resembles any
  real hospital's operations.
- **Possible later use, clearly labeled:** OP-18 medians for the five Bronx
  hospitals (184–279 minutes, 2025, *discharged* patients only) may be
  shown as a plausibility range beside simulated results. That comparison
  would be rough and descriptive: OP-18 excludes admitted patients, and our
  simulation will include them.
- **Official definitions unchanged.** See the verification log in
  [PUBLIC_DATA_METHODOLOGY.md §10](PUBLIC_DATA_METHODOLOGY.md#10-verification-log-milestone-3-review).

## 6. Known limitations

1. Every value is an unverified illustrative assumption.
2. Lab, imaging, and admission are independent given acuity; real ones are correlated.
3. No seasonality, holidays, weather, or surges beyond the weekday multiplier.
4. No arrival mode (ambulance vs. walk-in), age, or behavioral-health stream.
5. Acuity doesn't vary by hour of day.
6. Clock times are naive local times; daylight-saving changes aren't modeled
   (every day has exactly 24 hours).
7. Arrival times are rounded down to whole seconds.
8. Reproducibility holds for the same NumPy version. A future NumPy release
   could change the random sequence for a given seed.
