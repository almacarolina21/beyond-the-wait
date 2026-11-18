"""AI Explanation Evaluation: how well the local checks catch unsafe explanations.

Runs the offline evaluation suite (hand-written cases, frozen SIMULATED
packets) on page load. It never calls the Claude API: the evaluation package
imports no provider or API client.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from beyond_the_wait.dashboard import ui
from beyond_the_wait.evaluation.dataset import CATEGORIES, HELDOUT_SPLITS, load_dataset
from beyond_the_wait.evaluation.runner import EvalReport, run_suite
from beyond_the_wait.interpretation.faithfulness import VERDICTS

SPLIT_NAMES = {
    "dev": "Development (rules tuned on these)",
    "heldout": "Held-out round 1",
    "heldout2": "Held-out round 2",
    "heldout3": "Held-out round 3",
}
OUTCOME_NAMES = {
    "false_accept": "Unsafe text accepted",
    "missed_review": "Borderline text accepted without review",
    "false_reject": "Useful text rejected",
    "caught_for_review": "Unsafe text sent to review (not auto-rejected)",
    "unnecessary_review": "Useful text sent to review",
    "over_reject": "Borderline text rejected",
    "correct": "Right verdict, unexpected reason",
}


@st.cache_data(show_spinner="Running the offline evaluation…")
def _report() -> EvalReport:
    return run_suite(load_dataset())


def _pct(num: int, den: int) -> str:
    return f"{num}/{den} ({100 * num / den:.0f}%)" if den else "n/a"


ui.page_header(
    "AI explanation",
    "AI explanation evaluation",
    "How well do the local checks catch misleading or unsafe explanations, and how often do they "
    "block useful ones? Measured offline on hand-written test cases; no Claude API call is made.",
    badges=("SIMULATED",),
)
ui.callout(
    "<b>An evaluation tool, not a safety certificate.</b> These numbers describe the checks on "
    "this project's own test cases, written by the same author as the checks. They do not show "
    "that any explanation is clinically safe, and they say nothing yet about real Claude "
    "responses, which have not been evaluated."
)

report = _report()

# --- 1. Blind results ------------------------------------------------------------------------
ui.section("1. Blind results (the honest estimate)")
st.markdown(
    "Each held-out round was written, **locked with a file hash, and scored once** before any "
    "rule was changed in response to it. Those first runs are the independent estimates; the "
    "rules were then fixed, so later scores on the same cases are no longer independent."
)
rows = []
for split in HELDOUT_SPLITS:
    state = report.locks.get(split)
    if not state or not state.lock:
        continue
    f = state.lock["first_run"]
    rows.append(
        {
            "Round": SPLIT_NAMES[split],
            "Cases": f["cases"],
            "Unsafe detected": _pct(*f["unsafe_detected"]),
            "Unsafe accepted": len(f["false_accepts"]),
            "Useful text rejected": _pct(*f["legit_false_rejections"]),
            "Useful text sent to review": _pct(*f["legit_unnecessary_review"]),
            "File unchanged": "yes" if state.unchanged else "NO",
        }
    )
st.table(pd.DataFrame(rows).set_index("Round"))
st.caption(
    "Round 1 tested the original word-pattern checks; round 2 tested them after round-1 fixes; "
    "round 3 tested the added controlled-vocabulary gate, which sends unfamiliar wording to a "
    "human instead of accepting it."
)

# --- 2. Current run ----------------------------------------------------------------------------
ui.section("2. Current run, one split at a time")
split = st.selectbox(
    "Case set",
    list(report.splits),
    format_func=lambda s: SPLIT_NAMES.get(s, s),
    key="eval_split",
)
s = report.splits[split]
exp = s.expected_counts()
st.markdown(
    f"**{s.n} cases**: {exp.get('ACCEPT', 0)} should be accepted, "
    f"{exp.get('HUMAN_REVIEW', 0)} need human review, {exp.get('REJECT', 0)} should be rejected."
    + (" These scores are after fixes, not blind." if split != "dev" else "")
)
c1, c2, c3, c4 = st.columns(4)


def _frac(rate) -> str:  # noqa: ANN001
    return f"{rate.num}/{rate.den}"  # short enough to fit a metric tile at any width


c1.metric("Unsafe detected", _frac(s.unsafe_detection()))
c2.metric("Unsafe accepted", len(s.false_accepts()))
c3.metric("Useful rejected", _frac(s.legit_false_rejection()))
c4.metric(
    "Extra reviews", _frac(s.legit_unnecessary_review()), help="Useful text sent to human review"
)

conf = s.confusion()
st.markdown("**Confusion matrix** (rows: what should happen; columns: what the checks did)")
st.table(
    pd.DataFrame(
        [[conf[e][p] for p in VERDICTS] for e in VERDICTS],
        index=[f"should {e}" for e in VERDICTS],
        columns=[f"did {p}" for p in VERDICTS],
    )
)
with st.expander("Results by risk category"):
    st.table(
        pd.DataFrame(
            [
                {
                    "Risk": CATEGORIES[r["category"]],
                    "Cases": r["n"],
                    "Correct": r["correct"],
                    "Unsafe accepted": r["false_accept"],
                    "Useful rejected": r["false_reject"],
                    "Other": r["other"] + r["missed_review"],
                }
                for r in s.per_category()
            ]
        ).set_index("Risk")
    )
issues = s.issues()
with st.expander(f"Cases the checks got wrong or only partly right ({len(issues)})"):
    if not issues:
        st.markdown("None in this split.")
    for r in issues:
        st.markdown(
            f"- **{r.case.id}** · {OUTCOME_NAMES.get(r.outcome, r.outcome)}: "
            f"{r.case.rationale}"
            + (f"  \n  First signal: `{r.problems[0][:140]}`" if r.problems else "")
        )

# --- 3. Limits -------------------------------------------------------------------------------
ui.section("3. What this does not show")
st.markdown(
    "- **Real Claude output is untested.** Every case here was written by hand; live responses "
    "may fail in ways no case anticipated.\n"
    "- **Same author.** The cases and the checks were written by the same author, so blind "
    "rounds still share blind spots. Independent reviewers should write the next round.\n"
    "- **Word patterns are not understanding.** The checks can confirm numbers, citations, and "
    "known risky wording. They cannot confirm that a sentence is true, complete, or balanced; "
    "that is what human review (the rubric in `docs/AI_REVIEW_RUBRIC.md`) is for.\n"
    "- **100% test coverage is not 100% safety.** Coverage shows the code ran, not that every "
    "harmful explanation is caught."
)
st.caption(
    "Reproduce: `python -m beyond_the_wait.evaluation run`. Method and findings: "
    "`docs/AI_EVALUATION.md`."
)
ui.footer()
