# Architecture — Beyond the Wait: Bronx ED Access Lab

**Status:** Draft v0.7 · **Last updated:** 2026-11-16

> **Partly implemented.** As of Milestone 6, the **configuration layer and
> scenario levers (§2.2)**, **provenance labels**, the **public-data pipeline**,
> the **synthetic patient generator (§2.1)**, the **baseline simulation
> (§2.3)**, the **metrics and validation gate**, and **multi-seed paired
> what-if analysis (§2.4)**, and the **Streamlit dashboard (§2.7)** are
> implemented and tested. Everything
> else below is planned. Module names marked "planned" may change as we build.
> Terms are defined in the [PROJECT_SPEC glossary](PROJECT_SPEC.md#9-glossary).

> **Core principle:** *Python calculates. Claude explains. Humans decide.*
> Numbers flow **one way** — from deterministic code to the language model —
> and are checked again on the way out.

---

## 1. System Overview

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         STREAMLIT DASHBOARD  (app/)                      │
│   Context panel │ Scenario builder │ Results & charts │ AI explanation   │
└───────┬─────────────────┬──────────────────┬──────────────────┬──────────┘
        │                 │                  │                  ▲
        ▼                 ▼                  ▼                  │
┌──────────────┐  ┌───────────────┐  ┌───────────────┐  ┌───────┴─────────┐
│ 1. DATA      │  │ 2. CONFIG     │  │ 4. ANALYSIS   │  │ 6. GUARDRAILS   │
│ public refs  │  │ site profile  │  │ metrics, CIs, │  │ number check,   │
│ synthetic    │  │ scenarios     │  │ decomposition,│  │ cause check,    │
│ generator    │  │ (validated)   │  │ VALIDATION    │  │ safety check    │
└──────┬───────┘  └──────┬────────┘  │ GATE          │  └───────▲─────────┘
       │                 │           └──────┬────────┘          │
       │                 ▼                  │                   │
       │          ┌───────────────┐         ▼                   │
       └────────► │ 3. SIMULATION │  ┌───────────────┐  ┌───────┴─────────┐
                  │ SimPy ED model│─►│ results table │─►│ 5. INTERPRET    │
                  │ N replications│  │ (SIMULATED)   │  │ evidence packet │
                  └───────────────┘  └───────────────┘  │ → Claude API    │
                                                        └─────────────────┘
                     ┌─────────────────────────────────────────────┐
                     │ 7. EVALUATION  tests/ (math)  evals/ (LLM)  │
                     └─────────────────────────────────────────────┘
```

## 2. Components

### 2.1 Data layer — `src/beyond_the_wait/data/`

**Purpose:** Supply inputs and keep real and fake data strictly separated.

| Module | Responsibility |
|---|---|
| `provenance.py` ✅ | `Provenance` enum (`PUBLIC`, `SYNTHETIC`, `SIMULATED`), strict parsing, `ProvenanceRecord`, and table helpers `tag_dataframe()` / `require_provenance()`. Provenance is stored as **columns on every row**, so it survives filters, merges, and CSV export. |
| `public_sources.py` ✅ | Registry of the 5 verified official datasets; manifest models (URL, SHA-256, dates). |
| `ed_measures.py` ✅ | Measure catalog: definition, population, unit, and what each measure does *not* measure. Door-to-provider and boarding time raise `MeasureNotAvailableError`. |
| `cms_loader.py` / `nys_loader.py` ✅ | Validated loaders: raw text first, explicit unit conversion, official missing-data reasons, duplicate and reporting-period checks. |
| `facility_matching.py` ✅ | CMS↔NYS matching by normalized address + ZIP, validated against a human-reviewed crosswalk. |
| `public_pipeline.py` ✅ | `download` / `register` / `build` → `data/public/processed/` with reproducible, fingerprinted outputs. See [docs/PUBLIC_DATA_METHODOLOGY.md](docs/PUBLIC_DATA_METHODOLOGY.md). |
| `synthetic_generator.py` ✅ | Creates fictional patients from a site profile + seed: time-varying Poisson arrivals (hour by hour, each hour using its own date's weekday multiplier), ESI acuity, then lab, imaging, and hypothetical admission **conditional on acuity**. Separate random streams for arrivals and attributes. Produces **attributes only, no outcomes**. Every row is tagged `SYNTHETIC`. See [docs/SYNTHETIC_DATA_ASSUMPTIONS.md](docs/SYNTHETIC_DATA_ASSUMPTIONS.md). |

**Why it's designed this way:** In healthcare IT you'd never mix a test
patient into production reporting. Same idea here — the provenance tag
travels with the data so the UI and Claude can't confuse them.

### 2.2 Configuration layer — `src/beyond_the_wait/config/` + `config/` ✅ (Milestone 1)

**Purpose:** Keep *what we model* separate from *how we model it*.

- **Site profile** (`config/sites/*.yaml`): site identity (must be marked
  `fictional: true`), physician and nurse staffing by shift, treatment spaces,
  24 hourly arrival rates with optional day-of-week multipliers, patient mix
  (ESI distribution; lab, imaging, and admission probabilities by acuity),
  service-time assumptions (lognormal median/p90), and inpatient capacity.
  Every section has a required `source_note`. Schema version 2 (Milestone 3).
- **Scenario** (`config/scenarios/*.yaml`, schema v2): a named set of *relative*
  changes applied on top of a site profile by `apply_scenario()`, which
  re-validates the result. Each scenario has a `kind` (`intervention`,
  `archetype`, `sensitivity`, `stress_test`). **Controllable inputs**
  (physicians and triage nurses per shift, spaces, bed releases per day,
  diagnostic turnaround) are kept apart from **uncertain assumptions**
  (demand, physician concurrency), which are only allowed in sensitivity and
  stress tests.
- Validated with **Pydantic v2** models so a typo or impossible value (negative
  beds) fails loudly before any simulation runs.

| Module | Responsibility |
|---|---|
| `models.py` | The Pydantic models and all validation rules |
| `loader.py` | Safe YAML loading (no code execution, duplicate keys rejected), readable errors |
| `scenarios.py` | `apply_scenario()` returns a new validated profile and never modifies the original; `scenario_effects()` reports which changes are simulated, not simulated, or assumptions |
| `errors.py` | `ConfigError` / `ScenarioError` so callers can catch config problems in one place |

**Why:** This is the reusability requirement. Adapting to a new health system
means writing a new YAML file, not editing Python. It mirrors how Epic build
separates configuration records from application code.

### 2.3 Simulation layer — `src/beyond_the_wait/simulation/` ✅ (Milestone 4, baseline)

**Purpose:** The *only* place ED operations are numerically modeled.
Full assumptions: [docs/SIMULATION_MODEL.md](docs/SIMULATION_MODEL.md).

- **SimPy** discrete-event simulation. Constrained resources: triage nurses,
  physician slots (shift-scheduled), treatment spaces by type (eligibility by
  ESI), and a **simplified inpatient bed-release stream** for boarding. Lab and
  imaging are delays without capacity limits. Coordinators are not yet modeled.
- Patient pathway (implemented):
  `arrive → triage (FIFO; ESI-1 bypass) → treatment space (priority) → provider (priority) → lab/imaging in parallel → disposition → [admit: board in the ED space until a bed is released] / [discharge: discharge process] → depart`
- Each patient produces an **event log row** with timestamps for every stage,
  the same shape as an ED tracking-board audit trail, tagged `SIMULATED`.
- Stopping rule: arrival window, then drain until empty or a hard stop.
  Unfinished visits are kept and labeled; the warm-up cohort is labeled and
  excluded from metrics.

| Module | Responsibility |
|---|---|
| `resources.py` ✅ | `ScheduledPool`, `TreatmentSpaceManager`, `InpatientBedQueue`, `StepSeries`; non-preemptive priority; every assignment logged |
| `randomness.py` ✅ | Seeded child streams (common random numbers); lognormal durations; bed release times |
| `engine.py` ✅ | `SimulationSettings`, `run_simulation()` → `SimulationResult` |
| `validity.py` ✅ | Pre-run load checks; post-run flags (saturation, hard stop, unfinished, growing queue, short run, single seed) |
| `demo.py` ✅ | Command-line demonstration |
| *replications* ✅ (M6) | Run in `analysis/experiments.py`: N paired seeds per arm; bed releases drawn by thinning so paired arms share every release |

**Why discrete-event simulation?** Queues interact non-linearly — adding a
provider does nothing if every bed is full of boarders. Spreadsheet averages
miss this; a DES captures it. SimPy is pure Python, well documented, and
easy to test.

### 2.4 Analysis layer — `src/beyond_the_wait/analysis/`

**Purpose:** Turn raw event logs into trustworthy metrics.

| Module | Responsibility |
|---|---|
| `metrics.py` ✅ | Basic waits, arrival-to-provider, LOS by disposition, boarding, utilization, queue lengths, throughput (measured cohort and window; `SIMULATED`). |
| `detailed_metrics.py` ✅ | By-ESI percentiles and maxima, censoring-aware LOS, extreme waits, boarding distribution, **descriptive** stage decomposition, utilization by shift and hour, patients per staffed person-hour. LWBS marked **unavailable**. |
| `validation.py` ✅ | **Validation gate**: typed report (PASS / CAUTION / BLOCKED); 7 integrity checks (conservation, event order and units, cohorts, resources, summary consistency, provenance, sample-path Little's Law) plus interpretation and scope checks. `release_for_interpretation()` refuses BLOCKED or mismatched reports. See [docs/VALIDATION_GATE.md](docs/VALIDATION_GATE.md). |
| `demo.py` ✅ | Validated-analysis demonstration with gate examples |
| `statistics.py` ✅ | Paired t confidence interval for a mean difference (no SciPy), separate prediction interval, skewness. |
| `experiments.py` ✅ | Paired multi-seed replications (parallel), every replication through the gate, BLOCKED → comparison BLOCKED, CAUTIONs carried forward, comparison-level checks, congestion-shift detection, binding-constraint evidence (relief test), sensitivity grid and stability verdicts. |
| `whatif_demo.py` ✅ | What-if demonstration; exports CSVs for Milestone 7. See [docs/WHAT_IF_ANALYSIS.md](docs/WHAT_IF_ANALYSIS.md). |

### 2.5 Interpretation layer — `src/beyond_the_wait/interpretation/` ✅ (Milestone 8, mock-first)

> **Built:** `evidence.py` (typed packet from the verified bundle; BLOCKED refused),
> `prompt.py`, `schema.py` (structured response), `providers.py` (DEMO mock by default;
> live Claude only with `BTW_AI_MODE=live` **and** a key), `verify.py` (10 deterministic
> checks; numbers rendered from the packet), `service.py`. Fact IDs look like
> `M:boarding_p90`, `CK:overload_regime`, `LIM:fictional_model`; Claude writes value
> tokens (`{{M:boarding_p90.change}}`) instead of numbers. The design sketch below is
> kept for history. Full detail: [docs/AI_INTERPRETATION.md](docs/AI_INTERPRETATION.md).

**Purpose:** Use Claude to explain validated results in plain language.

1. **Evidence packet builder** — converts validated results into a compact JSON
   document. Each fact gets an ID, value, unit, CI, and provenance, e.g.:
   ```json
   {"id": "E3", "metric": "mean_boarding_hours", "scenario": "baseline",
    "value": 4.2, "ci95": [3.9, 4.5], "unit": "hours", "provenance": "SIMULATED"}
   ```
   It also lists the scenario assumptions and known model limitations.
2. **Claude client** — calls the Anthropic Messages API (Python `anthropic`
   SDK, model `claude-opus-5-5`) with:
   - a **system prompt** stating the rules: use only the packet, cite evidence
     IDs, never compute new numbers, never attribute causes to real hospitals,
     no clinical directives, always state uncertainty and limitations;
   - **structured output** (JSON schema) so the response has fixed fields:
     `summary`, `key_findings[]` (each with `evidence_ids`), `uncertainty`,
     `limitations`, `questions_for_team`, `not_supported_by_evidence`;
   - handling for the cases where Claude declines to answer or the API
     returns an error (the app checks the response status before using it).
3. **Graceful degradation** — no API key or an API error ⇒ the dashboard
   still shows all numbers; only the explanation panel is disabled.

**Why structured output with evidence IDs?** It makes part of grounding
*checkable by code*: we can verify that each finding points to real evidence
and that its numbers match. It does **not** prove the finding interprets that
evidence correctly — that still requires evaluation and human review (§2.6).

### 2.6 Guardrails — `src/beyond_the_wait/interpretation/verify.py` ✅ (Milestone 8; evals in Milestone 9)

> Built as `verify.py`: a failed check withholds the **whole** response (nothing is shown
> "with a warning"), and nothing is logged. Red-team regressions live in
> `tests/unit/test_interpretation.py`.

Deterministic checks run on every Claude response **before** display:

| Check | Method | Strength |
|---|---|---|
| Numeric fidelity | Extract every number from the text; each must match a packet value (with rounding tolerance). | Strong — exact comparison |
| Citation validity | Every `evidence_id` cited must exist in the packet. | Strong — exact comparison |
| Causal-language flags | Flag causal wording about real hospitals, and causes not among the modeled constraints. | Heuristic — can miss or over-flag |
| Directive-language flags | Pattern + rubric check for individual-patient treatment language. | Heuristic — can miss or over-flag |
| Required sections | Uncertainty and limitations must be non-empty. | Checks presence, not quality |

A failed check ⇒ the response is withheld (or shown with a warning) and logged.

**Limits of these checks.** Passing every guardrail means the response is
*consistent with the evidence packet's IDs and numbers* and *didn't trip known
red-flag patterns*. It does **not** establish that a causal interpretation is
correct or that the output is clinically safe. A response can cite real
evidence and still draw the wrong conclusion from it. Those properties are
assessed through the evaluation suite (§2.8) and human review, and the UI
always presents output as decision support for human review.

### 2.7 Presentation layer — `app/` + `src/beyond_the_wait/dashboard/` ✅ (Milestone 7)

**Streamlit** multipage app (`st.navigation`), **Plotly** charts. Page scripts in
`app/views/` hold layout and text only; `beyond_the_wait.dashboard` holds tested
data loaders (`data.py`), chart builders (`charts.py`), theme (`theme.py`), and
helpers (`ui.py`), all calling existing APIs. Every chart title carries a
provenance badge, and PUBLIC data never shares a chart with simulated data.
Multi-seed results come from a fingerprint-verified **results bundle**
(`analysis/results_bundle.py`), and BLOCKED comparisons are never displayed as
data. The app runs offline on localhost, with no API key, uploads, or
telemetry by default. Milestone 8 adds an explanation panel to the What-If Lab
(`dashboard/ai_panel.py`): an explicit *Explain these results* button, verified
output only, cited evidence IDs, and the banner *"Decision support for human
review. Based on simulated data."* See [docs/DASHBOARD.md](docs/DASHBOARD.md).

**Why Streamlit?** It turns Python scripts into an interactive web app with
no front-end code, letting the project focus on the analytics and AI design.

### 2.8 Evaluation layer — `tests/` and `evals/`

| Layer | Tool | What it proves |
|---|---|---|
| Unit tests | pytest | Generator distributions, metric formulas, config validation |
| Integration tests | pytest | Full pipeline: config → sim → metrics → evidence packet |
| Analytical validation | pytest | Simulation matches queueing theory on simple cases |
| LLM evals | `evals/` + `evaluation/` (offline) | 180 hand-written candidate responses with expected ACCEPT / HUMAN_REVIEW / REJECT verdicts, scored by the same `assess()` the dashboard uses; dev split plus three hash-locked blind held-out rounds ([AI_EVALUATION.md](docs/AI_EVALUATION.md)). Live Claude responses are not yet evaluated. |
| Human review | [AI_REVIEW_RUBRIC.md](docs/AI_REVIEW_RUBRIC.md) | A person scores 7 dimensions with hard-fail rules; the dashboard shows HUMAN_REVIEW explanations only with their flags |

Unit tests use a **mocked** Claude client so they are free, fast, and offline.
The offline eval suite is free and runs in the test suite; a live eval (real
Claude responses) costs money and runs only with explicit approval.

## 3. Data Flow (one request, end to end)

1. User selects a **site profile** and builds a **scenario** in the UI.
2. Config layer **validates** both (Pydantic).
3. Data layer **generates synthetic patients** (seeded) → tagged `SYNTHETIC`.
4. Simulation runs **N replications** for baseline and scenario → event logs tagged `SIMULATED`.
5. Analysis computes metrics, comparisons with CIs, and decomposition.
6. **Validation gate** checks results. Fail ⇒ stop, show diagnostics, no AI call.
7. Evidence packet is built from validated results only.
8. Claude returns a **structured** explanation citing evidence IDs.
9. **Guardrails** verify the explanation.
10. UI displays numbers, charts, provenance badges, and the verified explanation.

Public reference data (step 0) flows into the UI's context panel separately and
is never mixed into simulated results.

## 4. Key Design Decisions

| Decision | Choice | Alternative considered | Reason |
|---|---|---|---|
| Who computes numbers | Python only | Let Claude calculate | LLMs can make arithmetic errors; healthcare decisions need reproducible math |
| Grounding mechanism | Evidence IDs + structured output + code checks + evals + human review | Prompt instructions alone | Instructions alone can't be verified; code checks verify numbers and citations; evals and human review cover what code can't (interpretation, causality, safety) |
| Simulation approach | Discrete-event (SimPy) | Spreadsheet / closed-form queueing | Captures interacting queues (boarding blocks beds blocks providers) |
| Uncertainty | Replications + 95% CI | Single run | One run is one random draw, not an answer |
| Config format | YAML + Pydantic | Hard-coded constants | Reusable across sites; validated |
| Layout | `src/` package | Flat scripts | Importable, testable, professional |

Detailed decision records will live in `docs/decisions/` (one short file per decision).

## 5. Folder Structure

```
beyond-the-wait/
├── app/                       # Streamlit UI: streamlit_app.py + views/ (Milestone 7)
├── config/
│   ├── sites/                 # Site profiles (YAML)
│   └── scenarios/             # Intervention scenarios (YAML)
├── data/
│   ├── public/raw/            # Downloaded public files (git-ignored)
│   ├── public/reference/      # Human-reviewed facility crosswalk
│   ├── public/processed/      # Bronx ED reference tables + build manifest
│   ├── public/SOURCES.md      # Official sources and manual-download steps
│   └── synthetic/             # Generated synthetic patients (git-ignored)
├── docs/
│   ├── PUBLIC_DATA_METHODOLOGY.md  # What public ED measures do and don't mean
│   ├── SYNTHETIC_DATA_ASSUMPTIONS.md  # Every synthetic assumption (fictional, unverified)
│   ├── SIMULATION_MODEL.md    # Simulation journey, policies, boarding abstraction, validity
│   ├── VALIDATION_GATE.md     # PASS/CAUTION/BLOCKED gate, detailed metrics, limitations
│   ├── WHAT_IF_ANALYSIS.md    # Paired multi-seed comparisons, levers, sensitivity, results
│   ├── DASHBOARD.md           # Dashboard pages, data flow, safeguards, testing
│   └── decisions/             # Architecture decision records
├── evals/
│   ├── cases/                 # LLM evaluation cases
│   └── results/               # Eval run outputs (git-ignored)
├── src/beyond_the_wait/
│   ├── config/                # Pydantic config models + loaders
│   ├── data/                  # Provenance, public refs, synthetic generator
│   ├── simulation/            # SimPy ED model
│   ├── analysis/              # Metrics, comparison, decomposition, validation
│   ├── interpretation/        # Evidence packet, Claude client, guardrails
│   └── evaluation/            # Eval harness and scorers
├── tests/
│   ├── conftest.py · paths.py # Shared fixtures and file locations
│   ├── fixtures/public/       # Byte-for-byte excerpts of official files (offline tests)
│   ├── unit/
│   └── integration/
├── .env.example               # Template for the API key (real .env is git-ignored)
├── pyproject.toml             # Package metadata, dependencies, tool settings
├── LICENSE                    # MIT
├── PROJECT_SPEC.md · ARCHITECTURE.md · DEVELOPMENT_PLAN.md · README.md
```

## 6. Security & Privacy

- **Data scope is the privacy control.** By project rule, the only data in this
  system is public aggregate data and synthetic data we generate. No real
  patient-level data, PHI, or employer information is ever brought in, so none
  can be sent to the Claude API. This is enforced by design and by reviewing
  every commit — not by tooling.
- `.gitignore` keeps local secrets (`.env`) and regenerable outputs out of Git.
  It is a convenience, **not** a privacy safeguard: ignore rules match file
  names, not file contents.
- API key read from the environment, a git-ignored `.env`, or git-ignored
  Streamlit secrets; never logged or displayed. A key alone never enables live
  calls (`BTW_AI_MODE=live` is also required).
- Milestone 8 does **not** store prompts or responses: they live only in the
  browser session's memory. Any future eval logging (Milestone 9) must go to the
  git-ignored `evals/results/` folder and contain no secrets.
