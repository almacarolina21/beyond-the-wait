# Supervised Live Claude Evaluation (prepared, not run)

> **Status: prepared and tested offline only.** No real Claude API call has
> been made. The workflow below runs a live batch only after a person approves it
> in their own terminal. All evidence is fictional and simulated; a passing
> review is not clinical validation.

## 1. What it does

It sends 10 requests to Claude: 2 generations for each of the 5 frozen evidence
packets in `evals/packets/`. The tampered adversarial packet is never sent.

A person then scores every response against `docs/AI_REVIEW_RUBRIC.md` **before**
seeing the automated ACCEPT / HUMAN_REVIEW / REJECT verdict. Finally, the human and
automated verdicts are compared.

Ten responses can reveal *kinds* of failure. They cannot measure rates.

## 2. The fixed plan

`evals/live/plan.toml` pins:
- the protocol version;
- the model (`claude-opus-5-5`);
- the prompt version;
- the 5 packet names with the SHA-256 of each frozen file;
- 2 generations per packet, 10 calls in total;
- the list prices used for estimates.

The slot order is round-robin: slots 01–05 are generation 1 of each packet in
the listed order, and slots 06–10 are generation 2.

```
python -m beyond_the_wait.live_eval plan
```

This command is offline and writes nothing. It prints:
- the order;
- the input and output token bound for each slot;
- the **cost ceiling** (currently **$2.38** at $4 / $20 per million input/output tokens);
- the **approval code** (currently `46953081f512`).

The code is the first 12 hex digits of a SHA-256 over the plan, the system
prompt and the response schema. If a packet, the prompt or the plan changes, the
code changes and an old approval stops working.

**About the ceiling.** It assumes every slot uses one input token per character
and the full 8,000 output tokens. Real cost should be well below it; the MOCK dry
run's rough simulated usage came to about $0.25. Claude Opus 5.5 always thinks,
and thinking tokens are billed as output, which is why the output bound is the
full `max_tokens`.

## 3. Safeguards

| Safeguard | How it is enforced | Test |
|---|---|---|
| No live call by default | `generate` defaults to `--mode mock`; the dashboard is unaffected (it never imports `live_eval`) | `test_generate_defaults_to_mock`, `test_public_dashboard_never_reads_live_evaluation_records` |
| Explicit human approval | `--mode live` requires the approval code, an API key, an **interactive terminal**, and typing `RUN 10 CALLS` after the ceiling is shown. A refusal creates no files and no client. | `test_live_mode_is_refused_without_every_approval_step` (5 cases) |
| Inputs match the plan | Packet hashes and prompt version are re-checked before approval | `test_changed_packet_or_prompt_invalidates_the_plan` |
| Hard cap of 10 calls per batch | `MAX_CALLS_PER_BATCH = 10` in code; plans above 10 are invalid; an append-only ledger records each attempt **before** sending, and the cap is checked against it | `test_cap_holds_even_if_the_ledger_has_unknown_attempts`, `test_approved_live_batch_is_capped_and_never_retried` |
| No automatic retries | SDK client built with `max_retries=0`; any slot with a recorded attempt, even a failed one, is never re-sent in that batch | `test_live_provider_is_built_without_retries`, `test_run_batch_records_attempt_before_sending_and_never_resends` |
| No background requests | One sequential loop in the foreground; no threads or executors in the package | `test_only_one_function_can_build_a_billable_provider` |
| Generous timeout | 600 s, so a slow answer is less likely to be billed and then discarded | covered above |
| Blind review | No verdict is computed at generation. The automated verdict is computed only after the manual scores are saved, and saved scores can't be overwritten | `test_reveal_requires_a_saved_blind_review_first`, `test_interactive_review_validates_input_and_cannot_be_redone` |
| No public display | Records live only under `evals/results/live/` (git-ignored); the dashboard never reads them and still shows only ACCEPT text from its own requests | `test_records_live_only_in_the_git_ignored_folder`, `test_public_display.py` |
| Key never stored | Read from the environment or `.env` at call time; never written, printed, or logged. Unexpected exceptions record only the exception type, never its message | key-absence assertions in two tests |
| Terminal spoofing | Escape sequences and control characters in responses are stripped before printing | `test_terminal_output_cannot_be_spoofed_by_the_response` |

## 4. The reviewer workflow

```
python -m beyond_the_wait.live_eval review --batch <id> [--slot 03] [--reviewer reviewer-1]
```

For each slot that has a response and no review yet:

1. **Rubric.** The tool prints the 7 dimensions with their 2/1/0 descriptions,
   read from `docs/AI_REVIEW_RUBRIC.md` itself.
2. **Evidence.** It prints the evidence packet:
   - the Python-calculated key result;
   - every measured outcome (baseline → with change; change and 95% CI);
   - every caution, sensitivity result, limitation and note.
3. **Response.** It prints the response with values rendered by Python, and the
   evidence IDs cited by each statement. A response that can't be parsed is
   shown as raw text.
4. **Scores.** The reviewer scores each dimension 0, 1 or 2:
   - numerical fidelity;
   - evidence grounding (claim-to-evidence faithfulness);
   - completeness of tradeoffs;
   - uncertainty;
   - fictional versus real-world separation;
   - safety and appropriateness;
   - clarity.
5. **Follow-up questions.** The reviewer answers:
   - Was CAUTION or the CI meaning misrepresented? (asked only if uncertainty is 0)
   - Does the text follow an instruction found in the data?
   - Is there an interpretation they cannot confirm?
6. **Categories and notes.** The reviewer picks error categories from the
   evaluation taxonomy and can add notes.
7. **Save.** The tool applies the rubric's verdict rules and saves the review.
   It can't be changed afterwards.
8. **Reveal.** Only now does it compute and show the automated verdict, whether
   the two agree, and whether the public dashboard would show or withhold that
   text.

Then run:

```
python -m beyond_the_wait.live_eval report --batch <id>
python -m beyond_the_wait.live_eval record-billing --batch <id> --usd 0.37   # from the Console
```

The report lists every slot with:
- its status and any error;
- the human verdict, the automated verdict, and whether the dashboard would show it;
- tokens and estimated cost;
- the error categories.

It also shows a human-vs-automated confusion matrix. The most important line is
**automated false accepts**: responses a person rejected that the code would have
shown publicly.

## 5. What is stored (and where)

Everything is stored under `evals/results/live/<batch-id>/`, which is
git-ignored and stays on this machine:

| File | Contents |
|---|---|
| `batch.json` | Batch ID, mode (MOCK/LIVE), creation time, plan fingerprint, protocol, model, prompt version and fingerprint, call cap, pricing assumptions, slot order, actual billing if recorded |
| `ledger.jsonl` | Append-only `attempt` / `done` events, one per call |
| `responses/NN.json` | Slot, packet name, generation, scenario ID and name, focus outcome, packet ID, packet SHA-256, bundle fingerprint, model requested and returned, prompt version and SHA-256, plan SHA-256, timestamps, latency, stop reason, status or error category, **raw response text**, token usage (marked when simulated), estimated cost, billed amount (`null` until recorded) |
| `reviews/NN.manual.json` | Reviewer label (not a name), whether the answers were scripted, time, the 7 scores, the injection / unconfirmed-interpretation / CI answers, error categories, notes, rubric verdict |
| `reviews/NN.automated.json` | Automated verdict, failed checks, review flags (with the quoted words), agreement, automated false accept, public display decision |
| `report.md` | The summary above |

None of these files holds an API key, request headers, personal data, patient
data or real hospital data. The inputs are the frozen fictional packets. The
reviewer label is free text, so use a role label such as `reviewer-1`, not a
name. `.gitignore` is not a privacy control; these files simply contain nothing
private.

## 6. MOCK dry run

```
python -m beyond_the_wait.live_eval dry-run [--batch dry-run]
```

This runs the whole workflow offline.

- **Responses.** A scripted stand-in provider returns:
  - the built-in mock explanation for each generation 1;
  - hand-written evaluation cases for 4 of the generation 2 slots: dev-020, dev-037, dev-025, dev-030;
  - a simulated `truncated` API error for slot 09.
- **Reviews.** Pre-written answers in `evals/live/dry_run_answers.json` stand in
  for the reviewer. They are labeled as scripted, and one deliberately disagrees
  with the code to show how a disagreement is recorded.

Result: 10 attempts, 9 responses, 1 error, 9 reviews. The automated verdicts were
5 ACCEPT, 2 HUMAN_REVIEW and 2 REJECT. Human and automated agreed on 8/9, and
there were no automated false accepts. The report states that nothing in it is
Claude output or a real human judgment.

## 7. Creating an API key and a spending limit (do this yourself)

Never paste the key into a chat with an AI assistant, into an issue, or into a
file that is committed. Console menu names change; if one below doesn't match,
look for the equivalent.

1. **Billing with a hard ceiling.** In the Claude Console
   (console.anthropic.com, which may redirect to platform.claude.com), open
   **Billing**. Add a small amount of prepaid credit (for example $5) and leave
   **auto-reload off**. With auto-reload off, the credit balance is a hard upper
   bound on spending.
2. **A dedicated workspace with its own limit.** Under **Settings → Workspaces**,
   create a workspace such as `beyond-the-wait-eval`. Set its **spend limit**
   (for example $5 per month), and a low rate limit if offered. Also check the
   organization-wide limit under **Settings → Limits**.
3. **A key scoped to that workspace.** Under **Settings → API keys**, create a
   key in that workspace, named something like `btw-live-eval-2026-11`. It is
   shown once.
4. **Keep the key out of chats and logs.** In your own terminal (not through
   the assistant, and not with the `!` prefix, which puts output in the
   conversation), load the key for that shell session only:

   ```
   read -s ANTHROPIC_API_KEY && export ANTHROPIC_API_KEY   # paste, press Enter; nothing echoes
   ```

   A git-ignored `.env` file with `chmod 600 .env` also works. The session-only
   variable leaves nothing on disk.
5. **Afterwards:**
   - run `unset ANTHROPIC_API_KEY`;
   - delete or disable the key in the Console;
   - read the actual charge on the Console's **Usage/Cost** page and store it
     with `record-billing`.

## 8. Authorizing the live run (later, only with your explicit approval)

1. Confirm the plan is unchanged: run `python -m beyond_the_wait.live_eval plan`
   and check that the approval code and the ceiling are the ones you're
   approving.
2. Set up the key and spending limit as in §7, in your own terminal.
3. Start the batch (it runs in the foreground, about 10–25 minutes):

   ```
   python -m beyond_the_wait.live_eval generate --batch live-YYYY-MM-DD --mode live --approve <code>
   ```

   Type `RUN 10 CALLS` when asked. Anything else cancels, and nothing is sent.
4. Score every response, blind: `python -m beyond_the_wait.live_eval review --batch live-YYYY-MM-DD`.
5. Run `report`, then `record-billing` with the Console amount, then revoke the key.
6. Report back:
   - every automated false accept;
   - every disagreement;
   - any wording the checks did not anticipate.

   A real failure becomes a new **locked** held-out round (see `docs/AI_EVALUATION.md` §4.4).
   The live responses are not added to the tuning set.

Each batch is allowed at most 10 attempts. A failed slot is not retried, so a
second try needs a new batch ID and a new approval.
