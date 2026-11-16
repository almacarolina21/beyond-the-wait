"""What-If Lab: precomputed multi-seed paired comparisons (SIMULATED)."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from beyond_the_wait.dashboard import charts, ui
from beyond_the_wait.dashboard import data as d
from beyond_the_wait.dashboard.theme import DIRECTION_TEXT

ui.page_header(
    "Fictional model",
    "What-If Lab",
    "Each hypothetical intervention was simulated alongside the baseline on the <b>same</b> 30 random "
    "weeks, so differences come from the intervention, not luck. Results describe the "
    "<b>fictional model only</b>.",
    badges=("SIMULATED",),
)
load = ui.results()
if ui.results_unavailable(load):
    ui.footer()
    st.stop()
bundle = load.bundle
names = d.scenario_names()

ui.callout(
    f"<b>Model-internal evidence only.</b> {bundle.manifest['evidence_note']} {ui.NOT_A_RECOMMENDATION}"
)
if bundle.blocked:
    st.error(
        "Some comparisons were BLOCKED by the validation gate and are not shown: "
        + ", ".join(f"{b['scenario_id']} ({b['scope']})" for b in bundle.blocked)
    )

main = [
    c for c in bundle.manifest["comparisons"] if c["scope"] == "main" and c["status"] != "BLOCKED"
]
st.subheader("The interventions")
st.dataframe(
    pd.DataFrame(
        [
            {
                "Intervention (hypothetical)": names.get(c["scenario_id"], c["scenario_id"]),
                "What changes in the model": ", ".join(c["effects"].get("simulated", [])),
                "Type": "Controllable operational input"
                if not c["effects"].get("assumptions")
                else "Uncertain assumption",
                "Comparison status": c["status"],
            }
            for c in main
        ]
    ),
    hide_index=True,
    width="stretch",
)
st.caption(
    "Uncertain assumptions (how many patients a physician can handle at once, how many inpatient beds "
    "open per day, demand) are not interventions; they are varied on the Sensitivity page."
)

with st.expander("How to read these charts", expanded=False):
    st.markdown(
        "- Each dot is the **average change** across 30 paired simulated weeks (intervention minus baseline).\n"
        "- The bar is the **95% confidence interval of that average**: the range of average effects "
        "consistent with the simulations. If it crosses the dotted zero line, the model shows no "
        "detectable effect.\n"
        "- Negative means **lower in the model** (shorter waits). That is a property of the model, "
        "not a verdict that an intervention is safe or advisable.\n"
        "- A single future simulated week can differ much more than the interval suggests — see "
        "confidence vs. prediction intervals on the Methods page."
    )

st.subheader("Compare interventions on one outcome")
metric_labels = {
    "arrival_to_provider_p90": "Arrival to provider, 90th percentile",
    "arrival_to_provider_median": "Arrival to provider, median",
    "los_median_discharged": "Length of stay, discharged, median",
    "los_median_admitted": "Length of stay, admitted, median",
    "los_median_lower_bound_all": "Length of stay, all (includes unfinished visits)",
    "boarding_p90": "Boarding, 90th percentile",
    "boarding_share_over_4h": "Admitted patients boarding over 4 hours (share)",
    "provider_wait_p90": "Waiting for a physician, 90th percentile",
    "triage_wait_p90": "Waiting for triage, 90th percentile",
    "physician_utilization": "Physician utilization",
    "triage_utilization": "Triage-nurse utilization",
}
metric = st.selectbox("Outcome", list(metric_labels), format_func=metric_labels.get)
rows = d.comparison_rows(bundle, scope="main", metric=metric)
ui.plot(charts.forest(rows, title=metric_labels[metric], n_seeds=bundle.n_seeds))

st.subheader("One intervention in detail")
scenario = st.selectbox(
    "Intervention", [c["scenario_id"] for c in main], format_func=lambda s: names.get(s, s)
)
detail = d.comparison_rows(bundle, scope="main", scenario_id=scenario)
detail = detail[detail["metric"].isin(metric_labels)].copy()
detail["order"] = detail["metric"].map({m: i for i, m in enumerate(metric_labels)})
table = []
for r in detail.sort_values("order").itertuples():
    scale, unit = d.display_scale(r.unit)
    table.append(
        {
            "Outcome": metric_labels[r.metric],
            "Unit": "%" if r.unit == "fraction" else unit,
            "Baseline": round(r.baseline_mean * scale, 1),
            "Intervention": round(r.scenario_mean * scale, 1),
            "Change": round(r.mean_difference * scale, 2),
            "95% CI of mean change": f"[{r.ci95_low * scale:+.2f}, {r.ci95_high * scale:+.2f}]",
            "In the model": DIRECTION_TEXT[r.direction],
        }
    )
st.dataframe(pd.DataFrame(table), hide_index=True, width="stretch")
st.caption(
    "Utilization changes are reported as 'changed': lower utilization is neither better nor worse by itself."
)

stages = d.stage_effects(bundle, scope="main", scenario_id=scenario)
ui.plot(charts.stage_shift_chart(stages, scenario_name=names.get(scenario, scenario)))
if d.moves_congestion(stages):
    ui.callout(
        "<b>Congestion moves rather than disappears.</b> In the model, this intervention shortens one "
        "waiting stage while another gets longer — patients reach the next queue sooner and wait there "
        "instead. Look at total length of stay before concluding anything.",
        kind="note",
    )

meta = d.comparison_meta(bundle, scope="main", scenario_id=scenario)
with st.expander(f"Validation for this comparison · {meta.get('status', '?')}"):
    st.markdown(f"Replication gate status: `{meta.get('replication_status')}`")
    st.markdown(f"Overloaded replications: `{meta.get('overloaded_replications')}`")
    st.markdown(
        "CAUTION checks carried from individual runs: "
        + ", ".join(f"{k} ×{v}" for k, v in sorted(meta.get("carried_cautions", {}).items()))
    )
    ui.checks_table(d.comparison_checks(bundle, scope="main", scenario_id=scenario))

st.subheader("Archetypes: is the biggest delay the real constraint?")
st.markdown(
    "Four **fictional** problem conditions were built, and each was paired with a relief intervention. "
    "A constraint is called *binding* (inside the model) only when relieving it measurably improves "
    "the outcome — not because it is the largest stage."
)
ui.plot(charts.archetype_chart(bundle.archetypes, names))
arch = bundle.archetypes.copy()
st.dataframe(
    pd.DataFrame(
        [
            {
                "Archetype": names.get(r.archetype, r.archetype),
                "Relief": names.get(r.relief, r.relief),
                "Outcome": r.outcome,
                "Relief effect": f"{r.relief_effect:+.1f} [{r.relief_ci_low:+.1f}, {r.relief_ci_high:+.1f}]",
                "Binding in the model?": "Supported"
                if r.relief_direction == "improves"
                else "Not supported",
                "Largest stage": r.largest_stage.replace("stage_", "")
                .replace("_mean", "")
                .replace("_", " "),
            }
            for r in arch.itertuples()
        ]
    ),
    hide_index=True,
    width="stretch",
)
st.caption(
    "In most archetypes the largest stage (boarding) was not the constraint whose relief helped the outcome studied."
)
ui.footer()
