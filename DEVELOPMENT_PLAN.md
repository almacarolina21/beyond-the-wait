# Development Plan — Beyond the Wait: Bronx ED Access Lab

**Status:** Draft v0.2 · **Last updated:** 2026-11-15 · **Current:** Milestone 0 complete; Milestone 1 not started

Milestones 1–10 are **planned**. Items are checked off only when built and tested.

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

## Milestone 1 — Dev environment & core contracts
**Goal:** A working, testable Python package with the shared "vocabulary" the
rest of the project uses.
- `pyproject.toml` with dependencies (simpy, pandas, plotly, streamlit,
  pydantic, pyyaml, anthropic, python-dotenv) and dev tools (pytest, pytest-cov, ruff).
- Virtual environment instructions; `pip install -e ".[dev]"` works.
- `data/provenance.py`: `Provenance` enum + metadata helper.
- `config/models.py`: Pydantic `SiteProfile` and `Scenario` models.
- One example site profile: `config/sites/bronx_synthetic_example.yaml`
  (clearly marked as illustrative assumptions).
- **Tests:** provenance tags attach and persist; invalid configs (negative beds,
  probabilities > 1) are rejected; example YAML loads.

**You'll learn:** packaging, virtual environments, type-validated config, writing your first tests.

## Milestone 2 — Public reference data
**Goal:** Real, citable Bronx context — kept separate from everything else.
- Identify and verify candidate public sources (e.g., CMS Care Compare ED
  timeliness measures; NYS Department of Health hospital data). Confirm
  measure definitions, current availability, and reporting periods before use.
- Script to download raw files into `data/public/raw/` (git-ignored).
- Curate a small processed table for Bronx hospitals → `data/public/processed/`.
- `data/public/SOURCES.md` with URL, publisher, definition, period, retrieval date.
- `public_reference.py` loader returning DataFrames tagged `PUBLIC`.
- **Tests:** schema checks, no patient-level columns, provenance = `PUBLIC`.

**You'll learn:** data provenance, reproducible data pipelines, pandas cleaning.

## Milestone 3 — Synthetic patient generator
**Goal:** Realistic-but-fake patients driven by documented assumptions.
- Time-varying arrivals (hour-of-day × day-of-week) via non-homogeneous Poisson process.
- Patient attributes: acuity (1–5), needs lab / imaging, admission probability by acuity.
- Seeded randomness → identical output for identical seed.
- **Tests:** reproducibility; acuity mix within tolerance of config; arrival
  counts match expected rate over many days; all rows tagged `SYNTHETIC`.

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
- Scenario YAML files applied on top of a site profile.
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
