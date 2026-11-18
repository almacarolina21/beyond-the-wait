"""Plain-text (Markdown) rendering of an evaluation report."""

from __future__ import annotations

from beyond_the_wait.evaluation.dataset import CATEGORIES
from beyond_the_wait.evaluation.runner import EvalReport, SplitScores
from beyond_the_wait.interpretation.faithfulness import CLAIM_CLASSES, VERDICTS

SHORT_CLASS = {
    "SUPPORTED_DESCRIPTION": "SUPPORTED",
    "PLAUSIBLE_INTERPRETATION": "INTERPRET.",
    "UNSUPPORTED_CAUSAL": "CAUSAL",
    "REAL_WORLD_OR_CLINICAL": "REAL/CLIN.",
    "CONTRADICTED": "CONTRADICT.",
}


def _table(header: list[str], rows: list[list[object]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return out


def split_markdown(s: SplitScores) -> list[str]:
    exp = s.expected_counts()
    lines = [
        f"## {s.split} split: {s.n} response cases",
        "",
        "Class distribution (expected verdict): "
        + ", ".join(f"{v} {exp.get(v, 0)}" for v in VERDICTS)
        + ". By kind: "
        + ", ".join(f"{k} {n}" for k, n in sorted(s.kind_counts().items()))
        + ".",
        "",
        "### Headline rates (numerator/denominator)",
        "",
    ]
    fa = s.false_accepts()
    lines += _table(
        ["Measure", "Result"],
        [
            ["Unsafe outputs detected (REJECT or HUMAN_REVIEW)", s.unsafe_detection()],
            ["Unsafe outputs auto-rejected", s.unsafe_rejected()],
            ["Unsafe outputs falsely accepted (count)", len(fa)],
            ["Legitimate outputs falsely rejected", s.legit_false_rejection()],
            ["Legitimate outputs sent to unnecessary review", s.legit_unnecessary_review()],
            ["Borderline outputs flagged (REVIEW or REJECT)", s.borderline_flagged()],
            ["Rejected/flagged for the expected reason", s.right_reason()],
            ["Claim labels matched (faithfulness classifier)", s.claim_agreement()],
            [
                "Labeled unsafe claims classified as SUPPORTED",
                s.unsafe_claims_passed_as_supported(),
            ],
        ],
    )
    lines += ["", "### Confusion matrix (rows = expected, columns = predicted)", ""]
    conf = s.confusion()
    lines += _table(
        ["expected \\ predicted", *VERDICTS, "total"],
        [[e, *(conf[e][p] for p in VERDICTS), sum(conf[e].values())] for e in VERDICTS],
    )
    lines += ["", "### Check families (detection on targeted cases; false alarms)", ""]
    lines += _table(
        ["Dimension", "Detected when targeted", "Fired on non-REJECT cases"],
        [[name, hit, alarm] for name, hit, alarm in s.dimension_scores()],
    )
    lines += ["", "### Per risk category", ""]
    lines += _table(
        [
            "Category",
            "n",
            "expected",
            "correct",
            "false accept",
            "missed review",
            "false reject",
            "other",
        ],
        [
            [
                r["category"],
                r["n"],
                ", ".join(f"{k} {v}" for k, v in r["expected"].items()),
                r["correct"],
                r["false_accept"],
                r["missed_review"],
                r["false_reject"],
                r["other"],
            ]
            for r in s.per_category()
        ],
    )
    pairs = s.claim_pairs()
    if pairs:
        lines += ["", "### Claim classes (rows = label, columns = classifier)", ""]
        cc = s.claim_confusion()
        lines += _table(
            ["label \\ predicted", *(SHORT_CLASS[c] for c in CLAIM_CLASSES)],
            [[SHORT_CLASS[e], *(cc[e][p] for p in CLAIM_CLASSES)] for e in CLAIM_CLASSES],
        )
    if s.sanitizer:
        rem, keep = s.sanitizer_score()
        lines += [
            "",
            f"### Packet-text sanitizer: injections removed {rem}; harmless text kept {keep}",
        ]
        for r in s.sanitizer:
            if not r.correct:
                lines.append(
                    f"- **{r.case.id}** expected {r.case.expected}, got "
                    f'{"REMOVE" if r.removed else "KEEP"}: "{r.case.text[:90]}"'
                )
    issues = s.issues()
    lines += ["", f"### Missed, ambiguous, or wrong-reason cases ({len(issues)})", ""]
    if not issues:
        lines.append("None.")
    for r in issues:
        label = r.outcome if r.outcome != "correct" else "right verdict, unexpected reason"
        lines.append(
            f"- **{r.case.id}** [{r.case.category}] {label}: expected {r.case.expected}, "
            f"got {r.predicted}. {r.case.rationale}"
        )
        if r.problems:
            lines.append(f"  - First signal: {r.problems[0]}")
    return lines


def to_markdown(report: EvalReport) -> str:
    lines = [
        "# AI interpretation evaluation report (offline, deterministic)",
        "",
        f"Run date: {report.run_date}. Candidate responses are hand-written test data; no "
        "Claude API call is made. These results describe the guardrails on THIS dataset only. "
        "They are not a measure of clinical safety, and 100% code coverage is not 100% AI "
        "safety.",
        "",
    ]
    for state in report.locks.values():
        if not state.lock:
            continue
        status = (
            "unchanged since it was locked"
            if state.unchanged
            else "MODIFIED since it was locked: its scores are no longer a blind measurement"
        )
        first = state.lock.get("first_run", {})
        det, fa = first.get("unsafe_detected", [0, 0]), first.get("false_accepts", [])
        fr = first.get("legit_false_rejections", [0, 0])
        lines += [
            f"- `{state.split}.toml` SHA-256 `{(state.sha256 or '')[:16]}…` is {status} (locked "
            f"{state.lock.get('locked_on')}). First blind run: unsafe detected {det[0]}/{det[1]}, "
            f"{len(fa)} false accepts, legitimate false rejections {fr[0]}/{fr[1]}."
            + (
                " Rules were tuned on it afterwards: current scores are not independent."
                if state.lock.get("after_lock")
                else ""
            ),
        ]
    lines.append("")
    lines.append("Risk categories: " + ", ".join(CATEGORIES) + ".")
    lines.append("")
    for s in report.splits.values():
        lines += split_markdown(s)
        lines.append("")
    return "\n".join(lines)
