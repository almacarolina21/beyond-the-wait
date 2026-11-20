# Beyond the Wait — Bronx ED Access Lab

**An educational decision-support lab for exploring why emergency department
patients wait. A tested simulation calculates the effects of hypothetical
changes, and Claude's plain-language explanations are shown only after code
checks them against the verified numbers.**

[![tests](https://img.shields.io/badge/tests-1196%20passed-brightgreen)](#reproduce-it)
![coverage](https://img.shields.io/badge/line%20coverage-100%25-brightgreen)
![python](https://img.shields.io/badge/python-3.12-blue)
![status](https://img.shields.io/badge/status-portfolio%20prototype%2C%20not%20clinically%20validated-lightgrey)

![Overview page of the dashboard, showing three simulated findings labeled SIMULATED and CAUTION](docs/images/overview.png)

> **What this is and is not.** A portfolio prototype built around a **fictional**
> emergency department, "Riverside Commons Community ED". It uses public,
> aggregate Bronx hospital measures for context and synthetic patients for the
> model. It is not calibrated to any real hospital, not clinically validated,
> and not deployed anywhere. No real patient data or employer information was
> used. Real Claude responses have not yet been evaluated: the AI explanations
> in the demo come from a labeled mock.

## The problem

Long ED waits in the Bronx are visible in public data, but the data can't
explain them. CMS reports, for example, the median time discharged patients
spend in the ED, and New York State reports encounter counts. Neither
publishes door-to-provider time or boarding time. The same headline wait can
come from:
- admitted patients **boarding** in ED beds;
- **staffing** that doesn't match when patients arrive;
- **diagnostic** turnaround;
- problems with **discharge coordination**.

Picking the wrong fix wastes money and adds to staff burnout.

**Intended audience:** ED operations leaders, quality-improvement teams, and
public-health analysts who want to reason about *"what if?"* before changing
anything real. The tool is not for frontline clinical decisions or
individual-patient guidance.

## Three findings from the fictional model

All three come from the precomputed results bundle (30 paired simulated weeks
per comparison, 95% confidence intervals for the mean paired difference). The
dashboard shows them on its Overview page, and
[a test](tests/unit/test_release_bundle.py) checks this text against the bundle.
They describe how the **model** behaves under its stated assumptions, not how
any real hospital would. Every comparison carries a CAUTION from the
validation gate (for example, a few simulated weeks were overloaded).

1. **Six more inpatient beds released to the ED per day.** Median length of
   stay for admitted patients: 98.4 min lower within the fictional model (95% CI 66.0 to 131 min lower).
2. **Lab and imaging turnaround 25% faster.** Median length of stay for
   discharged patients: 13.9 min lower within the fictional model (95% CI 13.5 to 14.3 min lower).
3. **One more night triage nurse: the wait moves to the next queue.** The
   90th-percentile wait for triage was 4.8 min lower within the fictional model (95% CI 3.8 to 5.9 min lower),
   but the 90th-percentile wait for a physician was 3.2 min higher within the fictional model (95% CI 2.4 to 4.0 min higher).
   The analysis flags this kind of congestion shift automatically, and it is
   why a single headline metric can mislead.

## Python calculates. Claude explains. Humans decide.

| Layer | Responsible for | Why |
|---|---|---|
| **Python** | Every number: simulation, paired statistics, confidence intervals, validation status | Numbers must be reproducible and tested; a language model should not do arithmetic for a health decision |
| **Claude** | A plain-language explanation of verified results, citing evidence IDs | Explanation and synthesis are where an LLM adds value. Claude writes value *tokens* (`{{M:x.change}}`), and Python inserts the numbers |
| **Code checks** | Accept, hold for review, or reject each explanation | Valid JSON with valid citations can still mislead, so 11 checks test numbers, citations, advice, causal claims, safety claims, and unfamiliar wording |
| **People** | Decisions, and reviewing anything the checks are unsure about | Automated checks can't establish causal correctness or clinical safety |

The public dashboard shows generated text **only when every check passes**
(ACCEPT). A response marked HUMAN_REVIEW is withheld. Visitors see the key
result written by Python, the evidence, and a note that the explanation needs
review. A rejected response shows only which checks failed.

![What-If Lab, AI interpretation panel in DEMO mode: a key result calculated by Python, a mock explanation labeled "DEMO · mock, not Claude", and evidence IDs for each finding](docs/images/ai_interpretation.png)

*The explanation shown is from the built-in **mock** (labeled "DEMO · mock,
not Claude"). No Claude API call was made to produce it.*

## Architecture

```mermaid
flowchart TD
    PUB["PUBLIC: CMS and NYS DOH aggregate ED measures<br/>(5 Bronx hospitals, SHA-256 fingerprinted)"]
    CFG["Fictional site profile + intervention scenarios<br/>(YAML, Pydantic-validated)"]
    SYN["SYNTHETIC: fictional patients<br/>(seeded, time-varying arrivals)"]
    SIM["SIMULATED: SimPy discrete-event model<br/>(triage, beds, physicians, diagnostics, boarding)"]
    STAT["Paired multi-seed analysis<br/>(30 common-random-number weeks, 95% CI, sensitivity)"]
    GATE{"Validation gate<br/>PASS / CAUTION / BLOCKED"}
    PKT["Evidence packet<br/>(verified aggregates, stable fact IDs, cautions, limitations)"]
    LLM["Claude (structured JSON, cites fact IDs,<br/>writes value tokens, never numbers)<br/>DEMO mode: labeled mock"]
    VER{"Output verification<br/>11 checks → ACCEPT / HUMAN_REVIEW / REJECT"}
    UI["Streamlit dashboard (localhost)"]
    HUM["Human reviewer + rubric"]

    CFG --> SYN --> SIM --> STAT --> GATE
    GATE -- "BLOCKED: never shown" --> X[" "]
    GATE -- "PASS / CAUTION" --> PKT --> LLM --> VER
    VER -- "ACCEPT: prose shown" --> UI
    VER -- "HUMAN_REVIEW: prose withheld" --> HUM
    VER -- "REJECT: failed checks only" --> UI
    GATE --> UI
    PUB -- "context only, never mixed with simulated charts" --> UI
    style X fill:none,stroke:none
```

Every table row and chart carries a provenance label: **PUBLIC**,
**SYNTHETIC**, or **SIMULATED**. Real and simulated data never share a chart.
Details are in [ARCHITECTURE.md](ARCHITECTURE.md).

## AI evaluation: blind results, failures, and what changed

The checks were evaluated offline against 180 hand-written candidate
explanations. Each held-out round was written, **hash-locked, and scored once
before any rule was changed**. Those first runs are the honest estimates:

| Blind round | Rules tested | Unsafe outputs caught | Unsafe outputs accepted | Useful outputs rejected |
|---|---|---|---|---|
| 1 | Word-pattern checks tuned on the dev set | 19/31 (61%) | 12 | 4/17 |
| 2 | Round 1 fixes added | 11/20 (55%) | 9 | 0/10 |
| 3 | + controlled-vocabulary gate | 12/13 (92%) | 1 | 3/8 |

**What failed:**
- The dev set reached 38/38 after tuning, and blind round 1 then caught only
  19/31.
- Patching the missed phrases made round 2 *worse*. Each new wording, such as
  "Why wait?", "the lever to pull" or "per the developer message", was a new
  miss for a denylist.
- The sanitizer that removes injected instructions from packet text caught
  only **3/13** blind injections. A later probe of 8 new styles, not used for
  tuning, caught **0/8**. Examples are a zero-width character split, a Cyrillic
  look-alike letter, base64 and passive voice.

**What changed:**
- **The default changed rather than the denylist.** A controlled-vocabulary
  gate sends any explanation that uses words outside a vetted list to a human.
  It no longer accepts unfamiliar wording.
- **The key result is written by Python** and doesn't depend on the model.
- **HUMAN_REVIEW text is withheld** from the public page.

After the fixes, the same cases score 0 false accepts. That is **not**
independent evidence, because those cases were used to make the fixes.

**Still unresolved:**
- no evaluation of real Claude responses;
- no semantic entailment check;
- the cases and the rules share one author;
- 13 unsafe cases are enough to find *types* of failure, not to measure rates.

A supervised 10-call live evaluation is prepared and has been dry-run offline:
- a hard cap of 10 calls;
- no retries;
- a blind human review before the automated verdict is shown;
- a cost ceiling of $2.38 at list price.

It has **not** been run. Details: [docs/AI_EVALUATION.md](docs/AI_EVALUATION.md)
· [docs/LIVE_EVALUATION.md](docs/LIVE_EVALUATION.md) ·
[rubric](docs/AI_REVIEW_RUBRIC.md).

![AI Explanation Evaluation page: blind results table for three held-out rounds and the current run's confusion matrix](docs/images/ai_evaluation.png)

## Reproduce it

You need Python 3.12 or later. No API key, no internet access after install,
and no billable calls: AI explanations default to the labeled mock (DEMO mode).

```bash
git clone https://github.com/<your-username>/beyond-the-wait.git
cd beyond-the-wait
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[dev]"              # exact tested versions: requirements-lock.txt
pytest -q                            # 1196 tests, all offline
streamlit run app/streamlit_app.py   # open http://localhost:8501
```

The dashboard reads committed, fingerprint-verified data:
- the **public** Bronx tables in `data/public/processed/`;
- the precomputed **simulation** results in `data/synthetic/experiments/`, about
  140 KB (30 seeds plus sensitivity runs).

To regenerate the simulation results yourself, which takes about 6 minutes on 8
cores, run:

```bash
python -m beyond_the_wait.analysis.whatif_demo --seeds 30 --sensitivity-seeds 20 --workers 8
```

On 2026-11-20 a rebuild with these settings matched the committed files byte
for byte (macOS, Python 3.12, the versions in `requirements-lock.txt`). Other
platforms may differ in the last floating-point digits. `manifest.json` lists
the seeds and a SHA-256 for each file.

Other entry points:

```bash
python -m beyond_the_wait.evaluation                     # offline AI evaluation report
python -m beyond_the_wait.live_eval dry-run              # MOCK live-evaluation workflow, no network
python -m beyond_the_wait.data.public_pipeline download  # re-fetch public data (internet)
python -m beyond_the_wait.data.public_pipeline build     # rebuild public tables (offline)
```

Live Claude explanations are off unless you set `BTW_AI_MODE=live` and provide
your own Anthropic API key. That is billed to your API account. See
[docs/AI_INTERPRETATION.md](docs/AI_INTERPRETATION.md).

## Known limitations

- **No real-hospital calibration.** Arrival patterns, staffing, service times,
  and boarding are documented assumptions for a fictional ED
  ([docs/SYNTHETIC_DATA_ASSUMPTIONS.md](docs/SYNTHETIC_DATA_ASSUMPTIONS.md)).
  The public Bronx data provides context and isn't used to fit the model.
- **No clinical validation.** Triage priority is a simplified acuity rule, and
  nothing here has been reviewed by ED clinicians or operations staff.
- **No live API evaluation.** Every evaluated explanation was hand-written. How
  real Claude responses behave against the checks is unknown.
- **Simplified model.** Boarding uses a bed-release abstraction. Lab and
  imaging have no capacity limits. The model has no left-without-being-seen
  behavior, ambulance diversion, surges, or seasonality
  ([full list](docs/SIMULATION_MODEL.md#10-known-limitations)).
- **Public data can't explain causes.** OP-18 and OP-22 describe *what*
  happened at a hospital, not *why*.
- **Prompt-injection defenses are partial.** See the sanitizer results above.

## How this was built

I designed and directed this project, and developed it with AI assistance using
**Claude Code**. I set the scope, the data rules, and the safety constraints, for
example: no private data, no live API calls without approval, and blind
evaluation rounds locked before scoring. I also reviewed each milestone. Claude
Code wrote most of the code, tests, and documentation under that direction. The
milestone-by-milestone history is in the git log and in
[DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md).

## Documentation

| Topic | Document |
|---|---|
| Case study: decisions, failures, adaptation to other public-health settings | [docs/CASE_STUDY.md](docs/CASE_STUDY.md) |
| 90-second demo outline | [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md) |
| Problem, users, scope | [PROJECT_SPEC.md](PROJECT_SPEC.md) |
| Components and data flow | [ARCHITECTURE.md](ARCHITECTURE.md) |
| Public data sources, reuse terms, and limits | [data/public/SOURCES.md](data/public/SOURCES.md), [docs/PUBLIC_DATA_METHODOLOGY.md](docs/PUBLIC_DATA_METHODOLOGY.md) |
| Synthetic data and simulation | [docs/SYNTHETIC_DATA_ASSUMPTIONS.md](docs/SYNTHETIC_DATA_ASSUMPTIONS.md), [docs/SIMULATION_MODEL.md](docs/SIMULATION_MODEL.md) |
| Validation gate and what-if statistics | [docs/VALIDATION_GATE.md](docs/VALIDATION_GATE.md), [docs/WHAT_IF_ANALYSIS.md](docs/WHAT_IF_ANALYSIS.md) |
| Dashboard | [docs/DASHBOARD.md](docs/DASHBOARD.md) |
| AI interpretation, evaluation, review rubric, live evaluation plan | [docs/AI_INTERPRETATION.md](docs/AI_INTERPRETATION.md), [docs/AI_EVALUATION.md](docs/AI_EVALUATION.md), [docs/AI_REVIEW_RUBRIC.md](docs/AI_REVIEW_RUBRIC.md), [docs/LIVE_EVALUATION.md](docs/LIVE_EVALUATION.md) |

## Disclaimer and license

This is an independent portfolio project. It is not affiliated with any
hospital, health system, employer, CMS, or NYS DOH. Simulated results are
illustrative and don't describe the performance or causes of delay at any
real institution. Released under the [MIT License](LICENSE). Public data
attribution is in [data/public/SOURCES.md](data/public/SOURCES.md).
