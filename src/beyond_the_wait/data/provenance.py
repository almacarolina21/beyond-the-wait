"""Provenance labels: where a piece of data came from.

Every dataset, configuration, and metric in this project carries exactly one
of three labels. Keeping the label attached to the data is how we make sure
real public figures are never confused with made-up or simulated ones.

    PUBLIC     Official, publicly released aggregate data (e.g., a hospital's
               reported median ED time). Real, but describes *what* happened,
               not *why*.
    SYNTHETIC  Data or assumptions we wrote or generated ourselves (fictional
               site configurations, synthetic patients). No real person or
               hospital is described.
    SIMULATED  Outputs of our simulation model run on synthetic inputs.

Design note: the label set is deliberately closed. An unrecognized label
(including a lowercase spelling such as "public") is rejected rather than
guessed at, because silently mislabeling data is worse than failing loudly.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator


class Provenance(StrEnum):
    """The three supported provenance labels."""

    PUBLIC = "PUBLIC"
    SYNTHETIC = "SYNTHETIC"
    SIMULATED = "SIMULATED"

    @property
    def description(self) -> str:
        """Plain-language meaning of the label, suitable for display."""
        return _DESCRIPTIONS[self]


_DESCRIPTIONS: dict[Provenance, str] = {
    Provenance.PUBLIC: "Official, publicly released aggregate data.",
    Provenance.SYNTHETIC: "Fictional data or assumptions created for this project.",
    Provenance.SIMULATED: "Output of the simulation model run on synthetic inputs.",
}

SUPPORTED_LABELS: tuple[str, ...] = tuple(label.value for label in Provenance)


def parse_provenance(value: object) -> Provenance:
    """Convert a value to a Provenance label, or raise ValueError.

    Accepts an existing Provenance or an exact, uppercase label string.
    Anything else -- wrong case, unknown words, numbers, None -- is rejected.
    """
    if isinstance(value, Provenance):
        return value
    if isinstance(value, str) and value in SUPPORTED_LABELS:
        return Provenance(value)
    raise ValueError(
        f"Unsupported provenance label {value!r}. "
        f"Supported labels (exact, uppercase): {', '.join(SUPPORTED_LABELS)}."
    )


# A Pydantic field type that runs parse_provenance() before normal validation,
# so every model that uses it gets the same strict behavior and clear message.
ProvenanceLabel = Annotated[Provenance, BeforeValidator(parse_provenance)]


class ProvenanceRecord(BaseModel):
    """A provenance label plus a note on where the data came from.

    Example (YAML):
        provenance:
          label: SYNTHETIC
          source: "Illustrative assumptions written for this project."
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    label: ProvenanceLabel
    source: Annotated[str, Field(min_length=10, description="Where the data came from.")]
    retrieved_on: date | None = Field(
        default=None,
        description="Date public data was downloaded. Required when label is PUBLIC.",
    )

    @model_validator(mode="after")
    def _public_data_needs_retrieval_date(self) -> ProvenanceRecord:
        # Public datasets are revised over time, so we must record *when* we
        # captured them for results to be reproducible and citable.
        if self.label is Provenance.PUBLIC and self.retrieved_on is None:
            raise ValueError("PUBLIC data must include 'retrieved_on' (the download date).")
        return self
