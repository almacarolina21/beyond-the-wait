"""Sensitivity, Validation & Methods."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from beyond_the_wait.analysis.validation import SAFETY_LIMITATIONS
from beyond_the_wait.dashboard import charts, ui
from beyond_the_wait.dashboard import data as d
from beyond_the_wait.dashboard.theme import status_chip

ui.page_header(
    "Fictional model",
    "Sensitivity, validation & methods",
    "Which findings depend on assumptions we cannot verify, how every result is checked before it "
    "is shown, and the limits that apply to all of it.",
    badges=("SIMULATED",),
)
ui.esi1_callout()

load = ui.results()
bundle = load.bundle if load.status == "ok" else None
names = d.scenario_names()

st.header("Sensitivity to uncertain assumptions")
if bundle is None:
    ui.results_unavailable(load)
elif bundle.sensitivity is None:
    st.info(
        "The results bundle was built without the sensitivity grid. Rebuild without --skip-sensitivity:"
    )
    st.code(d.BUILD_COMMAND, language="bash")
else:
    st.markdown(
        "Two assumptions shape the model and cannot be verified: **how many patients one physician "
        "can evaluate at once** (1–3) and **how many inpatient beds open for ED patients per day** "
        f"(38–46). Each intervention was re-tested under every combination "
        f"({bundle.manifest.get('sensitivity_seeds')} paired seeds each)."
    )
    sens = bundle.sensitivity
    a, b = st.columns(2)
    intervention = a.selectbox(
        "Intervention", sorted(sens["intervention"].unique()), format_func=lambda s: names.get(s, s)
    )
    metric_names = {
        "arrival_to_provider_p90": "Arrival to provider, p90",
        "los_median_lower_bound_all": "Length of stay, all (incl. unfinished)",
        "boarding_p90": "Boarding, p90",
    }
    metric = b.selectbox(
        "Outcome", sorted(sens["metric"].unique()), format_func=lambda m: metric_names.get(m, m)
    )
    grid = d.sensitivity_grid(bundle, intervention=intervention, metric=metric)
    ui.plot(
        charts.sensitivity_heatmap(
            grid,
            title=f"{names.get(intervention, intervention)} · {metric_names.get(metric, metric)}",
        )
    )
    ui.callout(
        "<b>Stress regime.</b> Cells marked 'stress' had at least one overloaded simulated week (queues "
        "kept growing). Those describe how the model breaks down, not ordinary operation, and are "
        "excluded from the stability verdicts below. 'lower' / 'HIGHER' describe the model's numbers, "
        "not whether a change is good or safe."
    )
    stab = bundle.stability
    if stab is not None:
        st.markdown("**Stability across operating (non-stress) settings**")
        flag = {
            "unstable": "⚠ unstable — direction depends on assumptions",
            "reverses": "⚠ reverses — opposite directions under different assumptions",
            "no_support": "no supported effect under any setting",
            "stable_improves": "stable — lower in the model under every setting",
            "stable_worsens": "stable — higher in the model under every setting",
        }
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Intervention": names.get(r.intervention, r.intervention),
                        "Outcome": metric_names.get(r.metric, r.metric),
                        "Settings": r.settings,
                        "Verdict": flag.get(r.verdict, r.verdict),
                    }
                    for r in stab.itertuples()
                ]
            ),
            hide_index=True,
            width="stretch",
        )

st.header("How results are validated")
st.markdown(
    f"{status_chip('PASS')} internally consistent and within scope &nbsp; "
    f"{status_chip('CAUTION')} consistent, but interpretable only with stated limitations &nbsp; "
    f"{status_chip('BLOCKED')} corrupted or inconsistent — never shown as results",
    unsafe_allow_html=True,
)
st.markdown(
    "Every simulated run passes through a validation gate before any number is shown: patients are "
    "conserved, events happen in order with sensible units, no resource is double-booked, summaries "
    "match the event log, and labels are correct. A failure **blocks** the run. Comparisons containing "
    "a blocked run are blocked too. Overloaded but consistent runs are kept as **stress tests** and "
    "labeled CAUTION. Every result here is at least CAUTION, because it comes from a fictional model "
    "with unvalidated assumptions."
)
snap = ui.flow(42, 8)
with st.expander(
    f"Example: the gate's checks for one baseline run (seed 42) · {snap.report.status.value}"
):
    for c in snap.report.checks:
        st.markdown(
            f"{status_chip(c.status.value)} <b>{c.check}</b> ({c.category}) — {c.detail}<br>"
            f"<span style='color:#52514e'>Protects against: {c.protects_against}. "
            f"Cannot prove: {c.cannot_prove}.</span>",
            unsafe_allow_html=True,
        )

st.header("Confidence interval vs. prediction interval")
st.markdown(
    "- A **95% confidence interval of the mean difference** describes the *average* effect over many "
    "simulated weeks. It gets narrower with more seeds.\n"
    "- A **95% prediction interval** describes how different *one future simulated week* could be. It "
    "stays wide no matter how many seeds are run.\n"
    "- Neither describes a real hospital."
)
if bundle is not None:
    rows = d.comparison_rows(bundle, scope="main", metric="arrival_to_provider_p90")
    if not rows.empty:
        row = rows.iloc[0]
        ui.plot(charts.ci_vs_pi_chart(row, scenario_name=row["scenario_name"]))

st.header("Model and data limitations")
for item in SAFETY_LIMITATIONS:
    head, _, rest = item.partition(":")
    st.markdown(f"- **{head.title()}:** {rest.strip()}")
st.markdown(
    "- **Synthetic assumptions:** arrival patterns, acuity mix, task durations, and staffing are "
    "illustrative values, not measurements, and are not calibrated to any real hospital.\n"
    "- **Boarding model:** inpatient beds open for ED patients as a random stream (42/day by default, "
    "afternoon-weighted); 300 beds at 93% occupancy is *not* treated as 21 free beds."
)

st.header("Methodology documents")
st.markdown(
    "- `docs/PUBLIC_DATA_METHODOLOGY.md` — public measures, definitions, matching, limitations\n"
    "- `docs/SYNTHETIC_DATA_ASSUMPTIONS.md` — every synthetic assumption\n"
    "- `docs/SIMULATION_MODEL.md` — journey, queues, boarding model, stopping rule\n"
    "- `docs/VALIDATION_GATE.md` — PASS / CAUTION / BLOCKED checks\n"
    "- `docs/WHAT_IF_ANALYSIS.md` — paired multi-seed method, results, sensitivity"
)
if bundle is not None:
    s = bundle.manifest["settings"]
    st.caption(
        f"Results bundle: {len(s['seeds'])} paired seeds × {s['arrival_days']} days "
        f"({s['warmup_days']} warm-up), package {bundle.manifest['package_version']}, every file "
        "fingerprint verified on load."
    )
ui.footer()
