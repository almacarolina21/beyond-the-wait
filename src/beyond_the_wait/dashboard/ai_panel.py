"""What-If Lab panel: an explicit, verified AI explanation of one comparison.

Flow: selection -> evidence packet (Python) -> button click -> provider
(MOCK by default, LIVE only when enabled) -> local verification -> display.

Nothing is requested on page load or when a selector changes: only the
"Explain these results" button calls a provider. Results are kept in this
browser session's memory, keyed by the packet ID, mode, and model, so a
stale explanation is never shown for a different selection. Nothing is
written to disk or logged.
"""

from __future__ import annotations

import html
import re

import pandas as pd
import streamlit as st

from beyond_the_wait.analysis.results_bundle import BundleLoad
from beyond_the_wait.dashboard import ui
from beyond_the_wait.dashboard.theme import NEUTRAL, PROVENANCE_COLORS, status_chip
from beyond_the_wait.interpretation.evidence import (
    EvidencePacket,
    EvidenceRefused,
    MetricFact,
    build_evidence_packet,
)
from beyond_the_wait.interpretation.faithfulness import ReviewFlag
from beyond_the_wait.interpretation.providers import ai_config, make_provider
from beyond_the_wait.interpretation.service import Outcome, explain
from beyond_the_wait.interpretation.verify import headline, render, render_value

BUTTON_LABEL = "Explain these results"
BANNER = (
    "<b>Decision support for human review. Based on simulated data.</b> Generated text that "
    "explains the fictional model's verified numbers. It is not a recommendation and not "
    "clinical guidance; every number below is inserted by Python from the evidence packet."
)
REVIEW_NOTE = (
    "Every local check passed, but the rules below found wording that goes beyond what the "
    "evidence can prove. Treat the marked sentences as hypotheses for a qualified person to "
    "judge, not as findings."
)
_SECTION_NAMES = {
    "summary": "Summary",
    "key_findings": "Key finding",
    "tradeoffs": "Tradeoff",
    "uncertainties": "Uncertainty",
    "limitations": "Limitation",
    "response": "Whole explanation",
}
_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+!|<>$~])")


def md_escape(text: str) -> str:
    """Show generated text literally: no links, HTML, math, or formatting."""
    return _MD_SPECIAL.sub(r"\\\1", text)


def where(loc: str) -> str:
    """'key_findings[1]' -> 'Key finding 2' (readable location of a flag)."""
    m = re.fullmatch(r"([a-z_]+)(?:\[(\d+)\])?", loc)
    if not m:
        return loc
    name = _SECTION_NAMES.get(m.group(1), m.group(1).replace("_", " ").capitalize())
    return f"{name} {int(m.group(2)) + 1}" if m.group(2) is not None else name


def review_chip() -> str:
    color = "#b77900"
    return (
        f"<span class='btw-status' style='border-color:{color};color:{color}'>"
        "⚑ NEEDS HUMAN REVIEW</span>"
    )


def _secrets() -> object | None:
    try:
        return st.secrets
    except Exception:  # pragma: no cover - no secrets file is the normal case
        return None


def mode_chip(mode: str) -> str:
    label = "LIVE · Claude API" if mode == "LIVE" else "DEMO · mock, not Claude"
    color = PROVENANCE_COLORS["SIMULATED"] if mode == "LIVE" else NEUTRAL
    return (
        f"<span class='btw-status' style='border-color:{color};color:{color}'>"
        f"{html.escape(label)}</span>"
    )


def render_panel(load: BundleLoad, *, scenario_id: str, metric: str, scenario_name: str) -> None:
    ui.section("Explain these results (optional)")
    config = ai_config(_secrets())
    st.markdown(
        f"{mode_chip(config.mode)} <span class='btw-small'>{html.escape(config.note)}</span>",
        unsafe_allow_html=True,
    )
    try:
        packet = build_evidence_packet(
            load, scenario_id=scenario_id, metric=metric, scenario_name=scenario_name
        )
    except EvidenceRefused as exc:
        st.warning(f"No explanation is available for this selection: {exc}.")
        return
    focus = packet.fact(f"M:{metric}")
    st.markdown(
        f"<span class='btw-small'>Uses the evidence packet for <b>{html.escape(scenario_name)}"
        f"</b> with the outcome <b>{html.escape(getattr(focus, 'label', metric))}</b>: "
        f"{len(packet.facts)} facts from the verified results file (packet "
        f"<code>{packet.packet_id}</code>). No patient-level records are included. Nothing is "
        "sent until you click the button.</span>",
        unsafe_allow_html=True,
    )
    key = f"ai::{packet.packet_id}::{config.mode}::{config.model}"
    if st.button(BUTTON_LABEL, key="explain_button", type="primary", icon=":material/psychology:"):
        with st.spinner("Preparing and verifying the explanation…"):
            outcome = explain(packet, make_provider(config, _secrets()))
        st.session_state[key] = outcome
    outcome = st.session_state.get(key)
    if outcome is not None:
        show_outcome(outcome, packet)


def _mark(flags: dict[str, list[ReviewFlag]], loc: str) -> str:
    return " **⚑ needs review**" if loc in flags else ""


def show_outcome(outcome: Outcome, packet: EvidencePacket) -> None:
    if outcome.status == "error":
        st.info(f"Explanation unavailable: {outcome.message} The results above are unaffected.")
        return
    if outcome.status == "rejected" or outcome.interpretation is None:
        st.warning(outcome.message)
        failed = [c for c in outcome.report.checks if not c.passed] if outcome.report else []
        with st.expander("Why it was rejected"):
            st.markdown(
                "The generated text was withheld in full; no part of it is shown. Checks that "
                "failed:\n"
                + "\n".join(f"- **{c.description}** ({len(c.problems)} problem(s))" for c in failed)
            )
        return

    interp = outcome.interpretation
    n_checks = len(outcome.report.checks) if outcome.report else 0
    flags: dict[str, list[ReviewFlag]] = {}
    for f in outcome.flags:
        flags.setdefault(f.loc, []).append(f)
    with st.container(border=True):
        chip = review_chip() if outcome.status == "needs_review" else ""
        st.markdown(
            f"{mode_chip(outcome.mode)}{status_chip('PASS')}{chip} <span class='btw-small'>"
            f"Passed {n_checks} local checks · model: <code>{html.escape(outcome.model)}</code>"
            f" · packet <code>{outcome.packet_id}</code></span>",
            unsafe_allow_html=True,
        )
        ui.callout(BANNER)
        st.markdown(f"**Key result (calculated by Python):** {md_escape(headline(packet))}")
        if outcome.flags:
            st.warning(
                REVIEW_NOTE
                + "\n"
                + "\n".join(
                    f"- **{where(f.loc)}:** {md_escape(f.message.split(': ', 1)[1])}"
                    for f in outcome.flags
                )
            )
        st.markdown("**Generated explanation**")
        st.markdown(md_escape(render(interp.summary, packet)) + _mark(flags, "summary"))

        def cited(section: str, name: str, items: list) -> None:  # noqa: ANN001
            if not items:
                return
            st.markdown(f"**{name}**")
            for i, s in enumerate(items):
                ids = " ".join(f"`{x}`" for x in s.evidence_ids)
                mark = _mark(flags, f"{section}[{i}]")
                st.markdown(f"- {md_escape(render(s.text, packet))}{mark}  \n  Evidence: {ids}")

        cited("key_findings", "Key findings", interp.key_findings)
        cited("tradeoffs", "Tradeoffs inside the fictional model", interp.tradeoffs)
        cited("uncertainties", "Uncertainties and assumptions", interp.uncertainties)
        if interp.questions_for_humans:
            st.markdown("**Questions for qualified people to investigate**")
            for q in interp.questions_for_humans:
                st.markdown(f"- {md_escape(q)}")
        cited("limitations", "Limitations", interp.limitations)
        st.caption(md_escape(interp.disclaimer))
        if outcome.usage and outcome.mode == "LIVE":
            st.caption(
                f"Tokens used: {outcome.usage.get('input_tokens', 0):,} in, "
                f"{outcome.usage.get('output_tokens', 0):,} out."
            )

    used = sorted({i for _, s in interp.statements() for i in s.evidence_ids})
    with st.expander("Evidence behind this explanation (calculated by Python)"):
        measured = [f for i in used if isinstance(f := packet.fact(i), MetricFact)]
        if measured:
            st.table(
                pd.DataFrame(
                    [
                        {
                            "ID": f.id,
                            "Outcome": f.label,
                            "Baseline → with change": f"{render_value(f, 'baseline')} → "
                            f"{render_value(f, 'intervention')}",
                            "Change": render_value(f, "change"),
                        }
                        for f in sorted(measured, key=lambda f: not f.is_focus)
                    ]
                ).set_index("ID")
            )
        for i in used:
            f = packet.fact(i)
            if f is not None and not isinstance(f, MetricFact) and f.kind != "note":
                st.markdown(f"- `{i}` {md_escape(f.text)}")
    with st.expander("Local verification checks"):
        for c in outcome.report.checks if outcome.report else []:
            st.markdown(
                f"{status_chip('PASS' if c.passed else 'BLOCKED')} {c.description}",
                unsafe_allow_html=True,
            )
        st.caption(
            "These checks confirm structure, citations, numbers, units, and known red-flag "
            "wording. They cannot prove that a cited fact supports the sentence, that the "
            "explanation is complete or balanced, or that anything is clinically safe. Schema "
            "validity is not semantic correctness; human review is still required."
        )
