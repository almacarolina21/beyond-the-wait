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

import pandas as pd
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


# ---------------------------------------------------------------------------
# Provenance on tables (pandas DataFrames)
# ---------------------------------------------------------------------------
#
# Design choice: provenance is stored as ordinary COLUMNS on every row, not
# in DataFrame metadata (`df.attrs`). Columns survive filtering, merging,
# concatenation, and CSV export; `attrs` can be silently dropped by those
# operations. A row that travels anywhere carries its label with it.

PROVENANCE_COLUMNS: tuple[str, ...] = (
    "provenance",
    "source_dataset_id",
    "source_url",
    "retrieved_on",
)


def tag_dataframe(
    df: pd.DataFrame,
    *,
    label: Provenance | str,
    dataset_id: str,
    source_url: str,
    retrieved_on: date | None,
) -> pd.DataFrame:
    """Return a copy of `df` with provenance columns filled in on every row."""
    parsed = parse_provenance(label)
    for name, value in (("dataset_id", dataset_id), ("source_url", source_url)):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty string")
    if parsed is Provenance.PUBLIC and retrieved_on is None:
        raise ValueError("PUBLIC data must include retrieved_on (the download date).")
    out = df.copy()
    out["provenance"] = parsed.value
    out["source_dataset_id"] = dataset_id
    out["source_url"] = source_url
    out["retrieved_on"] = retrieved_on.isoformat() if retrieved_on else ""
    return out


def require_provenance(df: pd.DataFrame, expected: Provenance | str) -> None:
    """Raise ValueError unless every row carries the expected provenance label.

    For PUBLIC data, every row must also name its source dataset, URL, and
    retrieval date.
    """
    expected_label = parse_provenance(expected)
    missing = [col for col in PROVENANCE_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"table is missing provenance column(s): {missing}")
    found = set(df["provenance"].astype(str).unique())
    unexpected = sorted(found - {expected_label.value})
    if unexpected:
        raise ValueError(
            f"expected every row to be {expected_label.value}; also found {unexpected}"
        )
    if expected_label is Provenance.PUBLIC:
        for col in ("source_dataset_id", "source_url", "retrieved_on"):
            blank = df[col].isna() | (df[col].astype(str).str.strip() == "")
            if blank.any():
                raise ValueError(f"PUBLIC rows must have a non-empty {col!r}")
