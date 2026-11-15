# Beyond the Wait — Bronx ED Access Lab

> A Claude-powered decision-support lab for investigating emergency department
> patient-flow bottlenecks, testing operational interventions, and
> understanding their effects on patients **and** healthcare workers.

**Python calculates. Claude explains. Humans decide.**

![status](https://img.shields.io/badge/status-milestone%203%20of%2010-lightgrey)
![python](https://img.shields.io/badge/python-3.12-blue)

---

## Why this project exists

Two emergency departments can report the same long wait times for very
different reasons: admitted patients **boarding** in ED beds, **staffing**
that doesn't match arrival patterns, **diagnostic** delays, or
**care-coordination** barriers. Choosing the wrong fix costs money and
burns out staff.

This lab lets healthcare administrators safely explore *"what if?"* questions
with a transparent simulation, then get a plain-language explanation that is
grounded strictly in the validated results.

## What it will do (planned)

> 🚧 These features are **planned and not yet implemented.** So far the
> repository contains the planning documents and the configuration foundation
> described under [Project status](#project-status).

- 📊 **Public context** — official, aggregate Bronx hospital ED metrics, labeled `PUBLIC` with sources.
- 🧪 **Synthetic patients** — realistic-but-fake arrivals generated from documented assumptions, labeled `SYNTHETIC`.
- ⚙️ **ED flow simulation** — a SimPy discrete-event model of triage, beds, providers, diagnostics, boarding, and discharge coordination, labeled `SIMULATED`.
- 🔀 **Scenario comparison** — test interventions across many replications with 95% confidence intervals.
- 🔍 **Bottleneck decomposition** — see where simulated time is actually spent.
- 👩‍⚕️ **Patient *and* staff impact** — length of stay and boarding alongside utilization and workload peaks.
- 🤖 **Grounded AI explanations** — Claude interprets validated results, cites evidence, states uncertainty and limitations, and never invents causes.
- ✅ **Evaluations** — automated tests for analytical accuracy plus an eval suite for grounding, safety, and limitations.

## Responsible AI design

| Commitment | Planned approach |
|---|---|
| Real vs. synthetic data never confused | Provenance tag on every dataset, metric, and chart |
| Accurate numbers | All math in tested Python; Claude never calculates |
| No invented causes | Evidence-ID citations + structured output + automated guardrails + adversarial evals + human review |
| Human oversight | Decision support only; no individual-patient guidance |
| No private data | Only public aggregate data and synthetic data — no PHI, no employer data |

**An honest limit:** automated checks can confirm that Claude's numbers and
evidence citations match the validated results. They cannot, on their own,
prove that an interpretation is causally correct or clinically safe. That
requires evaluation and review by people with the right expertise. See
[PROJECT_SPEC.md §5.1](PROJECT_SPEC.md#51-what-automated-checks-can-and-cannot-do).

## Architecture at a glance

```
Site profile + Scenario (YAML, validated)
        │
        ▼
Synthetic patients ──► SimPy simulation (N runs) ──► Metrics + CIs ──► Validation gate
                                                                          │
                                         Evidence packet (validated facts)◄┘
                                                    │
                                                    ▼
                                    Claude (structured, cited explanation)
                                                    │
                                                    ▼
                                    Guardrail checks ──► Streamlit dashboard
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for details.

## Tech stack

| Purpose | Tool |
|---|---|
| Simulation | SimPy |
| Data & analysis | pandas |
| Visualization | Plotly |
| Dashboard | Streamlit |
| AI interpretation | Anthropic Claude API (`anthropic` Python SDK) |
| Config validation | Pydantic + YAML |
| Testing | pytest |

## Project status

✅ **Milestones 1–3 complete: configuration foundation, public reference data, and synthetic patient generator.**

*Milestone 1: configuration*
- **Provenance labels** (`PUBLIC` / `SYNTHETIC` / `SIMULATED`) with strict
  validation, on config objects and on every table row.
- **Validated configuration models** (Pydantic v2) for a fictional ED: staffing
  by shift, treatment spaces, hourly arrivals, inpatient capacity, and
  intervention scenarios. Safe YAML loading and `apply_scenario()`.
- **One fictional example site**, "Riverside Commons Community ED". It's
  labeled fictional and doesn't represent any real hospital.

*Milestone 2: public reference data*
- **Reproducible pipeline** from 5 official CMS and NYS DOH datasets to Bronx
  ED reference tables in `data/public/processed/`, with SHA-256 fingerprints
  and byte-identical rebuilds.
- **Measures kept distinct:** OP-18 (median ED time for *discharged*
  patients), OP-22 (left without being seen), ED volume, and NYS encounter
  counts. Door-to-provider and boarding time are **not publicly available**,
  and the code says so instead of substituting another measure.
- **Five Bronx hospitals matched** across CMS and NYS records by address, with
  a human-reviewed crosswalk: BronxCare, Montefiore, Lincoln, Jacobi, and
  St. Barnabas (SBH).
- **Missing and suppressed values preserved** with official explanations, never zeros.
- See [docs/PUBLIC_DATA_METHODOLOGY.md](docs/PUBLIC_DATA_METHODOLOGY.md) for
  what these measures mean, and why they **can't identify the cause of a
  hospital's delays**.

*Milestone 3: synthetic patient generator*
- **Fictional patients** for the fictional Riverside Commons ED: time-varying
  Poisson arrivals (hourly and weekday patterns), ESI-like acuity, and lab,
  imaging, and hypothetical-admission flags drawn **conditional on acuity**.
- **Reproducible:** an explicit seed gives identical patients every time.
- **Attributes only:** no waits or lengths of stay. Those come from the
  simulation (Milestone 4).
- Every row is labeled `SYNTHETIC`. Assumptions are documented as unverified
  and illustrative in
  [docs/SYNTHETIC_DATA_ASSUMPTIONS.md](docs/SYNTHETIC_DATA_ASSUMPTIONS.md).
  They are **not calibrated to any real hospital**.

**337 unit tests**, 100% line coverage, all offline.

**Not built yet:** simulation, dashboard, and Claude integration. See
[DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md).

## Getting started

Requires Python 3.12+.

```bash
git clone <repo-url>   # repository URL will be added once published
cd beyond-the-wait
python3 -m venv .venv                 # create an isolated environment
source .venv/bin/activate             # activate it (Windows: .venv\Scripts\activate)
pip install -e ".[dev]"               # install the project + dev tools
pytest                                # run the test suite
```

Try the configuration layer:

```bash
python -c "
from beyond_the_wait.config import load_site_profile, load_scenario, apply_scenario
site = load_site_profile('config/sites/fictional_bronx_community_ed.yaml')
print(site.site.display_name, '-', site.arrivals.expected_daily_arrivals, 'expected arrivals/day')
new = apply_scenario(site, load_scenario('config/scenarios/add_evening_physician.yaml'))
print('Evening physicians:', site.staffing.physicians_on_duty['evening'], '->', new.staffing.physicians_on_duty['evening'])
"
```

Generate one day of fictional ED patients:

```bash
python -m beyond_the_wait.data.synthetic_generator --seed 42 --date 2026-11-16
# options: --days N  --sample K  --out data/synthetic/day.csv
```

Rebuild the public reference tables (offline, from downloaded files):

```bash
python -m beyond_the_wait.data.public_pipeline download   # fetch official files (internet)
python -m beyond_the_wait.data.public_pipeline build      # validate + build (offline)
```

If `download` is refused by a publisher's server, it prints manual-download
steps; see [data/public/SOURCES.md](data/public/SOURCES.md).

An Anthropic API key is not needed until Milestone 8. When it is, you'll copy
`.env.example` to `.env` and add the key there.

## Repository layout

```
app/               Streamlit dashboard
config/            Site profiles and intervention scenarios (YAML)
data/              Public (aggregate) and synthetic data — see data/README.md
docs/decisions/    Architecture decision records
evals/             LLM evaluation cases and results
src/beyond_the_wait/
  config/          Validated configuration models
  data/            Provenance, public references, synthetic generator
  simulation/      SimPy ED model
  analysis/        Metrics, comparisons, decomposition, validation
  interpretation/  Evidence packets, Claude client, guardrails
  evaluation/      Eval harness and scorers
tests/             Unit and integration tests
```

Working code so far is in `src/beyond_the_wait/config/`, `src/beyond_the_wait/data/`,
`config/`, `data/public/`, and `tests/`. The other folders are placeholders
until their milestone is built.

## Documentation

- [PROJECT_SPEC.md](PROJECT_SPEC.md) — problem, users, features, scope, success criteria
- [ARCHITECTURE.md](ARCHITECTURE.md) — components and data flow
- [DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md) — milestones
- [data/README.md](data/README.md) — data provenance rules
- [data/public/SOURCES.md](data/public/SOURCES.md) — official public sources and manual-download steps
- [docs/SYNTHETIC_DATA_ASSUMPTIONS.md](docs/SYNTHETIC_DATA_ASSUMPTIONS.md) — every synthetic assumption, and how synthetic data differs from public data
- [docs/PUBLIC_DATA_METHODOLOGY.md](docs/PUBLIC_DATA_METHODOLOGY.md) — what the public ED measures do and don't mean

## Disclaimer

This is an independent, open-source portfolio project. It is **not** a
clinical tool and is not affiliated with any hospital, health system, or
employer. Simulated results are illustrative and do not describe the actual
performance or causes of delay at any real institution. All outputs are
intended for human review.

## License

Released under the [MIT License](LICENSE).
