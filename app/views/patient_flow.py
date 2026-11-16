"""Fictional Patient Flow: one validated simulation run of the fictional ED."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from beyond_the_wait.dashboard import charts, ui
from beyond_the_wait.dashboard import data as d

ui.page_header(
    "Fictional model",
    "A simulated week at Riverside Commons Community ED",
    "Riverside Commons is a <b>fictional</b> ED. Its patients are generated from stated assumptions "
    "(<b>synthetic</b>), and every wait, length of stay, and utilization figure below is a "
    "<b>simulated</b> model output. Nothing here describes a real hospital.",
    badges=("SYNTHETIC", "SIMULATED"),
)
ui.esi1_callout()

site = d.load_site()
summary = d.site_summary(site)
with st.expander("Fictional configuration and assumptions", expanded=False):
    a, b = st.columns([3, 2])
    a.markdown("**Staffing by shift**")
    a.dataframe(summary["staffing"], hide_index=True, width="stretch")
    b.markdown("**Treatment spaces**")
    b.dataframe(summary["spaces"], hide_index=True, width="stretch")
    st.markdown(
        "**Patient mix by ESI level** (conditional probabilities: e.g. P(lab) for ESI 2 = share of ESI-2 patients needing a lab test)"
    )
    st.dataframe(summary["acuity"], hide_index=True, width="stretch")
    st.markdown("**Key assumptions** — uncertain assumptions are varied on the Sensitivity page")
    st.dataframe(summary["assumptions"], hide_index=True, width="stretch")
    st.caption(
        "All values are illustrative and unverified. See docs/SYNTHETIC_DATA_ASSUMPTIONS.md and docs/SIMULATION_MODEL.md."
    )

c1, c2, c3 = st.columns([1, 1, 2])
seed = c1.selectbox(
    "Random seed", [42, 7, 11, 2026], help="Each seed is a different, equally valid random week."
)
days = c2.slider(
    "Days simulated",
    4,
    d.MAX_FLOW_DAYS,
    8,
    help="The first day is a warm-up and is excluded from statistics.",
)
c3.markdown(
    "<div class='btw-note'>This page shows <b>one</b> run. One run is one random draw — use the "
    "What-If Lab for comparisons across 30 seeds.</div>",
    unsafe_allow_html=True,
)
with st.spinner("Simulating and validating…"):
    snap = ui.flow(seed, days)

report = snap.report
ui.status_line(report.status.value, f"Validation gate for this run (seed {seed}, {days} days).")
if snap.blocked:
    st.error("This run was BLOCKED by the validation gate; no results are shown.")
    for reason in report.reasons:
        st.markdown(f"- {reason}")
    st.stop()
with st.expander("Why this run is CAUTION (not an error)"):
    ui.checks_table(pd.DataFrame([c.model_dump() for c in report.checks if c.status != "PASS"]))

t = snap.tables
ps = t["patient_summary"].set_index(["column", "group"])
k = st.columns(4)
k[0].metric("Patients measured", f"{int(ps.loc[('los_min', 'all'), 'patients']):,}")
k[1].metric(
    "Arrival to provider, median",
    f"{ps.loc[('arrival_to_provider_min', 'all'), 'median_min']:.0f} min",
)
k[2].metric("LOS discharged, median", f"{ps.loc[('los_min', 'discharged'), 'median_min']:.0f} min")
k[3].metric("LOS admitted, median", f"{ps.loc[('los_min', 'admitted'), 'median_min']:.0f} min")
st.caption("SIMULATED · fictional model · measured days only (warm-up excluded)")

ui.section("Who arrives, and when")
ui.plot(charts.arrivals_by_hour(snap.arrivals, days))

ui.section("Where time goes")
ui.plot(charts.stage_time_bars(t["stage_decomposition"]))

ui.section("Outcomes by acuity")
measure = st.radio(
    "Measure",
    ["arrival_to_provider_min", "los_min"],
    horizontal=True,
    format_func=lambda m: {
        "arrival_to_provider_min": "Arrival to provider",
        "los_min": "Length of stay",
    }[m],
)
ui.plot(
    charts.esi_outcome_dots(
        t["acuity_metrics"], measure=measure, title="Median and 90th percentile by ESI level"
    )
)
st.caption("Medians can look reassuring while the slowest 10% wait much longer — both are shown.")

ui.section("Staff and shifts")
ui.plot(charts.shift_utilization_bars(t["shift_utilization"]))
ui.plot(
    charts.hourly_line(
        t["hourly_profile"],
        column="physician_utilization",
        pct=True,
        title="Physician utilization by hour",
        yaxis="Utilization (%)",
    )
)
ui.plot(
    charts.hourly_line(
        t["hourly_profile"],
        column="physician_queue_mean",
        title="Patients waiting for a physician",
        yaxis="Patients (average)",
    )
)

ui.section("Boarding")
ui.plot(charts.boarding_box(snap.boarding))
bd = t["boarding_distribution"][
    ["group", "admitted_patients", "median", "p90", "max", "share_over_4h"]
]
st.dataframe(
    bd.assign(share_over_4h=bd["share_over_4h"] * 100).rename(
        columns={
            "group": "Group",
            "admitted_patients": "Admitted",
            "median": "Median (min)",
            "p90": "90th pct (min)",
            "max": "Max (min)",
            "share_over_4h": "Over 4 h (%)",
        }
    ),
    hide_index=True,
    width="stretch",
    column_config={
        "Median (min)": st.column_config.NumberColumn(format="%.0f"),
        "90th pct (min)": st.column_config.NumberColumn(format="%.0f"),
        "Max (min)": st.column_config.NumberColumn(format="%.0f"),
        "Over 4 h (%)": st.column_config.NumberColumn(format="%.0f%%"),
    },
)
ui.callout(
    "Boarding comes from a <b>simplified bed-release model</b>: inpatient beds open for ED patients at "
    "an assumed 42 per day, weighted to the afternoon. Admissions decided at night wait for the next "
    "afternoon's releases. This pattern follows from the assumption, not from real hospital data."
)
with st.expander("Extreme waits (unfinished visits counted at time so far)"):
    ew = t["extreme_waits"]
    view = ew[ew["acuity_esi"] == "all"][
        ["measure", "threshold_min", "patients_over", "denominator", "share_over", "max_min"]
    ]
    st.dataframe(
        view.assign(share_over=view["share_over"] * 100).rename(
            columns={
                "measure": "Measure",
                "threshold_min": "Threshold (min)",
                "patients_over": "Patients over",
                "denominator": "Patients",
                "share_over": "Share over (%)",
                "max_min": "Longest (min)",
            }
        ),
        hide_index=True,
        width="stretch",
        column_config={
            "Share over (%)": st.column_config.NumberColumn(format="%.1f%%"),
            "Longest (min)": st.column_config.NumberColumn(format="%.0f"),
        },
    )
ui.footer()
