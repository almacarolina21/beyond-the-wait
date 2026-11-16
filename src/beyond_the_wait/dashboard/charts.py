"""Plotly chart builders. Each takes prepared data and returns a Figure.

Conventions
    * The title starts with the provenance label (PUBLIC / SYNTHETIC / SIMULATED);
      the subtitle uses Plotly's own subtitle field, in a fixed title area.
    * No figure mixes PUBLIC with SYNTHETIC or SIMULATED values.
    * Axis margins grow to fit labels (automargin). Long category names are
      shortened on the axis only; the full name is always in the hover text.
    * Legends sit in a reserved band at the bottom of the figure, so they can't
      cover titles, data, or axis labels.
    * Effects are a point + 95% CI of the MEAN paired difference with the zero
      line marked, described as lower/higher WITHIN THE FICTIONAL MODEL.
`layout_problems()` checks these rules programmatically (used by the tests).
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from beyond_the_wait.dashboard.data import display_scale, short_label
from beyond_the_wait.dashboard.theme import (
    CATEGORICAL,
    ESI_COLORS,
    LEGEND_AREA_PX,
    PROVENANCE_COLORS,
    TEMPLATE,
    TEXT_2,
    TITLE_AREA_PX,
    direction_color,
    direction_label,
)

ROW_PX = 46  # height per horizontal category row
MIN_PLOT_PX = 160
MAX_AXIS_LABEL = 40  # longest category label drawn on an axis (full text in hover)
BOTTOM_PX = 64  # room for the x-axis title
# Fixed color per stage (never cycled): 7 categorical slots + dark gray for boarding.
STAGE_COLORS = {
    "waiting for triage": CATEGORICAL[0],
    "triage": CATEGORICAL[1],
    "waiting for a space": CATEGORICAL[2],
    "waiting for a physician": CATEGORICAL[3],
    "provider evaluation": CATEGORICAL[4],
    "lab/imaging results": CATEGORICAL[5],
    "discharge process": CATEGORICAL[6],
    "boarding": TEXT_2,
}
ZERO_LINE = {"color": "#52514e", "width": 1.5, "dash": "dot"}
CI_BAR = {"type": "data", "symmetric": False, "color": "#8a8984", "thickness": 2, "width": 6}


def _fig(title: str, provenance: str, subtitle: str = "") -> go.Figure:
    fig = go.Figure()
    fig.update_layout(
        template=TEMPLATE,
        title={"text": f"<b>{provenance}</b> · {title}", "subtitle": {"text": subtitle}},
    )
    return fig


def _rows_height(n_rows: int, *, legend: bool) -> int:
    plot = max(MIN_PLOT_PX, ROW_PX * n_rows)
    return TITLE_AREA_PX + plot + BOTTOM_PX + (LEGEND_AREA_PX if legend else 0)


def _legend_below(fig: go.Figure) -> go.Figure:
    """Reserve the bottom band for the legend (anchored there by the template)."""
    fig.update_layout(margin={"b": BOTTOM_PX + LEGEND_AREA_PX}, showlegend=True)
    return fig


def _clip(label: str, limit: int = MAX_AXIS_LABEL) -> str:
    return label if len(label) <= limit else label[: limit - 1].rstrip() + "…"


def _empty(fig: go.Figure, message: str) -> go.Figure:
    fig.add_annotation(
        text=message,
        showarrow=False,
        x=0.5,
        y=0.5,
        xref="paper",
        yref="paper",
        font={"color": TEXT_2, "size": 14},
    )
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    fig.update_layout(height=TITLE_AREA_PX + 200)
    return fig


# ---------------------------------------------------------------------------
# PUBLIC
# ---------------------------------------------------------------------------


def facility_axis_labels(df: pd.DataFrame) -> list[str]:
    """Official names as published, one row per facility ID. When two facilities
    share a name (NYS lists two 'BronxCare Hospital Center' sites), the ID is
    appended so neither value can hide behind the other."""
    counts = df["facility_name"].value_counts()
    labels = []
    for fid, name in zip(df["facility_id"], df["facility_name"], strict=True):
        suffix = f" (ID {fid})" if counts[name] > 1 else ""
        labels.append(_clip(name, MAX_AXIS_LABEL - len(suffix)) + suffix)
    return labels


def _public_bars(fig: go.Figure, df: pd.DataFrame, hover: str, x_title: str) -> go.Figure:
    df = df.assign(axis_label=facility_axis_labels(df))
    reported = df[df["status"] == "reported"]
    missing = df[df["status"] != "reported"]
    fig.add_bar(
        y=reported["axis_label"],
        x=reported["value"],
        orientation="h",
        marker={"color": PROVENANCE_COLORS["PUBLIC"], "cornerradius": 4},
        text=reported["display"],
        textposition="outside",
        cliponaxis=False,
        customdata=np.stack([reported["facility_name"], reported["display"]], axis=-1)
        if len(reported)
        else None,
        hovertemplate="%{customdata[0]}<br>" + hover + "<extra></extra>",
        showlegend=False,
    )
    # Unavailable values: an invisible zero-length bar keeps the row; text says why.
    fig.add_bar(
        y=missing["axis_label"],
        x=[0] * len(missing),
        orientation="h",
        showlegend=False,
        hoverinfo="skip",
        marker={"color": "rgba(0,0,0,0)"},
    )
    for label, text in zip(missing["axis_label"], missing["display"], strict=True):
        fig.add_annotation(
            y=label,
            x=0,
            xanchor="left",
            text=f"  {text}",
            showarrow=False,
            font={"color": TEXT_2, "size": 12},
        )
    fig.update_yaxes(categoryorder="array", categoryarray=list(df["axis_label"])[::-1])
    fig.update_xaxes(title_text=x_title, rangemode="tozero")
    fig.update_layout(height=_rows_height(len(df), legend=False), margin={"r": 80})
    return fig


def public_measure_bar(rows: pd.DataFrame, *, measure_id: str, unit_label: str) -> go.Figure:
    """One CMS measure across hospitals. Unavailable values are labeled, not drawn as 0."""
    df = rows[rows["measure_id"] == measure_id]
    if df.empty:
        return _empty(_fig(measure_id, "PUBLIC", "CMS Care Compare"), "No data")
    df = df.sort_values("facility_name")
    period = df["period"].iloc[0]
    fig = _fig(
        f"{measure_id}: {df['short_name'].iloc[0]}",
        "PUBLIC",
        f"Reporting period {period} · CMS Care Compare · hospital names as published",
    )
    return _public_bars(fig, df, "%{customdata[1]} · " + period, unit_label)


def nys_counts_bar(rows: pd.DataFrame, *, measure_id: str, year: int) -> go.Figure:
    df = rows[rows["measure_id"] == measure_id]
    subtitle = "NYS DOH SPARCS facility counts (aggregate) · reported per NYS site"
    if df.empty:
        return _empty(_fig(f"{measure_id}, calendar year {year}", "PUBLIC", subtitle), "No data")
    df = df.sort_values("facility_name")
    fig = _fig(f"{df['short_name'].iloc[0]}, {year}", "PUBLIC", subtitle)
    return _public_bars(fig, df, "%{customdata[1]} encounters", "ED encounters")


# ---------------------------------------------------------------------------
# SYNTHETIC and single-run SIMULATED
# ---------------------------------------------------------------------------


def arrivals_by_hour(arrivals: pd.DataFrame, days: int) -> go.Figure:
    """Average arrivals per clock hour, stacked by ESI (ordinal blue: darker = more urgent)."""
    fig = _fig(
        "Fictional arrivals by hour and acuity",
        "SYNTHETIC",
        f"Average per day over {days} generated days · generated, not observed",
    )
    counts = arrivals.groupby(["arrival_hour", "acuity_esi"]).size().unstack(fill_value=0) / days
    counts = counts.reindex(index=range(24), columns=range(1, 6), fill_value=0)
    for esi in range(1, 6):
        fig.add_bar(
            x=counts.index,
            y=counts[esi],
            name=f"ESI {esi}",
            marker={"color": ESI_COLORS[esi], "line": {"color": "white", "width": 1}},
            hovertemplate=f"ESI {esi} · %{{x}}:00<br>%{{y:.1f}} patients/day<extra></extra>",
        )
    fig.update_layout(
        barmode="stack",
        bargap=0.12,
        legend_traceorder="normal",
        height=TITLE_AREA_PX + 300 + BOTTOM_PX + LEGEND_AREA_PX,
    )
    fig.update_xaxes(title_text="Clock hour of arrival", dtick=2)
    fig.update_yaxes(title_text="Patients per day (average)")
    return _legend_below(fig)


def stage_time_bars(stages: pd.DataFrame) -> go.Figure:
    """Mean minutes in each stage, by disposition (stacked; sums to mean LOS)."""
    fig = _fig(
        "Where time in the ED goes",
        "SIMULATED",
        "Mean minutes per stage · one run · the largest stage is not necessarily the cause",
    )
    for stage in dict.fromkeys(stages["stage"]):  # chronological order from the metrics
        part = stages[stages["stage"] == stage]
        fig.add_bar(
            y=part["disposition"].str.capitalize(),
            x=part["mean_min"],
            orientation="h",
            name=stage,
            marker={
                "color": STAGE_COLORS.get(stage, TEXT_2),
                "line": {"color": "white", "width": 2},
            },
            hovertemplate=f"{stage}<br>%{{y}}: %{{x:.0f}} min (mean)<extra></extra>",
        )
    fig.update_layout(
        barmode="stack", height=_rows_height(2, legend=True) + 30, legend_traceorder="normal"
    )
    fig.update_xaxes(title_text="Minutes (mean per patient)")
    return _legend_below(fig)


def esi_outcome_dots(acuity: pd.DataFrame, *, measure: str, title: str) -> go.Figure:
    """Median and 90th percentile by ESI: a reminder that medians hide tails."""
    df = acuity[
        (acuity["disposition"] == "all")
        & (acuity["measure"] == measure)
        & (acuity["acuity_esi"] != "all")
    ].copy()
    df["esi_label"] = "ESI " + df["acuity_esi"].astype(str)
    fig = _fig(title, "SIMULATED", "One run · completed visits in the measured period")
    for r in df.itertuples():
        fig.add_shape(
            type="line",
            x0=r.median,
            x1=r.p90,
            y0=r.esi_label,
            y1=r.esi_label,
            line={"color": "#c9c8c2", "width": 2},
            layer="below",
        )
    fig.add_scatter(
        x=df["median"],
        y=df["esi_label"],
        mode="markers",
        name="Median",
        marker={"size": 11, "color": CATEGORICAL[0]},
        customdata=df["patients"],
        hovertemplate="%{y} median: %{x:.0f} min (n=%{customdata})<extra></extra>",
    )
    fig.add_scatter(
        x=df["p90"],
        y=df["esi_label"],
        mode="markers",
        name="90th percentile",
        marker={"size": 11, "color": CATEGORICAL[1], "symbol": "diamond"},
        hovertemplate="%{y} 90th percentile: %{x:.0f} min<extra></extra>",
    )
    fig.update_yaxes(categoryorder="array", categoryarray=[f"ESI {i}" for i in range(5, 0, -1)])
    fig.update_xaxes(title_text="Minutes")
    fig.update_layout(height=_rows_height(5, legend=True))
    return _legend_below(fig)


def shift_utilization_bars(shift_util: pd.DataFrame) -> go.Figure:
    df = shift_util[shift_util["resource"].isin(["physician_slots", "triage_nurses"])]
    fig = _fig(
        "Staff utilization by shift",
        "SIMULATED",
        "Share of available staff time spent busy · hover for time spent at ≥90% busy",
    )
    resources = [("physician_slots", "Physician slots"), ("triage_nurses", "Triage nurses")]
    for i, (res, label) in enumerate(resources):
        part = df[df["resource"] == res]
        fig.add_bar(
            x=part["shift"].str.capitalize(),
            y=part["utilization"] * 100,
            name=label,
            marker={"color": CATEGORICAL[i], "cornerradius": 4},
            text=[f"{v:.0f}%" for v in part["utilization"] * 100],
            customdata=part["share_time_at_or_above_90pct"] * 100,
            textposition="outside",
            cliponaxis=False,
            hovertemplate=(
                f"{label} · %{{x}}<br>Utilization %{{y:.0f}}%"
                "<br>%{customdata:.0f}% of the shift at ≥90% busy<extra></extra>"
            ),
        )
    fig.update_layout(barmode="group", height=TITLE_AREA_PX + 300 + BOTTOM_PX + LEGEND_AREA_PX)
    fig.update_yaxes(title_text="Utilization (%)", range=[0, 112])
    return _legend_below(fig)


def hourly_line(
    hourly: pd.DataFrame, *, column: str, title: str, yaxis: str, pct: bool = False
) -> go.Figure:
    fig = _fig(title, "SIMULATED", "One run · average over measured days by clock hour")
    y = hourly[column] * (100 if pct else 1)
    fig.add_scatter(
        x=hourly["hour"],
        y=y,
        mode="lines+markers",
        name=title,
        showlegend=False,
        line={"color": CATEGORICAL[0], "width": 2},
        marker={"size": 8},
        hovertemplate="%{x}:00 · %{y:.2f}" + ("%" if pct else "") + "<extra></extra>",
    )
    for r in hourly.assign(yval=y).nlargest(2, "yval").itertuples():
        fig.add_annotation(
            x=r.hour,
            y=r.yval,
            text=f"{int(r.hour):02d}:00",
            showarrow=True,
            arrowhead=0,
            ay=-24,
            font={"color": TEXT_2, "size": 12},
        )
    fig.update_xaxes(title_text="Clock hour", dtick=2)
    fig.update_yaxes(title_text=yaxis)
    fig.update_layout(height=TITLE_AREA_PX + 260 + BOTTOM_PX, showlegend=False)
    return fig


def boarding_box(boarding: pd.DataFrame) -> go.Figure:
    fig = _fig(
        "Boarding time by shift of the admission decision",
        "SIMULATED",
        "One run · simplified bed-release model · unfinished boarders counted so far",
    )
    for i, shift in enumerate(["day", "evening", "night"]):
        vals = boarding.loc[boarding["decision_shift"] == shift, "boarding_min"] / 60
        fig.add_box(
            y=vals,
            name=f"{shift.capitalize()} (n={len(vals)})",
            boxpoints="outliers",
            marker={"color": CATEGORICAL[i], "size": 5},
            line={"width": 2},
            hovertemplate="%{y:.1f} h<extra></extra>",
        )
    fig.update_yaxes(title_text="Hours boarding in the ED")
    fig.update_layout(showlegend=False, height=TITLE_AREA_PX + 300 + BOTTOM_PX)
    return fig


# ---------------------------------------------------------------------------
# Multi-seed comparisons (SIMULATED)
# ---------------------------------------------------------------------------


def _effect_traces(fig: go.Figure, df: pd.DataFrame, *, y: str, full: str, unit: str) -> None:
    """One trace per direction label so the legend explains each color in words."""
    df = df.assign(
        _label=[direction_label(r.direction, r.d) for r in df.itertuples()],
        _color=[direction_color(r.direction, r.d) for r in df.itertuples()],
    )
    for label, part in df.groupby("_label", sort=False):
        fig.add_scatter(
            x=part["d"],
            y=part[y],
            mode="markers",
            name=label,
            marker={
                "size": 12,
                "color": part["_color"].iloc[0],
                # identical-in-both-arms is hollow so it differs from "no detectable change"
                # by shape, not only by its (identical) neutral color
                "symbol": "circle-open" if part["direction"].iloc[0] == "no_change" else "circle",
                "line": {
                    "color": part["_color"].iloc[0]
                    if part["direction"].iloc[0] == "no_change"
                    else "white",
                    "width": 2,
                },
            },
            error_x=CI_BAR
            | {"array": part["hi"] - part["d"], "arrayminus": part["d"] - part["lo"]},
            customdata=np.stack(
                [part["lo"], part["hi"], part["n_pairs"], part[full]], axis=-1
            ).astype(object),
            hovertemplate=(
                "%{customdata[3]}<br>Mean change %{x:+.2f} "
                + unit
                + "<br>95% CI of the mean [%{customdata[0]:+.2f}, %{customdata[1]:+.2f}]"
                + "<br>%{customdata[2]} paired seeds<extra>"
                + label
                + "</extra>"
            ),
        )
    fig.add_vline(x=0, line=ZERO_LINE)


def forest(rows: pd.DataFrame, *, title: str, n_seeds: int) -> go.Figure:
    """Mean paired difference with 95% CI per scenario; zero line = no change."""
    if rows.empty:
        return _empty(_fig(title, "SIMULATED"), "No comparisons available")
    scale, unit = display_scale(rows["unit"].iloc[0])
    df = rows.assign(
        d=rows["mean_difference"] * scale,
        lo=rows["ci95_low"] * scale,
        hi=rows["ci95_high"] * scale,
        axis_label=[short_label(s) for s in rows["scenario_id"]],
    )
    fig = _fig(
        title,
        "SIMULATED",
        f"Change vs. baseline · dot = mean, bar = 95% CI of the mean · {n_seeds} paired seeds",
    )
    _effect_traces(fig, df, y="axis_label", full="scenario_name", unit=unit)
    fig.update_yaxes(categoryorder="array", categoryarray=list(df["axis_label"])[::-1])
    fig.update_xaxes(title_text=f"Change vs. baseline ({unit}) · left of 0 = lower in the model")
    fig.update_layout(height=_rows_height(df["axis_label"].nunique(), legend=True))
    return _legend_below(fig)


def stage_shift_chart(stages: pd.DataFrame, *, scenario_name: str) -> go.Figure:
    """Effect on each waiting stage: reveals waiting that moves rather than disappears."""
    if stages.empty:
        return _empty(_fig("Stage effects", "SIMULATED"), "No stage data")
    df = stages.assign(d=stages["mean_difference"], lo=stages["ci95_low"], hi=stages["ci95_high"])
    fig = _fig(
        "Where waiting changes",
        "SIMULATED",
        f"{_clip(scenario_name, 60)} · mean minutes per patient per stage vs. baseline, 95% CI",
    )
    _effect_traces(fig, df, y="stage", full="stage", unit="min")
    fig.update_yaxes(categoryorder="array", categoryarray=list(df["stage"])[::-1])
    fig.update_xaxes(title_text="Change in mean minutes per patient (min)")
    fig.update_layout(height=_rows_height(len(df), legend=True))
    return _legend_below(fig)


def archetype_chart(arch: pd.DataFrame, names: dict[str, str]) -> go.Figure:
    """Condition harm vs. relief effect, with 95% CIs, per fictional archetype."""
    fig = _fig(
        "Does relieving the suspected constraint help?",
        "SIMULATED",
        "Fictional archetypes · each row uses its own outcome (min) · 95% CI of the mean",
    )
    if arch.empty:
        return _empty(fig, "No archetype results in the bundle")
    labels = [short_label(a) for a in arch["archetype"]]
    full = [names.get(a, a) for a in arch["archetype"]]
    series = [
        ("condition", "Condition vs. baseline", "circle"),
        ("relief", "Relief vs. condition", "diamond"),
    ]
    for i, (prefix, label, symbol) in enumerate(series):
        d, lo, hi = arch[f"{prefix}_effect"], arch[f"{prefix}_ci_low"], arch[f"{prefix}_ci_high"]
        fig.add_scatter(
            x=d,
            y=labels,
            mode="markers",
            name=label,
            marker={"size": 12, "color": CATEGORICAL[i], "symbol": symbol},
            error_x=CI_BAR | {"array": hi - d, "arrayminus": d - lo},
            customdata=np.stack([full, arch["outcome"]], axis=-1),
            hovertemplate=(
                f"{label}<br>%{{customdata[0]}}<br>Outcome: %{{customdata[1]}}"
                "<br>%{x:+.1f} min<extra></extra>"
            ),
        )
    fig.add_vline(x=0, line=ZERO_LINE)
    fig.update_yaxes(categoryorder="array", categoryarray=labels[::-1])
    fig.update_xaxes(title_text="Mean paired difference (min)")
    fig.update_layout(height=_rows_height(len(arch), legend=True))
    return _legend_below(fig)


SENS_CODES = {
    "improves": 0,
    "inconclusive": 1,
    "no_change": 1,
    "changes": 1,
    "unavailable": 1,
    "worsens": 2,
    "blocked": 1,
}
SENS_TEXT = {
    "improves": "lower",
    "worsens": "HIGHER",
    "inconclusive": "n.s.",
    "no_change": "same",
    "changes": "changed",
    "unavailable": "n/a",
    "blocked": "BLOCKED",
}


def sensitivity_heatmap(grid: pd.DataFrame, *, title: str) -> go.Figure:
    """Direction of an intervention's effect under each assumption setting."""
    fig = _fig(
        title,
        "SIMULATED",
        "Cell = direction of the 95% CI under that assumption · 'stress' = overloaded week",
    )
    if grid.empty:
        return _empty(fig, "No sensitivity results in the bundle")
    conc = sorted(grid["concurrency"].unique())
    rel = sorted(grid["releases_per_day"].unique())
    z, text, hover = [], [], []
    for c in conc:
        zr, tr, hr = [], [], []
        for r in rel:
            row = grid[(grid["concurrency"] == c) & (grid["releases_per_day"] == r)].iloc[0]
            word = SENS_TEXT.get(row["direction"], row["direction"])
            zr.append(SENS_CODES.get(row["direction"], 1))
            stress = " · stress" if row["regime"] == "stress_test" else ""
            md = row.get("mean_difference")
            missing = md is None or (isinstance(md, float) and math.isnan(md))
            tr.append(f"{word}{stress}" + ("" if missing else f"<br>{md:+.1f}"))
            hr.append(
                f"Concurrency {c}, {r:g} releases/day<br>{word} within the fictional model"
                f"<br>Regime: {row['regime']}"
            )
        z.append(zr)
        text.append(tr)
        hover.append(hr)
    colorscale = [
        [0.0, "#86b6ef"],
        [0.33, "#86b6ef"],
        [0.34, "#f0efec"],
        [0.66, "#f0efec"],
        [0.67, "#f3b394"],
        [1.0, "#f3b394"],
    ]
    fig.add_heatmap(
        z=z,
        x=[f"{r:g}/day" for r in rel],
        y=[f"{c} at once" for c in conc],
        zmin=0,
        zmax=2,
        colorscale=colorscale,
        showscale=False,
        text=text,
        texttemplate="%{text}",
        textfont={"size": 13, "color": "#0b0b0b"},
        hovertext=hover,
        hovertemplate="%{hovertext}<extra></extra>",
        xgap=3,
        ygap=3,
    )
    fig.update_xaxes(title_text="Inpatient bed releases to the ED (assumption)")
    fig.update_yaxes(title_text="Patients per physician (assumption)")
    fig.update_layout(height=TITLE_AREA_PX + 80 * len(conc) + BOTTOM_PX + 20)
    return fig


def ci_vs_pi_chart(row: pd.Series, *, scenario_name: str) -> go.Figure:
    """Same estimate, two different intervals: average effect vs. one future run."""
    scale, unit = display_scale(row["unit"])
    d = row["mean_difference"] * scale
    fig = _fig(
        f"Confidence vs. prediction interval · {row['label']}",
        "SIMULATED",
        f"{_clip(scenario_name, 60)} · {int(row['n_pairs'])} paired seeds",
    )
    items = [
        ("95% CI of the mean", row["ci95_low"], row["ci95_high"], CATEGORICAL[0]),
        ("95% prediction (one run)", row["pred95_low"], row["pred95_high"], CATEGORICAL[1]),
    ]
    for label, lo, hi, color in items:
        fig.add_scatter(
            x=[lo * scale, hi * scale],
            y=[label, label],
            mode="lines+markers",
            name=label,
            line={"color": color, "width": 6},
            marker={"size": 10, "color": color},
            showlegend=False,
            hovertemplate=f"{label}<br>%{{x:+.2f}} {unit}<extra></extra>",
        )
    fig.add_scatter(
        x=[d, d],
        y=[items[0][0], items[1][0]],
        mode="markers",
        name="Mean change",
        showlegend=False,
        marker={"size": 13, "color": "#0b0b0b", "symbol": "line-ns-open", "line": {"width": 3}},
        hovertemplate=f"Mean %{{x:+.2f}} {unit}<extra></extra>",
    )
    fig.add_vline(x=0, line=ZERO_LINE)
    fig.update_xaxes(title_text=f"Change vs. baseline ({unit})")
    fig.update_layout(height=_rows_height(2, legend=False), showlegend=False)
    return fig


# ---------------------------------------------------------------------------
# Layout checks (used by tests)
# ---------------------------------------------------------------------------


def _category_labels(fig: go.Figure) -> list[str]:
    labels: list[str] = []
    for t in fig.data:
        if t.type not in ("bar", "scatter") or t.y is None or not len(t.y):
            continue
        if isinstance(t.y[0], str):
            labels.extend(str(y) for y in t.y)
    return labels


def layout_problems(fig: go.Figure) -> list[str]:
    """Human-readable layout problems, or [] if the figure follows the rules above."""
    lay = fig.layout
    problems: list[str] = []
    title = lay.title.text or ""
    if "<br>" in title:
        problems.append("title contains a line break; use the subtitle field")
    if not title.startswith(("<b>PUBLIC</b>", "<b>SYNTHETIC</b>", "<b>SIMULATED</b>")):
        problems.append("title does not start with a provenance label")
    template = lay.template.layout
    if (lay.margin.t or template.margin.t or 0) < TITLE_AREA_PX:
        problems.append("top margin smaller than the reserved title area")
    for name in ("xaxis", "yaxis"):
        axis, base = lay[name], template[name]
        visible = axis.visible is not False
        if (
            visible
            and (axis.automargin if axis.automargin is not None else base.automargin) is not True
        ):
            problems.append(f"{name} does not use automargin")
    legend_items = [t for t in fig.data if t.showlegend is not False]
    if lay.showlegend is not False and len(legend_items) >= 2:
        if (lay.legend.yref or template.legend.yref) != "container":
            problems.append("legend is not anchored to the bottom band of the figure")
        if (lay.margin.b or 0) < BOTTOM_PX + LEGEND_AREA_PX:
            problems.append("bottom margin too small for the legend band")
    labels = _category_labels(fig)
    if labels and max(len(x) for x in labels) > MAX_AXIS_LABEL:
        problems.append(f"an axis label is longer than {MAX_AXIS_LABEL} characters")
    n_rows = len(set(labels))
    if n_rows and (lay.height or 0) < TITLE_AREA_PX + min(MIN_PLOT_PX, ROW_PX * n_rows):
        problems.append("figure too short for its category rows")
    if not lay.height:
        problems.append("figure height not set")
    for t in fig.data:
        if t.type != "heatmap" and t.hoverinfo != "skip" and not t.hovertemplate:
            problems.append(f"a {t.type} trace has no hover text")
    return problems
