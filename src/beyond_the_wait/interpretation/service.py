"""One explanation, end to end: provider -> local verification -> outcome.

The interpretation is handed to the UI ONLY when every verification check
passes. Rejected or failed responses produce a readable fallback message and
are not displayed, logged, or saved.

A verified response is then assessed for claim faithfulness (Milestone 9):
"verified" means no review flag was raised; "needs_review" means it passed
every check but contains wording that a person must judge (a mechanism, a
magnitude judgment, a possibly omitted tradeoff). The UI shows those flags
only as reasons: the public dashboard withholds needs_review text entirely
(see dashboard.ai_panel.public_prose_allowed).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from beyond_the_wait.interpretation.evidence import EvidencePacket
from beyond_the_wait.interpretation.faithfulness import ReviewFlag, assess
from beyond_the_wait.interpretation.providers import InterpretationError, Mode, Provider
from beyond_the_wait.interpretation.schema import Interpretation
from beyond_the_wait.interpretation.verify import VerificationReport

Status = Literal["verified", "needs_review", "rejected", "error"]


@dataclass(frozen=True)
class Outcome:
    status: Status
    mode: Mode
    model: str
    packet_id: str
    interpretation: Interpretation | None = None  # set only when every check passed
    report: VerificationReport | None = None
    message: str = ""
    usage: dict[str, int] | None = None
    flags: tuple[ReviewFlag, ...] = ()


def explain(packet: EvidencePacket, provider: Provider) -> Outcome:
    """Ask the provider once, verify locally, and return a safe outcome."""
    try:
        response = provider.interpret(packet)
    except InterpretationError as exc:
        return Outcome(
            "error", provider.mode, provider.model, packet.packet_id, message=exc.user_message
        )
    except Exception:  # any other failure: no crash, no partial output, no details leaked
        return Outcome(
            "error",
            provider.mode,
            provider.model,
            packet.packet_id,
            message="An unexpected error occurred while preparing the explanation.",
        )
    assessment = assess(packet, response.text)
    report = assessment.report
    if assessment.verdict == "REJECT":
        return Outcome(
            "rejected",
            response.mode,
            response.model,
            packet.packet_id,
            report=report,
            message="The explanation failed local verification and was not shown. The "
            "charts and tables above are unaffected.",
            usage=response.usage,
        )
    review = assessment.verdict == "HUMAN_REVIEW"
    return Outcome(
        "needs_review" if review else "verified",
        response.mode,
        response.model,
        packet.packet_id,
        interpretation=report.interpretation,
        report=report,
        message="Passed every local check, but some wording needs human review before it is "
        "relied on."
        if review
        else "",
        usage=response.usage,
        flags=tuple(assessment.flags),
    )
