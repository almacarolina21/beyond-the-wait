# Simulation Model — Baseline ED Flow (Milestone 4)

**Provenance of every output: `SIMULATED`** · Fictional ED · Educational
operational model, **not** a clinically validated triage or staffing tool.

> Python calculates. Claude explains. Humans decide. This milestone is the
> "Python calculates" part: a discrete-event simulation (SimPy) of the
> fictional Riverside Commons Community ED. Nothing here describes a real
> hospital, and simulated outcomes are never mixed with public CMS/NYS values.

---

## 1. What a discrete-event simulation does

The simulation keeps a clock (minutes since 00:00 on the start date) and a
list of upcoming events: a patient arrives, triage ends, a bed opens. It jumps
from one event to the next. When a patient needs a resource that's busy, they
join a waiting list. Waits are never typed in. They **emerge** when demand
exceeds capacity, which is why simulation can show how constraints interact
(e.g., boarders holding beds, so new patients wait for a bed, so physicians
sit idle).

## 2. Patient journey

```
arrive ─► triage (triage nurse; FIFO) ─► wait for a treatment space (priority)
       │   ESI 1 may bypass triage                │
       │                                          ▼
       │                       provider evaluation (physician slot; priority)
       │                                          ▼
       │                    lab and/or imaging in parallel (delay only)
       │                                          ▼
       │                           disposition (instantaneous)
       │                     ┌────────────────────┴────────────────────┐
       │              discharge process                   BOARDING: keep the ED space
       │              (keeps the ED space)                until an inpatient bed is released
       ▼                     └────────────► leave the ED; space freed ◄┘
```

## 3. Resources and queue policies

| Resource | Capacity (fictional baseline) | Queue order |
|---|---|---|
| Triage nurses | 2 day / 2 evening / 1 night | **First come, first served.** Acuity is assigned *at* triage, so it can't set priority before triage. |
| Treatment spaces | 36 acute, 3 resuscitation, 8 fast-track, 6 hallway; each ESI level has an ordered list of allowed types | **Priority** (ESI 1 first), then first come within a level. A patient waiting for a full space type doesn't block a lower-priority patient whose type is free. |
| Physician slots | physicians on duty (4/4/2) × **2 concurrent patients** = 8/8/4 | **Priority** (ESI 1 first), then first come |
| Lab, imaging | Unlimited (delay only) | — |
| Inpatient beds for ED admissions | Simplified release stream (§5) | **First come, first served** among boarders |

### Non-preemptive priority, and how ESI 1 is handled

"Non-preemptive" means nobody is interrupted. When a unit frees up, the
highest-priority waiting patient gets it, but a patient already in a bed or
an exam keeps it.

- ESI 1 **bypasses triage** (`esi1_bypasses_triage: true`) and goes to the
  **front** of the space and physician queues.
- But if every physician slot is busy, an ESI-1 patient **waits** for the next
  one to free up. A test demonstrates this: an ESI-1 arrival waits 55 minutes
  for the only physician to finish an exam.
- A real ED would make room immediately (pull a clinician, resuscitate in a
  hallway). **This model understates how fast critical patients are seen when
  the ED is full**, and it isn't suitable for any conclusion about critical-care
  timeliness.
- Strict priority can make low-acuity patients wait a long time when busy.
  Real EDs counter this with fast-track areas and clinical judgment.

### Shift changes and midnight

Capacity follows the shift table (07:00, 15:00, 23:00), repeating every
1,440 minutes. The night shift (23:00–07:00) crosses midnight seamlessly.
When capacity **rises**, waiting patients start immediately. When it **falls**,
clinicians going off shift **finish their current patient**, and no new
patient starts until fewer are busy than the new capacity. So no assignment is
ever made above capacity (tests check every assignment), though busy can
briefly exceed scheduled capacity while handovers finish.

## 4. Assumptions added in Milestone 4

All values are in `config/sites/fictional_bronx_community_ed.yaml`
under `operations` (schema version 3). **All are illustrative and unverified.**

| Assumption | Value | Why it's needed |
|---|---|---|
| Triage nurses per shift | 2 / 2 / 1 | The config had total nurses only. Other nurses are not modeled as a constraint. |
| Physician concurrent patients | 2 | With 1, physician demand is about 1.40× supply on an average day, so queues would grow without limit. Real physicians interleave patients; the evaluation time is an *episode*, not uninterrupted minutes. |
| ESI 1 bypasses triage | yes | Visibly critical patients are taken straight back. |
| Space eligibility | ESI 1 → resus, acute · ESI 2 → acute, resus · ESI 3 → acute, hallway · ESI 4–5 → fast track, acute, hallway | Real EDs route by space type. |
| Inpatient bed releases | 42/day, afternoon-weighted; 2 open at start; at most 4 held for the ED | See §5. |

Service times (Milestone 3) are sampled per patient from lognormal
distributions with the configured median and 90th percentile.

## 5. Inpatient boarding: a simplified, explicitly labeled abstraction

**What we don't do:** treat "300 staffed beds at 93% occupancy" as 21 beds
permanently free for the ED. Occupancy is an average snapshot. Beds turn over
as inpatients leave, and ED admissions compete with surgical, transfer, and
direct admissions for each one.

**What we do (`model: simplified_release_stream`):**

1. Beds become available **to the ED** one at a time, at random moments
   (time-varying Poisson), averaging **42 per day**, weighted toward the
   afternoon (`hourly_release_profile`).
2. Admitted ED patients **board**: they keep their ED treatment space and wait
   first come, first served for a released bed.
3. A bed released when nobody is boarding is held for the ED, but at most **4**
   at once. Beyond that it goes to other hospital demand and is counted.
4. **2** beds are open when the simulation starts.

With about 37.7 expected admissions a day, the boarding queue runs at about
**90% load**. That's deliberately tight, so boarding pressure is visible.

**Limitations of this abstraction:** releases don't respond to ED demand or to
the time admitted patients have waited; there's no unit-specific bed matching
(ICU vs. telemetry vs. medical); bed cleaning and transport time are zero; and
`inpatient.staffed_beds` / `typical_occupancy` are **not** used by the
simulation. The scenario lever `inpatient_bed_change` therefore **has no effect
in Milestone 4**. A bed-release lever is planned for Milestone 6.

## 6. Stopping rule, unfinished visits, warm-up

- **Arrival window:** arrivals only in `[start, start + arrival_days)`.
- **Drain:** after arrivals stop, the simulation keeps running until every
  patient has left, **or** until `drain_hours` (default 72) after the window
  closes, the **hard stop**.
- **Unfinished visits are never silently excluded.** Patients still present at
  a hard stop stay in the table with `status = unfinished`, the stage they
  reached (`waiting_for_space`, `boarding`, …), and `time_in_ed_at_stop_min`,
  a lower bound on their LOS. They're counted separately and excluded from
  LOS medians, because their final LOS is unknown. A hard stop is flagged
  **critical**.
- **Warm-up:** the ED starts empty, which is unrealistically calm. Patients
  arriving during the first `warmup_days` (default 1) are simulated but labeled
  `warm_up` and excluded from statistics. Resource metrics use the window from
  the end of warm-up to the end of arrivals.
- **Carryover demand:** warm-up patients still in the ED when measurement
  starts *do* occupy resources then. That's realistic carryover. Measured
  patients still present when arrivals stop are followed through the drain
  period.

## 7. Outcome metrics (all `SIMULATED`)

| Metric | Definition |
|---|---|
| Triage wait | triage start − arrival (0 for ESI-1 bypass) |
| Space wait | space assigned − triage end |
| Provider wait | provider start − space assigned |
| Arrival to provider | provider start − arrival |
| ED length of stay | departure − arrival, completed visits, split discharge / admit |
| Boarding time | bed assigned − disposition (admitted patients) |
| Utilization | busy unit-minutes ÷ available unit-minutes (time-weighted) |
| Queue length | time-weighted mean and maximum waiting |
| Throughput | arrivals and departures per day; completed vs. unfinished |

These simulated measures share names with some public measures, but they are
**not comparable**. For example, CMS OP-18 is a median for *discharged* patients
from real hospitals' records. Our LOS comes from a fictional model.

## 8. Validity checks

**Before the run** (`simulation/validity.py`, rough arithmetic, not
steady-state formulas): load = work arriving ÷ capacity, for an average day
and the busiest hour.

| Load | Flag | Meaning |
|---|---|---|
| ≥ 1.0 average | **critical** | Queues grow without limit. Waits depend only on how long you ran it and are meaningless. |
| ≥ 0.85 average | warning | Long, very variable waits, sensitive to small changes |
| ≥ 1.0 busiest hour | warning | Queues build at peak and must clear later |

**After the run:** hard stop reached; unfinished visits; **average census
rising** from the first to the last measured day (a possibly unstable queue);
**more than 1% of patients staying over 24 hours**; fewer than 3 measured
days; and an always-on reminder that one seed is one random draw.

> **Lesson learned (Milestone 4).** The first growing-queue check compared
> median LOS by arrival day. In an overloaded test run (physicians limited to
> one patient at a time), it **missed the overload**: strict priority pushed
> the whole backlog onto ESI 4–5 patients, whose median LOS was 10,210 minutes
> for day-1 arrivals and *fell* to 4,130 for day-7 arrivals (late arrivals are
> served during the drain period). Meanwhile the higher-acuity patients who
> dominate the overall median looked stable. The check now uses the **census**
> (patients in the ED: 130 → 593 in that run) plus an **extreme-stay** check
> (40% over 24 h). A regression test keeps it that way. Under priority
> queueing, *who* waits matters as much as *how long* the average patient waits.

### Why one 24-hour run isn't enough

- A single run is **one random draw**. Another seed gives different numbers.
- A day is too short: the warm-up distortion is large, and a slowly growing
  queue can look stable for hours.
- Day-of-week effects need multiple weeks.
- **Policy comparisons need many seeds per scenario, confidence intervals,
  and common random numbers.** That's Milestone 6.

### Queueing theory, used only where it applies

Steady-state formulas assume constant capacity, unchanging arrival rates, a
single queue policy, and an infinitely long run. The full ED breaks all of
these (shifts, hourly rates, priorities, finite days). So the full model is
**not** checked against such formulas. Instead:

- one isolated server with Poisson arrivals and lognormal service times, run
  for 40,000 customers, matches the **Pollaczek–Khinchine** M/G/1 waiting-time
  formula within 10%, which validates the queueing machinery;
- **Little's Law** is checked as an exact sample-path identity (area under the
  "patients in ED" curve = sum of individual times in the ED), which needs
  no steady state.

## 9. Fair comparisons later: common random numbers

One seed drives four independent random streams: arrivals, patient
attributes, task durations, and bed releases. Every patient's task durations
are drawn up front, even for tasks they won't need. A scenario that changes
staffing therefore keeps the same patients, the same task durations, and the
same bed releases. Only the waiting changes. A test verifies this for the
"+1 evening physician" scenario.

## 10. Known limitations

1. Every operational value is an unverified illustrative assumption.
2. Non-preemptive priority understates the immediacy of critical care when the ED is full.
3. Lab and imaging have no capacity limits; there's no physician reassessment after results.
4. Only triage nurses are modeled; nurse-to-patient ratios aren't.
5. No left-without-being-seen behavior, and no patients leaving against advice.
6. Boarding uses the simplified release stream (§5); `inpatient_bed_change` has no effect yet.
7. Transfer, bed cleaning, and transport times are zero.
8. Fast track is open 24 hours; hallway spaces are always available.
9. No ambulance diversion, surges, or seasonality.
10. Results reflect one seed per run until Milestone 6.

## 11. How to run

```bash
python -m beyond_the_wait.simulation.demo --days 8 --warmup-days 1 --seed 42
```

```python
from datetime import date
from beyond_the_wait.config import load_site_profile
from beyond_the_wait.simulation.engine import SimulationSettings, run_simulation
from beyond_the_wait.analysis.metrics import patient_summary, resource_summary

site = load_site_profile("config/sites/fictional_bronx_community_ed.yaml")
result = run_simulation(site, SimulationSettings(start_date=date(2026, 11, 16),
                                                 arrival_days=8, warmup_days=1, seed=42))
patient_summary(result); resource_summary(result)
```

## 12. Validation gate (Milestone 5)

Every run can now be passed through a PASS / CAUTION / BLOCKED gate with
integrity checks, censoring-aware and by-ESI metrics, and attached safety
limitations. Inconsistent outputs are BLOCKED from interpretation. See
[VALIDATION_GATE.md](VALIDATION_GATE.md).
