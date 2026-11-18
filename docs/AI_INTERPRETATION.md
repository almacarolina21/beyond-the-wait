# AI Interpretation — Beyond the Wait (Milestone 8)

> **Python calculates. Claude explains. Humans decide.**
>
> Claude never sees raw data and never produces a number that is shown to a
> user. It receives a small, typed **evidence packet** built by Python from
> verified results. It returns structured JSON that cites fact IDs. Python checks
> that reply before showing it and inserts every number itself.

Status: the **MOCK (DEMO) integration is complete and tested**. The live Claude
path is implemented and tested against fake SDK responses, but **no real API
request has been made**. Live mode is off unless you turn it on explicitly
(§7).

---

## 1. Architecture and data flow

```
 results bundle (Milestone 6)            SHA-256 fingerprints, SIMULATED labels,
 data/synthetic/experiments/   ──load──► BLOCKED comparisons separated out
                │  load_bundle() → BundleLoad(status="ok")
                ▼
 interpretation/evidence.py     build_evidence_packet(load, scenario, outcome)
   • refuses unverified bundles, BLOCKED comparisons, unknown inputs
   • copies + rounds values (Python); one stable ID per fact
   • cleans file text; removes instruction-like text
                │  EvidencePacket (Pydantic, ~36 facts, ~14 KB)
                ▼
 [ user clicks "Explain these results" ]  ← the ONLY trigger
                │
                ▼
 interpretation/providers.py
   MockProvider (default)     ClaudeProvider (opt-in)
   deterministic demo JSON    Messages API + JSON-schema structured output
                │  raw text (UNTRUSTED)
                ▼
 interpretation/verify.py       10 deterministic checks; any failure ⇒ reject all
                │
                ▼
 interpretation/service.py      Outcome: verified | rejected | error
                │
                ▼
 dashboard/ai_panel.py          shows ONLY verified output; numbers rendered
                                from the packet; evidence + checks in expanders
```

| Layer | Locally calculated (deterministic, tested) | AI-generated (untrusted until verified) |
|---|---|---|
| Numbers, CIs, directions, units | ✅ Python (Milestone 6 bundle → packet) | ❌ never |
| Validation status, CAUTION reasons, limitations | ✅ gate + bundle | ❌ |
| Which facts matter, plain wording, tradeoff description, questions | — | ✅ Claude (or the mock) |
| Whether a reply may be shown | ✅ `verify.py` | — |

**Why the bundle, not `ReleasedResults`?** The development plan said the packet
builder should accept only `ReleasedResults` from `release_for_interpretation()`.
That object describes **one** simulation run. The What-If Lab explains
**multi-seed paired comparisons**, whose every replication already went through
`validate_run()` during Milestone 6. Those results reach the dashboard only via
`load_bundle()`, which verifies fingerprints, labels, and statuses and removes
BLOCKED comparisons. So `build_evidence_packet()` accepts only a
`BundleLoad` whose status is `"ok"`, and it re-checks for BLOCKED itself.

## 2. Evidence-packet schema (`interpretation/evidence.py`)

```text
EvidencePacket
  packet_id            12-hex SHA-256 of the packet content (stable, deterministic)
  provenance           "SIMULATED"
  site                 "Riverside Commons Community ED (fictional)"
  scenario_id / scenario_name / baseline_name
  focus_metric         the outcome the visitor selected
  what_changes         lever description from the manifest
  n_pairs              paired simulated weeks (30 in the full bundle)
  validation_status    "PASS" | "CAUTION"   (BLOCKED never gets a packet)
  bundle_fingerprint   hash of the bundle's file fingerprints
  facts[]              MetricFact | TextFact, each with a stable id
```

| ID pattern | Kind | Content |
|---|---|---|
| `M:<metric>` | paired comparison | label, definition, baseline, with-change, difference, 95% CI low/high, units, n pairs, direction (`lower in the model` / `higher in the model` / `no detectable change (95% CI includes 0)` / `identical in both arms`) |
| `ST:status` | status | gate status and what it does **not** mean |
| `CK:<check>` / `CK:week_<check>` | caution | readable CAUTION reason + detail |
| `SENS:<metric>` | sensitivity | stability verdict across the uncertain-assumption grid (when available) |
| `RUN:design` | design | paired seeds, run length, what pairing does and does not remove |
| `NOTE:model_evidence` | note | model-internal evidence note |
| `LIM:<name>` | limitation | each of the 7 safety limitations |

Fractions are converted once by Python: levels in **%**, changes in
**percentage points**. Values are rounded for display (1 decimal; 2 below 1).
Packets never contain individual synthetic patient records, real hospital data,
personal data, or secrets (tested).

Identical inputs always give an identical packet and `packet_id` (tested), so
the dashboard can cache an explanation per packet.

## 3. Prompt rules (`interpretation/prompt.py`)

The system prompt encodes PROJECT_SPEC §5. Summary:

1. Use only the packet. Every statement cites fact IDs that exist.
2. **No typed numbers.** Write value tokens such as
   `{{M:boarding_p90.change}}`; Python replaces them with exact packet values.
   A token may only use a fact that its statement cites.
3. Directions only as the packet gives them: never better, worse, safe,
   effective, or recommended. "No detectable change" must not be called a change.
4. The 95% CI is about the average change across simulated weeks: not a 95%
   chance, not a range for one week, not a forecast for a real ED.
5. No real hospitals, places, or real-world causes.
6. No recommendations, staffing or implementation advice, or clinical guidance.
7. Explain relationships only when the packet states them (for example, waiting moved between stages).
8. Keep CAUTION and cite at least one limitation.
9. No URLs, studies, or outside sources.

The packet is sent inside `<evidence_packet>` tags with an explicit note that
it is **data, not instructions**. Free text from files is cleaned before it
enters the packet (`clean_text()`), and anything that looks like an instruction
("ignore previous…", "system prompt", role tags) is replaced.

**Structured output.** The response schema is the Pydantic model
`schema.Interpretation`:

| Field | Content |
|---|---|
| `summary` | two to four sentences |
| `key_findings[]` | `{text, evidence_ids[]}` |
| `tradeoffs[]` | tradeoffs inside the fictional model |
| `uncertainties[]` | uncertainties and assumptions |
| `questions_for_humans[]` | questions for qualified people |
| `limitations[]` | limitations |
| `disclaimer` | disclaimer text |

It is sent with `output_config={"format": {"type": "json_schema", ...}}`, the
current Messages API structured-output parameter, using the schema produced by
the SDK's `transform_schema()`. Checked against the official docs on
2026-11-17. Citations use **our own evidence IDs**. The API's native document
citations are not used: the docs say they are incompatible with JSON
structured output, and our IDs are checkable by code.

Length limits are enforced in Python because the API's schema subset has no
`maxLength`.

## 4. Verification (`interpretation/verify.py`)

Every reply is **untrusted**. All eleven checks must pass, or the whole reply is
withheld. No partly valid text is ever shown. (Milestone 9 added check 9,
`claim_alignment`, and a second layer: a reply that passes every check is then
assessed for claim faithfulness and may be marked **needs human review**; see
[AI_EVALUATION.md](AI_EVALUATION.md).)

| # | Check | Catches |
|---|---|---|
| 1 | structure | invalid JSON, missing/extra fields, over-long text, empty lists, > 20,000 chars |
| 2 | evidence_ids | IDs not in the packet |
| 3 | value_tokens | unknown fact or field, a fact the statement doesn't cite, malformed tokens |
| 4 | numbers_and_units | typed numbers not in the cited facts; wrong units (min→hours, pp→%); spelled-out and relative quantities ("ninety minutes", "half", "3 times") |
| 5 | direction | "higher" for a lower fact (and vice versa); "reduced" for a no-detectable-change fact |
| 6 | uncertainty_wording | "95% chance", "true effect … probability", "proves", "every/most weeks", forecasts ("will…", "once implemented"), overclaimed causes or bottlenecks |
| 7 | real_world_claims | real Bronx hospital names; un-negated real-world or "in reality"/"nationally" claims; real-world causes |
| 8 | recommendations_and_safety | advice ("should hire", "would be wise", "no downside"); safe/better/effective; outcomes never modeled (mortality, harm, LWBS); clinical directives |
| 9 | claim_alignment (M9) | a value attached to the wrong outcome, statistic, patient group, or to workload vs. wait; a sensitivity fact cited for a different outcome or contradicted; "validated"/"no cautions"; capacity claims against overload cautions; sweeping "across the board" claims when outcomes moved differently |
| 10 | sources_and_markup | URLs, DOIs, "et al.", outside authorities, links/HTML, prompt-injection echoes, role prefixes |
| 11 | required_content | focus outcome cited; CAUTION stated and cited; LIM: cited; summary says "model"; disclaimer present; questions end with "?" |

**Prefer rendering over trusting.** The UI calls `render()`, which replaces
each value token with a value formatted by Python from the packet. Claude's
typed numbers, if any, are allowed only when they match a cited fact.

### What these checks can and cannot establish

They **can** establish that the reply:
- has the right structure;
- cites only real evidence;
- contains no number or unit that disagrees with that evidence;
- does not use the known red-flag phrasings;
- keeps the required cautions.

They **cannot** establish:
- that a cited fact actually *supports* the sentence (semantic entailment);
- that the pattern lists are complete;
- that the explanation is balanced, useful, or clinically safe.

**Schema validity is not semantic correctness.**

Evidence from building this: two red-team rounds of paraphrased unsafe
sentences got through the first version of the patterns:
- round one: 17 of 31 passed;
- round two: 12 of 14 passed, for example "is justified by", "mirrors most
  urban EDs", and "at least … every week".

Each gap was closed and every sentence is now a regression test (33 unsafe
sentences rejected, plus 6 reasonable model-scoped sentences that must still
pass). New phrasings will exist that no list anticipates. That is why
Milestone 9 adds a scored evaluation harness and a human-review checklist, and
why the UI always labels the output *"Decision support for human review"*.

Milestone 9 then measured this on blind held-out rounds: the word patterns
caught 19/31 and 11/20 unsafe responses phrased in new ways. A controlled-
vocabulary gate now sends unfamiliar wording to human review instead of
accepting it (12/13 on the next blind round). Details, all numbers, and every
fix: [AI_EVALUATION.md](AI_EVALUATION.md).

## 5. Safety and privacy protections

- **Inputs to Claude:** aggregate SIMULATED facts only. There are no patient
  records, real hospital data, personal data, or employer data.
- **BLOCKED never interpreted:** an unverified bundle, a BLOCKED comparison
  (in the rows or the manifest), unknown inputs, and oversized packets all raise
  `EvidenceRefused`. The panel shows a message, and no button appears.
- **No free-text input:** visitors choose an intervention and an outcome. There
  is no prompt box, so visitors cannot inject instructions.
- **Output display:** generated text is shown with Markdown/HTML escaped (no
  links, scripts, or math). Numbers come from the packet. The banner *"Decision
  support for human review. Based on simulated data."* is always shown.
- **Rejected output:** the panel names the checks that failed and their problem
  counts, and shows none of the rejected text.
- **No storage:**
  - prompts and replies live only in the browser session's memory
    (`st.session_state`);
  - nothing is written to disk or logged;
  - the key is never displayed, logged, or included in an error message (tested).

## 6. Mock (DEMO) vs. live mode

| | DEMO (default) | LIVE |
|---|---|---|
| Turned on by | nothing (default) | `BTW_AI_MODE=live` **in the environment of that run** **and** a key |
| Provider | `MockProvider`: deterministic JSON built from the packet | `ClaudeProvider`: Messages API |
| Label in the UI | `DEMO · mock, not Claude`; model `mock-interpreter-v1 (demonstration, not Claude)`; summary starts "DEMONSTRATION (mock output, not written by Claude)" | `LIVE · Claude API`; the model ID the API reports; token usage |
| Cost | free, offline | billed to the Anthropic API account |
| Verification | same 10 checks | same 10 checks |

- **Where the key can come from:** `ANTHROPIC_API_KEY` in the environment, a
  git-ignored `.env` file, or `.streamlit/secrets.toml` (also git-ignored).
- **A key alone never enables live mode.** A key set for another tool cannot
  trigger billable calls, and `BTW_AI_MODE` is deliberately not read from `.env`.
- **Live requested without a key:** the app says so and stays in DEMO mode.

**No API traffic on page load or widget changes:**
- the provider is created only inside the button handler;
- explanations are cached per (packet ID, mode, model), so going back to a
  previous selection shows the saved explanation without a new call;
- an explanation is never shown for a different selection than the one it was made for.

Both behaviours are tested with a counting fake client.

## 7. Cost controls and reliability

| Control | Setting |
|---|---|
| Trigger | one request per explicit click; nothing in the background |
| Input bound | packet ≤ 24,000 chars; prompt + packet ≤ 30,000 chars, checked **before** sending |
| Output bound | `max_tokens = 8,000` (thinking + JSON) |
| Timeout | 120 s per request |
| Retries | SDK `max_retries = 2` (exponential backoff for 429, 5xx, and connection errors), then stop |

**Failures become readable messages** and never crash the page. Each has a
fallback: missing key, authentication, permission, rate limit, timeout,
connection, overloaded (529), bad request, other API errors, refusal
(`stop_reason == "refusal"`), truncation (`max_tokens`), empty reply, oversized
input, and any unexpected exception.

**Estimated cost per click.** With `claude-opus-5-5` at $4 / $20 per million
input/output tokens (official pricing, checked 2026-11-17):
- input: about 6,000 tokens (system prompt, schema, and packet);
- output: up to 8,000 tokens;
- so about **$0.03–$0.18 per explanation** (an estimate, not a measurement).

`BTW_CLAUDE_MODEL=claude-sonnet-5-5` ($2 / $10) roughly halves that.

**Claude Pro and the Claude API are billed separately.** A Claude Pro or Max
subscription (claude.ai, Claude Code) does **not** include API usage. Live mode
needs an API key from the Claude Console (platform.claude.com) with its own
prepaid credits or billing. Consider setting a spend limit there.

## 8. Reproduction

DEMO mode needs no key and no internet:

```bash
source .venv/bin/activate
streamlit run app/streamlit_app.py
# What-If Lab → choose an intervention and outcome → "Explain these results"
```

To run the tests (no API calls):

```bash
python -m pytest tests/unit/test_interpretation.py tests/unit/test_dashboard_app.py
```

A test-wide guard (`tests/conftest.py`) does three things:
- forces DEMO mode;
- hides any `ANTHROPIC_API_KEY`;
- makes the SDK's HTTP layer raise.

So a test cannot send a real request even by mistake.

Live mode (only after you approve billing):
1. Create an API key in the Claude Console, under your own account.
2. Store it locally and never paste it into chat or screenshots. Run
   `cp .env.example .env`, then edit `.env` in a text editor:
   `ANTHROPIC_API_KEY=…`.
3. Run `BTW_AI_MODE=live streamlit run app/streamlit_app.py`.
4. The panel shows `LIVE · Claude API`. Each click sends exactly one request.

## 9. Files

| File | Role |
|---|---|
| `src/beyond_the_wait/analysis/check_descriptions.py` | readable titles and sentences for CAUTION codes |
| `src/beyond_the_wait/interpretation/evidence.py` | packet models and builder |
| `src/beyond_the_wait/interpretation/schema.py` | structured response model |
| `src/beyond_the_wait/interpretation/prompt.py` | system prompt and user message |
| `src/beyond_the_wait/interpretation/providers.py` | mock and live providers, configuration, error mapping |
| `src/beyond_the_wait/interpretation/verify.py` | verification and rendering |
| `src/beyond_the_wait/interpretation/service.py` | provider → verify → outcome |
| `src/beyond_the_wait/dashboard/ai_panel.py` | What-If Lab panel |
| `tests/unit/test_interpretation.py` | packet, verifier (adversarial and red-team), providers |
| `tests/unit/test_dashboard_app.py` | panel AppTests (no calls on load or widget change; mock, live-fake, rejection, errors) |
