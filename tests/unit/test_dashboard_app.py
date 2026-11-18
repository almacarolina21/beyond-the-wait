"""Headless page tests with Streamlit's AppTest (no browser, no network, no API key)."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from tests.paths import REPO_ROOT

PAGES = ["overview", "public_data", "patient_flow", "what_if", "methods", "ai_evaluation"]
REAL_NAMES = ("lincoln", "barnabas", "jacobi", "montefiore", "bronxcare")


def _run(page: str) -> AppTest:
    return AppTest.from_file(
        str(REPO_ROOT / "app" / "views" / f"{page}.py"), default_timeout=180
    ).run()


def _text(at: AppTest) -> str:
    parts = (
        [m.value for m in at.markdown] + [c.value for c in at.caption] + [t.value for t in at.title]
    )
    return " ".join(str(p) for p in parts)


@pytest.fixture
def with_bundle(monkeypatch: pytest.MonkeyPatch, small_bundle_dir: Path) -> Path:
    monkeypatch.setenv("BTW_RESULTS_DIR", str(small_bundle_dir))
    return small_bundle_dir


@pytest.mark.parametrize("page", PAGES)
def test_every_page_runs_without_exceptions(with_bundle: Path, page: str) -> None:
    at = _run(page)
    assert not at.exception, [e.value for e in at.exception]
    assert not at.error, [e.value for e in at.error]


@pytest.mark.parametrize("page", ["patient_flow", "what_if", "methods"])
def test_simulated_pages_name_no_real_hospital(with_bundle: Path, page: str) -> None:
    assert not any(n in _text(_run(page)).lower() for n in REAL_NAMES)


def test_simulated_pages_carry_safety_messages(with_bundle: Path) -> None:
    for page in ("patient_flow", "methods"):
        assert "ESI-1 PRIORITY LIMITATION" in _text(_run(page))
    assert "Model-internal evidence only" in _text(_run("what_if"))


def test_entry_point_builds_navigation() -> None:
    at = AppTest.from_file(str(REPO_ROOT / "app" / "streamlit_app.py"), default_timeout=180).run()
    assert not at.exception


@pytest.mark.parametrize("page", ["what_if", "methods"])
def test_missing_bundle_shows_build_instructions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, page: str
) -> None:
    monkeypatch.setenv("BTW_RESULTS_DIR", str(tmp_path))
    at = _run(page)
    assert not at.exception
    assert any("not been built" in i.value for i in at.info)
    assert any("whatif_demo" in c.value for c in at.code)


def test_tampered_bundle_is_refused(
    monkeypatch: pytest.MonkeyPatch, small_bundle_dir: Path, tmp_path: Path
) -> None:
    bad = tmp_path / "bad"
    shutil.copytree(small_bundle_dir, bad)
    path = bad / "comparisons.csv"
    path.write_text(path.read_text().replace("CAUTION", "PASS"))
    monkeypatch.setenv("BTW_RESULTS_DIR", str(bad))
    at = _run("what_if")
    assert any("failed verification" in e.value for e in at.error)
    assert not at.get("plotly_chart")  # no comparison charts from an unverified bundle


def test_blocked_comparison_is_listed_not_plotted(
    monkeypatch: pytest.MonkeyPatch, small_bundle_dir: Path, tmp_path: Path
) -> None:
    b = tmp_path / "blocked"
    shutil.copytree(small_bundle_dir, b)
    df = pd.read_csv(b / "comparisons.csv")
    victim = (df["scope"] == "main") & (df["scenario_id"] == "faster_diagnostics")
    numeric = [
        "baseline_mean",
        "scenario_mean",
        "mean_difference",
        "sd_difference",
        "ci95_low",
        "ci95_high",
        "pred95_low",
        "pred95_high",
    ]
    df.loc[victim, numeric] = float("nan")
    df.loc[victim, "comparison_status"] = "BLOCKED"
    df.to_csv(b / "comparisons.csv", index=False)
    manifest = json.loads((b / "manifest.json").read_text())
    manifest["files"]["comparisons"]["sha256"] = hashlib.sha256(
        (b / "comparisons.csv").read_bytes()
    ).hexdigest()
    for c in manifest["comparisons"]:
        if c["scope"] == "main" and c["scenario_id"] == "faster_diagnostics":
            c["status"] = "BLOCKED"
    (b / "manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setenv("BTW_RESULTS_DIR", str(b))
    at = _run("what_if")
    assert not at.exception
    assert any("BLOCKED" in e.value and "faster_diagnostics" in e.value for e in at.error)
    assert "faster_diagnostics" not in at.selectbox[1].options  # cannot be selected as valid data
    forest = json.loads(at.get("plotly_chart")[0].proto.spec)
    assert all("25% faster" not in str(tr.get("y")) for tr in forest["data"])


def test_patient_flow_seed_selection_updates_results(with_bundle: Path) -> None:
    at = _run("patient_flow")
    first = at.metric[0].value
    at.selectbox[0].set_value(7).run()
    assert not at.exception and at.metric[0].value != first


def test_what_if_selectors_switch_metric_and_scenario(with_bundle: Path) -> None:
    at = _run("what_if")
    at.selectbox[0].set_value("boarding_p90").run()
    at.selectbox[1].set_value("increase_bed_releases").run()
    assert not at.exception
    assert any(
        "Boarding" in json.loads(c.proto.spec)["layout"]["title"]["text"]
        for c in at.get("plotly_chart")
    )


def test_public_page_switches_tabs_inputs(with_bundle: Path) -> None:
    at = _run("public_data")
    at.radio[0].set_value("OP_18c").run()
    at.selectbox[0].set_value(2024).run()
    assert not at.exception


def test_checks_table_handles_empty_and_rows(tmp_path: Path) -> None:
    # A real script file (not AppTest.from_function): from_function leaves Python's
    # __main__ pointing at a generated script, which breaks later tests that start
    # worker processes (macOS re-imports __main__ in each worker).
    script = tmp_path / "checks_app.py"
    script.write_text(
        "import pandas as pd\n"
        "from beyond_the_wait.dashboard import ui\n"
        "ui.checks_table(pd.DataFrame(columns=['check', 'status', 'detail']))\n"
        "ui.checks_table(pd.DataFrame([{'check': 'single_seed', 'status': 'CAUTION', "
        "'detail': 'one seed'}]))\n"
    )
    at = AppTest.from_file(str(script)).run()
    text = " ".join(m.value for m in at.markdown)
    assert "No non-PASS checks." in text and "single_seed" in text and "CAUTION" in text


def test_overview_features_verified_findings_and_file_check_disclaimer(with_bundle: Path) -> None:
    at = _run("overview")
    text = _text(at)
    assert "What the fictional model shows" in " ".join(s.value for s in at.subheader)
    assert text.count("within the fictional model") >= 3
    assert "not</b> clinical or real-world validation" in text
    assert "What-If Lab" in text  # link text (plain fallback when run standalone)


def test_overview_without_bundle_still_renders(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("BTW_RESULTS_DIR", str(tmp_path))
    at = _run("overview")
    assert not at.exception and any("not built yet" in i.value for i in at.info)


def test_what_if_order_and_single_disclaimer(with_bundle: Path) -> None:
    at = _run("what_if")
    text = _text(at)
    assert text.count("Model-internal evidence only") == 1  # once, in an expander
    heads = [s.value for s in at.subheader]
    assert heads.index("Compare interventions on one outcome") < heads.index(
        "One intervention in detail"
    )
    assert heads[-1] == "Methods and validation"
    assert "Why CAUTION" in text


# ---------------------------------------------------------------------------
# Milestone 8: AI explanation panel (no real API traffic; see conftest guard)
# ---------------------------------------------------------------------------


class _CountingAnthropic:
    """Stands in for anthropic.Anthropic; records construction and requests."""

    instances = 0
    requests = 0

    def __init__(self, **kwargs: object) -> None:
        type(self).instances += 1
        self.messages = self

    def create(self, **kwargs: object) -> object:
        from types import SimpleNamespace

        from beyond_the_wait.interpretation.evidence import EvidencePacket
        from beyond_the_wait.interpretation.providers import mock_interpretation

        type(self).requests += 1
        content = kwargs["messages"][0]["content"]
        blob = content.split("<evidence_packet>\n", 1)[1].split("\n</evidence_packet>", 1)[0]
        interp = mock_interpretation(EvidencePacket.model_validate_json(blob))
        text = json.dumps(interp.model_dump()).replace(
            "DEMONSTRATION (mock output, not written by Claude). ", ""
        )
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=text)],
            stop_reason="end_turn",
            model="claude-opus-5-5",
            usage=SimpleNamespace(input_tokens=4000, output_tokens=700),
        )


@pytest.fixture
def live_fake(monkeypatch: pytest.MonkeyPatch, with_bundle: Path) -> type[_CountingAnthropic]:
    import anthropic

    _CountingAnthropic.instances = _CountingAnthropic.requests = 0
    monkeypatch.setattr(anthropic, "Anthropic", _CountingAnthropic)
    monkeypatch.setenv("BTW_AI_MODE", "live")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    return _CountingAnthropic


def _explain_button(at: AppTest):  # noqa: ANN202
    return at.button(key="explain_button")


def test_no_api_calls_on_page_load_or_widget_changes(live_fake: type) -> None:
    at = _run("what_if")
    assert not at.exception
    at.selectbox[0].set_value("boarding_p90").run()
    at.selectbox[1].set_value("increase_bed_releases").run()
    at.selectbox[0].set_value("triage_wait_p90").run()
    assert not at.exception
    assert live_fake.instances == 0 and live_fake.requests == 0
    assert "LIVE · Claude API" in _text(at)
    assert "sk-test" not in _text(at)  # the key is never displayed


def test_live_explanation_only_after_explicit_click(live_fake: type) -> None:
    at = _run("what_if")
    _explain_button(at).click().run()
    assert not at.exception
    assert live_fake.requests == 1
    text = _text(at)
    assert "Decision support for human review. Based on simulated data." in text
    assert "LIVE · Claude API" in text and "claude-opus-5-5" in text
    assert "Key findings" in text and "`M:" in text
    assert "Tokens used: 4,000 in, 700 out." in " ".join(c.value for c in at.caption)
    # Changing an unrelated widget re-runs the page but sends nothing new, and the
    # old explanation is not shown for the new selection.
    at.selectbox[1].set_value("increase_bed_releases").run()
    assert live_fake.requests == 1
    assert "Decision support for human review. Based on simulated data." not in _text(at)
    at.selectbox[1].set_value(at.selectbox[1].options[0]).run()  # back: cached, no new call
    assert live_fake.requests == 1


def test_demo_mode_explanation_is_labeled_and_verified(with_bundle: Path) -> None:
    at = _run("what_if")
    assert "DEMO · mock, not Claude" in _text(at)
    assert "Nothing is sent until you click the button" in _text(at)
    _explain_button(at).click().run()
    assert not at.exception
    text = _text(at)
    assert "DEMONSTRATION" in text and "not written by Claude" in text
    assert "Passed 11 local checks" in text and "mock-interpreter-v1" in text
    assert "Key result (calculated by Python)" in text and "NEEDS HUMAN REVIEW" not in text
    assert "Questions for qualified people to investigate" in text
    assert "{{" not in text  # every value token was replaced by a Python-formatted value
    assert not any(n in text.lower() for n in REAL_NAMES)
    assert text.count("Model-internal evidence only") == 1


def test_rejected_explanation_is_withheld(
    monkeypatch: pytest.MonkeyPatch, with_bundle: Path
) -> None:
    from beyond_the_wait.dashboard import ai_panel
    from beyond_the_wait.interpretation.providers import ProviderResponse

    class Unsafe:
        mode, model = "LIVE", "fake-model"

        def interpret(self, packet: object) -> ProviderResponse:
            return ProviderResponse(
                json.dumps({"summary": "Hire 3 nurses at Lincoln."}), "LIVE", "fake-model"
            )

    monkeypatch.setattr(ai_panel, "make_provider", lambda *a, **k: Unsafe())
    at = _run("what_if")
    _explain_button(at).click().run()
    assert not at.exception
    assert any("failed local verification" in w.value for w in at.warning)
    assert "Hire 3 nurses" not in _text(at) and "lincoln" not in _text(at).lower()


def test_needs_review_explanation_is_shown_with_its_flags(
    monkeypatch: pytest.MonkeyPatch, with_bundle: Path
) -> None:
    from beyond_the_wait.dashboard import ai_panel
    from beyond_the_wait.interpretation.providers import ProviderResponse, mock_interpretation

    class Interpretive:
        mode, model = "LIVE", "fake-model"

        def interpret(self, packet: object) -> ProviderResponse:
            data = mock_interpretation(packet).model_dump()  # type: ignore[arg-type]
            data["summary"] = data["summary"].replace(
                "Validation status", "This suggests the queue moved downstream. Validation status"
            )
            return ProviderResponse(json.dumps(data), "LIVE", "fake-model")

    monkeypatch.setattr(ai_panel, "make_provider", lambda *a, **k: Interpretive())
    at = _run("what_if")
    _explain_button(at).click().run()
    assert not at.exception
    text = _text(at)
    assert "NEEDS HUMAN REVIEW" in text and "needs review" in text
    assert "Key result (calculated by Python)" in text
    assert any("hypotheses for a qualified person" in w.value for w in at.warning)


def test_review_location_names_are_readable() -> None:
    from beyond_the_wait.dashboard.ai_panel import where

    assert where("key_findings[1]") == "Key finding 2"
    assert where("summary") == "Summary" and where("response") == "Whole explanation"
    assert where("odd loc!") == "odd loc!"


def test_evaluation_page_reports_blind_rounds_and_limits() -> None:
    at = _run("ai_evaluation")
    assert not at.exception
    text = _text(at)
    assert "locked with a file hash" in text and "no Claude API call" in text
    assert "not a safety certificate" in text and "100% test coverage is not 100% safety" in text
    assert len(at.table[0].value) == 3  # one row per locked held-out round
    at.selectbox(key="eval_split").set_value("heldout").run()
    assert not at.exception and "not blind" in _text(at)


def test_api_failure_shows_fallback(monkeypatch: pytest.MonkeyPatch, with_bundle: Path) -> None:
    from beyond_the_wait.dashboard import ai_panel
    from beyond_the_wait.interpretation.providers import InterpretationError

    class Failing:
        mode, model = "LIVE", "fake-model"

        def interpret(self, packet: object) -> object:
            raise InterpretationError("rate_limit")

    monkeypatch.setattr(ai_panel, "make_provider", lambda *a, **k: Failing())
    at = _run("what_if")
    _explain_button(at).click().run()
    assert not at.exception
    assert any("rate limit" in i.value and "unaffected" in i.value for i in at.info)


def test_live_requested_without_key_falls_back_to_demo(
    monkeypatch: pytest.MonkeyPatch, with_bundle: Path, tmp_path: Path
) -> None:
    from beyond_the_wait.interpretation import providers

    monkeypatch.setattr(providers, "REPO_ROOT", tmp_path)  # ignore any local .env
    monkeypatch.setenv("BTW_AI_MODE", "live")
    at = _run("what_if")
    assert not at.exception
    assert "no API key was found" in _text(at) and "DEMO · mock, not Claude" in _text(at)


def test_md_escape_neutralizes_markup() -> None:
    from beyond_the_wait.dashboard.ai_panel import md_escape

    assert md_escape("[x](http://a) <b>$5</b>") == r"\[x\]\(http://a\) \<b\>\$5\</b\>"


def test_panel_explains_refusal_instead_of_crashing(
    monkeypatch: pytest.MonkeyPatch, with_bundle: Path
) -> None:
    from beyond_the_wait.dashboard import ai_panel
    from beyond_the_wait.interpretation.evidence import EvidenceRefused

    def refuse(*args: object, **kwargs: object) -> None:
        raise EvidenceRefused("faster_diagnostics is BLOCKED by the validation gate")

    monkeypatch.setattr(ai_panel, "build_evidence_packet", refuse)
    at = _run("what_if")
    assert not at.exception
    assert any("No explanation is available" in w.value for w in at.warning)
    assert all(b.key != "explain_button" for b in at.button)  # nothing to send
