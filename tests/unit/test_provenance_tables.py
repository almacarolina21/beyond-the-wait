"""Tests for provenance columns on tables (tag_dataframe / require_provenance)."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from beyond_the_wait.data.provenance import (
    PROVENANCE_COLUMNS,
    Provenance,
    require_provenance,
    tag_dataframe,
)


def _public(df: pd.DataFrame) -> pd.DataFrame:
    return tag_dataframe(
        df,
        label="PUBLIC",
        dataset_id="yv7e-xc69",
        source_url="https://data.cms.gov/example.csv",
        retrieved_on=date(2026, 11, 15),
    )


def test_tag_adds_columns_without_modifying_input() -> None:
    original = pd.DataFrame({"x": [1, 2]})
    tagged = _public(original)
    assert list(original.columns) == ["x"]
    assert set(PROVENANCE_COLUMNS) <= set(tagged.columns)
    assert (tagged["provenance"] == "PUBLIC").all()
    assert (tagged["retrieved_on"] == "2026-11-15").all()


def test_provenance_survives_filter_concat_and_csv(tmp_path) -> None:
    tagged = _public(pd.DataFrame({"x": [1, 2, 3]}))
    combined = pd.concat([tagged[tagged["x"] > 1], tagged.head(1)])
    path = tmp_path / "t.csv"
    combined.to_csv(path, index=False)
    reloaded = pd.read_csv(path, dtype=str)
    require_provenance(reloaded, Provenance.PUBLIC)


def test_require_rejects_mixed_labels() -> None:
    public = _public(pd.DataFrame({"x": [1]}))
    synthetic = tag_dataframe(
        pd.DataFrame({"x": [2]}),
        label="SYNTHETIC",
        dataset_id="generator",
        source_url="beyond_the_wait",
        retrieved_on=None,
    )
    with pytest.raises(ValueError, match="also found \\['SYNTHETIC'\\]"):
        require_provenance(pd.concat([public, synthetic]), "PUBLIC")


def test_require_rejects_missing_columns() -> None:
    with pytest.raises(ValueError, match="missing provenance column"):
        require_provenance(pd.DataFrame({"x": [1]}), "PUBLIC")


def test_public_rows_need_source_details() -> None:
    tagged = _public(pd.DataFrame({"x": [1, 2]}))
    tagged.loc[1, "source_url"] = ""
    with pytest.raises(ValueError, match="source_url"):
        require_provenance(tagged, "PUBLIC")


def test_public_tag_requires_retrieval_date() -> None:
    with pytest.raises(ValueError, match="retrieved_on"):
        tag_dataframe(
            pd.DataFrame({"x": [1]}),
            label="PUBLIC",
            dataset_id="d",
            source_url="u",
            retrieved_on=None,
        )


@pytest.mark.parametrize(("dataset_id", "source_url"), [("", "u"), ("d", "  ")])
def test_tag_requires_source_identifiers(dataset_id: str, source_url: str) -> None:
    with pytest.raises(ValueError, match="non-empty"):
        tag_dataframe(
            pd.DataFrame({"x": [1]}),
            label="SYNTHETIC",
            dataset_id=dataset_id,
            source_url=source_url,
            retrieved_on=None,
        )


def test_tag_rejects_unsupported_label() -> None:
    with pytest.raises(ValueError, match="Unsupported provenance label"):
        tag_dataframe(
            pd.DataFrame({"x": [1]}),
            label="REAL",
            dataset_id="d",
            source_url="u",
            retrieved_on=date(2026, 1, 1),
        )
