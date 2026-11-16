"""Overview: the problem, the principle, the three kinds of data, and what the app can't do."""

from __future__ import annotations

import streamlit as st

from beyond_the_wait.dashboard import ui
from beyond_the_wait.dashboard.theme import badge

ui.page_header(
    "Beyond the Wait · Bronx ED Access Lab",
    "Why are patients waiting — and what might help?",
    "An educational decision-support lab for exploring emergency department (ED) patient flow. "
    "It pairs <b>real, public</b> Bronx hospital measures with a <b>fictional</b> ED simulation, "
    "so leaders can explore what-if questions without touching real operations or real patients.",
)

st.subheader("The problem")
st.markdown(
    "Two emergency departments can report the same long waits for very different reasons: "
    "admitted patients **boarding** in ED beds while they wait for an inpatient bed, **staffing** "
    "that doesn't match when patients arrive, slow **lab or imaging** results, or delays "
    "**coordinating** discharge. Fixing the wrong one costs money and staff energy. Public "
    "measures show *how long* patients waited, not *why*. A simulation can explore *why* — but "
    "only inside a model whose assumptions you can see."
)

st.subheader("Our principle")
cols = st.columns(3)
for col, (head, body) in zip(
    cols,
    [
        (
            "Python calculates",
            "Every number comes from tested Python: the simulation, the statistics, "
            "and a validation gate that blocks inconsistent results.",
        ),
        (
            "Claude explains",
            "In a later milestone, Claude will explain validated results in plain "
            "language. It will never calculate numbers or invent causes. (Not built yet.)",
        ),
        (
            "Humans decide",
            "Nothing here is a recommendation. Operational and clinical decisions "
            "require qualified people and real-world evidence.",
        ),
    ],
    strict=True,
):
    col.markdown(
        f"<div class='btw-card'><h4>{head}</h4><p>{body}</p></div>", unsafe_allow_html=True
    )

st.subheader("Three kinds of data — always labeled")
cols = st.columns(3)
for col, (label, head, body) in zip(
    cols,
    [
        (
            "PUBLIC",
            "Real, official, aggregate",
            "CMS and NYS Department of Health figures for real "
            "Bronx hospitals. Hospital-level totals and medians only. They describe what happened, not why.",
        ),
        (
            "SYNTHETIC",
            "Fictional inputs",
            "Invented patients and operating assumptions for the fictional "
            "Riverside Commons Community ED. No real person or hospital is described.",
        ),
        (
            "SIMULATED",
            "Model outputs",
            "Waits, lengths of stay, and boarding produced by running the "
            "fictional ED in a discrete-event simulation. Never compared on one chart with PUBLIC data.",
        ),
    ],
    strict=True,
):
    col.markdown(
        f"<div class='btw-card'>{badge(label)}<h4>{head}</h4><p>{body}</p></div>",
        unsafe_allow_html=True,
    )

st.subheader("What this app can and cannot tell you")
can, cannot = st.columns(2)
with can:
    st.markdown("**It can**")
    st.markdown(
        "- Show official Bronx ED measures with their definitions, periods, and gaps\n"
        "- Show how waits build up in a *fictional* ED under stated assumptions\n"
        "- Compare hypothetical interventions across 30 paired simulations, with 95% confidence intervals\n"
        "- Reveal when an intervention **moves** waiting to another stage instead of removing it\n"
        "- Show which conclusions change when uncertain assumptions change"
    )
with cannot:
    st.markdown("**It cannot**")
    st.markdown(
        "- Explain why any real hospital has delays\n"
        "- Predict what would happen in a real ED\n"
        "- Show that any intervention is clinically safe or advisable\n"
        "- Replace clinical judgment, triage, or operational expertise\n"
        "- Report a left-without-being-seen rate for the model (not modeled)"
    )
ui.callout(f"<b>Not a recommendation.</b> {ui.NOT_A_RECOMMENDATION}")

st.subheader("How to use this app")
st.markdown(
    "1. **Bronx Public Data** — the real-world context, with measure definitions and caveats.\n"
    "2. **Fictional Patient Flow** — one simulated week in the fictional ED: arrivals, stages, staffing, boarding.\n"
    "3. **What-If Lab** — hypothetical interventions compared with the baseline across 30 paired seeds.\n"
    "4. **Sensitivity, Validation & Methods** — which findings depend on assumptions, how results are "
    "validated, and the safety limitations."
)
load = ui.results()
if load.status == "ok":
    ui.status_line(
        "PASS",
        f"Precomputed multi-seed results loaded and verified ({load.bundle.n_seeds} paired seeds).",
    )
else:
    st.caption("Multi-seed results are not built yet; the What-If Lab explains how to build them.")
ui.footer()
