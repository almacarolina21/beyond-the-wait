# Development Plan — Beyond the Wait: Bronx ED Access Lab

**Status:** Draft v0.5 · **Last updated:** 2026-11-15 · **Current:** Milestone 3 complete; Milestone 4 not started

Milestones 4–10 are **planned**. Items are checked off only when built and tested.

Each milestone is small enough to finish, test, and commit on its own.
**Rule:** a milestone is not done until its tests pass and you can explain
what it does in your own words.

| # | Milestone | Key output | Depends on |
|---|---|---|---|
| 0 | Project foundation | Docs, folder structure, `.gitignore` | — |
| 1 | Dev environment & core contracts | Installable package, pytest running, provenance + config models | 0 |
| 2 | Public reference data | Curated Bronx aggregate metrics with sources | 1 |
| 3 | Synthetic patient generator | Seeded, validated synthetic arrivals | 1 |
| 4 | Baseline ED simulation | SimPy model producing event logs | 3 |
| 5 | Metrics & validation gate | Patient/staff metrics, Little's Law checks | 4 |
| 6 | Scenarios, uncertainty & bottlenecks | Replications, CIs, decomposition | 5 |
| 7 | Streamlit dashboard (no AI) | Interactive app with provenance badges | 2, 6 |
| 8 | Claude interpretation | Evidence packet + structured explanation | 6 |
| 9 | Guardrails & evaluation suite | Automated grounding/safety checks + eval report | 8 |
| 10 | Polish & portfolio packaging | Demo, decision records, case-study write-up | all |

---

## Milestone 0 — Project foundation ✅
**Goal:** Agree on what we're building before writing code.
- [x] `PROJECT_SPEC.md`, `ARCHITECTURE.md`, `DEVELOPMENT_PLAN.md`, `README.md`
- [x] `.gitignore`, `.env.example`, `data/README.md`
- [x] Folder structure and Python package skeleton (empty modules only)
- [x] MIT `LICENSE`
- [x] `git init` and initial local commit
- [ ] Create GitHub repo and push (deferred until reviewed)

**Done when:** a reviewer can read the docs and understand the plan.

## Milestone 1 — Dev environment & core contracts ✅
**Goal:** A working, testable Python package with the shared "vocabulary" the
rest of the project uses.
- [x] `pyproject.toml` (Python ≥ 3.12) with runtime dependencies (pydantic v2,
  pyyaml, simpy, pandas, plotly, streamlit, anthropic, python-dotenv) and dev
  tools (pytest, pytest-cov, ruff). Packages for later milestones are installed
  but not yet used.
- [x] Project virtual environment (`.venv`); `pip install -e ".[dev]"` works.
- [x] `data/provenance.py`: `Provenance` enum (`PUBLIC`/`SYNTHETIC`/`SIMULATED`),
  strict label parsing, and `ProvenanceRecord` (label + source + retrieval date
  required for `PUBLIC`).
- [x] `config/models.py`: Pydantic models for site identity, physician/nurse
  staffing by shift, treatment spaces, hourly arrivals, inpatient capacity,
  and intervention scenarios.
- [x] `config/loader.py`: safe YAML loading (no code execution, duplicate keys
  rejected) with readable error messages.
- [x] `config/scenarios.py`: `apply_scenario()` produces a new, re-validated
  site profile. *(Pulled forward from Milestone 6 so scenario validation could
  be tested now.)*
- [x] Fictional example site: `config/sites/fictional_bronx_community_ed.yaml`
  ("Riverside Commons Community ED") and example scenario
  `config/scenarios/add_evening_physician.yaml`.
- [x] **Tests:** 132 unit tests, 100% line coverage of `src/`. They cover negative
  staffing/beds, invalid arrival rates, missing fields, unsupported provenance
  labels, shift coverage, real-hospital-name guard, scenario application, and
  YAML safety.

**Deferred:** attaching provenance to pandas DataFrames moves to Milestones 2–3,
where the first DataFrames are created. Acuity mix and service-time assumptions
move to Milestone 3, where the synthetic generator needs them.

**You'll learn:** packaging, virtual environments, type-validated config, writing your first tests.

## Milestone 2 — Public reference data ✅
**Goal:** Real, citable Bronx context — kept separate from everything else.
- [x] Verified 5 official sources against publisher catalog APIs (2026-11-15):
  CMS Timely and Effective Care, CMS Footnote Crosswalk, CMS Hospital General
  Information, NYS DOH Health Facility General Information, NYS DOH ED
  Encounters by Facility (aggregate SPARCS counts).
- [x] Measure catalog (`data/ed_measures.py`) separating OP-18 (ED time for
  discharged patients), OP-22 (left without being seen), EDV (volume
  category), and NYS encounter counts. **Door-to-provider and admitted-patient
  boarding time are not available** from any reviewed source; asking for
  them raises an explanatory error.
- [x] Loaders (`cms_loader.py`, `nys_loader.py`) preserving IDs, URLs, measure
  IDs, dates, units, raw scores, and official missing-data reasons. Missing or
  suppressed values are never converted to 0.
- [x] Facility matching (`facility_matching.py`) plus a human-reviewed
  crosswalk (`data/public/reference/bronx_facility_crosswalk.csv`): 5 CMS Bronx
  ED hospitals matched to NYS records. Exclusions and NYS-only sites documented.
- [x] Reproducible pipeline (`public_pipeline.py`: `download` / `register` /
  `build`) with SHA-256 manifests and byte-identical rebuilds.
- [x] Processed outputs committed: `bronx_ed_measures.csv` (470 rows),
  `bronx_facilities.csv` (15 rows), `build_manifest.json`.
- [x] `data/public/SOURCES.md` and `docs/PUBLIC_DATA_METHODOLOGY.md`.
- [x] **Tests:** 262 total (100% line coverage), offline, using byte-for-byte
  excerpts of official files plus fictional edge cases.

**Changes from plan:** `public_reference.py` was split into focused modules.
DataFrame provenance helpers (`tag_dataframe`, `require_provenance`) were
added here instead of Milestone 3. The CMS catalog refused Python's standard
HTTP client (HTTP 403), so a documented manual `register` path was added and
used.

**You'll learn:** data provenance, reproducible data pipelines, pandas cleaning.

## Milestone 3 — Synthetic patient generator ✅
**Goal:** Realistic-but-fake patients driven by documented assumptions.
- [x] Site profile **schema v2**: optional day-of-week multipliers (must
  average 1.0), `patient_mix` (ESI 1–5 distribution; lab, imaging, and
  admission probabilities *by acuity*), and `service_times` (lognormal
  median/p90 per task, for Milestone 4). Overall admission implied by the mix
  must agree with `inpatient.ed_admission_fraction`.
- [x] `data/synthetic_generator.py`: time-varying Poisson arrivals (exact,
  hour by hour; each hour uses its own date's multiplier), conditional
  attributes, NumPy `default_rng` with separate arrival and attribute streams.
- [x] Output: anonymous `SYN-…` IDs, arrival date/time, acuity, lab and
  imaging needs, hypothetical admission flag, and `SYNTHETIC` provenance.
  **No outcomes** (waits, length of stay) are generated.
- [x] `validate_synthetic_patients()` and a CLI demonstration
  (`python -m beyond_the_wait.data.synthetic_generator`).
- [x] `docs/SYNTHETIC_DATA_ASSUMPTIONS.md` (assumption register, all
  unverified illustrative values) and a verification log for the Milestone 2
  CMS definitions (unchanged).
- [x] **Tests:** 337 total, 100% line coverage. Covers reproducibility,
  Poisson count distribution, hourly and weekday profiles, acuity and
  conditional proportions, zero/low/high volume, midnight boundaries, and
  validation. Mutation-checked: deliberately broken generators fail the tests.

**Changes from plan:** service times are configured and validated here but
sampled in Milestone 4; patient attributes and simulation outcomes stay
separate.

**You'll learn:** probability distributions, random seeds, statistical testing.

## Milestone 4 — Baseline ED simulation
**Goal:** Patients flow through a simulated ED.
- SimPy resources: triage, beds, providers, lab, imaging, inpatient beds, coordinators.
- Patient process with timestamps for each stage → event log DataFrame.
- Start simple (triage → bed → provider → depart), then add diagnostics,
  boarding, and coordination one at a time, testing after each.
- **Tests:** every patient that arrives is accounted for; timestamps are
  ordered; a single-server case matches the M/M/1 formula within tolerance.

**You'll learn:** discrete-event simulation, generators/processes in SimPy, queueing basics.

## Milestone 5 — Metrics & validation gate
**Goal:** Trustworthy numbers from event logs.
- Patient metrics: door-to-provider, LOS (admitted vs. discharged), boarding hours, LWBS proxy.
- Staff metrics: utilization by role/shift, time above high-load threshold, patients per staff-hour.
- Validation gate: Little's Law, no negative durations, patient conservation, warm-up check.
- **Tests:** metrics on hand-built tiny event logs with known answers; validation
  catches deliberately broken data.

**You'll learn:** turning operational definitions into code (like building a report spec).

## Milestone 6 — Scenarios, uncertainty & bottleneck decomposition
**Goal:** Answer "what changes if we…?" honestly.
- More scenario YAML files, applied with `apply_scenario()` (built in Milestone 1);
  extend scenario levers as new site-profile fields are added (e.g., lab turnaround).
- Run N replications per scenario; compute means and 95% CIs.
- Baseline vs. scenario differences with CIs.
- LOS decomposition by stage → dominant simulated bottleneck.
- Example scenarios: boarding-heavy, staffing-mismatch, diagnostic-delay, coordination-delay.
- **Tests:** reproducibility across runs; removing a constraint never *increases*
  that stage's wait (directional sanity); each archetype scenario surfaces its
  intended bottleneck.

**You'll learn:** experimental design, confidence intervals, counterfactual reasoning.

## Milestone 7 — Streamlit dashboard (no AI yet)
**Goal:** An administrator can use the tool without code.
- Pages: Public Context · Scenario Builder · Results & Comparison · About/Limitations.
- Plotly charts; provenance badge on every metric and chart.
- Caching so repeated runs are fast.
- **Tests:** UI helper functions unit-tested; manual smoke-test checklist.

**You'll learn:** building interactive apps, state management, UX for non-technical users.

## Milestone 8 — Claude interpretation
**Goal:** Grounded plain-language explanations.
- Evidence packet builder (IDs, values, CIs, provenance, assumptions, limitations).
- System prompt encoding the rules from PROJECT_SPEC §5.
- Claude call via the `anthropic` Python SDK with structured JSON output.
- Handle API errors, missing key, and refusals gracefully.
- Explanation panel in the dashboard.
- **Tests (mocked, offline):** packet contains only validated data; client handles
  errors; parsing of structured output.

**You'll learn:** LLM API integration, prompt design, structured outputs, failure handling.

## Milestone 9 — Guardrails & evaluation suite
**Goal:** Prove the AI behaves — with numbers, not vibes.
- Guardrails: numeric fidelity, citation validity, cause check, directive check, required sections.
- Eval cases in `evals/cases/`: normal scenarios, edge cases (tiny differences,
  wide CIs), and adversarial prompts (asks for real-hospital causes, asks for
  patient treatment advice, asks Claude to compute a new number).
- Scoring report: accuracy, grounding, safety, limitations — tracked over time.
- Human review checklist: a person reviews all adversarial/safety-case outputs
  and a sample of normal ones for causal overreach and unsafe framing.
- Document known gaps: what the automated checks cannot catch (PROJECT_SPEC §5.1).
- **Tests:** guardrails catch seeded bad outputs (hand-written wrong explanations).

**Important:** passing guardrails and evals shows good behavior on our test
cases. It does not by itself establish causal correctness or clinical safety.

**You'll learn:** LLM evaluation design — the core skill for Applied AI roles.

## Milestone 10 — Polish & portfolio packaging
- Decision records in `docs/decisions/`.
- README screenshots / short demo video.
- Case-study write-up: problem, approach, results, limitations, what you'd do next.
- Optional: GitHub Actions to run tests on every push; deploy to Streamlit Community Cloud.
- Interview prep: practice explaining each layer in 2 minutes.

---

## Working Agreements

- **Small commits** with clear messages after each passing step.
- **Test first where practical:** write the expected behavior, then the code.
- **No real data, ever** — check before every commit.
- **Document assumptions** where they're made (YAML `source` fields, docstrings).
- **Explain before moving on:** after each milestone, summarize it in your own words.
