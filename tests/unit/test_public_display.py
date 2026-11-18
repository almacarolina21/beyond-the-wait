"""Release gate: unverified or unsafe AI prose never reaches the public dashboard.

Every hand-written evaluation case (unsafe, borderline, and legitimate) is sent
through the real service and the real panel renderer. Whatever the verdict, the
public display may contain generated prose only when the verdict is ACCEPT.
Offline: providers here are local fakes that return fixed text.
"""

from __future__ import annotations

import json
import re

import pytest
from streamlit.testing.v1 import AppTest

from beyond_the_wait.dashboard.ai_panel import public_prose_allowed
from beyond_the_wait.evaluation.dataset import EvalCase, load_dataset
from beyond_the_wait.interpretation.faithfulness import assess
from beyond_the_wait.interpretation.providers import ProviderResponse
from beyond_the_wait.interpretation.service import Outcome, explain

DATASET = load_dataset()


class _Fixed:
    mode, model = "LIVE", "fake-model"

    def __init__(self, raw: str) -> None:
        self.raw = raw

    def interpret(self, packet: object) -> ProviderResponse:
        return ProviderResponse(self.raw, "LIVE", "fake-model")


def _outcome(case: EvalCase) -> Outcome:
    return explain(DATASET.packets[case.packet], _Fixed(DATASET.candidate(case)))


def _render(outcome: Outcome, packet_name: str) -> AppTest:
    def script(outcome, packet_name):  # noqa: ANN001, ANN202 - annotations are not in scope
        from beyond_the_wait.dashboard.ai_panel import show_outcome
        from beyond_the_wait.evaluation.dataset import load_dataset

        show_outcome(outcome, load_dataset().packets[packet_name])

    return AppTest.from_function(script, args=(outcome, packet_name), default_timeout=60).run()


def _displayed(at: AppTest) -> str:
    """Every piece of text the page shows, including warnings, expanders, and tables."""
    parts: list[str] = []
    for kind in ("markdown", "caption", "title", "warning", "info", "error", "success", "code"):
        parts += [str(e.value) for e in getattr(at, kind)]
    parts += [t.value.to_string() for t in at.table]
    return "\n".join(parts)


def _prose_fragments(raw: str) -> list[str]:
    """Distinctive pieces of the candidate's own wording (outside value tokens)."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return [f for f in re.split(r"\{\{[^}]*\}\}", raw) if len(f.strip()) >= 25]
    out: list[str] = []

    def walk(v: object) -> None:
        if isinstance(v, str):
            out.extend(
                f.strip()
                for f in re.split(r"\{\{[^}]*\}\}|[.;:]", v)
                if len(f.strip()) >= 25 and " " in f.strip()  # skip evidence IDs (Python data)
            )
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    walk(data)
    return out


def _md_plain(text: str) -> str:
    return text.replace("\\", "")


OUTCOMES = {c.id: _outcome(c) for c in DATASET.cases}


def test_three_verdicts_are_preserved_and_review_is_never_accept() -> None:
    seen = set()
    for case in DATASET.cases:
        out = OUTCOMES[case.id]
        verdict = assess(DATASET.packets[case.packet], DATASET.candidate(case)).verdict
        expected_status = {"ACCEPT": "verified", "HUMAN_REVIEW": "needs_review"}.get(
            verdict, "rejected"
        )
        assert out.status == expected_status, case.id
        assert public_prose_allowed(out) == (verdict == "ACCEPT"), case.id
        seen.add(verdict)
    assert seen == {"ACCEPT", "HUMAN_REVIEW", "REJECT"}


@pytest.mark.parametrize(
    "case", [c for c in DATASET.cases if c.expected != "ACCEPT"], ids=lambda c: c.id
)
def test_cases_labelled_unsafe_or_review_are_never_public(case: EvalCase) -> None:
    assert not public_prose_allowed(OUTCOMES[case.id]), case.rationale


@pytest.mark.parametrize("case", DATASET.cases, ids=lambda c: c.id)
def test_rendered_panel_never_shows_unverified_prose(case: EvalCase) -> None:
    out = OUTCOMES[case.id]
    at = _render(out, case.packet)
    assert not at.exception, [e.value for e in at.exception]
    shown = _md_plain(_displayed(at))
    fragments = _prose_fragments(DATASET.candidate(case))
    leaked = [f for f in fragments if f in shown]
    if public_prose_allowed(out):
        assert "Generated explanation" in shown
        assert leaked, "detector sanity: ACCEPT prose should be visible"
        return
    assert "Generated explanation" not in shown
    assert not leaked, leaked
    if out.status == "needs_review":
        assert "requires human review and is not shown" in shown
        assert "Key result (calculated by Python)" in shown
        assert "Evidence behind this explanation" in [e.label for e in at.expander][0]


def test_unexpected_status_fails_closed() -> None:
    case = next(c for c in DATASET.cases if OUTCOMES[c.id].status == "verified")
    ok = OUTCOMES[case.id]
    for bad in (
        Outcome("weird", ok.mode, ok.model, ok.packet_id, ok.interpretation, ok.report),  # type: ignore[arg-type]
        Outcome("verified", ok.mode, ok.model, ok.packet_id, None, ok.report),
    ):
        assert not public_prose_allowed(bad)
    weird = Outcome("weird", ok.mode, ok.model, ok.packet_id, ok.interpretation, ok.report)  # type: ignore[arg-type]
    shown = _md_plain(_displayed(_render(weird, case.packet)))
    assert "Generated explanation" not in shown
    assert "requires human review and is not shown" in shown
