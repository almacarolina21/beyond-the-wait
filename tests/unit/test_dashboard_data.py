"""Tests for the dashboard's data layer (no Streamlit needed)."""

from __future__ import annotations

import dataclasses
import shutil
from pathlib import Path

import pandas as pd
import pytest

from beyond_the_wait.analysis.results_bundle import load_bundle
from beyond_the_wait.dashboard import data as d
from beyond_the_wait.data.provenance import Provenance, require_provenance
from tests.paths import PROCESSED_DIR


@pytest.fixture(scope="module")
def public() -> d.PublicData:
    load = d.load_public_data(PROCESSED_DIR)
    assert load.status == "ok"
    return load.data


# --- PUBLIC -----------------------------------------------------------------------------------


def test_public_data_is_verified_public(public: d.PublicData) -> None:
    require_provenance(public.measures, Provenance.PUBLIC)
    require_provenance(public.facilities, Provenance.PUBLIC)


def test_missing_and_invalid_public_data(tmp_path: Path) -> None:
    assert d.load_public_data(tmp_path).status == "missing"
    for name in ("bronx_ed_measures.csv", "bronx_facilities.csv", "build_manifest.json"):
        shutil.copy(PROCESSED_DIR / name, tmp_path / name)
    m = pd.read_csv(tmp_path / "bronx_ed_measures.csv", dtype=str, keep_default_na=False)
    m.assign(provenance="SIMULATED").to_csv(tmp_path / "bronx_ed_measures.csv", index=False)
    load = d.load_public_data(tmp_path)
    assert load.status == "invalid" and "verification" in load.problems[0]


def test_unavailable_values_are_text_never_zero(public: d.PublicData) -> None:
    rows = d.cms_measure_rows(public.measures)
    na = rows[rows["status"] != "reported"]
    assert len(na) > 0
    assert na["display"].str.startswith("Not available").all()
    assert na["display"].str.contains("footnote 1").all()
    assert na["value"].isna().all()


def test_display_units(public: d.PublicData) -> None:
    rows = d.cms_measure_rows(public.measures).set_index(["facility_id", "measure_id"])
    assert rows.loc[("330080", "OP_18b"), "display"] == "228 min"
    assert rows.loc[("330080", "OP_22"), "display"] == "9%"
    assert rows.loc[("330080", "EDV"), "display"] == "medium"


def test_reporting_periods_are_explicit_and_differ(public: d.PublicData) -> None:
    periods = d.reporting_periods(public.measures).set_index("measure_id")["period"]
    assert periods["OP_18b"] == "CY 2025" and periods["OP_22"] == "CY 2024"
    wide = d.cms_wide_table(public.measures)
    assert any("CY 2025" in c for c in wide.columns) and any("CY 2024" in c for c in wide.columns)
    assert len(wide) == 5


def test_measures_stay_distinct(public: d.PublicData) -> None:
    rows = d.cms_measure_rows(public.measures)
    assert set(rows.loc[rows["measure_id"].str.startswith("OP_18"), "unit"]) == {"minutes"}
    assert set(rows.loc[rows["measure_id"] == "OP_22", "unit"]) == {"percent"}
    nys = d.nys_rows(public.measures, year=2025)
    assert set(nys["unit"]) == {"encounters"}


def test_suppressed_values_preserved(public: d.PublicData) -> None:
    q = d.nys_rows(public.measures, year=2025, period_type="quarter")
    s = q[q["status"] == "suppressed"]
    assert len(s) > 0 and (s["display"] == "Suppressed (S)").all() and s["value"].isna().all()


@pytest.mark.parametrize(
    ("start", "end", "text"),
    [
        ("2025-01-01", "2025-12-31", "CY 2025"),
        ("2025-04-01", "2025-06-30", "Apr 01, 2025 – Jun 30, 2025"),
    ],
)
def test_period_text(start: str, end: str, text: str) -> None:
    assert d.period_text(start, end) == text


def test_definitions_and_crosswalk(public: d.PublicData) -> None:
    defs = d.measure_definitions(["OP_18b", "OP_22"])
    assert "boarding" in defs.set_index("Measure").loc["OP_18b", "Does NOT measure"]
    cw = d.crosswalk_view(public.facilities)
    assert set(cw["Status"]) == {"matched", "excluded", "nys_only"}


# --- Fictional site and live run -----------------------------------------------------------------


def test_site_summary() -> None:
    s = d.site_summary(d.load_site())
    staffing = s["staffing"].set_index("Shift")
    assert staffing.loc["night", "Physician slots (x concurrency)"] == 4
    assert s["acuity"]["Share of arrivals"].sum() == pytest.approx(1.0)
    assert "Uncertain assumption" in set(s["assumptions"]["Type"])


def test_run_flow_releases_validated_tables() -> None:
    snap = d.run_flow(42, 3)
    assert not snap.blocked and snap.report.status.value == "CAUTION"
    assert {"acuity_metrics", "shift_utilization", "stage_decomposition"} <= set(snap.tables)
    require_provenance(snap.arrivals, Provenance.SYNTHETIC)
    require_provenance(snap.boarding, Provenance.SIMULATED)


def test_run_flow_cap() -> None:
    with pytest.raises(ValueError, match="between 2 and 14"):
        d.run_flow(1, 30)


def test_run_flow_blocked_returns_no_tables(monkeypatch: pytest.MonkeyPatch) -> None:
    real = d.run_simulation

    def corrupt(site, settings):  # noqa: ANN001, ANN202
        r = real(site, settings)
        return dataclasses.replace(r, patients=r.patients.assign(provenance="PUBLIC"))

    monkeypatch.setattr(d, "run_simulation", corrupt)
    snap = d.run_flow(42, 3)
    assert snap.blocked and snap.tables == {} and snap.arrivals is None


# --- Precomputed results helpers ---------------------------------------------------------------


@pytest.fixture(scope="module")
def bundle(small_bundle_dir: Path):  # noqa: ANN201
    return load_bundle(small_bundle_dir).bundle


def test_results_dir_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BTW_RESULTS_DIR", str(tmp_path))
    assert d.results_dir() == tmp_path and d.load_results().status == "missing"


def test_comparison_helpers(bundle) -> None:  # noqa: ANN001
    rows = d.comparison_rows(bundle, scope="main", metric="arrival_to_provider_p90")
    assert len(rows) == 5 and rows["scenario_name"].str.len().min() > 3
    detail = d.comparison_rows(bundle, scope="main", scenario_id="add_night_physician")
    assert set(detail["scenario_id"]) == {"add_night_physician"}
    assert d.display_scale("fraction") == (100.0, "percentage points")
    assert d.display_scale("min") == (1.0, "min")
    meta = d.comparison_meta(bundle, scope="main", scenario_id="add_night_physician")
    assert meta["status"] == "CAUTION" and meta["effects"]["simulated"]
    assert d.comparison_meta(bundle, scope="main", scenario_id="nope") == {}
    checks = d.comparison_checks(bundle, scope="main", scenario_id="add_night_physician")
    assert len(checks) > 0 and (checks["status"] != "PASS").all()
    all_checks = d.comparison_checks(
        bundle, scope="main", scenario_id="add_night_physician", include_pass=True
    )
    assert len(all_checks) >= len(checks)


def test_stage_effects_and_congestion(bundle) -> None:  # noqa: ANN001
    stages = d.stage_effects(bundle, scope="main", scenario_id="add_night_triage_nurse")
    assert list(stages["stage"])[0] == "Waiting for triage"
    assert d.moves_congestion(pd.DataFrame({"direction": ["improves", "worsens"]}))
    assert not d.moves_congestion(pd.DataFrame({"direction": ["improves", "inconclusive"]}))


def test_sensitivity_grid(bundle) -> None:  # noqa: ANN001
    grid = d.sensitivity_grid(bundle, intervention="increase_bed_releases", metric="boarding_p90")
    assert len(grid) == 9
    empty = dataclasses.replace(bundle, sensitivity=None)
    assert d.sensitivity_grid(empty, intervention="x", metric="y").empty


def test_scenario_names() -> None:
    names = d.scenario_names()
    assert names["baseline"] == "Baseline" and names["increase_bed_releases"].startswith("Six more")
