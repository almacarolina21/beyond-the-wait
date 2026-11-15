"""Tests for provenance labels (PUBLIC / SYNTHETIC / SIMULATED)."""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from beyond_the_wait.data.provenance import (
    SUPPORTED_LABELS,
    Provenance,
    ProvenanceRecord,
    parse_provenance,
)


def test_exactly_three_labels_exist() -> None:
    assert SUPPORTED_LABELS == ("PUBLIC", "SYNTHETIC", "SIMULATED")


@pytest.mark.parametrize("label", ["PUBLIC", "SYNTHETIC", "SIMULATED"])
def test_supported_labels_parse(label: str) -> None:
    parsed = parse_provenance(label)
    assert parsed is Provenance(label)
    assert parsed.description  # every label has a display description


def test_parse_accepts_existing_enum_member() -> None:
    assert parse_provenance(Provenance.SIMULATED) is Provenance.SIMULATED


UNSUPPORTED = ["public", "Synthetic", "REAL", "PHI", "", " PUBLIC", None, 1, True]


@pytest.mark.parametrize("bad", UNSUPPORTED)
def test_unsupported_labels_are_rejected(bad: object) -> None:
    with pytest.raises(ValueError, match="Unsupported provenance label"):
        parse_provenance(bad)


def test_record_rejects_unsupported_label() -> None:
    with pytest.raises(ValidationError, match="Unsupported provenance label"):
        ProvenanceRecord(label="ACTUAL", source="Somewhere plausible")


def test_record_accepts_synthetic_without_date() -> None:
    record = ProvenanceRecord(label="SYNTHETIC", source="Assumptions written for tests.")
    assert record.label is Provenance.SYNTHETIC
    assert record.retrieved_on is None


def test_public_record_requires_retrieval_date() -> None:
    with pytest.raises(ValidationError, match="retrieved_on"):
        ProvenanceRecord(label="PUBLIC", source="CMS Care Compare download")


def test_public_record_with_retrieval_date_is_valid() -> None:
    record = ProvenanceRecord(
        label="PUBLIC", source="CMS Care Compare download", retrieved_on=date(2026, 11, 15)
    )
    assert record.label is Provenance.PUBLIC


@pytest.mark.parametrize("source", ["", "   ", "short"])
def test_record_requires_meaningful_source(source: str) -> None:
    with pytest.raises(ValidationError):
        ProvenanceRecord(label="SYNTHETIC", source=source)


def test_record_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ProvenanceRecord(label="SYNTHETIC", source="Assumptions for tests.", verified=True)


def test_record_is_immutable() -> None:
    record = ProvenanceRecord(label="SYNTHETIC", source="Assumptions written for tests.")
    with pytest.raises(ValidationError):
        record.label = Provenance.PUBLIC  # type: ignore[misc]
