"""Overview: purpose, verified headline findings, a path into the What-If Lab, then detail."""

from __future__ import annotations

import streamlit as st

from beyond_the_wait.dashboard import data as d
from beyond_the_wait.dashboard import ui
from beyond_the_wait.dashboard.theme import badge

ui.page_header(
    "Beyond the Wait · Bronx ED Access Lab",
    "Why are patients waiting — and what might help?",
    "An educational lab for emergency department (ED) patient flow. It sets <b>real, public</b> "
    "Bronx measures beside a <b>fictional</b> ED simulation, so leaders can test what-if "
    "questions without touching real operations or real patients.",
)
ui.principle()

# --- Above the fold: what the fictional model shows, and where to go next -------------------
load = ui.results()
if load.status == "ok":
    st.subheader("What the fictional model shows")
    st.markdown(
        "<span class='btw-small'>Three precomputed comparisons from the <b>fictional</b> "
        f"Riverside Commons ED, each across {load.bundle.n_seeds} paired simulated weeks. "
        "Model behavior under stated assumptions, not evidence about any real hospital.</span>",
        unsafe_allow_html=True,
    )
    findings = d.key_findings(load.bundle)
    for col, f in zip(st.columns(len(findings) or 1), findings, strict=False):
        with col:
            ui.finding_card(f["title"], f["text"], f["status"])
    st.markdown(
        "<span class='btw-small'>CAUTION means the gate found issues worth reading (for "
        "example, overloaded simulated weeks), not errors. Details are in the What-If Lab.</span>",
        unsafe_allow_html=True,
    )
else:
    st.info("Multi-seed results are not built yet; the What-If Lab explains how to build them.")

c1, c2, c3 = st.columns(3)
with c1:
    ui.page_link("what_if", "**Open the What-If Lab →**", icon=":material/science:")
with c2:
    ui.page_link("patient_flow", "See one simulated week", icon=":material/timeline:")
with c3:
    ui.page_link("public_data", "Explore Bronx public data", icon=":material/public:")

# --- Below the fold: context, data types, limits ----------------------------------------------
ui.section("Three kinds of data, always labeled")
cards = [
    (
        "PUBLIC",
        "Real, official, aggregate",
        "CMS and NYS Department of Health figures for real Bronx hospitals. They show how "
        "long patients waited, not why.",
    ),
    (
        "SYNTHETIC",
        "Fictional inputs",
        "Invented patients and assumptions for the fictional Riverside Commons Community ED. "
        "No real person or hospital is described.",
    ),
    (
        "SIMULATED",
        "Model outputs",
        "Waits, lengths of stay, and boarding from running the fictional ED. Never placed on "
        "one chart with PUBLIC data.",
    ),
]
for col, (label, head, body) in zip(st.columns(3), cards, strict=True):
    col.markdown(
        f"<div class='btw-card'>{badge(label)}<h4>{head}</h4><p>{body}</p></div>",
        unsafe_allow_html=True,
    )

ui.section("What this app can and cannot tell you")
can, cannot = st.columns(2)
with can:
    st.markdown(
        "**It can**\n"
        "- Show official Bronx ED measures with definitions, periods, and gaps\n"
        "- Show how waits build up in a *fictional* ED under stated assumptions\n"
        "- Compare hypothetical changes across paired simulations, with 95% intervals\n"
        "- Reveal when a change **moves** waiting to another stage\n"
        "- Show which findings depend on uncertain assumptions"
    )
with cannot:
    st.markdown(
        "**It cannot**\n"
        "- Explain why any real hospital has delays\n"
        "- Predict what would happen in a real ED\n"
        "- Show that any change is clinically safe or advisable\n"
        "- Replace clinical judgment, triage, or operational expertise\n"
        "- Report a left-without-being-seen rate for the model (not modeled)"
    )
ui.callout(f"<b>Not a recommendation.</b> {ui.NOT_A_RECOMMENDATION}")

with st.expander("The problem this lab explores"):
    st.markdown(
        "Two EDs can report the same long waits for very different reasons: admitted patients "
        "**boarding** in ED beds, **staffing** that doesn't match arrivals, slow **lab or "
        "imaging** results, or slow **discharge**. Fixing the wrong one wastes money and staff "
        "energy. Public measures show *how long* patients waited, not *why*. A simulation can "
        "explore *why*, but only inside a model whose assumptions you can see."
    )
with st.expander("How the principle works in practice"):
    st.markdown(
        "- **Python calculates:** the simulation, the paired statistics, and a validation gate "
        "that blocks inconsistent results are all tested Python code.\n"
        "- **Claude explains:** in Milestone 8, Claude will describe validated results in plain "
        "language. It will never calculate numbers or invent causes. *Not built yet; no API key "
        "is used anywhere in this app.*\n"
        "- **Humans decide:** operational and clinical decisions need qualified people and "
        "real-world evidence."
    )

ui.verification_line(load)
ui.footer()
