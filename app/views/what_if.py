"""What-If Lab: precomputed multi-seed paired comparisons (SIMULATED).

Order: title -> short disclaimer -> outcome chart -> plain reading -> one intervention
in detail -> optional AI explanation (button) -> archetypes -> methods and validation.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from beyond_the_wait.analysis.check_descriptions import describe_check
from beyond_the_wait.analysis.experiments import MODEL_EVIDENCE_NOTE
from beyond_the_wait.dashboard import ai_panel, charts, ui
from beyond_the_wait.dashboard import data as d
from beyond_the_wait.dashboard.theme import direction_label, status_chip

ui.page_header(
    "Fictional model",
    "What-If Lab",
    "Compare hypothetical changes with the fictional ED's baseline. Each change was simulated "
    "on the <b>same</b> random weeks as the baseline, which removes much of the week-to-week "
    "chance variation from the comparison; the 95% intervals show the uncertainty that remains.",
    badges=("SIMULATED",),
)
load = ui.results()
if ui.results_unavailable(load):
    ui.footer()
    st.stop()
bundle = load.bundle
names = d.scenario_names()

ui.callout(
    "<b>Fictional model only.</b> These results show how the model behaves under its "
    "assumptions, not what would happen in a real ED. Nothing here is a recommendation."
)
if bundle.blocked:
    st.error(
        "BLOCKED by the validation gate and therefore not shown as data: "
        + ", ".join(f"{b['scenario_id']} ({b['scope']})" for b in bundle.blocked)
    )
main = [
    c for c in bundle.manifest["comparisons"] if c["scope"] == "main" and c["status"] != "BLOCKED"
]

# --- 1. One outcome, all interventions --------------------------------------------------------
st.subheader("Compare interventions on one outcome")
metric = st.selectbox(
    "Outcome", list(d.METRIC_LABELS), format_func=d.METRIC_LABELS.get, key="metric"
)
rows = d.comparison_rows(bundle, scope="main", metric=metric)
ui.plot(charts.forest(rows, title=d.METRIC_LABELS[metric], n_seeds=bundle.n_seeds))

st.markdown("**What this shows**")
for line in d.interpretation(rows, metric_label=d.METRIC_LABELS[metric]):
    st.markdown(f"- {line}")
st.caption(
    "Generated from the verified results file. 'Lower' describes the number in the model; "
    "it does not mean safer or better for patients."
)

# --- 2. One intervention in detail ------------------------------------------------------------
ui.section("One intervention in detail")
scenario = st.selectbox(
    "Intervention",
    [c["scenario_id"] for c in main],
    format_func=lambda s: names.get(s, s),
    key="scenario",
)
meta = d.comparison_meta(bundle, scope="main", scenario_id=scenario)
checks = d.comparison_checks(bundle, scope="main", scenario_id=scenario)
effects = meta.get("effects", {})
st.markdown(
    f"{status_chip(meta.get('status', 'CAUTION'))} "
    f"<span class='btw-small'><b>What changes in the model:</b> "
    f"{', '.join(effects.get('simulated', [])) or 'n/a'}"
    + (
        f" · <b>Uncertain assumption:</b> {', '.join(effects['assumptions'])}"
        if effects.get("assumptions")
        else ""
    )
    + "</span>",
    unsafe_allow_html=True,
)
if meta.get("status") == "CAUTION":
    reasons = [describe_check(c) for c in sorted(checks["check"].unique())]
    st.markdown(
        "<span class='btw-small'><b>Why CAUTION:</b> read these before relying on this "
        "comparison (details under Methods and validation below).</span>",
        unsafe_allow_html=True,
    )
    st.markdown("\n".join(f"- {r}" for r in reasons) or "- See the validation details below.")

detail = d.comparison_rows(bundle, scope="main", scenario_id=scenario)
detail = detail[detail["metric"].isin(d.METRIC_LABELS)].copy()
detail["order"] = detail["metric"].map({m: i for i, m in enumerate(d.METRIC_LABELS)})
TABLE_WORD = {
    "Lower within the fictional model": "Lower",
    "Higher within the fictional model": "Higher",
}
table = []
for r in detail.sort_values("order").itertuples():
    scale, unit = d.display_scale(r.unit)
    unit = "%" if r.unit == "fraction" else unit
    word = direction_label(r.direction, r.mean_difference)
    table.append(
        {
            "Outcome": d.METRIC_LABELS[r.metric],
            "Baseline → with change": f"{r.baseline_mean * scale:.1f} → "
            f"{r.scenario_mean * scale:.1f} {unit}",
            "Change [95% CI]": f"{r.mean_difference * scale:+.1f} "
            f"[{r.ci95_low * scale:+.1f}, {r.ci95_high * scale:+.1f}]",
            "In the model": TABLE_WORD.get(word, word.split(" (")[0]),
        }
    )
# Static table: wraps text to the page width instead of scrolling sideways.
st.table(pd.DataFrame(table).set_index("Outcome"))
st.caption(
    "'Lower' and 'Higher' are within the fictional model; 'No detectable change' means the 95% CI "
    "includes 0. Shares change in percentage points. Utilization is workload, not quality."
)

stages = d.stage_effects(bundle, scope="main", scenario_id=scenario)
ui.plot(charts.stage_shift_chart(stages, scenario_name=names.get(scenario, scenario)))
if d.moves_congestion(stages):
    ui.callout(
        "<b>Congestion moves rather than disappears.</b> In the model, this change shortens one "
        "waiting stage while another gets longer: patients reach the next queue sooner and wait "
        "there instead. Check total length of stay before drawing conclusions."
    )

# --- 3. Optional AI explanation (explicit button; verified before display) ----------------
ai_panel.render_panel(
    load, scenario_id=scenario, metric=metric, scenario_name=names.get(scenario, scenario)
)

# --- 4. Archetypes ----------------------------------------------------------------------------
ui.section("Archetypes: is the biggest delay the real constraint?")
st.markdown(
    "Four **fictional** problem conditions, each paired with a relief change. A constraint is "
    "called *binding* (inside the model) only when relieving it measurably lowers the outcome, "
    "not because it is the largest stage."
)
ui.plot(charts.archetype_chart(bundle.archetypes, names))
arch_rows = []
for r in bundle.archetypes.itertuples():
    arch_rows.append(
        {
            "Archetype": d.short_label(r.archetype),
            "Outcome": d.METRIC_LABELS.get(r.outcome, r.outcome),
            "Relief effect, min [95% CI]": f"{r.relief_effect:+.0f} "
            f"[{r.relief_ci_low:+.0f}, {r.relief_ci_high:+.0f}]",
            "Binding in the model?": "Supported"
            if r.relief_direction == "improves"
            else "Not supported",
        }
    )
# Static table: wraps text to the page width instead of scrolling sideways.
st.table(pd.DataFrame(arch_rows).set_index("Archetype"))
st.caption(
    "In most archetypes the largest stage (boarding) was not the constraint whose relief "
    "lowered the outcome studied. The largest stage is not automatically the cause."
    if (bundle.archetypes["largest_stage"] == "stage_boarding_mean").all()
    else "The largest stage is not automatically the constraint whose relief helps."
)

# --- 5. Methods and validation details --------------------------------------------------------
ui.section("Methods and validation")
with st.expander(f"Validation details: {names.get(scenario, scenario)} · {meta.get('status')}"):
    st.markdown(f"Replication gate status: `{meta.get('replication_status')}`")
    st.markdown(f"Overloaded simulated weeks: `{meta.get('overloaded_replications')}`")
    st.markdown(
        "CAUTION checks carried from individual runs: "
        + (
            ", ".join(f"{k} ×{v}" for k, v in sorted(meta.get("carried_cautions", {}).items()))
            or "none"
        )
    )
    ui.checks_table(checks)
with st.expander("All interventions and what they change"):
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Intervention (hypothetical)": names.get(c["scenario_id"], c["scenario_id"]),
                    "What changes in the model": ", ".join(c["effects"].get("simulated", [])),
                    "Type": "Uncertain assumption"
                    if c["effects"].get("assumptions")
                    else "Operational lever",
                    "Status": c["status"],
                }
                for c in main
            ]
        ),
        hide_index=True,
        width="stretch",
    )
    st.caption(
        "Uncertain assumptions (patients per physician at once, inpatient bed releases, demand) "
        "are not interventions; they are varied on the Sensitivity page."
    )
with st.expander("How to read these results"):
    st.markdown(
        f"- {MODEL_EVIDENCE_NOTE}\n"
        f"- Each dot is the **average change** across {bundle.n_seeds} paired simulated weeks "
        "(change minus baseline).\n"
        "- The bar is the **95% confidence interval of that average**. If it crosses the dotted "
        "zero line, the model shows no detectable change.\n"
        "- A single future simulated week can differ much more than this interval suggests: "
        "see confidence vs. prediction intervals on the Methods page.\n"
        f"- {ui.NOT_A_RECOMMENDATION}"
    )
ui.footer()
