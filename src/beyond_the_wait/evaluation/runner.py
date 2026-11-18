"""Offline evaluation runner and scoring.

For every case: build the candidate response, run ``assess()`` (the exact
verification + faithfulness code the dashboard uses), and compare the
predicted verdict with the independently written expected verdict.

Scoring never collapses to one accuracy number. Every rate is reported with
its numerator and denominator, per split, alongside the class distribution.

Outcome types (expected -> predicted):

    REJECT       -> ACCEPT        false_accept      (the critical failure)
    REJECT       -> HUMAN_REVIEW  caught_for_review (detected, not auto-rejected)
    HUMAN_REVIEW -> ACCEPT        missed_review     (borderline text auto-accepted)
    HUMAN_REVIEW -> REJECT        over_reject       (stricter than needed)
    ACCEPT       -> REJECT        false_reject      (useful text withheld)
    ACCEPT       -> HUMAN_REVIEW  unnecessary_review (extra human work)
    same         -> same          correct
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date

from beyond_the_wait.evaluation.dataset import (
    CATEGORIES,
    DIMENSIONS,
    LOCK_FILES,
    SPLIT_FILES,
    SPLITS,
    Dataset,
    EvalCase,
    SanitizerCase,
    file_sha256,
)
from beyond_the_wait.interpretation.evidence import clean_text
from beyond_the_wait.interpretation.faithfulness import (
    CLAIM_CLASSES,
    VERDICTS,
    Assessment,
    assess,
)

REMOVED_MARKER = "[text removed"
OUTCOME_TYPES = {
    ("REJECT", "ACCEPT"): "false_accept",
    ("REJECT", "HUMAN_REVIEW"): "caught_for_review",
    ("HUMAN_REVIEW", "ACCEPT"): "missed_review",
    ("HUMAN_REVIEW", "REJECT"): "over_reject",
    ("ACCEPT", "REJECT"): "false_reject",
    ("ACCEPT", "HUMAN_REVIEW"): "unnecessary_review",
}
SEVERITY = {
    "false_accept": 0,
    "missed_review": 1,
    "false_reject": 2,
    "caught_for_review": 3,
    "unnecessary_review": 4,
    "over_reject": 5,
    "wrong_reason": 6,
}


@dataclass
class CaseResult:
    case: EvalCase
    predicted: str
    failed_checks: list[str]
    flags: list[str]
    problems: list[str]
    claim_results: list[tuple[str, str, str]]  # (loc, expected label, predicted label)

    @property
    def outcome(self) -> str:
        return OUTCOME_TYPES.get((self.case.expected, self.predicted), "correct")

    @property
    def right_reason(self) -> bool | None:
        """Did the expected check (or flag) fire? None when the case names none."""
        if self.predicted == "REJECT" and self.case.expected_checks:
            return bool(set(self.case.expected_checks) & set(self.failed_checks))
        if self.predicted == "HUMAN_REVIEW" and self.case.expected_flags:
            return bool(set(self.case.expected_flags) & set(self.flags))
        return None


@dataclass
class SanitizerResult:
    case: SanitizerCase
    removed: bool

    @property
    def correct(self) -> bool:
        return self.removed == (self.case.expected == "REMOVE")


@dataclass
class Rate:
    num: int
    den: int

    @property
    def value(self) -> float | None:
        return self.num / self.den if self.den else None

    def __str__(self) -> str:
        if not self.den:
            return "n/a (0 cases)"
        return f"{self.num}/{self.den} ({100 * self.num / self.den:.0f}%)"


@dataclass
class SplitScores:
    split: str
    results: list[CaseResult]
    sanitizer: list[SanitizerResult] = field(default_factory=list)

    # --- distributions --------------------------------------------------------
    @property
    def n(self) -> int:
        return len(self.results)

    def expected_counts(self) -> Counter[str]:
        return Counter(r.case.expected for r in self.results)

    def kind_counts(self) -> Counter[str]:
        return Counter(r.case.kind for r in self.results)

    def confusion(self) -> dict[str, dict[str, int]]:
        m = {e: dict.fromkeys(VERDICTS, 0) for e in VERDICTS}
        for r in self.results:
            m[r.case.expected][r.predicted] += 1
        return m

    # --- headline rates (always with denominators) ------------------------------
    def _with(self, expected: str) -> list[CaseResult]:
        return [r for r in self.results if r.case.expected == expected]

    def unsafe_detection(self) -> Rate:
        u = self._with("REJECT")
        return Rate(sum(r.predicted != "ACCEPT" for r in u), len(u))

    def unsafe_rejected(self) -> Rate:
        u = self._with("REJECT")
        return Rate(sum(r.predicted == "REJECT" for r in u), len(u))

    def false_accepts(self) -> list[CaseResult]:
        return [r for r in self.results if r.outcome == "false_accept"]

    def legit_false_rejection(self) -> Rate:
        a = self._with("ACCEPT")
        return Rate(sum(r.predicted == "REJECT" for r in a), len(a))

    def legit_unnecessary_review(self) -> Rate:
        a = self._with("ACCEPT")
        return Rate(sum(r.predicted == "HUMAN_REVIEW" for r in a), len(a))

    def borderline_flagged(self) -> Rate:
        b = self._with("HUMAN_REVIEW")
        return Rate(sum(r.predicted != "ACCEPT" for r in b), len(b))

    def right_reason(self) -> Rate:
        judged = [r for r in self.results if r.right_reason is not None]
        return Rate(sum(bool(r.right_reason) for r in judged), len(judged))

    def dimension_scores(self) -> list[tuple[str, Rate, Rate]]:
        """(dimension, detection on targeted cases, false alarms on non-REJECT cases)."""
        out = []
        for name, checks in DIMENSIONS.items():
            targeted = [r for r in self.results if set(r.case.expected_checks) & set(checks)]
            hit = sum(bool(set(r.failed_checks) & set(checks)) for r in targeted)
            clean = [r for r in self.results if r.case.expected != "REJECT"]
            alarms = sum(bool(set(r.failed_checks) & set(checks)) for r in clean)
            out.append((name, Rate(hit, len(targeted)), Rate(alarms, len(clean))))
        return out

    def per_category(self) -> list[dict]:
        rows = []
        for cat in CATEGORIES:
            rs = [r for r in self.results if r.case.category == cat]
            if not rs:
                continue
            oc = Counter(r.outcome for r in rs)
            rows.append(
                {
                    "category": cat,
                    "n": len(rs),
                    "expected": dict(Counter(r.case.expected for r in rs)),
                    "correct": oc["correct"],
                    "false_accept": oc["false_accept"],
                    "missed_review": oc["missed_review"],
                    "false_reject": oc["false_reject"],
                    "other": len(rs)
                    - oc["correct"]
                    - oc["false_accept"]
                    - oc["missed_review"]
                    - oc["false_reject"],
                }
            )
        return rows

    # --- claim-level faithfulness ----------------------------------------------
    def claim_pairs(self) -> list[tuple[str, str]]:
        return [(e, p) for r in self.results for _, e, p in r.claim_results]

    def claim_agreement(self) -> Rate:
        pairs = self.claim_pairs()
        return Rate(sum(e == p for e, p in pairs), len(pairs))

    def claim_confusion(self) -> dict[str, dict[str, int]]:
        m = {e: dict.fromkeys(CLAIM_CLASSES, 0) for e in CLAIM_CLASSES}
        for e, p in self.claim_pairs():
            m[e][p] += 1
        return m

    def unsafe_claims_passed_as_supported(self) -> Rate:
        """Labeled causal/real-world/contradicted claims that the classifier called supported."""
        bad = [
            (e, p)
            for e, p in self.claim_pairs()
            if e in ("UNSUPPORTED_CAUSAL", "REAL_WORLD_OR_CLINICAL", "CONTRADICTED")
        ]
        return Rate(sum(p == "SUPPORTED_DESCRIPTION" for _, p in bad), len(bad))

    # --- sanitizer ---------------------------------------------------------------
    def sanitizer_score(self) -> tuple[Rate, Rate]:
        """(injections removed, harmless text kept)."""
        rem = [s for s in self.sanitizer if s.case.expected == "REMOVE"]
        keep = [s for s in self.sanitizer if s.case.expected == "KEEP"]
        return Rate(sum(s.removed for s in rem), len(rem)), Rate(
            sum(not s.removed for s in keep), len(keep)
        )

    def issues(self) -> list[CaseResult]:
        """Every case that is not simply correct, worst first."""
        out = [r for r in self.results if r.outcome != "correct" or r.right_reason is False]
        return sorted(
            out,
            key=lambda r: SEVERITY.get(r.outcome if r.outcome != "correct" else "wrong_reason"),
        )


@dataclass
class LockState:
    split: str
    lock: dict | None
    sha256: str | None

    @property
    def unchanged(self) -> bool | None:
        if not self.lock or self.sha256 is None:
            return None
        return self.lock.get("sha256") == self.sha256


@dataclass
class EvalReport:
    splits: dict[str, SplitScores]
    locks: dict[str, LockState]
    run_date: str = field(default_factory=lambda: date.today().isoformat())


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------


def run_case(ds: Dataset, case: EvalCase) -> CaseResult:
    packet = ds.packets[case.packet]
    a: Assessment = assess(packet, ds.candidate(case))
    predicted = {c.loc: c.claim_class for c in a.claims}
    claim_results = [(lbl.loc, lbl.label, predicted.get(lbl.loc, "MISSING")) for lbl in case.claims]
    return CaseResult(
        case=case,
        predicted=a.verdict,
        failed_checks=[c.name for c in a.report.checks if not c.passed],
        flags=sorted({f.kind for f in a.flags}),
        problems=a.report.problems + [f.message for f in a.flags],
        claim_results=claim_results,
    )


def run_sanitizer(case: SanitizerCase) -> SanitizerResult:
    return SanitizerResult(case, clean_text(case.text).startswith(REMOVED_MARKER))


def run_suite(ds: Dataset) -> EvalReport:
    splits: dict[str, SplitScores] = {}
    for split in SPLITS:
        cases = [c for c in ds.cases if c.split == split]
        san = [s for s in ds.sanitizer if s.split == split]
        if cases or san:
            splits[split] = SplitScores(
                split, [run_case(ds, c) for c in cases], [run_sanitizer(s) for s in san]
            )
    locks = {
        split: LockState(
            split,
            read_lock(split),
            file_sha256(SPLIT_FILES[split]) if SPLIT_FILES[split].exists() else None,
        )
        for split in LOCK_FILES
    }
    return EvalReport(splits, locks)


def read_lock(split: str) -> dict | None:
    import json

    path = LOCK_FILES[split]
    return json.loads(path.read_text()) if path.exists() else None


def summary_dict(s: SplitScores) -> dict:
    """Compact, JSON-friendly numbers for one split (used by the lock and reports)."""
    fa = s.false_accepts()
    rem, keep = s.sanitizer_score()
    return {
        "cases": s.n,
        "expected": dict(s.expected_counts()),
        "confusion": s.confusion(),
        "unsafe_detected": [s.unsafe_detection().num, s.unsafe_detection().den],
        "unsafe_rejected": [s.unsafe_rejected().num, s.unsafe_rejected().den],
        "false_accepts": [r.case.id for r in fa],
        "legit_false_rejections": [
            s.legit_false_rejection().num,
            s.legit_false_rejection().den,
        ],
        "legit_unnecessary_review": [
            s.legit_unnecessary_review().num,
            s.legit_unnecessary_review().den,
        ],
        "borderline_flagged": [s.borderline_flagged().num, s.borderline_flagged().den],
        "claim_agreement": [s.claim_agreement().num, s.claim_agreement().den],
        "sanitizer_removed": [rem.num, rem.den],
        "sanitizer_kept": [keep.num, keep.den],
        "issues": {r.case.id: r.outcome for r in s.issues()},
    }
