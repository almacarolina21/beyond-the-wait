# What-If Analysis — Multi-Seed Paired Comparisons (Milestone 6)

**Provenance of every number: `SIMULATED`** · Fictional ED · Hypothetical
interventions · **Model-internal evidence only**

> A 95% confidence interval here describes how the *fictional model* responds
> across random seeds under its stated assumptions. It is not evidence about
> any real hospital, and no intervention is shown to be clinically safe or
> advisable. Decisions require qualified human review.

Code: `analysis/experiments.py`, `analysis/statistics.py`,
`config/scenarios.py`, `simulation/randomness.py`.
Run: `python -m beyond_the_wait.analysis.whatif_demo --seeds 30 --sensitivity-seeds 20 --workers 8`
(about 6 minutes on 8 cores). CSVs for Milestone 7 go to `data/synthetic/experiments/` (git-ignored).

---

## 1. Scenario levers: what actually changes the model

| Lever (scenario schema v2) | Kind of input | Lands in | Simulated? |
|---|---|---|---|
| `staffing_changes` role `physicians` | Controllable | physicians on duty per shift | ✅ |
| `staffing_changes` role `triage_nurses` | Controllable | triage nurses per shift | ✅ |
| `staffing_changes` role `nurses` | Controllable | total nurses on duty | ❌ **No direct effect** (only triage is modeled); flagged by `scenario_effects()` |
| `treatment_space_changes` | Controllable | spaces per type | ✅ (no visible effect while spaces aren't binding) |
| `bed_release_change_per_day` | Controllable (hospital-wide) | inpatient bed releases per day | ✅ |
| `diagnostic_turnaround_multiplier` | Controllable | lab and imaging median and p90 | ✅ |
| `arrival_multiplier` | **Uncertain assumption** (demand) | hourly arrival rates | ✅, `sensitivity`/`stress_test` only |
| `physician_concurrency_override` | **Uncertain assumption** (1–3, hypothetical) | patients per physician | ✅, `sensitivity`/`stress_test` only |
| ~~`inpatient_bed_change`~~ | — | changed `staffed_beds`, which the simulation never used | **Removed**; rejected with guidance |

Scenario `kind`: `intervention` (hypothetical operational change), `archetype`
(fictional problem condition), `sensitivity` (vary an assumption), or
`stress_test` (deliberate overload). Assumption changes are **rejected** in
interventions and archetypes. `apply_scenario()` returns a new validated
profile; the baseline is never modified, and tests show scenarios are reversible.

## 2. Pairing and common random numbers

Every arm (baseline, or baseline + scenario) is run on the **same seeds**. One
seed drives four independent random streams, so for each seed:

| Stream | Same in both arms? |
|---|---|
| Arrivals and patient attributes | Yes (unless demand changes, which is sensitivity only) |
| Each patient's task durations (all drawn up front) | Yes; diagnostic multipliers scale each patient's draw exactly |
| Inpatient bed releases | Yes, by **thinning**: more releases per day keep every baseline release plus extras |

So the paired difference `d_i = scenario_i − baseline_i` reflects the
intervention, not different luck. Example: a ±30-minute seed-to-seed swing
shared by both arms cancels out of `d_i`. A test shows the paired interval
stays under ±0.5 min where an unpaired comparison would be about ±15.

## 3. Statistics

- **Paired t interval** for the mean difference: mean ± t(0.975, n−1) × sd/√n,
  with n = 30 paired seeds (20 for the sensitivity grid).
- **Assumptions:** independent seeds; the *average* difference is approximately
  normal (reasonable at n = 30 unless differences are very skewed, so
  |skewness| > 2 is flagged).
- **Confidence vs. prediction interval:** the CI describes the *average*
  effect across many runs. A *single* future run varies more; its 95%
  prediction interval is mean ± t × sd × √(1 + 1/n), reported separately by
  `describe()`. Neither describes a real hospital.
- **Verdicts:** `improves` / `worsens` when the CI excludes 0 (the direction
  depends on whether lower is better); `inconclusive` when it includes 0;
  `changes` for neutral metrics like utilization; `no_change` for identical
  results.
- t critical values: exact table for df ≤ 7, Cornish–Fisher expansion above
  that (within 3×10⁻⁵ of published tables, tested).

## 4. Validation integration and comparison-level checks

Every replication goes through the Milestone 5 gate. Metrics are extracted
only from `release_for_interpretation()` output, so a BLOCKED run produces
**no numbers**.

| Comparison check | Status when triggered |
|---|---|
| `blocked_replications`: any replication BLOCKED | **BLOCKED**, no estimates reported |
| `seed_pairing`: arms ran different seeds | **BLOCKED** |
| `seed_coverage`: fewer than 30 paired seeds | CAUTION |
| `missing_data`: a metric undefined for some seeds (excluded **and counted**) | CAUTION |
| `excessive_variability`: CI half-width > 25% of the baseline level | CAUTION |
| `skewed_differences`: \|skewness\| > 2 | CAUTION |
| `censoring`: unfinished measured visits (completed-only LOS biased low) | CAUTION |
| `overload_regime`: any overloaded replication (stress-test regime) | CAUTION |
| `carried_cautions`: replication-level CAUTIONs, counted across seeds | CAUTION |
| `unsupported_conclusions`: always ("model-internal"); plus sensitivity kind, ineffective levers, nothing changed | CAUTION |

## 5. Results: baseline vs. five interventions (30 paired seeds, 8 days, 1 warm-up day)

All comparisons: **CAUTION**, all 60 replications per comparison CAUTION, none BLOCKED.
Mean over seeds; diff = intervention − baseline; 95% CI of the mean paired difference.

| Metric (minutes unless noted) | Baseline | +1 evening physician | +1 night physician | +1 night triage nurse | +6 bed releases/day | Diagnostics 25% faster |
|---|---|---|---|---|---|---|
| Arrival to provider, median | 16.7 | −1.1 [−1.3, −0.9] | −0.5 [−0.6, −0.4] | −1.2 [−1.4, −1.1] | n.s. | n.s. |
| Arrival to provider, p90 | 55.1 | −5.2 [−6.7, −3.8] | −3.8 [−4.7, −3.0] | −2.4 [−3.2, −1.6] | n.s. | n.s. |
| Waiting for triage, p90 | 32.1 | 0 | 0 | −4.8 [−5.9, −3.8] | 0 | 0 |
| Waiting for a physician, p90 | 14.2 | −6.7 [−8.2, −5.3] | −4.6 [−5.4, −3.7] | **+3.2 [+2.4, +4.0]** | n.s. | n.s. |
| LOS discharged, median | 145.7 | −2.9 [−3.6, −2.2] | −1.9 [−2.3, −1.4] | −0.7 [−1.0, −0.4] | n.s. | −13.9 [−14.3, −13.5] |
| LOS discharged, p90 | 256.3 | −4.8 [−6.3, −3.3] | −3.1 [−4.4, −1.8] | −1.0 [−1.7, −0.2] | n.s. | −28.1 [−29.3, −27.0] |
| LOS admitted, median | 315.0 | −2.0 [−3.6, −0.5] | −0.5 [−0.9, −0.1] | n.s. | **−98.4 [−130.9, −66.0]** | −28.6 [−35.4, −21.8] |
| LOS admitted, p90 | 788.9 | n.s. | n.s. | n.s. | **−166.1 [−197.5, −134.8]** | −32.7 [−41.7, −23.7] |
| Boarding, median | 159.2 | n.s. | n.s. | n.s. | **−111.2 [−143.3, −79.2]** | n.s. |
| Boarding, p90 | 629.4 | n.s. | **+2.9 [+1.2, +4.6]** | **+2.9 [+0.8, +5.0]** | **−165.9 [−198.0, −133.7]** | n.s. |
| Boarding > 4 h (share of admitted) | 37.4% | −0.3 pts | n.s. | +0.2 pts | **−13.6 pts [−16.5, −10.8]** | −1.1 pts [−1.9, −0.3] |
| Physician-slot utilization | 70.1% | −6.4 pts (changes) | −6.4 pts (changes) | n.s. | n.s. | n.s. |
| Triage-nurse utilization | 70.6% | 0 | 0 | −11.8 pts (changes) | 0 | 0 |

n.s. = 95% interval includes 0.

**Comparison CAUTIONs shared by all:** one baseline seed (of 30) was
overloaded, so the overload regime is flagged; skewed paired differences for
several metrics; the space-wait stage is too noisy to interpret; and evidence
is model-internal. Carried replication CAUTIONs include **`esi1_timeliness` in
25–32 of 60 replications**: an ESI-1 patient waited more than 10 min for a
physician in about half of all runs. Seed 42 alone (Milestone 5) had hidden this.

### Congestion moves between stages

- **+1 night triage nurse:** triage wait p90 −4.8 min, but physician wait p90
  **+3.2 min**, and boarding p90 +2.9. Patients reach the physician queue
  sooner and wait there instead. A hand-checked test reproduces this exactly
  (two patients, one physician: triage wait 5 → 0, physician wait 25 → 30,
  arrival-to-provider unchanged).
- **+1 night physician:** physician wait improves, but **boarding p90 +2.9 min**.
  Admission decisions happen earlier at night, and those patients then wait
  longer for the (unchanged) afternoon bed releases. Their total LOS still
  improves slightly, so the boarding "worsening" is relocated waiting, not added waiting.

## 6. Archetypes: is the suspected constraint actually binding?

Each fictional archetype was compared to the baseline (does the condition
hurt?), then a relief intervention was compared to the archetype (does
relieving the constraint help?). **A binding-constraint claim is made only
when the relief interval excludes zero.**

| Archetype (fictional) | Condition vs. baseline | Relief intervention | Relief effect (95% CI) | Largest mean stage under the condition |
|---|---|---|---|---|
| Boarding-heavy (38 releases/day) | LOS admitted median +261 [+184, +339] | +6 releases/day | **−316 [−409, −223]**, supported | boarding (463 min) |
| Physician mismatch (5/3/2) | Arrival-to-provider p90 +55.5 [+41.6, +69.4] | Move 1 physician day → evening | **−55.5 [−69.4, −41.6]**, supported | **boarding (239 min)** |
| Triage mismatch (1 day triage nurse) | Arrival-to-provider p90 +161 [+148, +173] | +1 day triage nurse | **−161 [−173, −148]**, supported | **boarding (318 min)** |
| Diagnostic delay (×1.5) | LOS discharged median +27.2 [+26.5, +27.8] | Diagnostics ×0.75 | **−20.3 [−20.7, −19.8]**, supported | **boarding (255 min)** |

**Lesson:** in three of four archetypes, the **largest** stage (boarding) was
**not** the constraint whose relief fixed the outcome being studied. "Largest
time component" ≠ "cause". In the boarding-heavy archetype, relieving beds
also **moved congestion**: space waits fell but physician waits rose, as
patients previously stuck upstream reached physicians.

## 7. Sensitivity to fictional assumptions

Grid: physician concurrency ∈ {1, 2, 3} × bed releases/day ∈ {38, 42, 46},
20 paired seeds per setting, for three interventions. The regime is decided
**per comparison**: if any replication in either arm was overloaded, that
comparison is in the **stress-test regime** and excluded from stability
verdicts. That covers every setting with concurrency 1 and every setting with
38 releases/day (boarding load about 0.99), plus, for some interventions, a
setting where a single seed overloaded. So the number of operating settings
behind each verdict differs: 4 for bed releases and the night physician, 2
for faster diagnostics.

| Intervention | Metric | Verdict across operating assumptions |
|---|---|---|
| +6 bed releases/day | boarding p90; LOS (lower bound, all) | **stable improvement** (4/4) |
| +6 bed releases/day | arrival-to-provider p90 | never supported (no effect) |
| Diagnostics ×0.75 | LOS (lower bound, all) | stable improvement |
| Diagnostics ×0.75 | boarding p90 | stable improvement* |
| Diagnostics ×0.75 | arrival-to-provider p90 | never supported |
| +1 night physician | arrival-to-provider p90 | stable improvement (4/4) |
| +1 night physician | LOS (lower bound, all) | **⚠ unstable**: improves at concurrency 2, n.s. at 3 |
| +1 night physician | boarding p90 | **⚠ unstable**: worsens at concurrency 2, n.s. at 3 |

\* Based on only 2 operating settings (the others had at least one overloaded
replication); treat it as weakly supported.

**What this means:** the value of an extra night physician depends on an
assumption we can't verify (how many patients a physician can handle at
once). If physicians can manage 3, the extra physician's LOS benefit
disappears inside this model. Bed releases and diagnostic turnaround keep
their direction across the operating range tested.

## 8. Inside the fictional model: what looks promising, tradeoffs, uncertainty

*Model-internal, hypothetical, not a recommendation.*

- **More inpatient bed releases** show the largest, most stable effects on
  admitted patients (median admitted LOS about −98 min; share boarding over 4 h
  −13.6 points), with no supported effect on discharged patients. **Uncertain:**
  the release mechanism is an abstraction; "6 more releases per day" has no
  defined real-world cost or feasibility in this model.
- **Faster diagnostics** helps *both* discharged (about −14 min median) and
  admitted patients (about −29 min), stable across assumptions. **Uncertain:**
  lab and imaging have no capacity limits in the model, so how a 25% speed-up
  would be achieved isn't represented.
- **+1 evening physician** gives the largest reduction in tail
  arrival-to-provider time (p90 −5.2 min) with small LOS gains. **+1 night
  physician** helps less, and its LOS benefit is **assumption-sensitive**.
- **+1 night triage nurse** mostly **relocates** waiting from triage to the
  physician queue.
- Across all of them: a fictional model, one demand pattern, no LWBS, and
  non-preemptive ESI-1 handling, where ESI-1 delays over 10 min occur in about
  half of the runs.

## 9. Tests (Milestone 6)

- **Statistics:** published t values, a hand-calculated interval, prediction
  vs. confidence interval, pairing variance reduction, invalid inputs.
- **Levers:** schema rules, retired-lever rejection, baseline immutability and
  reversibility, each lever changing the intended setting and simulated
  behavior, the `nurses` lever having no simulated effect, and bed-release
  thinning keeping a superset of releases.
- **Comparisons (hand-built replications):** exact paired difference and CI;
  verdict rules; BLOCKED replications invalidate a comparison; seed-mismatch
  blocking; CAUTION carry-forward; missing-data counting; censoring;
  overload; seed coverage; noise and skew flags; congestion-shift detection;
  text always scoped to the fictional model with no real-hospital names.
- **Integration:** same seeds give the same results; parallel equals serial; a
  real BLOCKED replication stops a comparison; a small sensitivity grid.
- **Priority under different resources:** scarce beds, two-slot physicians,
  shift-change capacity increases, triage relief relocating waits, and the
  FIFO boarding queue.
- **No invalid invariants:** tests don't assume extra capacity can never
  lengthen any individual patient's wait.
- **Mutation-checked:** ignoring BLOCKED, misaligning pairs, and uncoupling bed
  releases each fail the tests.

## 10. Limitations specific to this milestone

1. Only one fictional site and one demand pattern; archetypes are constructed examples.
2. The thinning change altered seed-42 bed randomness compared with Milestones 4–5 (documented there).
3. Paired-t intervals assume roughly normal mean differences; several metrics are flagged as skewed.
4. Sensitivity covers two assumptions over small grids. Durations, demand shape, and release timing aren't varied.
5. "Binding" is defined operationally (relief improves the outcome) and only inside the model.
6. Staffing costs and feasibility aren't modeled, so no intervention is "better value".
