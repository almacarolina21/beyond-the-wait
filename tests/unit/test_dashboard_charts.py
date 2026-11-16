"""Tests for chart builders: provenance in titles, honest encodings, empty inputs."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import pytest

from beyond_the_wait.analysis.results_bundle import load_bundle
from beyond_the_wait.dashboard import charts
from beyond_the_wait.dashboard import data as d
from tests.paths import PROCESSED_DIR

REAL_NAMES = ("lincoln", "barnabas", "jacobi", "montefiore", "bronxcare")


def _title(fig: go.Figure) -> str:
    return fig.layout.title.text


@pytest.fixture(scope="module")
def snap():  # noqa: ANN201
    return d.run_flow(42, 4)


@pytest.fixture(scope="module")
def bundle(small_bundle_dir: Path):  # noqa: ANN201
    return load_bundle(small_bundle_dir).bundle


@pytest.fixture(scope="module")
def public_rows() -> pd.DataFrame:
    return d.cms_measure_rows(d.load_public_data(PROCESSED_DIR).data.measures)


def test_public_chart_is_public_and_labels_unavailable(public_rows: pd.DataFrame) -> None:
    fig = charts.public_measure_bar(public_rows, measure_id="OP_18d", unit_label="Median minutes")
    assert _title(fig).startswith("<b>PUBLIC</b>") and "CY 2025" in fig.layout.title.subtitle.text
    notes = [a.text for a in fig.layout.annotations]
    assert any("Not available" in t for t in notes)
    assert all(v == 0 for v in fig.data[1].x)  # placeholder rows only, no fabricated values
    assert len(fig.data[0].x) == 0  # nothing reported for OP_18d


def test_public_reported_values_match_source(public_rows: pd.DataFrame) -> None:
    fig = charts.public_measure_bar(public_rows, measure_id="OP_18b", unit_label="Median minutes")
    values = dict(zip(fig.data[0].y, fig.data[0].x, strict=True))
    assert values["LINCOLN MEDICAL & MENTAL HEALTH CENTER"] == 228


def test_nys_chart(public_rows: pd.DataFrame) -> None:
    nys = d.nys_rows(d.load_public_data(PROCESSED_DIR).data.measures, year=2025)
    fig = charts.nys_counts_bar(nys, measure_id="NYS_ED_ADMITTED", year=2025)
    assert (
        _title(fig).startswith("<b>PUBLIC</b>") and fig.layout.xaxis.title.text == "ED encounters"
    )


def test_simulated_charts_never_claim_public_and_name_no_real_hospital(snap) -> None:  # noqa: ANN001
    t = snap.tables
    figs = [
        charts.stage_time_bars(t["stage_decomposition"]),
        charts.esi_outcome_dots(t["acuity_metrics"], measure="los_min", title="LOS"),
        charts.shift_utilization_bars(t["shift_utilization"]),
        charts.hourly_line(
            t["hourly_profile"], column="physician_utilization", title="U", yaxis="%", pct=True
        ),
        charts.boarding_box(snap.boarding),
    ]
    for fig in figs:
        assert _title(fig).startswith("<b>SIMULATED</b>")
        assert not any(n in fig.to_json().lower() for n in REAL_NAMES)
    syn = charts.arrivals_by_hour(snap.arrivals, 4)
    assert _title(syn).startswith("<b>SYNTHETIC</b>") and len(syn.data) == 5  # one series per ESI


def test_arrivals_chart_averages_per_day(snap) -> None:  # noqa: ANN001
    fig = charts.arrivals_by_hour(snap.arrivals, 4)
    total = sum(sum(tr.y) for tr in fig.data)
    assert total == pytest.approx(len(snap.arrivals) / 4)


def test_stage_bars_sum_to_mean_los(snap) -> None:  # noqa: ANN001
    sd = snap.tables["stage_decomposition"]
    fig = charts.stage_time_bars(sd)
    per_dispo = {}
    for tr in fig.data:
        for y, x in zip(tr.y, tr.x, strict=True):
            per_dispo[y] = per_dispo.get(y, 0) + x
    assert per_dispo["Discharge"] == pytest.approx(
        sd[sd["disposition"] == "discharge"]["mean_min"].sum()
    )


def test_forest_plot_values_and_scaling(bundle) -> None:  # noqa: ANN001
    rows = d.comparison_rows(bundle, scope="main", metric="boarding_share_over_4h")
    fig = charts.forest(rows, title="Boarding > 4h", n_seeds=2)
    xs = np.concatenate([np.asarray(tr.x, dtype=float) for tr in fig.data])
    assert sorted(xs) == pytest.approx(sorted(rows["mean_difference"] * 100))  # fractions -> points
    assert "percentage points" in fig.layout.xaxis.title.text
    assert any(s.x0 == 0 for s in fig.layout.shapes)  # zero line present
    assert _title(fig).startswith("<b>SIMULATED</b>")


def test_forest_error_bars_match_ci(bundle) -> None:  # noqa: ANN001
    rows = d.comparison_rows(bundle, scope="main", metric="arrival_to_provider_p90")
    fig = charts.forest(rows, title="A", n_seeds=2)
    for tr in fig.data:
        for x, plus, minus, (lo, hi, _n, _name) in zip(
            tr.x, tr.error_x.array, tr.error_x.arrayminus, tr.customdata, strict=True
        ):
            assert x + plus == pytest.approx(hi) and x - minus == pytest.approx(lo)


def test_stage_shift_archetype_heatmap_and_ci_pi(bundle) -> None:  # noqa: ANN001
    stages = d.stage_effects(bundle, scope="main", scenario_id="add_night_triage_nurse")
    assert _title(charts.stage_shift_chart(stages, scenario_name="x")).startswith(
        "<b>SIMULATED</b>"
    )
    arch = charts.archetype_chart(bundle.archetypes, d.scenario_names())
    assert len(arch.data) == 2
    grid = d.sensitivity_grid(bundle, intervention="increase_bed_releases", metric="boarding_p90")
    heat = charts.sensitivity_heatmap(grid, title="Beds")
    texts = [t for row in heat.data[0].text for t in row]
    assert any("stress" in t for t in texts)  # concurrency 1 is always a stress regime
    row = d.comparison_rows(bundle, scope="main", metric="arrival_to_provider_p90").iloc[0]
    cp = charts.ci_vs_pi_chart(row, scenario_name="x")
    ci, pi = cp.data[0].x, cp.data[1].x
    assert pi[0] <= ci[0] and pi[1] >= ci[1]  # prediction interval is wider


@pytest.mark.parametrize(
    "builder",
    [
        lambda: charts.forest(pd.DataFrame(), title="x", n_seeds=0),
        lambda: charts.stage_shift_chart(pd.DataFrame(), scenario_name="x"),
        lambda: charts.archetype_chart(pd.DataFrame(), {}),
        lambda: charts.sensitivity_heatmap(pd.DataFrame(), title="x"),
        lambda: charts.public_measure_bar(
            pd.DataFrame(columns=["measure_id"]), measure_id="OP_22", unit_label="%"
        ),
        lambda: charts.nys_counts_bar(
            pd.DataFrame(columns=["measure_id"]), measure_id="NYS_ED_TOTAL", year=2025
        ),
    ],
)
def test_empty_inputs_render_a_message(builder) -> None:  # noqa: ANN001
    fig = builder()
    assert fig.layout.annotations and fig.layout.annotations[0].text.startswith("No ")


def test_nys_chart_labels_suppressed_counts_as_text() -> None:
    measures = d.load_public_data(PROCESSED_DIR).data.measures
    q1 = d.nys_rows(measures, year=2025, period_type="quarter")
    q1 = q1[q1["period_start"] == "2025-01-01"]
    fig = charts.nys_counts_bar(q1, measure_id="NYS_ED_ADMITTED", year=2025)
    assert any("Suppressed (S)" in a.text for a in fig.layout.annotations)
    assert "Suppressed (S)" not in [str(v) for v in fig.data[0].x]


# --- Layout rules (Milestone 7.1): checked on every chart the app draws --------------------------


def _all_figures(snap, bundle, public_rows):  # noqa: ANN001, ANN202
    t = snap.tables
    measures = d.load_public_data(PROCESSED_DIR).data.measures
    row = d.comparison_rows(bundle, scope="main", metric="arrival_to_provider_p90").iloc[0]
    return {
        "op18b": charts.public_measure_bar(public_rows, measure_id="OP_18b", unit_label="min"),
        "op18d": charts.public_measure_bar(public_rows, measure_id="OP_18d", unit_label="min"),
        "nys": charts.nys_counts_bar(
            d.nys_rows(measures, year=2025), measure_id="NYS_ED_ADMITTED", year=2025
        ),
        "arrivals": charts.arrivals_by_hour(snap.arrivals, 4),
        "stages": charts.stage_time_bars(t["stage_decomposition"]),
        "esi": charts.esi_outcome_dots(t["acuity_metrics"], measure="los_min", title="LOS"),
        "shift": charts.shift_utilization_bars(t["shift_utilization"]),
        "hourly": charts.hourly_line(
            t["hourly_profile"], column="physician_utilization", title="U", yaxis="%", pct=True
        ),
        "boarding": charts.boarding_box(snap.boarding),
        "forest": charts.forest(
            d.comparison_rows(bundle, scope="main", metric="boarding_p90"), title="B", n_seeds=2
        ),
        "stage_shift": charts.stage_shift_chart(
            d.stage_effects(bundle, scope="main", scenario_id="add_night_triage_nurse"),
            scenario_name="A very long fictional scenario name " * 3,
        ),
        "archetype": charts.archetype_chart(bundle.archetypes, d.scenario_names()),
        "heat": charts.sensitivity_heatmap(
            d.sensitivity_grid(bundle, intervention="increase_bed_releases", metric="boarding_p90"),
            title="Beds",
        ),
        "cipi": charts.ci_vs_pi_chart(row, scenario_name="x"),
    }


def test_every_chart_passes_layout_rules(snap, bundle, public_rows) -> None:  # noqa: ANN001
    for name, fig in _all_figures(snap, bundle, public_rows).items():
        assert charts.layout_problems(fig) == [], name


def test_layout_checker_catches_violations() -> None:
    bad = go.Figure(go.Scatter(x=[1], y=["x" * 60], mode="markers", name="a"))
    bad.add_scatter(x=[2], y=["b"], name="b")
    bad.update_layout(title="no label<br>second line", margin={"t": 10, "b": 10}, height=100)
    bad.update_yaxes(automargin=False)
    problems = " ".join(charts.layout_problems(bad))
    for text in ("line break", "provenance", "top margin", "automargin", "legend", "longer than",
                 "too short", "hover"):  # fmt: skip
        assert text in problems, text
    assert "height not set" in " ".join(charts.layout_problems(go.Figure()))


def test_long_names_are_shortened_on_axes_but_kept_in_hover(bundle) -> None:  # noqa: ANN001
    fig = charts.archetype_chart(bundle.archetypes, d.scenario_names())
    assert all(len(y) <= charts.MAX_AXIS_LABEL for y in fig.data[0].y)
    assert any("Archetype:" in str(c[0]) for c in fig.data[0].customdata)


def test_duplicate_facility_names_get_their_own_rows() -> None:
    df = pd.DataFrame(
        {"facility_id": ["1", "2", "3"], "facility_name": ["Same", "Same", "Other " + "y" * 50]}
    )
    labels = charts.facility_axis_labels(df)
    assert labels[0] != labels[1] and "(ID 1)" in labels[0] and labels[2].endswith("…")
    assert len(set(labels)) == 3 and max(map(len, labels)) <= charts.MAX_AXIS_LABEL


def test_nys_duplicate_bronxcare_sites_both_visible() -> None:
    measures = d.load_public_data(PROCESSED_DIR).data.measures
    nys = d.nys_rows(measures, year=2025)
    fig = charts.nys_counts_bar(nys, measure_id="NYS_ED_ADMITTED", year=2025)
    ys = list(fig.data[0].y) + list(fig.data[1].y)
    assert len(ys) == len(set(ys)) == len(nys[nys["measure_id"] == "NYS_ED_ADMITTED"])


def test_stage_colors_are_unique_and_not_cycled(snap) -> None:  # noqa: ANN001
    fig = charts.stage_time_bars(snap.tables["stage_decomposition"])
    colors = [tr.marker.color for tr in fig.data]
    assert len(colors) == len(set(colors))


def test_effect_wording_is_model_scoped(bundle) -> None:  # noqa: ANN001
    rows = d.comparison_rows(bundle, scope="main", metric="arrival_to_provider_p90")
    names = {tr.name for tr in charts.forest(rows, title="A", n_seeds=2).data}
    allowed = {"Lower within the fictional model", "Higher within the fictional model",
               "No detectable change (95% CI includes 0)", "Identical in both arms"}  # fmt: skip
    assert names <= allowed
    assert not any(w in " ".join(names).lower() for w in ("better", "worse", "safe", "recommend"))


def test_direction_helpers_neutral_for_unsigned() -> None:
    from beyond_the_wait.dashboard.theme import NEUTRAL, direction_color, direction_label

    assert direction_color("inconclusive", -5.0) == NEUTRAL
    assert direction_color("changes", -1.0) != direction_color("changes", 1.0)
    assert direction_label("blocked", 0.0) == "BLOCKED: not shown"
