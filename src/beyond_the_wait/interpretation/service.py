"""One explanation, end to end: provider -> local verification -> outcome.

The interpretation is handed to the UI ONLY when every verification check
passes. Rejected or failed responses produce a readable fallback message and
are not displayed, logged, or saved.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from beyond_the_wait.interpretation.evidence import EvidencePacket
from beyond_the_wait.interpretation.providers import InterpretationError, Mode, Provider
from beyond_the_wait.interpretation.schema import Interpretation
from beyond_the_wait.interpretation.verify import VerificationReport, verify

Status = Literal["verified", "rejected", "error"]


@dataclass(frozen=True)
class Outcome:
    status: Status
    mode: Mode
    model: str
    packet_id: str
    interpretation: Interpretation | None = None  # set only when status == "verified"
    report: VerificationReport | None = None
    message: str = ""
    usage: dict[str, int] | None = None


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
    report = verify(packet, response.text)
    if not report.passed:
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
    return Outcome(
        "verified",
        response.mode,
        response.model,
        packet.packet_id,
        interpretation=report.interpretation,
        report=report,
        usage=response.usage,
    )
