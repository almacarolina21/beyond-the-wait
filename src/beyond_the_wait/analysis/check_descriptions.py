"""Plain-language descriptions of validation-check codes.

The gate and the multi-seed experiments name their checks with short codes
(`overload_regime`, `skewed_differences`, ...). Those codes are precise for
developers but unreadable for a hospital leader. This module is the single
place that turns a code into a sentence, so the dashboard and the AI
evidence packet describe a CAUTION the same way.
"""

from __future__ import annotations

# code -> (short title, one-sentence description)
CHECK_DESCRIPTIONS: dict[str, tuple[str, str]] = {
    # Comparison-level checks (Milestone 6)
    "blocked_replications": (
        "Blocked simulated weeks",
        "At least one simulated week failed the validation gate, so the comparison is blocked.",
    ),
    "seed_pairing": (
        "Seed pairing",
        "The baseline and the change must be run on the same random seeds to be compared.",
    ),
    "seed_coverage": (
        "Enough simulated weeks",
        "Checks that enough paired simulated weeks were run to estimate an average change.",
    ),
    "missing_data": (
        "Missing values",
        "Checks whether any simulated week could not produce a value for an outcome.",
    ),
    "excessive_variability": (
        "Imprecise estimates",
        "For some outcomes the 95% interval is wide compared with the baseline level, so the "
        "size of the change is uncertain.",
    ),
    "skewed_differences": (
        "Lopsided differences",
        "For some outcomes a few simulated weeks differ much more than the rest, so the 95% "
        "interval is less reliable than usual.",
    ),
    "censoring": (
        "Unfinished visits",
        "Some patients were still in the ED when the simulation stopped, so some lengths of "
        "stay are lower bounds.",
    ),
    "overload_regime": (
        "Overloaded weeks (stress test)",
        "In at least one simulated week queues kept growing; results there describe a stress "
        "test, not ordinary operation.",
    ),
    "carried_cautions": (
        "Cautions from individual weeks",
        "Individual simulated weeks carried their own cautions (for example model assumptions, "
        "extreme waits, or delays for the most urgent patients).",
    ),
    "unsupported_conclusions": (
        "Model-only evidence",
        "The evidence comes from inside the fictional model and cannot support conclusions about "
        "real hospitals.",
    ),
    # Replication-level checks (Milestone 5 gate), counted in carried_cautions
    "single_seed": (
        "One random seed",
        "A single simulated week is one random draw; only averages across many weeks are compared.",
    ),
    "capacity_and_stability": (
        "Capacity and stability",
        "Demand is close to or above capacity, so queues may grow during the week.",
    ),
    "esi1_timeliness": (
        "Most urgent patients",
        "Some ESI 1 patients waited longer than an illustrative review threshold because the "
        "model does not let them pre-empt other patients.",
    ),
    "extreme_waits": (
        "Extreme waits",
        "Some simulated patients waited far longer than typical, which the averages may hide.",
    ),
    "model_assumptions": (
        "Model assumptions",
        "Results depend on illustrative assumptions that have not been checked against any real "
        "ED.",
    ),
    "no_bottleneck_claims": (
        "No bottleneck claims",
        "A single run cannot show which constraint truly limits flow.",
    ),
}


def check_title(code: str) -> str:
    """Short readable title, or the code with spaces if unknown."""
    return CHECK_DESCRIPTIONS.get(code, (code.replace("_", " ").capitalize(), ""))[0]


def describe_check(code: str) -> str:
    """One readable sentence for a check code (falls back to the spaced code)."""
    title, text = CHECK_DESCRIPTIONS.get(code, (code.replace("_", " ").capitalize(), ""))
    return f"{title}: {text}" if text else title
