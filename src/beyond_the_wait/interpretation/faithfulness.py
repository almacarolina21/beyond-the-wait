"""Claim-level faithfulness: is each sentence supported by the facts it cites?

`verify.py` answers "is anything provably wrong?" (wrong IDs, numbers, units,
directions, labels, red-flag wording). Passing it does NOT mean every sentence
is supported. This module adds a second, more honest layer:

1. Each cited statement (and the summary) gets a claim class:

   SUPPORTED_DESCRIPTION     restates cited facts in descriptive words
   PLAUSIBLE_INTERPRETATION  goes beyond the facts (a mechanism, a hedge such as
                             "suggests", a magnitude judgment such as "modest");
                             it may be reasonable, but nothing in the packet
                             proves it
   UNSUPPORTED_CAUSAL        asserts a cause or certainty the evidence cannot give
   REAL_WORLD_OR_CLINICAL    real-world claims, forecasts, recommendations,
                             safety or clinical statements
   CONTRADICTED              misstates its cited facts (number, unit, direction,
                             outcome name, statistic, sensitivity verdict, CI)

2. Whole-response review flags for things deterministic code can notice but
   cannot judge: an opposite-direction outcome that is never mentioned
   (possible cherry-picking), a value shown without naming its outcome, or a
   robustness claim with no sensitivity fact cited.

3. A three-way verdict:

   REJECT        any verification check failed (nothing is shown)
   HUMAN_REVIEW  verification passed, but at least one review flag was raised;
                 the explanation may be shown only with the flags visible
   ACCEPT        verification passed and no review flag was raised

The classifier is a deterministic word-pattern method. It is conservative by
design (it would rather send a sound sentence to a human than wave through an
unsound one), and it cannot recognise every paraphrase. ACCEPT therefore means
"no problem detected by these rules", never "proven faithful".
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Literal

from beyond_the_wait.interpretation.evidence import EvidencePacket, MetricFact
from beyond_the_wait.interpretation.verify import (
    FACT_ID,
    METRIC_FAMILIES,
    ROBUST_WORDS,
    SENS_UNSTABLE,
    TOKEN,
    Located,
    VerificationReport,
    _approved,
    _negated,
    _outcome_text,
    _unnegated,
    check_text,
    families_in,
    located_texts,
    new_checks,
    opposing_facts,
    parse,
    token_segments,
    verify,
)
from beyond_the_wait.interpretation.vocabulary import packet_vocabulary, unvetted

Verdict = Literal["ACCEPT", "HUMAN_REVIEW", "REJECT"]
ClaimClass = Literal[
    "SUPPORTED_DESCRIPTION",
    "PLAUSIBLE_INTERPRETATION",
    "UNSUPPORTED_CAUSAL",
    "REAL_WORLD_OR_CLINICAL",
    "CONTRADICTED",
]
CLAIM_CLASSES: tuple[ClaimClass, ...] = (
    "SUPPORTED_DESCRIPTION",
    "PLAUSIBLE_INTERPRETATION",
    "UNSUPPORTED_CAUSAL",
    "REAL_WORLD_OR_CLINICAL",
    "CONTRADICTED",
)
VERDICTS: tuple[Verdict, ...] = ("ACCEPT", "HUMAN_REVIEW", "REJECT")

# Sections whose sentences make claims about outcomes. Uncertainty and
# limitation sentences are expected to hedge ("may", "can differ"), so hedges
# are only flagged there when the sentence also talks about a measured value.
CLAIM_SECTIONS = {"summary", "key_findings", "tradeoffs"}

MECHANISM = re.compile(
    r"\b(because|since|due to|thanks to|as a (result|consequence)|result(s|ed|ing)? in|"
    r"le(d|ads?) to|lead(ing)? to|so that|thereby|therefore|thus|hence|consequently|"
    r"which (means|meant|caused|allowed|freed|pushed|shifted|moved)|allow(s|ed|ing)?|"
    r"enabl\w+|free[sd]? up|freeing|push(es|ed|ing)?|shift(s|ed|ing)?\b[^.;]{0,30}"
    r"\b(from|to|downstream|onto|elsewhere|toward)|mov(es|ed|ing)\b[^.;]{0,20}\b(downstream|"
    r"elsewhere|onto|toward)|downstream|upstream|bottleneck\w*|congest\w*|pressure|knock-on|"
    r"ripple|spill\w*|cascad\w*|feeds? into|absorb\w*|offset\w*|compensat\w*|"
    r"traded? (one|off)|at the (cost|expense) of|in exchange for|in turn|crowd\w* out|"
    r"translat\w* into|turn(s|ed)? into|carr(y|ies|ied) (over|through)|flow(s|ed)? (through|"
    r"into)|contribut\w*|driv(e|es|en|ing)|produc(e|es|ed|ing)|yield(s|ed)?|"
    r"brought (about|down|up)|bring(s)? (down|up)|explains?|account(s|ed)? for)\b",
    re.IGNORECASE,
)
HEDGED = re.compile(
    r"\b(suggest\w*|indicat\w*|impl(y|ies|ied)|appears?|seems?|likely|unlikely|probabl[ey]|"
    r"possibl[ey]|perhaps|presumabl[ey]|may (reflect|indicate|be due|mean|suggest|point)|"
    r"might|could (reflect|indicate|mean|be|point)|consistent with|points? to|hints?|"
    r"one (possible )?explanation|plausibl\w*|we infer|inferred)\b",
    re.IGNORECASE,
)
MAGNITUDE = re.compile(
    r"\b(slight(ly)?|marginal(ly)?|negligibl[ey]|minor|small(er|est)?|modest(ly)?|tiny|"
    r"trivial|insignificant|barely|mere(ly)?|substantial(ly)?|significant(ly)?|sharp(ly)?|"
    r"dramatic(ally)?|large(r|st)?|huge|massive|major|considerabl[ey]|meaningful(ly)?|"
    r"notabl[ey]|remarkabl[ey]|impressive(ly)?|strong(ly)?|big(ger|gest)?|steep(ly)?|"
    r"vast(ly)?|sizeable|sizable|material(ly)?|noticeabl[ey]|appreciabl[ey]|"
    r"limited|mild(ly)?|moderate(ly)?)\b",
    re.IGNORECASE,
)

FlagKind = Literal[
    "interpretation",
    "magnitude",
    "omitted_tradeoff",
    "unnamed_value",
    "uncited_robustness",
    "omitted_sensitivity",
    "unvetted_wording",
    "rhetorical_question",
]
FLAG_TEXT: dict[str, str] = {
    "interpretation": "goes beyond the cited facts (a mechanism or a hedged inference)",
    "magnitude": "judges the size of a change; the packet gives numbers, not judgments",
    "omitted_tradeoff": "an outcome that moved the opposite way is never mentioned",
    "unnamed_value": "shows a value without naming which outcome it belongs to",
    "uncited_robustness": "claims the result is robust or stable without citing a SENS: fact",
    "omitted_sensitivity": "the focus result depends on uncertain assumptions, but the "
    "sensitivity fact that says so is never cited",
    "unvetted_wording": "uses words outside the controlled vocabulary; a person should check "
    "what they claim",
    "rhetorical_question": "asks a question outside the questions section (questions there can "
    "carry advice)",
}


@dataclass(frozen=True)
class ReviewFlag:
    kind: FlagKind
    loc: str  # "key_findings[1]", "summary", or "response"
    detail: str

    @property
    def message(self) -> str:
        return f"{self.loc}: {FLAG_TEXT[self.kind]} ({self.detail})"


@dataclass(frozen=True)
class ClaimAssessment:
    loc: str
    text: str
    claim_class: ClaimClass
    reasons: tuple[str, ...] = ()


@dataclass
class Assessment:
    verdict: Verdict
    report: VerificationReport
    flags: list[ReviewFlag] = field(default_factory=list)
    claims: list[ClaimAssessment] = field(default_factory=list)

    @property
    def flagged_locs(self) -> set[str]:
        return {f.loc for f in self.flags}


# ---------------------------------------------------------------------------
# Review flags (deterministic signals that need human judgment)
# ---------------------------------------------------------------------------


# "Because the concurrency ASSUMPTION is fictional, ..." gives an epistemic reason
# (why a number is uncertain), not a mechanism (why a number moved).
EPISTEMIC = re.compile(
    r"\b(assum\w*|fictional|simulat\w*|the model\b|limitation\w*|intervals?|uncertain\w*|"
    r"cautions?|sensitivity|illustrative|packet|evidence|reported|listed|appears? in)\b",
    re.IGNORECASE,
)


def _mechanisms(plain: str) -> list[str]:
    hits = []
    for sentence in re.split(r"(?<=[.!?;])\s+", plain):
        for m in MECHANISM.finditer(sentence):
            if EPISTEMIC.search(sentence[m.end() : m.end() + 60]):
                continue
            if not _negated(sentence, m.start()):
                hits.append(m.group(0))
    return hits


def _speaks_about_values(item: Located) -> bool:
    if item.section in CLAIM_SECTIONS:
        return True
    return bool(TOKEN.search(item.text)) or any(
        i.startswith("M:") for i in (item.evidence_ids or ())
    )


def statement_flags(
    packet: EvidencePacket, item: Located, vocab: frozenset[str] | None = None
) -> list[ReviewFlag]:
    """Review flags for one piece of prose (no hard failures are decided here)."""
    flags: list[ReviewFlag] = []
    if vocab is not None and (odd := unvetted(item.text, vocab)):
        flags.append(ReviewFlag("unvetted_wording", item.loc, ", ".join(odd[:5])))
    if item.section != "questions_for_humans" and "?" in TOKEN.sub(" ", item.text):
        flags.append(ReviewFlag("rhetorical_question", item.loc, "'?'"))
    if item.section in ("disclaimer", "questions_for_humans"):
        return flags
    plain = FACT_ID.sub(" ", TOKEN.sub(" value ", item.text))
    if _speaks_about_values(item):
        hits = _mechanisms(plain) or _unnegated(HEDGED, plain)
        if hits:
            flags.append(ReviewFlag("interpretation", item.loc, f"'{hits[0]}'"))
        if hits := _unnegated(MAGNITUDE, plain):
            flags.append(ReviewFlag("magnitude", item.loc, f"'{hits[0]}'"))
    prose = _outcome_text(item.text, packet)
    if not families_in(TOKEN.sub(" ", prose)):
        for fid, _, _ in token_segments(prose):
            fact = packet.fact(fid)
            if isinstance(fact, MetricFact) and METRIC_FAMILIES.get(fact.metric):
                flags.append(ReviewFlag("unnamed_value", item.loc, fid))
                break
    if _unnegated(ROBUST_WORDS, plain) and not any(
        i.startswith("SENS:") for i in (item.evidence_ids or ())
    ):
        flags.append(ReviewFlag("uncited_robustness", item.loc, "no SENS: fact cited"))
    return flags


def response_flags(packet: EvidencePacket, items: list[Located]) -> list[ReviewFlag]:
    vocab = packet_vocabulary(packet)
    flags = [f for item in items for f in statement_flags(packet, item, vocab)]
    cited = {i for item in items for i in (item.evidence_ids or ())}
    cited |= {m.group(1) for item in items for m in TOKEN.finditer(item.text)}
    opposite = opposing_facts(packet)
    if opposite and not any(f.id in cited for f in opposite):
        names = ", ".join(f.id for f in opposite[:4])
        flags.append(ReviewFlag("omitted_tradeoff", "response", names))
    sens = packet.fact(f"SENS:{packet.focus_metric}")
    if (
        sens is not None
        and SENS_UNSTABLE.search(getattr(sens, "text", ""))
        and sens.id not in cited
    ):
        flags.append(ReviewFlag("omitted_sensitivity", "response", sens.id))
    return flags


# ---------------------------------------------------------------------------
# Claim classes
# ---------------------------------------------------------------------------

_CAUSAL_MARKERS = ("overclaims certainty or a cause",)
_REAL_MARKERS = ("future/implementation", "forecast without model context")
_CONTRADICTION_CHECKS = (
    "evidence_ids",
    "value_tokens",
    "numbers_and_units",
    "direction",
    "claim_alignment",
)


def classify(packet: EvidencePacket, item: Located) -> ClaimAssessment:
    """Claim class for one statement, from the same rules the verifier uses."""
    outs = new_checks()
    check_text(packet, item, outs, _approved(list(packet.facts)))
    real = outs["real_world_claims"].problems + outs["recommendations_and_safety"].problems
    real += [p for p in outs["uncertainty_wording"].problems if any(m in p for m in _REAL_MARKERS)]
    causal = [
        p for p in outs["uncertainty_wording"].problems if any(m in p for m in _CAUSAL_MARKERS)
    ]
    misread = [
        p
        for p in outs["uncertainty_wording"].problems
        if p not in causal and not any(m in p for m in _REAL_MARKERS)
    ]
    wrong = [p for name in _CONTRADICTION_CHECKS for p in outs[name].problems] + misread
    if real:
        return ClaimAssessment(item.loc, item.text, "REAL_WORLD_OR_CLINICAL", tuple(real))
    if wrong:
        return ClaimAssessment(item.loc, item.text, "CONTRADICTED", tuple(wrong))
    if causal:
        return ClaimAssessment(item.loc, item.text, "UNSUPPORTED_CAUSAL", tuple(causal))
    soft = [
        f.message
        for f in statement_flags(packet, item, packet_vocabulary(packet))
        if f.kind not in ("unnamed_value", "unvetted_wording")
    ]
    if soft:
        return ClaimAssessment(item.loc, item.text, "PLAUSIBLE_INTERPRETATION", tuple(soft))
    return ClaimAssessment(item.loc, item.text, "SUPPORTED_DESCRIPTION")


def claim_items(items: list[Located]) -> list[Located]:
    return [i for i in items if i.section not in ("questions_for_humans", "disclaimer")]


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------


def assess(packet: EvidencePacket, raw: str) -> Assessment:
    """Verify, then flag what needs a human. REJECT > HUMAN_REVIEW > ACCEPT."""
    report = verify(packet, raw)
    if report.interpretation is None:
        return Assessment("REJECT", report)
    items = located_texts(report.interpretation)
    claims = [classify(packet, i) for i in claim_items(items)]
    if not report.passed:
        return Assessment("REJECT", report, claims=claims)
    flags = response_flags(packet, items)
    return Assessment("HUMAN_REVIEW" if flags else "ACCEPT", report, flags, claims)


def assess_data(packet: EvidencePacket, data: object) -> Assessment:
    """assess() for an already-parsed candidate (dict) or raw string."""
    return assess(packet, data if isinstance(data, str) else json.dumps(data))


__all__ = [
    "CLAIM_CLASSES",
    "VERDICTS",
    "Assessment",
    "ClaimAssessment",
    "ReviewFlag",
    "assess",
    "assess_data",
    "classify",
    "parse",
]
