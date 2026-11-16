# Project Specification — Beyond the Wait: Bronx ED Access Lab

**Status:** Draft v0.2 · **Owner:** Project author · **Last updated:** 2026-11-15

> **Planning document.** This spec describes what we *intend* to build. As of
> Milestone 5, the foundation exists: validated configuration models,
> provenance labels, a public-data pipeline producing Bronx ED reference
> tables (the data behind F1), the synthetic patient generator (F2), a
> single-run baseline ED simulation (F3), patient and staff metrics (F7), and
> a validation gate that blocks inconsistent results (groundwork for F8–F10),
> as library and command-line tools. **None of features F1–F10 are implemented as
> user-facing features yet.** See
> [DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md) for progress. Unfamiliar terms are defined in the [Glossary](#9-glossary).

---

## 1. Problem Statement

Emergency departments (EDs) in high-need communities such as the Bronx face
long lengths of stay, crowded waiting rooms, and exhausted staff. Leaders often
see the *symptom* — "patients are waiting too long" — but the **same wait time
can come from very different constraints**:

| Constraint | What it looks like | Typical lever |
|---|---|---|
| **Inpatient boarding** | Admitted patients stay in ED beds waiting for an inpatient bed | Discharge-before-noon, bed management, surge units |
| **Staffing mismatch** | Provider/nurse schedules don't match hourly arrival patterns | Shift redesign, flex staffing |
| **Diagnostic delays** | Waiting on lab results or imaging | Point-of-care testing, imaging prioritization |
| **Care-coordination barriers** | Patients medically ready to leave but blocked by consults, transport, social work, placement | Coordinator roles, discharge lounges |

Choosing the wrong intervention wastes money and staff energy. Administrators
need a safe way to **ask "what if?"** before changing real operations, and to
understand the effect on **both patients and the people caring for them**.

## 2. Vision

A transparent, reproducible decision-support lab where an administrator can:

1. See public, aggregate context for Bronx EDs (clearly labeled as real data).
2. Explore a simulated ED built from synthetic patients (clearly labeled as synthetic).
3. Test operational interventions and compare outcomes with uncertainty ranges.
4. Read a plain-language explanation from Claude that is **grounded only in the
   validated results**, states its limitations, and defers to human judgment.

**Guiding principle:** *Python calculates. Claude explains. Humans decide.*

## 3. Target Users

| User | Need | How the app helps |
|---|---|---|
| **Primary: ED / hospital operations administrators** | Prioritize which bottleneck to address | Scenario comparison, bottleneck decomposition, plain-language summary |
| **Nurse managers & ED medical directors** | Understand staff workload effects | Staff-impact metrics (utilization, peak load) alongside patient metrics |
| **Quality / performance-improvement analysts** | Reproducible, auditable analysis | Versioned configs, seeds, exportable results, provenance labels |
| **Public-health / health-system planners** | Adapt the method to another site | Site-profile configs; no Bronx-specific logic hard-coded |
| **Secondary: technical reviewers / interviewers** | Judge design quality and responsible AI use | Architecture docs, tests, evaluation suite |

**Non-users:** This tool is **not** for frontline clinical decisions about
individual patients.

## 4. Features

### 4.1 In scope (MVP) — planned

All features below are **planned, not yet built**. Each is scheduled in a
milestone in DEVELOPMENT_PLAN.md.

| # | Feature | Description |
|---|---|---|
| F1 | **Public context panel** | Displays selected public aggregate ED metrics for Bronx hospitals with source, measure definition, and reporting period. Tagged `PUBLIC`. |
| F2 | **Synthetic patient generator** | Generates patient arrivals with acuity (ESI-like levels 1–5), hour-of-day/day-of-week patterns, diagnostic needs, and admission probability. Reproducible via random seed. Tagged `SYNTHETIC`. |
| F3 | **ED flow simulation** | SimPy discrete-event model: arrival → triage → bed → provider → diagnostics → disposition → departure/boarding. Tagged `SIMULATED`. |
| F4 | **Scenario builder** | Adjust levers (staffing by shift, bed count, lab/imaging turnaround, inpatient bed availability, coordination delay) and save as named scenarios. |
| F5 | **Multi-run comparison** | Runs each scenario many times (replications) and reports means with confidence intervals — never a single-run number as "the answer." |
| F6 | **Bottleneck decomposition** | Quantifies how much of length of stay is attributable to each stage (waiting for bed, provider, diagnostics, boarding, coordination) in the *simulation*. |
| F7 | **Patient & staff impact view** | Patient metrics (door-to-provider, LOS, left-without-being-seen proxy, boarding hours) and staff metrics (utilization, time above high-load threshold, patients per staff-hour). |
| F8 | **Claude interpretation** | Claude receives a structured evidence packet of validated results and returns a grounded explanation, uncertainty statement, limitations, and questions for the human team. |
| F9 | **Guardrails** | Automated checks that confirm Claude's numbers match the evidence and its citations exist, and that *flag* possible causal claims, clinical directives, or missing oversight language for review. A first line of defense — not proof of correctness (see §5.1). |
| F10 | **Evaluation suite** | Pytest tests for the math + an eval harness for Claude outputs (accuracy, grounding, safety, limitations). |

### 4.2 Out of scope

- Real patient-level data of any kind (including de-identified record-level data).
- Predictions or recommendations for individual patients.
- Claims about **why** a specific real hospital has long waits.
- Production deployment, authentication, or EHR integration.
- Staffing schedules intended for direct operational use without human review.
- Financial / reimbursement modeling (possible future extension).

### 4.3 Future extensions (post-MVP)

- Behavioral-health and pediatric patient streams.
- Calibration helper that fits synthetic parameters to a site's own aggregate metrics.
- Equity lens (e.g., differential impact by acuity or arrival time).
- Export of an "analysis brief" PDF for leadership meetings.

## 5. Requirements Traceability

How each of the project's key requirements **will be** addressed (planned):

| Key requirement | Planned approach |
|---|---|
| 1. Distinguish real vs. synthetic data | Provenance tag on every dataset/metric; UI badges; `data/README.md` rules |
| 2. Python, not Claude, does numerics | Simulation & analysis layers; Claude receives numbers, never computes them |
| 3. Claude interprets validated results & uncertainty | Validation gate before evidence packet; CIs included in packet |
| 4. Never invent causes of delays | Prompt rules + structured output requiring evidence IDs + automated guardrail flags + adversarial evals + human review of outputs |
| 5. Patient safety & human oversight | Clinical use out of scope; mandatory "for human review" framing; directive-language flags; human review of eval results |
| 6. Reusable components | Site profile & scenario configs; layers communicate via typed data contracts |
| 7. Evaluate accuracy, grounding, safety, limitations | `tests/` (math) + `evals/` (Claude outputs) with scored reports + documented human review |

### 5.1 What automated checks can and cannot do

Automated checks (planned in Milestone 9) are useful but limited. Being clear
about this is part of the design.

| Automated checks **can** | Automated checks **cannot** |
|---|---|
| Confirm every number Claude writes appears in the evidence packet | Prove that a cited piece of evidence actually *supports* the claim made about it |
| Confirm every cited evidence ID exists | Establish that a simulated bottleneck is the *real* cause of delays at any hospital |
| Flag causal or directive wording using patterns and rubrics | Catch every unsafe or misleading phrasing (patterns miss things; rubrics can be wrong) |
| Confirm required sections (uncertainty, limitations) are present | Judge whether those sections are *adequate* |

**Therefore:** causal correctness and clinical safety cannot be established by
evidence-ID checks alone. They require (a) a designed evaluation set including
adversarial cases, and (b) review by people with appropriate operational and
clinical expertise. Even then, the simulation shows effects *in a model*, not
proven causes in a real ED.

## 6. Success Criteria

These are **targets** for the finished MVP. None are met yet; each box is
checked only when its test or evaluation passes.

### 6.1 Analytical correctness
- [ ] Simulation reproduces known queueing results within tolerance on simple
      test cases (e.g., M/M/c queue average wait vs. formula; Little's Law
      L = λW holds within 5%).
- [ ] Same seed + same config ⇒ identical results (reproducibility).
- [ ] Every reported comparison includes a 95% confidence interval from ≥ 30 replications.
- [ ] ≥ 85% line coverage on `simulation/` and `analysis/`.

### 6.2 Grounding & safety (Claude)
- [ ] **100%** of numbers in Claude's explanations match the evidence packet (automated check).
- [ ] **100%** of cited evidence IDs exist in the packet (automated check).
- [ ] **0** invented causes across the adversarial eval set (e.g., "Why does Hospital X have long waits?" with no causal evidence → Claude says the data can't answer that). *Measured by automated flags **plus** human review of every adversarial-case output.*
- [ ] **0** individual-patient clinical directives across the safety eval set. *Measured by automated flags **plus** human review.*
- [ ] **100%** of explanations include an uncertainty statement and limitations section (presence checked automatically; adequacy reviewed by a human on a sample).
- [ ] Every explanation is clearly labeled as decision support requiring human review.

Passing these criteria means the system behaved well **on the evaluation set**.
It does not certify clinical safety or causal accuracy in real use (see §5.1).

### 6.3 Usability
- [ ] An administrator can build and compare two scenarios in under 5 minutes without reading code.
- [ ] Every chart and number shows its provenance tag (`PUBLIC` / `SYNTHETIC` / `SIMULATED`).

### 6.4 Reusability
- [ ] A new site can be configured by adding one site-profile file — no code changes.

### 6.5 Portfolio readiness
- [ ] README lets a reviewer install, test, and run the app in ≤ 10 minutes.
- [ ] Author can explain every architectural decision (see `docs/decisions/`).

## 7. Assumptions & Constraints

- Public aggregate metrics are reported with lag (often a year or more) and at
  hospital level; they are context, not ground truth for the simulation.
- The simulation is a **simplified model**. Its value is comparing scenarios
  *relative to each other*, not forecasting exact real-world numbers.
- Synthetic parameters (arrival rates, service times) are documented
  assumptions informed by published literature and public aggregates, and are
  editable.
- Claude API calls cost money and require an API key; the app must work fully
  (minus explanations) without one.

## 8. Ethical & Safety Commitments

- No real patient data. No employer proprietary information.
- Clear labels prevent synthetic results from being mistaken for real hospital performance.
- Explanations describe *simulated* effects and avoid blaming specific real
  institutions or staff.
- The tool supports — never replaces — clinical and operational leadership judgment.
- Staff well-being is treated as a first-class outcome, not only throughput.

## 9. Glossary

| Term | Meaning |
|---|---|
| **ED** | Emergency department |
| **LOS** | Length of stay — time from arrival to leaving the ED |
| **Boarding** | An admitted patient waiting in the ED because no inpatient bed is available |
| **Door-to-provider** | Time from arrival until first seen by a physician, PA, or NP |
| **LWBS** | Left without being seen — patient leaves before evaluation |
| **ESI** | Emergency Severity Index, a 5-level triage scale (1 = most urgent) |
| **Utilization** | Share of time a resource (nurse, bed, provider) is busy |
| **DES** | Discrete-event simulation — models a system as a sequence of events (arrive, start service, finish) over time |
| **Replication** | One complete simulation run with its own random seed; many replications show the range of likely outcomes |
| **CI (95% confidence interval)** | A range that expresses how precisely we've estimated an average from the replications |
| **Little's Law** | A queueing rule: average number in system = arrival rate × average time in system. Used as a sanity check |
| **Evidence packet** | The structured set of validated facts (with IDs) that Claude is allowed to use |
| **Provenance** | Where a piece of data came from: `PUBLIC`, `SYNTHETIC`, or `SIMULATED` |
| **PHI** | Protected health information — identifiable patient data. Never used in this project |
