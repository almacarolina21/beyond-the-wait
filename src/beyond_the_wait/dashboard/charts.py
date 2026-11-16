"""Plotly chart builders. Each takes prepared data and returns a Figure.

Conventions
    * The title starts with the provenance label (PUBLIC / SYNTHETIC / SIMULATED).
      No figure ever mixes PUBLIC with SYNTHETIC or SIMULATED values.
    * Units are in axis titles; hover text repeats value, unit, and context.
    * One y-axis per chart. Legends appear for 2+ series.
    * Effects are shown as point + 95% CI of the MEAN paired difference, with
      the zero line marked, and a text verdict in hover and legend.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from beyond_the_wait.dashboard.data import display_scale
from beyond_the_wait.dashboard.theme import (
    CATEGORICAL,
    DIRECTION_COLORS,
    DIRECTION_TEXT,
    ESI_COLORS,
    NEUTRAL,
    PROVENANCE_COLORS,
    TEMPLATE,
    TEXT_2,
)


def _fig(title: str, provenance: str, subtitle: str = "") -> go.Figure:
    fig = go.Figure()
    sub = f"<br><span style='font-size:12px;color:{TEXT_2}'>{subtitle}</span>" if subtitle else ""
    fig.update_layout(template=TEMPLATE, title={"text": f"<b>{provenance}</b> · {title}{sub}"})
    return fig


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
    return fig


# ---------------------------------------------------------------------------
# PUBLIC
# ---------------------------------------------------------------------------


def public_measure_bar(rows: pd.DataFrame, *, measure_id: str, unit_label: str) -> go.Figure:
    """One CMS measure across hospitals. Unavailable values are labeled, not drawn as 0."""
    df = rows[rows["measure_id"] == measure_id]
    if df.empty:
        return _empty(_fig(measure_id, "PUBLIC", "CMS Care Compare"), "No data")
    df = df.sort_values("facility_name")
    period, name = df["period"].iloc[0], df["short_name"].iloc[0]
    fig = _fig(f"{measure_id}: {name}", "PUBLIC", f"Reporting period {period} · CMS Care Compare")
    reported = df[df["status"] == "reported"]
    fig.add_bar(
        y=reported["facility_name"],
        x=reported["value"],
        orientation="h",
        marker={"color": PROVENANCE_COLORS["PUBLIC"], "cornerradius": 4},
        text=reported["display"],
        textposition="outside",
        cliponaxis=False,
        customdata=np.stack([reported["period"], reported["facility_id"]], axis=-1),
        hovertemplate="%{y}<br>%{text} · %{customdata[0]}<br>CCN %{customdata[1]}<extra></extra>",
        name=name,
        showlegend=False,
    )
    for row in df[df["status"] != "reported"].itertuples():
        fig.add_annotation(
            y=row.facility_name,
            x=0,
            xanchor="left",
            text=f"  {row.display}",
            showarrow=False,
            font={"color": TEXT_2, "size": 12},
        )
    fig.add_bar(
        y=df[df["status"] != "reported"]["facility_name"],
        x=[0] * int((df["status"] != "reported").sum()),
        orientation="h",
        showlegend=False,
        hoverinfo="skip",
    )
    fig.update_layout(height=120 + 46 * len(df), xaxis_title=unit_label, yaxis_title=None)
    fig.update_yaxes(categoryorder="array", categoryarray=list(df["facility_name"])[::-1])
    return fig


def nys_counts_bar(rows: pd.DataFrame, *, measure_id: str, year: int) -> go.Figure:
    df = rows[rows["measure_id"] == measure_id]
    subtitle = "NYS DOH SPARCS facility counts (aggregate) · NYS facility IDs"
    if df.empty:
        return _empty(_fig(f"{measure_id}, calendar year {year}", "PUBLIC", subtitle), "No data")
    df = df.sort_values("facility_name")
    fig = _fig(f"{df['short_name'].iloc[0]}, calendar year {year}", "PUBLIC", subtitle)
    reported = df[df["status"] == "reported"]
    fig.add_bar(
        y=reported["facility_name"],
        x=reported["value"],
        orientation="h",
        marker={"color": PROVENANCE_COLORS["PUBLIC"], "cornerradius": 4},
        text=reported["display"],
        textposition="outside",
        cliponaxis=False,
        hovertemplate="%{y}<br>%{text} encounters<extra></extra>",
        showlegend=False,
    )
    for row in df[df["status"] != "reported"].itertuples():
        fig.add_annotation(
            y=row.facility_name,
            x=0,
            xanchor="left",
            text=f"  {row.display}",
            showarrow=False,
            font={"color": TEXT_2, "size": 12},
        )
    fig.update_layout(height=120 + 40 * len(df), xaxis_title="ED encounters", yaxis_title=None)
    fig.update_yaxes(categoryorder="array", categoryarray=list(df["facility_name"])[::-1])
    return fig


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
        xaxis_title="Clock hour of arrival",
        yaxis_title="Patients per day (average)",
        height=380,
        bargap=0.12,
    )
    fig.update_xaxes(dtick=2)
    return fig


def stage_time_bars(stages: pd.DataFrame) -> go.Figure:
    """Mean minutes in each stage, by disposition (stacked; sums to mean LOS)."""
    fig = _fig(
        "Where time in the ED goes (mean minutes per stage)",
        "SIMULATED",
        "One run · descriptive only: the largest stage is not necessarily the cause of delay",
    )
    order = list(dict.fromkeys(stages["stage"]))
    for i, stage in enumerate(order):
        part = stages[stages["stage"] == stage]
        fig.add_bar(
            y=part["disposition"].str.capitalize(),
            x=part["mean_min"],
            orientation="h",
            name=stage,
            marker={
                "color": CATEGORICAL[i % len(CATEGORICAL)],
                "line": {"color": "white", "width": 2},
            },
            hovertemplate=f"{stage}<br>%{{y}}: %{{x:.0f}} min (mean)<extra></extra>",
        )
    fig.update_layout(
        barmode="stack",
        xaxis_title="Minutes (mean per patient)",
        height=300,
        legend={"y": -0.35, "yanchor": "top"},
        margin={"b": 110},
    )
    return fig


def esi_outcome_dots(acuity: pd.DataFrame, *, measure: str, title: str) -> go.Figure:
    """Median and 90th percentile by ESI: a reminder that medians hide tails."""
    df = acuity[
        (acuity["disposition"] == "all")
        & (acuity["measure"] == measure)
        & (acuity["acuity_esi"] != "all")
    ].copy()
    df["esi_label"] = "ESI " + df["acuity_esi"].astype(str)
    fig = _fig(title, "SIMULATED", "One run · completed visits in the measured period")
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
    fig.update_layout(xaxis_title="Minutes", height=320)
    fig.update_yaxes(categoryorder="array", categoryarray=[f"ESI {i}" for i in range(5, 0, -1)])
    return fig


def shift_utilization_bars(shift_util: pd.DataFrame) -> go.Figure:
    df = shift_util[shift_util["resource"].isin(["physician_slots", "triage_nurses"])].copy()
    fig = _fig(
        "Staff utilization by shift",
        "SIMULATED",
        "Share of available staff time spent busy · labels show share of shift at ≥90% busy",
    )
    for i, (res, label) in enumerate(
        [("physician_slots", "Physician slots"), ("triage_nurses", "Triage nurses")]
    ):
        part = df[df["resource"] == res]
        fig.add_bar(
            x=part["shift"].str.capitalize(),
            y=part["utilization"] * 100,
            name=label,
            marker={"color": CATEGORICAL[i], "cornerradius": 4},
            text=[f"{v:.0%} at ≥90%" for v in part["share_time_at_or_above_90pct"]],
            textposition="outside",
            hovertemplate=(
                f"{label} · %{{x}}<br>Utilization %{{y:.0f}}%<br>%{{text}}<extra></extra>"
            ),
        )
    fig.update_layout(
        barmode="group", yaxis_title="Utilization (%)", yaxis_range=[0, 105], height=360
    )
    return fig


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
        line={"color": CATEGORICAL[0], "width": 2},
        marker={"size": 8},
        hovertemplate="%{x}:00 · %{y:.2f}" + ("%" if pct else "") + "<extra></extra>",
        showlegend=False,
    )
    top = hourly.assign(yval=y).nlargest(2, "yval")
    for r in top.itertuples():
        fig.add_annotation(
            x=r.hour,
            y=r.yval,
            text=f"{int(r.hour):02d}:00",
            showarrow=True,
            arrowhead=0,
            ay=-28,
            font={"color": TEXT_2, "size": 12},
        )
    fig.update_layout(xaxis_title="Clock hour", yaxis_title=yaxis, height=320)
    fig.update_xaxes(dtick=2)
    return fig


def boarding_box(boarding: pd.DataFrame) -> go.Figure:
    fig = _fig(
        "Boarding time by shift of the admission decision",
        "SIMULATED",
        "One run · simplified bed-release model · unfinished boarders at time so far",
    )
    order = ["day", "evening", "night"]
    for i, shift in enumerate(order):
        vals = boarding.loc[boarding["decision_shift"] == shift, "boarding_min"] / 60
        fig.add_box(
            y=vals,
            name=f"{shift.capitalize()} (n={len(vals)})",
            boxpoints="outliers",
            marker={"color": CATEGORICAL[i], "size": 5},
            line={"width": 2},
            hovertemplate="%{y:.1f} h<extra></extra>",
        )
    fig.update_layout(yaxis_title="Hours boarding in the ED", showlegend=False, height=360)
    return fig


# ---------------------------------------------------------------------------
# Multi-seed comparisons (SIMULATED)
# ---------------------------------------------------------------------------


def forest(rows: pd.DataFrame, *, title: str, n_seeds: int) -> go.Figure:
    """Mean paired difference with 95% CI per scenario; zero line = no effect."""
    if rows.empty:
        return _empty(_fig(title, "SIMULATED"), "No comparisons available")
    scale, unit = display_scale(rows["unit"].iloc[0])
    df = rows.assign(
        d=rows["mean_difference"] * scale, lo=rows["ci95_low"] * scale, hi=rows["ci95_high"] * scale
    )
    fig = _fig(
        title,
        "SIMULATED",
        f"Mean paired difference vs. baseline with 95% CI of the mean · {n_seeds} paired seeds"
        " · fictional model",
    )
    for direction, part in df.groupby("direction", sort=False):
        fig.add_scatter(
            x=part["d"],
            y=part["scenario_name"],
            mode="markers",
            name=DIRECTION_TEXT.get(direction, direction),
            marker={
                "size": 12,
                "color": DIRECTION_COLORS.get(direction, NEUTRAL),
                "line": {"color": "white", "width": 2},
            },
            error_x={
                "type": "data",
                "symmetric": False,
                "array": part["hi"] - part["d"],
                "arrayminus": part["d"] - part["lo"],
                "color": "#8a8984",
                "thickness": 2,
                "width": 6,
            },
            customdata=np.stack([part["lo"], part["hi"], part["n_pairs"]], axis=-1),
            hovertemplate=(
                "%{y}<br>Mean difference %{x:+.2f} "
                + unit
                + "<br>95% CI [%{customdata[0]:+.2f}, %{customdata[1]:+.2f}]"
                + "<br>%{customdata[2]} paired seeds<extra>"
                + DIRECTION_TEXT.get(direction, "")
                + "</extra>"
            ),
        )
    fig.add_vline(x=0, line={"color": "#52514e", "width": 1.5, "dash": "dot"})
    fig.update_layout(
        xaxis_title=f"Change vs. baseline ({unit}) · negative = lower in the model",
        height=170 + 52 * df["scenario_name"].nunique(),
        legend={"y": -0.25, "yanchor": "top"},
        margin={"b": 100},
    )
    return fig


def stage_shift_chart(stages: pd.DataFrame, *, scenario_name: str) -> go.Figure:
    """Effect on each waiting stage: reveals waiting that moves rather than disappears."""
    if stages.empty:
        return _empty(_fig("Stage effects", "SIMULATED"), "No stage data")
    df = stages.assign(d=stages["mean_difference"], lo=stages["ci95_low"], hi=stages["ci95_high"])
    fig = _fig(
        f"Where waiting changes: {scenario_name}",
        "SIMULATED",
        "Mean minutes per patient in each stage, change vs. baseline with 95% CI",
    )
    for direction, part in df.groupby("direction", sort=False):
        fig.add_scatter(
            x=part["d"],
            y=part["stage"],
            mode="markers",
            name=DIRECTION_TEXT.get(direction, direction),
            marker={
                "size": 12,
                "color": DIRECTION_COLORS.get(direction, NEUTRAL),
                "line": {"color": "white", "width": 2},
            },
            error_x={
                "type": "data",
                "symmetric": False,
                "array": part["hi"] - part["d"],
                "arrayminus": part["d"] - part["lo"],
                "color": "#8a8984",
                "thickness": 2,
                "width": 6,
            },
            hovertemplate="%{y}: %{x:+.2f} min<extra></extra>",
        )
    fig.add_vline(x=0, line={"color": "#52514e", "width": 1.5, "dash": "dot"})
    fig.update_yaxes(categoryorder="array", categoryarray=list(df["stage"])[::-1])
    fig.update_layout(
        xaxis_title="Change in mean minutes per patient",
        height=360,
        legend={"y": -0.3, "yanchor": "top"},
        margin={"b": 100},
    )
    return fig


def archetype_chart(arch: pd.DataFrame, names: dict[str, str]) -> go.Figure:
    """Condition harm vs. relief effect, with 95% CIs, per fictional archetype."""
    fig = _fig(
        "Archetypes: does relieving the suspected constraint help?",
        "SIMULATED",
        "Each row uses its own outcome metric (minutes) · fictional conditions",
    )
    if arch.empty:
        return _empty(fig, "No archetype results in the bundle")
    labels = [names.get(a, a).replace("Archetype: ", "") for a in arch["archetype"]]
    for i, (prefix, label) in enumerate(
        [("condition", "Condition vs. baseline"), ("relief", "Relief vs. condition")]
    ):
        d, lo, hi = arch[f"{prefix}_effect"], arch[f"{prefix}_ci_low"], arch[f"{prefix}_ci_high"]
        fig.add_scatter(
            x=d,
            y=labels,
            mode="markers",
            name=label,
            marker={
                "size": 12,
                "color": CATEGORICAL[i],
                "symbol": "circle" if i == 0 else "diamond",
            },
            error_x={
                "type": "data",
                "symmetric": False,
                "array": hi - d,
                "arrayminus": d - lo,
                "color": "#8a8984",
                "thickness": 2,
                "width": 6,
            },
            customdata=arch["outcome"],
            hovertemplate=f"{label}<br>%{{y}} · %{{customdata}}<br>%{{x:+.1f}} min<extra></extra>",
        )
    fig.add_vline(x=0, line={"color": "#52514e", "width": 1.5, "dash": "dot"})
    fig.update_layout(xaxis_title="Mean paired difference (minutes)", height=360)
    return fig


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
        "Cell = direction of the 95% CI under that assumption"
        " · 'stress' = an overloaded replication occurred",
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
            zr.append(SENS_CODES.get(row["direction"], 1))
            stress = " · stress" if row["regime"] == "stress_test" else ""
            md = row.get("mean_difference")
            mdt = (
                "" if md is None or (isinstance(md, float) and math.isnan(md)) else f"<br>{md:+.1f}"
            )
            tr.append(f"{SENS_TEXT.get(row['direction'], row['direction'])}{stress}{mdt}")
            hr.append(
                f"Concurrency {c}, {r:g} releases/day<br>{DIRECTION_TEXT.get(row['direction'], '')}"
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
        y=[f"Concurrency {c}" for c in conc],
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
    fig.update_layout(
        xaxis_title="Inpatient bed releases to the ED (assumption)",
        yaxis_title="Patients per physician at once (assumption)",
        height=340,
    )
    return fig


def ci_vs_pi_chart(row: pd.Series, *, scenario_name: str) -> go.Figure:
    """Same estimate, two different intervals: average effect vs. one future run."""
    scale, unit = display_scale(row["unit"])
    d = row["mean_difference"] * scale
    fig = _fig(
        f"Confidence vs. prediction interval: {row['label']}",
        "SIMULATED",
        f"{scenario_name} · {int(row['n_pairs'])} paired seeds",
    )
    items = [
        ("95% CI of the mean difference", row["ci95_low"], row["ci95_high"], CATEGORICAL[0]),
        (
            "95% prediction interval (one future run)",
            row["pred95_low"],
            row["pred95_high"],
            CATEGORICAL[1],
        ),
    ]
    for label, lo, hi, color in items:
        fig.add_scatter(
            x=[lo * scale, hi * scale],
            y=[label, label],
            mode="lines+markers",
            name=label,
            line={"color": color, "width": 6},
            marker={"size": 10, "color": color},
            hovertemplate=f"{label}<br>%{{x:+.2f}} {unit}<extra></extra>",
            showlegend=False,
        )
    fig.add_scatter(
        x=[d, d],
        y=[items[0][0], items[1][0]],
        mode="markers",
        name="Mean difference",
        marker={"size": 13, "color": "#0b0b0b", "symbol": "line-ns-open", "line": {"width": 3}},
        hovertemplate=f"Mean %{{x:+.2f}} {unit}<extra></extra>",
        showlegend=False,
    )
    fig.add_vline(x=0, line={"color": "#52514e", "width": 1.5, "dash": "dot"})
    fig.update_layout(xaxis_title=f"Change vs. baseline ({unit})", height=260)
    return fig
