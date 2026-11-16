# Validation Gate and Detailed Metrics (Milestone 5)

**Provenance of every number: `SIMULATED`** · Fictional ED · Not clinically
validated · Not a staffing recommendation

> The gate sits between the simulation ("Python calculates") and the future
> Claude interpretation layer ("Claude explains"). Its job is to make sure
> Claude is only ever handed numbers that are internally consistent, labeled,
> and accompanied by their limitations, and **never** numbers from a run that
> failed an integrity check.

Code: `src/beyond_the_wait/analysis/validation.py`,
`src/beyond_the_wait/analysis/detailed_metrics.py`.
Demo: `python -m beyond_the_wait.analysis.demo --seed 42 --gate-examples`.

---

## 1. Three statuses

| Status | Meaning | Can it be released for interpretation? |
|---|---|---|
| **PASS** | Mathematically consistent and within declared scope | Yes |
| **CAUTION** | Internally consistent, but interpretable only with stated limitations (one seed, near saturation, overload stress test, unfinished visits, model simplifications) | Yes, with the report attached |
| **BLOCKED** | Corrupted, incomplete, or mathematically inconsistent | **No.** `release_for_interpretation()` raises `BlockedResultError` |

The overall status is the **worst** status of any check. In Milestone 5, every
valid run is at least **CAUTION**, because every run is a single seed of a
fictional model with unvalidated assumptions.

### Overload is not corruption

An overloaded configuration (for example, physicians limited to one patient at
a time) produces long but **internally consistent** results. It's labeled
`overloaded = true` and **CAUTION**, never BLOCKED, so its diagnostic value
(*how* and *where* the system fails) is kept. Pass `intent="stress_test"` to
record that the overload was deliberate.

### Release rule

`release_for_interpretation(result, report)` is the only intended path from
the simulation to interpretation. It refuses when:

1. the report is **BLOCKED**, or
2. the report's **fingerprint** (SHA-256 of the event log plus run settings)
   doesn't match the result, so a report can't be reused for a different or
   later-altered result.

Interpretation checks are **not run** on BLOCKED results, so no interpretive
text is generated from inconsistent numbers.

## 2. Integrity checks (failure ⇒ BLOCKED)

| Check | Protects against | Cannot prove |
|---|---|---|
| `provenance` | Rows mislabeled `PUBLIC`/`SYNTHETIC`, or mixed in from another run | That the simulated numbers are realistic |
| `patient_conservation` | Patients lost, duplicated, or miscounted at the stop (input count = completed + unfinished = census) | That patients were routed sensibly |
| `event_order_and_units` | Impossible timestamps, missing intermediate stages, negative times, unit/arithmetic errors in derived minutes | That durations are clinically plausible |
| `cohort_denominators` | Warm-up patients leaking into statistics; an **empty measured cohort** | That the warm-up period is long enough |
| `resource_conservation` | Double-booked staff or spaces; staff, space, and bed counts that don't balance with the event log | That the capacity assumptions are right |
| `summary_consistency` | Summary metrics that disagree with the event log (counts, medians, stage sums, shift totals) | That the metrics answer the right question |
| `littles_law_sample_path` | Census and length-of-stay bookkeeping drifting apart (an exact identity, not a queueing formula) | Anything about steady-state behavior |

If a check **crashes** on malformed data (e.g., a missing column), the crash
is caught and the check is **BLOCKED**. Bad data can't escape the gate by
breaking it.

## 3. Interpretation and scope checks (CAUTION, never BLOCKED)

| Check | CAUTION when | Protects against |
|---|---|---|
| `capacity_and_stability` | Load ≥ 0.85 or peak hour ≥ 1 (pre-run); overload, hard stop, or growing census (post-run) | Presenting a strained or unstable system as typical |
| `extreme_waits` | Any measured patient over 24 h in the ED (unfinished counted at time so far) | A reassuring median masking extreme waits |
| `esi1_timeliness` | Any ESI-1 patient waits more than 10 min for a physician (an **illustrative review trigger, not a clinical standard**) | Overlooking delays to the most critical patients |
| `censoring` | Any measured visit unfinished at the stop | Selection bias from completed-only LOS |
| `observation_length` | Fewer than 3 measured days | Conclusions from too short a window |
| `single_seed` | Always (until Milestone 6) | Treating one random draw as a stable estimate |
| `no_bottleneck_claims` | Always (until Milestone 6) | Causal "dominant bottleneck" claims from one run |
| `model_assumptions` | Always | Forgetting results depend on unvalidated assumptions |

## 4. Safety limitations attached to every report

1. **Fictional model**: not clinically validated; describes no real hospital.
2. **Not a staffing recommendation**: no result establishes that a change would be safe in a real ED.
3. **ESI-1 priority limitation**: non-preemptive; a critical patient waits for the next free physician or space.
4. **Triage is first come, first served**: acuity priority applies only after triage.
5. **Physician concurrency (2) is fictional**: results are highly sensitive to it; not yet varied.
6. **Bed releases (42/day) are a simplified abstraction**: boarding depends heavily on it.
7. **Left without being seen is not modeled**: no LWBS rate is reported.

## 5. Detailed metrics (all `SIMULATED`, all with a `unit` column)

| Table | What it adds |
|---|---|
| `acuity_metrics` | Arrival-to-provider, LOS, and boarding by ESI × disposition: median, p90, p95, max, counts over 4 h and 24 h |
| `censoring_summary` | Completed-only LOS vs. a **lower bound** that includes unfinished visits |
| `extreme_waits` | Patients over 4/8/24 h in the ED and over 1/4 h to a provider, by ESI, including unfinished |
| `boarding_distribution` | Boarding percentiles and shares over 2/4/8 h, by the shift when admission was decided |
| `stage_decomposition` | Mean minutes per stage (sums exactly to mean LOS); **descriptive only** |
| `shift_utilization` | Utilization, share of time ≥ 90% utilized, and patients per staffed person-hour by shift (physician-hours, triage-nurse-hours; arrivals per nurse-hour, descriptive) |
| `hourly_profile` | Physician utilization and queue, triage queue, arrivals, median arrival-to-provider by clock hour |

**Unavailable:** `left_without_being_seen_rate`. Patients can't leave before
evaluation in the model, so any rate, even 0%, would be invented.

## 6. Baseline findings (seed 42; one run; suggestive, not established)

Validation status: **CAUTION**. All 7 integrity checks PASS. CAUTIONs:
peak-hour physician and triage load, bed releases near saturation, single
seed, no bottleneck claims, model assumptions.

- **Boarding finding survives and sharpens.** Overall median boarding is 11 min,
  but for admissions decided on the **night shift** it's **410 min**, and 86%
  of those patients boarded over 4 hours (day shift: 22 min, 0%). This is
  driven by the *assumed* afternoon-weighted bed releases.
- **The "23:00 pinch point" is refined, not confirmed as unique.** By clock
  hour, the highest physician queue and utilization are at **13:00** (queue
  1.47, utilization 0.92). **23:00 is second** (0.94, 0.86), ahead of 12:00.
  Both are pinch points in this run; neither is established without multiple
  seeds.
- **Daily averages hid peak strain.** Triage nurses average about 0.7
  utilization but spend **56–64% of each shift at ≥ 90% utilization**.
- **Priority starts only after triage.** ESI-2 median arrival-to-provider
  (15.0 min) is no faster than ESI 3–5 (13.3–14.4 min).
- **ESI 1:** 11 patients, longest wait for a physician 4.2 min (PASS). The
  non-preemptive limitation still applies whenever the ED is full.
- **No extreme waits in the baseline** (max LOS 907 min, an admitted ESI-1
  patient who boarded). The overload stress test, by contrast, produces 540 of
  1,338 patients over 24 h. It's flagged CAUTION and overloaded, not hidden.

## 7. What the gate cannot do

- It can't validate assumptions (concurrency, bed releases, durations).
- It can't make one seed into a stable estimate.
- It can't establish causality or a dominant bottleneck.
- It can't make the model clinically valid or its implications safe for a real ED.
- Passing integrity checks means **internally consistent**, not **true**.
