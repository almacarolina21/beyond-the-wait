# Architecture — Beyond the Wait: Bronx ED Access Lab

**Status:** Draft v0.2 · **Last updated:** 2026-11-15

> **Planned design.** This document describes the architecture we intend to
> build. As of Milestone 0, the folders exist but **none of the modules below
> are implemented**. Module names marked "planned" may change as we build.
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

| Module (planned) | Responsibility |
|---|---|
| `provenance.py` | `Provenance` enum (`PUBLIC`, `SYNTHETIC`, `SIMULATED`) and a helper that attaches source metadata to every DataFrame/metric. |
| `public_reference.py` | Loads curated public aggregate metrics (e.g., CMS Care Compare ED timeliness measures, NYS DOH data) from `data/public/processed/`, with source, definition, and period. |
| `synthetic_generator.py` | Creates synthetic patients: arrival time (time-varying Poisson process), acuity, diagnostic needs, admission flag. Driven by a site profile + random seed. |

**Why it's designed this way:** In healthcare IT you'd never mix a test
patient into production reporting. Same idea here — the provenance tag
travels with the data so the UI and Claude can't confuse them.

### 2.2 Configuration layer — `src/beyond_the_wait/config/` + `config/`

**Purpose:** Keep *what we model* separate from *how we model it*.

- **Site profile** (`config/sites/*.yaml`): beds, staff by shift, arrival
  pattern, acuity mix, service-time assumptions, admission rate. Each
  assumption carries a `source` note ("assumption", "literature", or a public
  dataset reference).
- **Scenario** (`config/scenarios/*.yaml`): a named set of changes applied on
  top of a site profile (e.g., "+1 evening provider", "lab turnaround −20%").
- Validated with **Pydantic** models so a typo or impossible value (negative
  beds) fails loudly before any simulation runs.

**Why:** This is the reusability requirement. Adapting to a new health system
means writing a new YAML file, not editing Python. It mirrors how Epic build
separates configuration records from application code.

### 2.3 Simulation layer — `src/beyond_the_wait/simulation/`

**Purpose:** The *only* place ED operations are numerically modeled.

- **SimPy** discrete-event simulation. Resources: triage nurses, ED beds,
  providers, lab, imaging, inpatient beds (for boarding), coordinators.
- Patient pathway:
  `arrive → triage → wait for bed → provider eval → diagnostics (optional) → disposition decision → [admit: board until inpatient bed] / [discharge: coordination delay] → depart`
- Each patient produces an **event log row** with timestamps for every stage —
  the same shape as an ED tracking-board audit trail.
- **Replications:** each scenario runs N times (default 30) with different
  seeds; a warm-up period is discarded.

**Why discrete-event simulation?** Queues interact non-linearly — adding a
provider does nothing if every bed is full of boarders. Spreadsheet averages
miss this; a DES captures it. SimPy is pure Python, well documented, and
easy to test.

### 2.4 Analysis layer — `src/beyond_the_wait/analysis/`

**Purpose:** Turn raw event logs into trustworthy metrics.

| Module (planned) | Responsibility |
|---|---|
| `metrics.py` | Patient metrics (door-to-provider, LOS by disposition, boarding hours, LWBS proxy) and staff metrics (utilization, time above load threshold, patients per staff-hour). |
| `comparison.py` | Scenario vs. baseline differences with 95% confidence intervals across replications. |
| `decomposition.py` | Breaks average LOS into time spent in each stage → identifies the dominant *simulated* bottleneck. |
| `validation.py` | **Validation gate**: sanity checks (Little's Law, no negative durations, conservation of patients, CI width acceptable, warm-up adequate). Results that fail are *not* sent to Claude. |

### 2.5 Interpretation layer — `src/beyond_the_wait/interpretation/`

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

### 2.6 Guardrails — `src/beyond_the_wait/interpretation/guardrails.py` (planned)

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

### 2.7 Presentation layer — `app/`

**Streamlit** multipage app. Plotly charts. Every number and chart carries a
provenance badge. The explanation panel shows Claude's output with its cited
evidence IDs linked to the underlying metrics, plus a fixed banner:
*"Decision support for human review. Based on simulated data."*

**Why Streamlit?** It turns Python scripts into an interactive web app with
no front-end code, letting the project focus on the analytics and AI design.

### 2.8 Evaluation layer — `tests/` and `evals/`

| Layer | Tool | What it proves |
|---|---|---|
| Unit tests | pytest | Generator distributions, metric formulas, config validation |
| Integration tests | pytest | Full pipeline: config → sim → metrics → evidence packet |
| Analytical validation | pytest | Simulation matches queueing theory on simple cases |
| LLM evals | `evals/` harness | Claude outputs scored for accuracy, grounding, safety, limitations on a fixed case set (normal, edge, and adversarial) |
| Human review | Documented review checklist | A person checks adversarial and safety-case outputs, and a sample of others, for causal overreach and unsafe framing that automated scoring can miss |

Unit tests use a **mocked** Claude client so they are free, fast, and offline.
LLM evals run on demand because they cost money.

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
├── app/                       # Streamlit UI (Milestone 7)
├── config/
│   ├── sites/                 # Site profiles (YAML)
│   └── scenarios/             # Intervention scenarios (YAML)
├── data/
│   ├── public/raw/            # Downloaded public files (git-ignored)
│   ├── public/processed/      # Curated aggregate reference tables
│   └── synthetic/             # Generated synthetic patients (git-ignored)
├── docs/decisions/            # Architecture decision records
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
│   ├── unit/
│   └── integration/
├── .env.example               # Template for the API key (real .env is git-ignored)
├── LICENSE                    # MIT
├── PROJECT_SPEC.md · ARCHITECTURE.md · DEVELOPMENT_PLAN.md · README.md
```

## 6. Security & Privacy (planned practices)

- **Data scope is the privacy control.** By project rule, the only data in this
  system is public aggregate data and synthetic data we generate. No real
  patient-level data, PHI, or employer information is ever brought in, so none
  can be sent to the Claude API. This is enforced by design and by reviewing
  every commit — not by tooling.
- `.gitignore` keeps local secrets (`.env`) and regenerable outputs out of Git.
  It is a convenience, **not** a privacy safeguard: ignore rules match file
  names, not file contents.
- API key read from environment / `.env`; never logged or displayed.
- Logs of Claude calls (evidence packets and responses, used for evaluation)
  are written to a git-ignored folder.
