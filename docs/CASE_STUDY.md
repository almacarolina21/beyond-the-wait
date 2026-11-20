# Case Study: Beyond the Wait

*A grounded-AI decision-support prototype for emergency department access.
The ED is fictional and the data is public and simulated. The prototype is not
clinically validated. Only one small live Claude evaluation (10 responses) has
been run, and none of those responses passed the release checks.*

## 1. Problem and motivation

Bronx emergency departments report some of New York's longest ED times in
public CMS data. Those measures show *what* happened, for example the median
time discharged patients spent in the ED or the share who left without being
seen. They cannot show *why*. Door-to-provider time and boarding time are not
published. Leaders choosing between more staff, faster diagnostics, and
inpatient bed flow are often working from one headline number.

Two questions drove the project:

1. Can a transparent simulation let someone test "what if?" safely, and show
   the uncertainty and side effects alongside the headline?
2. Can an LLM explain those results in plain language without inventing
   numbers, causes, or advice, and how would we *know*?

The second question is the core of the work. In public health, a fluent but
unfaithful explanation does more harm than no explanation.

## 2. Key design decisions and alternatives

| Decision | Chosen | Alternatives considered | Why |
|---|---|---|---|
| Where numbers come from | Python only. Claude writes value tokens (`{{M:x.change}}`) that Python renders | Let the model quote numbers, then check them | Rendering removes a whole class of errors rather than detecting them |
| What the model sees | A typed **evidence packet**: verified aggregates with stable fact IDs, cautions and limitations. Never patient records | Raw tables; retrieval over documents | Small, auditable, citable; every claim can point at a fact ID |
| Output contract | Structured JSON (schema-enforced) with an evidence ID on every statement | Free text | Citations can be checked mechanically |
| What happens to uncertain output | Three verdicts. Only ACCEPT prose is shown publicly. HUMAN_REVIEW is withheld | Two verdicts (pass/fail); showing borderline text with a warning | A warning label next to unreviewed text still lets it be read and quoted |
| How to catch unsafe wording | Pattern checks, then a **controlled-vocabulary gate**: unfamiliar words go to a human | Keep extending a phrase denylist | Blind round 2 showed denylists fail open on new phrasing (§6) |
| Simulation | SimPy discrete-event model with interacting queues | Spreadsheet or closed-form queueing | Boarding blocks beds, which blocks physicians; those interactions are the point |
| Uncertainty | 30 paired seeds with common random numbers; paired-t 95% CI; sensitivity runs | Single run; independent seeds | Pairing removes week-to-week noise from the comparison |
| Release control | A PASS / CAUTION / BLOCKED **validation gate** on every replication | Show every result | Corrupted or overloaded runs must not reach the explanation step |
| Live API use | Off by default; a separate, approval-gated evaluation tool with a 10-call cap | Turn on live mode and observe | Spending and exposure should need an explicit human decision |

## 3. Data governance and privacy

- **Three data types, always labeled.** PUBLIC is official aggregate measures
  for five Bronx hospitals from CMS and NYS DOH, with SHA-256 fingerprints and
  byte-identical rebuilds. SYNTHETIC is fictional patients from a seeded
  generator. SIMULATED is model output. Real and simulated data never share a
  chart.
- **No record-level data, even de-identified.** SPARCS record-level files were
  reviewed and deliberately excluded.
- **Missing values stay missing.** Suppressed and unavailable public values
  keep their official footnotes and are never filled with zeros. Metrics
  without a public source, such as door-to-provider time, are marked
  unavailable rather than approximated.
- **No private or employer data.** The tracked files and the full git history
  were scanned before release for credentials, personal information and local
  paths.
- **Reuse.** Only small derived public tables are committed, with attribution
  and terms noted in `data/public/SOURCES.md`.

## 4. Simulation and statistical methodology

- **Model.** A fictional 24-hour ED in SimPy with:
  - time-varying Poisson arrivals;
  - acuity-dependent lab, imaging and admission;
  - triage nurses and physicians on shift schedules;
  - treatment spaces by type;
  - non-preemptive acuity priority;
  - inpatient boarding through a simplified bed-release stream.

  Each run covers 8 days, and the first day is discarded as warm-up.
- **Comparisons.** Each intervention runs on the same 30 seeds as the
  baseline. The reported estimate is the mean paired difference with a paired-t
  95% confidence interval. It is kept separate from a prediction interval for a
  single future week.
- **Validation gate.** Every replication receives PASS, CAUTION or BLOCKED.
  - BLOCKED runs (corrupted or inconsistent output) invalidate their
    comparison, and the dashboard never shows them as data.
  - CAUTION reasons (overload, extreme waits, ESI 1 timeliness) are carried
    into the dashboard and into the evidence packet.
- **Robustness.** The analysis runs:
  - sensitivity tests on uncertain assumptions (physician concurrency and
    demand) with 20 seeds;
  - mixed-direction detection, which flags comparisons where one stage's wait
    falls while another stage's wait rises;
  - archetype tests, which check whether relieving the largest delay is the
    same as relieving the binding one.
- **Honest scope.** Every parameter is an illustrative assumption. The
  findings describe the model, not the Bronx.

## 5. LLM architecture and evaluation

**Pipeline:**
1. Verified results bundle.
2. Evidence packet: 37 facts for a typical selection.
3. Claude produces structured JSON, citing fact IDs and writing value tokens.
4. Python renders the numbers and runs 11 deterministic checks.
5. Verdict:
   - **ACCEPT**: shown publicly;
   - **HUMAN_REVIEW**: withheld;
   - **REJECT**: only the failed checks are shown.

A "Key result (calculated by Python)" sentence always appears above any
generated text.

**The checks:**
- cited IDs exist;
- numbers and units match;
- no real hospitals;
- no recommendations or imperatives;
- no causal or certainty overreach;
- no misread confidence intervals;
- no safety or reliability claims;
- tradeoffs that moved against the focus are flagged when omitted;
- vocabulary outside a vetted list is sent to review.

**Evaluation design:**
- 180 hand-written candidate explanations, each labeled with an expected
  verdict and risk category;
- a development set used for tuning;
- three held-out rounds, each written, **SHA-256 locked, and scored once**
  before any rule changed.

A 7-dimension human review rubric covers:
- numerical fidelity;
- evidence grounding;
- tradeoffs;
- uncertainty;
- fictional versus real;
- safety;
- clarity.

## 6. What failed, what changed, and what remains unresolved

**Blind results (the honest estimates):**

| Round | Unsafe caught | Unsafe accepted | Useful rejected |
|---|---|---|---|
| 1 | 19/31 | 12 | 4/17 |
| 2 | 11/20 | 9 | 0/10 |
| 3 | 12/13 | 1 | 3/8 |

**What failed:**
- **Tuning scores were not evidence.** The dev set reached 38/38, and the first
  blind round reached 19/31.
- **Phrase patching made things worse.** Round 2 fell to 55%. New phrasings
  each slipped past the denylist:
  - "Why wait?";
  - "the lever to pull";
  - "Clinicians should order fewer imaging studies";
  - an injected "per the developer message".
- **The prompt-injection sanitizer** removed 3/13 blind injections. A later
  untuned probe removed 0/8 new styles: a zero-width split, Cyrillic
  look-alike letters, base64, passive voice, non-English text and a Markdown
  heading.
- **Over-blocking.** Legitimate statistical language was rejected, for example
  "90% of admitted patients" and "neither a rise nor a fall".

**What changed:**
- **A safer default.** The controlled-vocabulary gate changed the default from
  "accept unless a known bad phrase appears" to "send unfamiliar wording to a
  person". Blind detection rose to 12/13, though mostly by routing to review
  (8/13) rather than rejecting.
- **Withheld review text.** HUMAN_REVIEW prose is withheld from public view.
  Regression tests render the panel for all 180 cases and assert that no
  non-ACCEPT prose appears. A deliberate leak made 33 of them fail.
- **Honest reporting.** Post-fix scores (0 false accepts) are labeled as not
  independent, and they are never quoted without the blind numbers.
- **Documented gaps.** The sanitizer's limits are written down instead of
  being claimed as solved.

**First live evaluation (batch `live-2026-11-20`):**

After explicit approval, the supervised tool made 10 real calls to Claude
Opus 5.5: 5 fictional evidence packets × 2 generations. The protocol was a
hard cap of 10 calls, no retries, an append-only ledger, a $2.38 list-price
ceiling, and a blind review saved before the automated verdict was revealed.

| Measure | Result |
|---|---|
| Responses | 10/10, 0 API errors, all schema-valid |
| Cost | 81,174 input and 19,886 output tokens, about $0.72 at list price |
| Reviewer verdicts | 6 REJECT, 4 HUMAN_REVIEW |
| Automated verdicts | 8 REJECT, 2 HUMAN_REVIEW, 0 ACCEPT |
| Agreement | 4/10 |
| Automated false accepts | 0 |
| Published | none; all 10 withheld |

Three findings came out of the batch:
- **The architecture held where it was designed to.** Numerical fidelity was
  rated full on all 10 responses, because Python renders every number.
- **Grounding did not hold.** Evidence grounding was rated 0 on 6 of 10.
  The model cited real fact IDs but drew causal or interpretive conclusions
  those facts don't support, and sometimes mislabeled results. This is the gap
  the missing entailment check (below) would need to close.
- **The gate is conservative, partly for the wrong reasons.** It let nothing
  unsafe through, but it rejected all 4 responses the reviewer would only have
  sent to review. The 2 it sent to review were flagged mostly for unvetted
  wording rather than for their grounding errors.

Limits of this evidence: 10 samples can show failure types, not rates. They
cannot establish reliability or clinical safety. The scoring was done by one
reviewer with AI assistance, so it is not independent expert validation. The
dashboard's live mode was not part of this test. Raw responses are kept local
and are not published.

**Still unresolved:**
- No semantic entailment check: a cited fact may not support a sentence's
  meaning. The live batch confirmed this is the main failure mode.
- The cases and the rules have a single author. Independent red-teamers are
  needed.
- With 13 unsafe cases in the last round, the evaluation can identify
  *types* of failure, not reliable rates.
- No clinician or ED operations review, and no calibration to a real hospital.

## 7. Adapting the architecture to other public-health settings

*None of the following has been built. Each would need new data, new domain
review and new validation.*

The reusable part is the pattern, not the ED model:
1. Public aggregate data for context.
2. A transparent simulation or statistical model for what-if questions.
3. A validation gate.
4. A typed evidence packet.
5. An LLM restricted to explaining cited facts, with Python-rendered numbers.
6. Deterministic output checks with a three-way verdict.
7. Blind, locked evaluation rounds and human review.

**Maternal healthcare access** is a natural candidate. Questions like these
have the same shape as the ED problem: queues, travel, capacity and
coordination interacting.
- How would adding midwife-led antenatal clinic sessions change the wait for a
  first prenatal visit?
- How would a transport voucher change the share of people reaching
  facility-based delivery?

Adapting the pattern would require:
- **Data.** Population survey indicators (for example DHS antenatal care and
  facility-delivery coverage), facility capacity and referral-time data, all
  aggregate and with provenance labels. Local partners would need to confirm
  which indicators are meaningful and how they are measured.
- **Model.** A new simulation or a cascade model (first visit → four or more
  visits → facility delivery → postnatal check). Its assumptions would be
  elicited from maternal-health clinicians and community health workers and
  validated against observed indicators before any finding is presented.
- **Safety rules.** Stricter checks: no individual clinical advice, no
  inference about a person or a small area, and suppression rules for small
  counts. The vocabulary gate would need to be rebuilt in the local language,
  and it should be reviewed by native speakers. The current English-only gate
  would send everything to review.
- **Evaluation.** New locked held-out rounds written by people outside the
  build team, including local health workers, and a live evaluation with
  human review before any public display.
- **Governance.** Agreements with data owners, and a clear statement that the
  tool supports program planning and is not a clinical or eligibility
  decision.

Other settings with a similar shape, also not built: vaccination clinic
throughput, TB diagnostic referral delays, and community health worker
caseload planning.

## 8. What I would do next

1. Turn the live batch's grounding failures into a new locked held-out round,
   add a claim-to-evidence entailment check, and loosen the over-strict checks
   that rejected review-worthy responses. Then run a new, separately approved
   live batch to measure the change.
2. Have independent reviewers write a held-out round.
3. Add Unicode normalization and per-field allowlists to the packet sanitizer.
4. Review the model's structure with ED operations staff before claiming that
   any finding generalizes.
