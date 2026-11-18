"""MOCK dry run: a scripted, offline stand-in for Claude that exercises every path.

Generation 1 of each packet is the built-in mock explanation. Generation 2 is a
hand-written evaluation case (or a simulated API error), chosen so the dry run
shows an ACCEPT, a HUMAN_REVIEW, a REJECT, and an error. None of this is model
output; every record is labeled MOCK.
"""

from __future__ import annotations

from beyond_the_wait.evaluation.dataset import Dataset, load_dataset
from beyond_the_wait.interpretation.evidence import EvidencePacket
from beyond_the_wait.interpretation.providers import (
    InterpretationError,
    MockProvider,
    Mode,
    ProviderResponse,
)

DRY_RUN_MODEL = "dry-run-script-v1 (MOCK, not Claude)"
# packet -> what generation 2 returns: an evaluation case id, or ("error", kind)
SECOND_GENERATION: dict[str, str | tuple[str, str]] = {
    "triage_nurse": "dev-020",  # plausible mechanism -> HUMAN_REVIEW expected
    "night_physician": "dev-037",  # real-world generalization -> REJECT expected
    "bed_releases": "dev-025",  # magnitude judgment ('dramatic') -> HUMAN_REVIEW expected
    "evening_physician": ("error", "truncated"),  # simulated cut-off answer
    "diagnostics": "dev-030",  # 'root cause' overclaim -> REJECT expected
}


class DryRunProvider:
    mode: Mode = "MOCK"
    model: str = DRY_RUN_MODEL

    def __init__(self, names: dict[str, str], dataset: Dataset | None = None) -> None:
        """`names` maps packet_id -> packet name."""
        self._names = names
        self._dataset = dataset or load_dataset(("dev",))
        self._calls: dict[str, int] = {}

    def interpret(self, packet: EvidencePacket) -> ProviderResponse:
        name = self._names[packet.packet_id]
        self._calls[name] = generation = self._calls.get(name, 0) + 1
        if generation == 1 or name not in SECOND_GENERATION:
            text = MockProvider().interpret(packet).text
        else:
            action = SECOND_GENERATION[name]
            if isinstance(action, tuple):
                raise InterpretationError(action[1])  # type: ignore[arg-type]
            case = next(c for c in self._dataset.cases if c.id == action)
            text = self._dataset.candidate(case)
        return ProviderResponse(text, "MOCK", self.model, stop_reason="end_turn")
