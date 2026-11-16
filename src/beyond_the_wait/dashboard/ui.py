"""Streamlit helpers shared by all pages: headers, callouts, caching, chart display."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from beyond_the_wait.analysis.validation import SAFETY_LIMITATIONS
from beyond_the_wait.dashboard import data as d
from beyond_the_wait.dashboard.theme import CSS, badge, status_chip

ESI1_LIMITATION = next(t for t in SAFETY_LIMITATIONS if t.startswith("ESI-1"))
NOT_A_RECOMMENDATION = next(t for t in SAFETY_LIMITATIONS if t.startswith("NOT A STAFFING"))


def apply_style() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def page_header(eyebrow: str, title: str, lede: str, badges: tuple[str, ...] = ()) -> None:
    chips = "".join(badge(b) for b in badges)
    st.markdown(f"<div class='btw-eyebrow'>{eyebrow}</div>", unsafe_allow_html=True)
    st.title(title)
    st.markdown(f"{chips}<p class='btw-lede'>{lede}</p>", unsafe_allow_html=True)


def callout(text: str, kind: str = "note") -> None:
    cls = "btw-callout" if kind == "safety" else "btw-note"
    st.markdown(f"<div class='{cls}'>{text}</div>", unsafe_allow_html=True)


# Page registry, filled by app/streamlit_app.py so pages can link to each other.
# Empty when a page script runs on its own (e.g. in tests); links then render as text.
PAGES: dict[str, object] = {}


def page_link(key: str, label: str, icon: str | None = None) -> None:
    page = PAGES.get(key)
    if page is None:
        st.markdown(f"**{label}** (use the sidebar)")
    else:
        st.page_link(page, label=label, icon=icon)


def section(title: str) -> None:
    """A section heading with consistent spacing and a hairline divider."""
    st.markdown("<div class='btw-section'></div>", unsafe_allow_html=True)
    st.subheader(title)


def finding_card(title: str, text: str, status: str) -> None:
    st.markdown(
        f"<div class='btw-finding'>{badge('SIMULATED')}{status_chip(status)}"
        f"<div class='btw-finding-title'>{title}</div><p>{text}</p></div>",
        unsafe_allow_html=True,
    )


def principle() -> None:
    st.markdown(
        "<div class='btw-principle'><span><b>Python calculates.</b> Tested code produces "
        "every number.</span><span><b>Claude explains.</b> Planned for Milestone 8; not "
        "built yet.</span><span><b>Humans decide.</b> Nothing here is a recommendation."
        "</span></div>",
        unsafe_allow_html=True,
    )


def verification_line(load) -> None:  # noqa: ANN001
    """Bundle file checks passed. Explicitly NOT a clinical-safety validation."""
    if load.status == "ok":
        st.markdown(
            f"{status_chip('PASS')} <span class='btw-small'>Results files verified "
            f"({load.bundle.n_seeds} paired seeds; fingerprints and labels checked). File "
            "verification only: it is <b>not</b> clinical or real-world validation.</span>",
            unsafe_allow_html=True,
        )


def esi1_callout() -> None:
    callout(f"<b>Safety limitation.</b> {ESI1_LIMITATION}", kind="safety")


def plot(fig: go.Figure, key: str | None = None) -> None:
    st.plotly_chart(
        fig,
        width="stretch",
        theme=None,
        key=key,
        config={"displaylogo": False, "modeBarButtonsToRemove": ["lasso2d", "select2d"]},
    )


def status_line(status: str, text: str = "") -> None:
    st.markdown(f"{status_chip(status)} {text}", unsafe_allow_html=True)


def footer() -> None:
    st.markdown(
        "<div class='btw-footer'>Beyond the Wait — Bronx ED Access Lab · independent, fictional "
        "educational decision-support project · not clinically validated · not affiliated with "
        "any hospital · <b>Python calculates. Claude explains. Humans decide.</b></div>",
        unsafe_allow_html=True,
    )


def _mtime(path: Path) -> float:
    return path.stat().st_mtime if path.exists() else 0.0


@st.cache_data(show_spinner=False)
def _public(directory: str, stamp: float) -> d.PublicLoad:
    return d.load_public_data(Path(directory))


def public_data() -> d.PublicLoad:
    directory = d.public_dir()
    return _public(str(directory), _mtime(directory / "build_manifest.json"))


@st.cache_data(show_spinner=False)
def _results(directory: str, stamp: float):  # noqa: ANN202
    return d.load_results(Path(directory))


def results():  # noqa: ANN201
    directory = d.results_dir()
    return _results(str(directory), _mtime(directory / "manifest.json"))


@st.cache_data(show_spinner=False, max_entries=8)
def flow(seed: int, days: int) -> d.FlowSnapshot:
    return d.run_flow(seed, days)


def results_unavailable(load) -> bool:  # noqa: ANN001
    """Show a clear message if the precomputed bundle is missing or invalid."""
    if load.status == "ok":
        return False
    if load.status == "missing":
        st.info(
            "The precomputed multi-seed results have not been built on this computer yet. "
            "They take about 6 minutes on 8 cores and only need to be built once:"
        )
        st.code(d.BUILD_COMMAND, language="bash")
    else:
        st.error("The results bundle failed verification and will not be displayed:")
        for p in load.problems:
            st.markdown(f"- {p}")
        st.code(d.BUILD_COMMAND, language="bash")
    return True


def checks_table(checks: pd.DataFrame) -> None:
    if checks.empty:
        st.markdown("No non-PASS checks.")
        return
    for row in checks.itertuples():
        st.markdown(
            f"{status_chip(row.status)} <b>{row.check}</b> — {row.detail}", unsafe_allow_html=True
        )


def table_height(n_rows: int) -> int:
    """Pixel height that shows every row without an inner scrollbar (max 20 rows)."""
    return 38 + 35 * min(n_rows, 20)
