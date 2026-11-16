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

PAGES = ["overview", "public_data", "patient_flow", "what_if", "methods"]
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
