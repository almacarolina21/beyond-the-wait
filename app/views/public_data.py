"""Bronx Public Data: official CMS and NYS aggregate measures (PUBLIC only)."""

from __future__ import annotations

import streamlit as st

from beyond_the_wait.dashboard import charts, ui
from beyond_the_wait.dashboard import data as d

ui.page_header(
    "Real world",
    "Bronx public ED measures",
    "Official, aggregate figures for five Bronx hospitals from CMS Care Compare and the New York "
    "State Department of Health. These describe <b>what</b> was reported — not <b>why</b>.",
    badges=("PUBLIC",),
)
ui.callout(
    "<b>How to read this page.</b> These are real hospitals, so no simulated or fictional value appears "
    "here. A longer median does not show the cause of delay: the measures leave out key factors "
    "(OP-18 excludes admitted patients, so it cannot show boarding), cover different years, and "
    "are not adjusted for patient mix. Do not rank hospitals with them."
)

load = ui.public_data()
if load.status != "ok":
    st.error("Public data could not be loaded or failed verification:")
    for p in load.problems:
        st.markdown(f"- {p}")
    st.code("python -m beyond_the_wait.data.public_pipeline build", language="bash")
    st.stop()

measures, facilities = load.data.measures, load.data.facilities
rows = d.cms_measure_rows(measures)

st.subheader("Reporting periods differ")
periods = d.reporting_periods(measures)
c1, c2 = st.columns([2, 3])
c1.dataframe(
    periods.rename(columns={"measure_id": "Measure", "measure": "Name", "period": "Period"}),
    hide_index=True,
    width="stretch",
)
c2.markdown(
    "OP-18 (time in the ED) covers **calendar 2025**; OP-22 (left without being seen) and ED volume "
    "cover **calendar 2024**. Putting them side by side mixes years, so each chart below states its "
    "own period."
)

tabs = st.tabs(
    [
        "ED time (OP-18)",
        "Left without being seen (OP-22)",
        "ED volume",
        "NYS encounter counts",
        "Facility matching",
        "Definitions & sources",
    ]
)

with tabs[0]:
    choice = st.radio(
        "OP-18 variant",
        ["OP_18b", "OP_18a", "OP_18c", "OP_18d"],
        format_func=lambda m: f"{m} · {rows.loc[rows['measure_id'] == m, 'short_name'].iloc[0]}",
        horizontal=True,
    )
    ui.plot(
        charts.public_measure_bar(
            rows, measure_id=choice, unit_label="Median minutes, arrival to departure"
        )
    )
    ui.callout(
        "<b>OP-18 counts only patients who went home</b> (visits ending in admission at the same "
        "hospital are excluded), so it does not measure boarding or door-to-provider time. "
        "'Not available' values are shown as text with their CMS footnote, never as zero."
    )

with tabs[1]:
    ui.plot(
        charts.public_measure_bar(rows, measure_id="OP_22", unit_label="Percent of ED patients")
    )
    ui.callout(
        "OP-22 is the share of patients who left before evaluation by a physician, advanced practice "
        "nurse, or physician assistant. It is a different measure from OP-18, and neither one is "
        "door-to-provider time, which no reviewed public source reports."
    )

with tabs[2]:
    edv = rows[rows["measure_id"] == "EDV"][["facility_name", "facility_id", "display", "period"]]
    st.dataframe(
        edv.rename(
            columns={
                "facility_name": "Hospital (CMS name)",
                "facility_id": "CMS CCN",
                "display": "ED volume category",
                "period": "Period",
            }
        ),
        hide_index=True,
        width="stretch",
    )
    st.caption(
        "CMS reports ED volume as a category, not a count; it is not converted to numbers here."
    )

with tabs[3]:
    a, b = st.columns(2)
    year = a.selectbox("Calendar year", [2025, 2024])
    nys = d.nys_rows(measures, year=year)
    measure = b.selectbox(
        "Count",
        d.NYS_MEASURE_ORDER,
        format_func=lambda m: nys.loc[nys["measure_id"] == m, "short_name"].iloc[0],
    )
    ui.plot(charts.nys_counts_bar(nys, measure_id=measure, year=year))
    ui.callout(
        "These are <b>counts, not times</b>. They are reported per NYS site (PFI), which does not map "
        "one-to-one onto CMS hospital records — see Facility matching. Quarterly values NYS redacted "
        "appear as <b>Suppressed (S)</b>; calendar-year totals are shown here."
    )

with tabs[4]:
    ui.callout(
        "<b>A matched address is not a matched scope.</b> CMS reports one result per certification "
        "number, which can cover several NYS sites. For example, BronxCare's CMS record matches its "
        "Fulton Avenue site, but CMS's OP-22 figure is based on far more patients than that site "
        "reported to NYS. Never divide CMS figures by NYS site counts."
    )
    st.dataframe(d.crosswalk_view(facilities), hide_index=True, width="stretch")

with tabs[5]:
    st.dataframe(
        d.measure_definitions(d.CMS_MEASURE_ORDER + d.NYS_MEASURE_ORDER),
        hide_index=True,
        width="stretch",
    )
    st.markdown(
        "**Source files used** (fingerprints verified when the reference tables were built):"
    )
    inputs = load.data.manifest.get("inputs", {})
    st.dataframe(
        [
            {
                "Dataset": k,
                "Retrieved": v["retrieved_on"],
                "Publisher last modified": v["source_last_modified"],
                "URL": v["download_url"],
            }
            for k, v in inputs.items()
        ],
        hide_index=True,
        width="stretch",
    )
    st.caption("Full method: docs/PUBLIC_DATA_METHODOLOGY.md · sources: data/public/SOURCES.md")

with st.expander("All CMS values in one table (as published)"):
    st.dataframe(d.cms_wide_table(measures), hide_index=True, width="stretch")
ui.footer()
