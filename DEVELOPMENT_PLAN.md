# Development Plan — Beyond the Wait: Bronx ED Access Lab

**Status:** Draft v0.10 · **Last updated:** 2026-11-18 · **Current:** Milestone 9 complete offline; live evaluation prepared and dry-run, awaiting approval

Milestone 10 is **planned**. Items are checked off only when built and tested.

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

## Milestone 4 — Baseline ED simulation ✅
**Goal:** Patients flow through a simulated ED.
- [x] Site profile **schema v3**: `operations` section (triage nurses per shift,
  physician concurrency = 2, ESI-1 triage bypass, space eligibility by ESI,
  **simplified inpatient bed-release stream**), each with a source note.
- [x] `simulation/resources.py`: shift-scheduled staff pools, multi-type
  treatment-space allocator, boarding bed queue; non-preemptive priority;
  every assignment logged.
- [x] `simulation/engine.py`: arrive → triage → space → provider → diagnostics
  → discharge or board → leave. Stopping rule with drain period and hard stop;
  unfinished visits kept and labeled; warm-up cohort labeled. Output tagged
  `SIMULATED`.
- [x] `simulation/randomness.py`: four independent seeded streams (arrivals,
  attributes, durations, bed releases) for common random numbers.
- [x] `analysis/metrics.py`: waits, arrival-to-provider, LOS by disposition,
  boarding, utilization, queue lengths, throughput (measured window only).
- [x] `simulation/validity.py`: pre-run load checks and post-run flags
  (saturation, hard stop, unfinished, growing queue, short run, single seed).
- [x] Demo CLI `python -m beyond_the_wait.simulation.demo`; `docs/SIMULATION_MODEL.md`.
- [x] **Tests:** 418 total, 100% line coverage. Deterministic hand-checked
  journeys; priority; shifts and midnight; zero arrivals; saturation;
  admission and discharge paths; boarding; unfinished visits; no
  double-booking; event order; reproducibility; common random numbers; an
  M/G/1 Pollaczek–Khinchine check (only where its assumptions hold);
  sample-path Little's Law. Mutation-checked.

**Changes from plan:** no coordinator resource (no configured inputs); lab and
imaging are delays without capacity limits. Basic metrics, Little's Law, and
the validity flags were pulled forward from Milestone 5. A single-server
M/G/1 check replaced M/M/1, because service times are lognormal.

**You'll learn:** discrete-event simulation, generators/processes in SimPy, queueing basics.

## Milestone 5 — Metrics & validation gate ✅
**Goal:** Trustworthy numbers from event logs.
- [x] `analysis/detailed_metrics.py`: waits, LOS, and boarding **by ESI and
  disposition** (median/p90/p95/max, counts over 4 h and 24 h); censoring-aware
  LOS (completed-only vs. lower bound including unfinished); extreme waits;
  boarding distribution by decision shift; **descriptive** stage decomposition;
  utilization **by shift**, time at ≥ 90% utilization, patients per staffed
  physician-hour, triage-nurse-hour, and nurse-hour; hourly profile.
- [x] LWBS rate explicitly **unavailable** (no departure mechanism yet; not invented).
- [x] `analysis/validation.py`: typed `ValidationReport` (PASS / CAUTION /
  BLOCKED). 7 integrity checks (BLOCKED on failure, including crashes), plus
  interpretation and scope checks (CAUTION). Overload stress tests are flagged,
  not discarded. `release_for_interpretation()` refuses BLOCKED or mismatched
  (fingerprint) reports.
- [x] Safety limitations attached to every report (ESI-1, triage FIFO,
  physician concurrency, bed releases, LWBS, not clinically validated, not a
  staffing recommendation).
- [x] Demo `python -m beyond_the_wait.analysis.demo --seed 42 --gate-examples`;
  `docs/VALIDATION_GATE.md`.
- [x] **Tests:** 469 total, 100% line coverage. Adversarial cases: a falsely
  reassuring median (hand-checked ESI-5 starvation), completed-only LOS bias,
  invalid timestamps, unit error, lost patient, wrong provenance, resource
  double-booking, bed accounting, census tampering, empty cohorts, report
  reuse; overload stress test preserved; hand-checked shift staffing.
  Mutation-checked.
- [ ] LWBS proxy: needs a departure (balking/reneging) model; deferred.

**You'll learn:** turning operational definitions into code (like building a report spec).

## Milestone 6 — Multi-seed scenario comparison & what-if analysis ✅
**Goal:** Answer "what changes if we…?" honestly.
- [x] Lever audit: `nurses` has no simulated effect (flagged); spaces are wired up but
  not binding at baseline; `inpatient_bed_change` had **no effect**, so it was
  **removed** (scenario schema v2 rejects it with guidance).
- [x] Real levers: physicians and **triage nurses** by shift,
  **`bed_release_change_per_day`**, **`diagnostic_turnaround_multiplier`**, plus
  assumption levers (`physician_concurrency_override` 1–3, `arrival_multiplier`),
  allowed only in `sensitivity`/`stress_test` scenarios. `scenario_effects()`
  reports simulated / not simulated / assumption changes. Baseline is immutable;
  scenarios are reversible.
- [x] Bed releases drawn by **thinning**, so paired scenarios share every release
  (common random numbers for the bed lever).
- [x] `analysis/statistics.py`: paired t CI (no SciPy; tested against published
  tables), separate prediction interval, skewness.
- [x] `analysis/experiments.py`: paired replications (parallel), every replication
  through the Milestone 5 gate; BLOCKED → comparison BLOCKED with no numbers;
  CAUTIONs carried forward; checks for seed coverage, pairing, missing data,
  variability, skew, censoring, overload regime, unsupported conclusions;
  congestion-shift detection; binding-constraint evidence (relief test, not
  largest stage); sensitivity grid and stability verdicts.
- [x] 12 fictional scenario files: 7 interventions, 4 archetypes (boarding-heavy,
  physician mismatch, triage mismatch, diagnostic delay), 1 sensitivity.
- [x] Demo `python -m beyond_the_wait.analysis.whatif_demo` (30 paired seeds; 20 for
  sensitivity); CSV export for Milestone 7; `docs/WHAT_IF_ANALYSIS.md`.
- [x] **Tests:** 568 total, 100% line coverage. Mutation-checked (BLOCKED handling,
  pairing, bed-release coupling).

**Changes from plan:**
- The planned invariant "removing a constraint never increases a stage's wait" was
  **deliberately dropped**: it isn't valid for interacting queues. Our own results
  show extra triage capacity *increasing* physician waits, and tests now assert
  that relocation instead.
- "Coordination delay" was replaced by a triage-mismatch archetype, because no
  coordination resource is modeled.
- Seed-42 boarding numbers in Milestones 4–5 docs were updated for the new
  bed-release sampling.

**You'll learn:** experimental design, confidence intervals, counterfactual reasoning.

## Milestone 7 — Streamlit dashboard (no AI yet) ✅
**Goal:** An administrator can use the tool without code.
- [x] Five pages: Overview · Bronx Public Data · Fictional Patient Flow · What-If Lab ·
  Sensitivity, Validation & Methods (`app/streamlit_app.py`, `app/views/`).
- [x] Testable support package `beyond_the_wait.dashboard` (data, charts, theme, ui);
  pages hold no calculation logic. `boarding_values()` extracted for reuse.
- [x] Fingerprinted **results bundle** (`analysis/results_bundle.py`) written by
  `whatif_demo`; verified on load; BLOCKED comparisons excluded and listed.
- [x] Provenance badge in every chart title; PUBLIC never mixed with simulated data;
  unavailable and suppressed values as text; neutral effect colors (no traffic lights);
  ESI-1 and "not a recommendation" callouts.
- [x] Caching: public data and bundle by file timestamp; one live run by (seed, days),
  capped at 14 days; 30-seed experiments never run by the app.
- [x] Security: localhost only, no uploads, no telemetry, no API key.
- [x] **Tests:** 649 total (after 7.1), 100% line coverage, including headless `AppTest` page tests
  (missing, tampered, and BLOCKED bundles; selectors). Live server health check passed.
- [x] **7.1 Visual design and UX:** chart layout rules enforced in code and tests
  (no clipped labels, fixed title/legend bands); Overview with generated findings and a
  path into the What-If Lab; What-If Lab reordered (chart first, details in expanders);
  model-scoped wording ("Lower within the fictional model"); duplicate NYS facility names
  shown separately; compact tables. All pages screenshot in headless Chrome at 1024 px (most also at 1280/1440 px).
- [ ] Not yet checked: phone widths, zoom, hover/expander states, screen readers
  (`docs/DASHBOARD.md` §8). The palette validator was not run (Node.js unavailable).

**You'll learn:** building interactive apps, state management, UX for non-technical users.

## Milestone 8 — Claude interpretation ✅ (mock-first)
**Goal:** Grounded plain-language explanations.

Done (2026-11-17) — see [docs/AI_INTERPRETATION.md](docs/AI_INTERPRETATION.md):
- [x] Typed evidence packet from the **verified results bundle** (multi-seed comparisons, every
  replication gated in Milestone 6). This replaces the `ReleasedResults` input planned below,
  which covers single runs only. BLOCKED, unverified, or unknown inputs raise `EvidenceRefused`.
- [x] Stable fact IDs; CAUTION reasons, sensitivity verdicts, and all 7 limitations carried.
- [x] Prompt rules + structured JSON output (`output_config.format`, SDK `transform_schema`).
- [x] Value tokens: Claude writes `{{M:x.change}}`; Python renders every number.
- [x] Deterministic verification (10 checks; 11 since Milestone 9), whole-response rejection,
  readable fallbacks.
- [x] Mock provider (default) and live provider (opt-in: `BTW_AI_MODE=live` + key), timeouts,
  bounded retries, size limits, refusal/truncation/error handling; nothing logged.
- [x] What-If Lab panel: explicit button, no calls on load or widget change (tested), MOCK/LIVE
  label and model name, evidence and check expanders. Readable CAUTION reasons; softened
  common-random-numbers wording.
- [x] Tests: adversarial verifier cases + red-team regressions, fake-SDK provider tests,
  AppTests. No real API request has been made.
- [ ] Live activation: needs your approval, an API key, and one supervised test call.

Original plan:
- Evidence packet builder (IDs, values, CIs, provenance, assumptions, limitations).
  It must accept **only** `ReleasedResults` from `release_for_interpretation()`
  (Milestone 5), so BLOCKED runs can never be packaged, and must carry the
  report's CAUTION reasons and safety limitations.
- System prompt encoding the rules from PROJECT_SPEC §5.
- Claude call via the `anthropic` Python SDK with structured JSON output.
- Handle API errors, missing key, and refusals gracefully.
- Explanation panel in the dashboard (What-If Lab), fed only by `ReleasedResults` and the
  verified results bundle.
- **Tests (mocked, offline):** packet contains only validated data; client handles
  errors; parsing of structured output.

**You'll learn:** LLM API integration, prompt design, structured outputs, failure handling.

## Milestone 9 — Guardrails & evaluation suite ✅ (offline)
**Goal:** Prove the AI behaves — with numbers, not vibes.

Done (2026-11-17) — see [docs/AI_EVALUATION.md](docs/AI_EVALUATION.md):
- [x] Threat model: how valid JSON with valid IDs can still mislead; what code can verify vs.
  what needs semantic or human judgment.
- [x] Dataset: 180 candidate responses (55 ACCEPT / 23 HUMAN_REVIEW / 102 REJECT) + 35
  sanitizer cases, 18 risk categories, 5 frozen packets + 1 tampered packet; dev split and three
  held-out rounds, each SHA-256-locked with its first blind run recorded.
- [x] Offline runner: confusion matrices, detection / false-accept / false-reject / review rates
  with denominators, check families, per-category coverage, claim-label agreement, issue lists.
- [x] Faithfulness layer: claim classes, review flags, three-way verdict
  (ACCEPT / HUMAN_REVIEW / REJECT), Python-rendered key result, controlled-vocabulary gate.
- [x] Adversarial rounds documented honestly: blind detection 19/31 → 11/20 → 12/13 (after the
  vocabulary gate); every miss fixed and kept as a case or test.
- [x] Human review rubric ([docs/AI_REVIEW_RUBRIC.md](docs/AI_REVIEW_RUBRIC.md)); needs-review
  display in the What-If panel; AI Explanation Evaluation page.
- [x] Supervised live-evaluation workflow prepared offline (`beyond_the_wait.live_eval`: fixed 10-call
  plan, approval code + TTY + typed confirmation, no retries, hard cap, blind reviewer CLI, MOCK dry
  run; docs/LIVE_EVALUATION.md).
- [ ] Live evaluation of real Claude responses (needs explicit approval; not run).
- [ ] Held-out cases written by independent reviewers.

Original plan:
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
