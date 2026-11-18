"""The structured response Claude must return (and the mock provider imitates).

Claude's JSON is parsed into these models, but passing this schema only
means the response has the right SHAPE. Whether its content is grounded is
decided separately by `verify.py`.

Size limits (max lengths, max list sizes) are enforced here in Python. The
API's JSON-schema feature does not support length constraints, so the SDK's
`transform_schema()` moves them into field descriptions; this module still
rejects an over-long response.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

MAX_STATEMENT = 600
MAX_IDS = 8


class Statement(BaseModel):
    """One sentence or short paragraph, tied to the packet facts that support it."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(
        max_length=MAX_STATEMENT,
        description="Plain language. Write measured values only as value tokens "
        "such as {{M:boarding_p90.change}}; never type digits for measured values.",
    )
    evidence_ids: list[str] = Field(
        min_length=1,
        max_length=MAX_IDS,
        description="IDs of the packet facts that support this statement (for example "
        "'M:boarding_p90', 'CK:overload_regime', 'LIM:fictional_model').",
    )


class Interpretation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(
        max_length=900,
        description="Two to four sentences: what the fictional model shows for this change.",
    )
    key_findings: list[Statement] = Field(min_length=1, max_length=6)
    tradeoffs: list[Statement] = Field(
        max_length=4,
        description="Operational tradeoffs INSIDE the fictional model, e.g. waiting that moves "
        "from one stage to another. Empty if the packet shows none.",
    )
    uncertainties: list[Statement] = Field(
        min_length=1,
        max_length=5,
        description="Important uncertainties and assumptions, citing CK:, SENS:, RUN: or LIM: "
        "facts.",
    )
    questions_for_humans: list[str] = Field(
        min_length=1,
        max_length=5,
        description="Questions qualified people would need to investigate with real data. "
        "Questions, not advice.",
    )
    limitations: list[Statement] = Field(min_length=1, max_length=5)
    disclaimer: str = Field(
        max_length=400,
        description="States that this is a fictional simulation for human review and not a "
        "recommendation or clinical guidance.",
    )

    def statements(self) -> list[tuple[str, Statement]]:
        """(section, statement) pairs for every cited statement."""
        out: list[tuple[str, Statement]] = []
        for section in ("key_findings", "tradeoffs", "uncertainties", "limitations"):
            out.extend((section, s) for s in getattr(self, section))
        return out

    def free_texts(self) -> list[tuple[str, str]]:
        """(section, text) for every piece of prose, cited or not."""
        texts = [("summary", self.summary), ("disclaimer", self.disclaimer)]
        texts += [(sec, s.text) for sec, s in self.statements()]
        texts += [("questions_for_humans", q) for q in self.questions_for_humans]
        return texts
