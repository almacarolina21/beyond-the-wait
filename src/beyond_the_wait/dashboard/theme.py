"""Visual system for the dashboard: palette, Plotly template, badges, CSS.

Color rules (from the project's data-visualization method)
    * Colors come from a documented, previously validated reference palette.
      We use its values unmodified. (The palette validator could not be run on
      this machine -- Node.js is not installed -- so every mark ALSO carries a
      text label or hover text; meaning never depends on color alone.)
    * Categorical hues are assigned in a fixed order, never cycled.
    * ESI levels are ORDERED, so they use one-hue (blue) ordinal steps,
      darkest = most urgent, starting no lighter than step 250.
    * Status colors (PASS / CAUTION / BLOCKED) are reserved for validation
      status and always shown with an icon and a word.
    * Effect directions are NOT traffic lights: "lower in the model" is a neutral
      blue and "higher in the model" an orange, with text; nothing is "green = safe".
"""

from __future__ import annotations

import html

import plotly.graph_objects as go
import plotly.io as pio

# Surfaces and text (light mode).
SURFACE = "#fcfcfb"
SURFACE_2 = "#f3f2ee"
GRID = "#e6e5e0"
TEXT = "#0b0b0b"
TEXT_2 = "#52514e"
TEXT_3 = "#7a7974"
NEUTRAL = "#8a8984"

# Categorical palette, fixed order (reference instance, light mode).
CATEGORICAL = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6"]

# Ordinal blue steps for ESI 1 (darkest, most urgent) .. ESI 5 (lightest).
ESI_COLORS = {1: "#104281", 2: "#1c5cab", 3: "#2a78d6", 4: "#5598e7", 5: "#86b6ef"}

# Provenance identity (paired with a text badge everywhere).
PROVENANCE_COLORS = {"PUBLIC": "#2a78d6", "SYNTHETIC": "#eb6834", "SIMULATED": "#6250d6"}

# Effect direction inside the model: neutral identities + text, not judgments.
DIRECTION_COLORS = {
    "improves": "#2a78d6",
    "worsens": "#eb6834",
    "changes": "#6250d6",
    "inconclusive": NEUTRAL,
    "no_change": NEUTRAL,
    "unavailable": NEUTRAL,
    "blocked": NEUTRAL,
}
DIRECTION_TEXT = {  # phrasing for estimates without a clear sign-based verdict
    "inconclusive": "No detectable change (95% CI includes 0)",
    "no_change": "Identical in both arms",
    "unavailable": "Unavailable",
    "blocked": "BLOCKED: not shown",
}
LOWER = "Lower within the fictional model"
HIGHER = "Higher within the fictional model"


def direction_label(direction: str, mean_difference: float) -> str:
    """Plain, model-scoped wording. Describes the NUMBER (lower/higher), never
    calls a result better, safer, or recommended."""
    if direction in ("improves", "worsens", "changes"):
        return LOWER if mean_difference < 0 else HIGHER
    return DIRECTION_TEXT.get(direction, direction)


def direction_color(direction: str, mean_difference: float) -> str:
    if direction in ("improves", "worsens", "changes"):
        return DIRECTION_COLORS["improves"] if mean_difference < 0 else DIRECTION_COLORS["worsens"]
    return NEUTRAL


# Reserved status palette with icons.
STATUS_STYLE = {
    "PASS": ("#0ca30c", "✓"),
    "CAUTION": ("#b77900", "!"),
    "BLOCKED": ("#d03b3b", "✕"),
}

FONT_STACK = (
    "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'Inter', 'Helvetica Neue', Arial, sans-serif"
)


# Fixed title area: title + subtitle live in the top margin (container coordinates),
# legends live at the very bottom of the figure (container coordinates). Neither
# can overlap the plot, the axis labels, or each other.
TITLE_AREA_PX = 92
LEGEND_AREA_PX = 44


def plotly_template() -> go.layout.Template:
    """Thin marks, recessive grid, readable type, labels never clipped."""
    axis = {
        "gridcolor": GRID,
        "zerolinecolor": "#c9c8c2",
        "linecolor": GRID,
        "tickfont": {"color": TEXT_2, "size": 12},
        "title": {"font": {"color": TEXT_2, "size": 12}, "standoff": 10},
        "automargin": True,  # grow margins to fit tick labels instead of clipping them
    }
    t = go.layout.Template()
    t.layout = go.Layout(
        font={"family": FONT_STACK, "size": 13, "color": TEXT},
        title={
            "font": {"size": 16, "color": TEXT},
            "x": 0,
            "xanchor": "left",
            "xref": "container",
            "y": 1,
            "yanchor": "top",
            "yref": "container",
            "pad": {"t": 14, "l": 14},
            "subtitle": {"font": {"size": 12, "color": TEXT_2}},
        },
        paper_bgcolor=SURFACE,
        plot_bgcolor=SURFACE,
        colorway=CATEGORICAL,
        margin={"l": 16, "r": 32, "t": TITLE_AREA_PX, "b": 56},
        xaxis=axis,
        yaxis=axis,
        legend={
            "font": {"color": TEXT_2, "size": 12},
            "orientation": "h",
            "xref": "container",
            "yref": "container",
            "x": 0.01,
            "xanchor": "left",
            "y": 0.01,
            "yanchor": "bottom",
            "title": {"text": ""},
        },
        hoverlabel={"font": {"family": FONT_STACK, "size": 12}, "bgcolor": "white"},
        bargap=0.25,
    )
    return t


pio.templates["beyond_the_wait"] = plotly_template()
TEMPLATE = "beyond_the_wait"


def badge(label: str) -> str:
    """Inline HTML badge for a provenance label (text always present)."""
    color = PROVENANCE_COLORS.get(label, NEUTRAL)
    return (
        f"<span class='btw-badge' style='border-color:{color};color:{color}'>"
        f"{html.escape(label)}</span>"
    )


def status_chip(status: str) -> str:
    color, icon = STATUS_STYLE.get(status, (NEUTRAL, "?"))
    return (
        f"<span class='btw-status' style='border-color:{color};color:{color}'>"
        f"{icon} {html.escape(status)}</span>"
    )


CSS = f"""
<style>
:root {{ --btw-text-2: {TEXT_2}; --btw-surface-2: {SURFACE_2}; }}
.block-container {{ max-width: 1180px; padding-top: 3.6rem; padding-bottom: 4rem; }}
h1, h2, h3 {{ letter-spacing: -0.01em; }}
h1 {{ font-weight: 650; }}
p, li {{ line-height: 1.6; }}
.btw-eyebrow {{ text-transform: uppercase; letter-spacing: .08em; font-size: .78rem;
  color: {TEXT_2}; font-weight: 600; margin-bottom: .2rem; }}
.btw-lede {{ font-size: 1.1rem; color: {TEXT_2}; max-width: 720px; margin-bottom: .2rem; }}
.btw-badge, .btw-status {{ display: inline-block; border: 1.5px solid; border-radius: 999px;
  padding: 1px 10px; font-size: .75rem; font-weight: 650; letter-spacing: .05em;
  background: white; vertical-align: middle; margin-right: 6px; }}
.btw-card {{ background: white; border: 1px solid {GRID}; border-radius: 12px;
  padding: 1rem 1.1rem; height: 100%; }}
.btw-card h4 {{ margin: .4rem 0 .3rem 0; font-size: 1.02rem; }}
.btw-card p {{ color: {TEXT_2}; font-size: .93rem; margin: 0; }}
.btw-callout {{ border-left: 4px solid #d03b3b; background: #fbf3f2; border-radius: 8px;
  padding: .85rem 1rem; margin: .6rem 0 1rem 0; color: {TEXT}; font-size: .95rem; }}
.btw-note {{ border-left: 4px solid {NEUTRAL}; background: {SURFACE_2}; border-radius: 8px;
  padding: .75rem 1rem; margin: .4rem 0 1rem 0; color: {TEXT_2}; font-size: .92rem; }}
.btw-footer {{ color: {TEXT_3}; font-size: .82rem; border-top: 1px solid {GRID};
  margin-top: 3rem; padding-top: .8rem; }}
/* Section rhythm: one divider + consistent space before each major section. */
.btw-section {{ margin-top: 1.8rem; padding-top: .2rem; border-top: 1px solid {GRID}; }}
.btw-principle {{ display: flex; flex-wrap: wrap; gap: .4rem 1.4rem; margin: .4rem 0 1.2rem 0;
  font-size: .95rem; color: {TEXT_2}; }}
.btw-principle b {{ color: {TEXT}; }}
.btw-finding {{ background: white; border: 1px solid {GRID}; border-radius: 12px;
  padding: .95rem 1.05rem; height: 100%; }}
.btw-finding .btw-finding-title {{ font-weight: 650; font-size: 1rem; margin: .45rem 0 .35rem 0;
  color: {TEXT}; }}
.btw-finding p {{ color: {TEXT_2}; font-size: .92rem; margin: 0; line-height: 1.55; }}
.btw-small {{ font-size: .85rem; color: {TEXT_2}; }}
@media (max-width: 700px) {{ .block-container {{ padding-left: 1rem; padding-right: 1rem; }} }}
</style>
"""
