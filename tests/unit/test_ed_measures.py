"""Tests for the measure catalog: different ED measures must stay distinct."""

from __future__ import annotations

import pytest

from beyond_the_wait.data.ed_measures import (
    CMS_ED_MEASURE_IDS,
    MEASURES,
    MeasureKind,
    MeasureNotAvailableError,
    Unit,
    measures_of_kind,
)


def test_cms_ed_measures_cataloged() -> None:
    assert set(CMS_ED_MEASURE_IDS) == {"EDV", "OP_18a", "OP_18b", "OP_18c", "OP_18d", "OP_22"}


def test_length_of_stay_and_lwbs_are_different_kinds_and_units() -> None:
    los = {m.measure_id for m in measures_of_kind(MeasureKind.ED_LENGTH_OF_STAY_DISCHARGED)}
    lwbs = {m.measure_id for m in measures_of_kind(MeasureKind.LEFT_WITHOUT_BEING_SEEN)}
    assert los == {"OP_18a", "OP_18b", "OP_18c", "OP_18d"}
    assert lwbs == {"OP_22"}
    assert all(MEASURES[m].unit is Unit.MINUTES for m in los)
    assert MEASURES["OP_22"].unit is Unit.PERCENT


@pytest.mark.parametrize(
    "kind", [MeasureKind.DOOR_TO_PROVIDER, MeasureKind.ADMITTED_PATIENT_BOARDING_TIME]
)
def test_unavailable_kinds_raise_with_explanation(kind: MeasureKind) -> None:
    with pytest.raises(MeasureNotAvailableError, match="not|No reviewed"):
        measures_of_kind(kind)


def test_door_to_provider_error_warns_against_substitution() -> None:
    with pytest.raises(MeasureNotAvailableError, match="must not be used as substitutes"):
        measures_of_kind(MeasureKind.DOOR_TO_PROVIDER)


def test_op18_documents_that_it_excludes_admitted_patients() -> None:
    for measure_id in ("OP_18a", "OP_18b", "OP_18c", "OP_18d"):
        m = MEASURES[measure_id]
        assert "admitted" in m.population.lower() or "admission" in m.population.lower()
        assert any("boarding" in item for item in m.does_not_measure)


def test_every_measure_states_limits_and_a_source() -> None:
    for m in MEASURES.values():
        assert m.does_not_measure, m.measure_id
        assert m.definition_source.startswith("https://"), m.measure_id


def test_nys_admitted_count_is_not_a_time_measure() -> None:
    admitted = MEASURES["NYS_ED_ADMITTED"]
    assert admitted.kind is MeasureKind.ED_ENCOUNTER_COUNT
    assert admitted.unit is Unit.ENCOUNTERS
    assert "says nothing about how long" in admitted.definition
