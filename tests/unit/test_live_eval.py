"""Supervised live evaluation: plan, spending safeguards, blind review, dry run.

Offline: conftest blocks the SDK's HTTP layer, and these tests also block
sockets. "Live" paths use fake providers or a fake SDK client class.
"""

from __future__ import annotations

import json
import shutil
import socket
from pathlib import Path

import pytest

from beyond_the_wait.evaluation.dataset import PACKETS_DIR
from beyond_the_wait.interpretation.providers import InterpretationError, ProviderResponse
from beyond_the_wait.live_eval import __main__ as cli
from beyond_the_wait.live_eval import batch as bm
from beyond_the_wait.live_eval import plan as pm
from beyond_the_wait.live_eval import review as rv
from beyond_the_wait.live_eval.report import record_billing, summarize
from tests.paths import REPO_ROOT

PLAN = pm.load_plan()
CODE = pm.approval_code(PLAN)
FAKE_KEY = "sk-ant-test-NEVER-WRITE-THIS-0000"


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(*a: object, **k: object) -> None:
        raise RuntimeError("network blocked in live-eval tests")

    monkeypatch.setattr(socket.socket, "connect", blocked)


def _run(tmp: Path, *argv: str, ask=None, interactive=False) -> tuple[int, str]:  # noqa: ANN001
    lines: list[str] = []
    code = cli.main(
        ["--root", str(tmp), *argv],
        out=lines.append,
        ask=ask or (lambda _p: ""),
        interactive=lambda: interactive,
    )
    return code, "\n".join(lines)


class FakeLive:
    mode, model = "LIVE", "claude-opus-5-5"

    def __init__(self) -> None:
        self.calls = 0

    def interpret(self, packet: object) -> ProviderResponse:
        from beyond_the_wait.interpretation.providers import MockProvider

        self.calls += 1
        text = MockProvider().interpret(packet).text  # type: ignore[arg-type]
        return ProviderResponse(
            text,
            "LIVE",
            "claude-opus-5-5",
            "end_turn",
            {"input_tokens": 5000, "output_tokens": 900},
        )


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------


def test_plan_is_ten_calls_over_the_five_frozen_packets_in_fixed_order() -> None:
    order = [(s.id, s.packet, s.generation) for s in pm.slots(PLAN)]
    assert len(order) == 10 == PLAN.max_calls == pm.MAX_CALLS_PER_BATCH
    assert order[0] == ("01", "triage_nurse", 1) and order[5] == ("06", "triage_nurse", 2)
    assert {p for _, p, _ in order} == {
        "triage_nurse",
        "night_physician",
        "bed_releases",
        "evening_physician",
        "diagnostics",
    }
    assert pm.input_problems(PLAN) == []
    assert pm.approval_code(pm.load_plan()) == CODE and len(CODE) == 12
    assert 0.5 < pm.cost_ceiling(PLAN, pm.load_planned_packets(PLAN)) < 3.0


@pytest.mark.parametrize(
    "change, message",
    [
        ({"max_calls": 11}, "less than or equal to 10"),
        ({"generations_per_packet": 3}, "max_calls must equal"),
        ({"packets": ["triage_nurse", "triage_nurse"]}, "distinct"),
        (
            {
                "packets": ["triage_nurse_tampered"],
                "max_calls": 2,
                "packet_sha256": {"triage_nurse_tampered": "x"},
            },
            "tampered",
        ),
        ({"packet_sha256": {"triage_nurse": "x"}}, "pin exactly"),
    ],
)
def test_plan_validation_refuses_unsafe_plans(change: dict, message: str) -> None:
    data = PLAN.model_dump() | change
    with pytest.raises(ValueError, match=message):
        pm.Plan.model_validate(data)


def test_changed_packet_or_prompt_invalidates_the_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    packets = tmp_path / "packets"
    shutil.copytree(PACKETS_DIR, packets)
    (packets / "diagnostics.json").write_text((packets / "diagnostics.json").read_text() + " ")
    (packets / "bed_releases.json").unlink()
    problems = pm.input_problems(PLAN, packets)
    assert any("diagnostics.json changed" in p for p in problems)
    assert any("bed_releases.json is missing" in p for p in problems)
    with pytest.raises(pm.PlanError):
        pm.load_planned_packets(PLAN, packets)
    code, text = _run(tmp_path, "--packets", str(packets), "plan")
    assert code == 1 and "live mode is refused" in text
    monkeypatch.setattr(pm, "PROMPT_VERSION", "other")
    assert any("prompt version" in p for p in pm.input_problems(PLAN))


def test_plan_command_shows_order_ceiling_and_code(tmp_path: Path) -> None:
    code, text = _run(tmp_path, "plan")
    assert code == 0 and "slot 10: diagnostics generation 2" in text
    assert "Cost ceiling at list price" in text and CODE in text
    assert not any(tmp_path.iterdir())  # planning writes nothing


# ---------------------------------------------------------------------------
# MOCK dry run: the whole workflow, offline
# ---------------------------------------------------------------------------


def test_dry_run_exercises_every_verdict_and_stays_blind(tmp_path: Path) -> None:
    code, text = _run(tmp_path, "dry-run", "--batch", "dry")
    assert code == 0, text
    d = tmp_path / "dry"
    meta = json.loads((d / "batch.json").read_text())
    assert meta["mode"] == "MOCK" and meta["plan_sha256"].startswith(CODE)
    s = summarize(d)
    assert s["attempts"] == 10 and s["responses"] == 9 and s["errors"] == 1 and s["reviewed"] == 9
    autos = {
        json.loads(p.read_text())["automated_verdict"] for p in d.glob("reviews/*.automated.json")
    }
    assert autos == {"ACCEPT", "HUMAN_REVIEW", "REJECT"}
    for p in d.glob("reviews/*.automated.json"):
        a = json.loads(p.read_text())
        assert (a["public_display"] == "shown") == (a["automated_verdict"] == "ACCEPT")
        manual = p.with_name(p.name.replace("automated", "manual"))
        assert manual.stat().st_mtime_ns <= p.stat().st_mtime_ns  # scored before reveal
    rec = json.loads((d / "responses" / "09.json").read_text())
    assert rec["status"] == "error" and rec["error_kind"] == "truncated"
    rec = json.loads((d / "responses" / "01.json").read_text())
    for key in (
        "model_requested",
        "prompt_version",
        "prompt_sha256",
        "packet_sha256",
        "scenario_id",
        "focus_metric",
        "usage",
        "estimated_cost_usd",
        "billed_usd",
    ):
        assert key in rec
    assert rec["usage_simulated"] is True and rec["mode"] == "MOCK"
    report = (d / "report.md").read_text()
    assert "Nothing here is Claude output or a real human judgment" in report
    assert "Human/automated agreement: 8/9" in report
    # re-running sends nothing new and reviews nothing twice
    code, text = _run(tmp_path, "dry-run", "--batch", "dry")
    assert code == 0 and "already attempted" in text and "Nothing left to review" in text


def test_generate_defaults_to_mock(tmp_path: Path) -> None:
    code, _ = _run(tmp_path, "generate", "--batch", "batch-1")
    assert code == 0
    assert json.loads((tmp_path / "batch-1" / "batch.json").read_text())["mode"] == "MOCK"
    assert not list((tmp_path / "batch-1" / "reviews").iterdir())  # no verdict computed yet


# ---------------------------------------------------------------------------
# Live authorization: every refusal sends nothing and writes nothing
# ---------------------------------------------------------------------------


@pytest.fixture
def no_live_client(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    made: list[int] = []

    def forbidden(model: str) -> None:
        made.append(1)
        raise AssertionError("a live provider must not be created")

    monkeypatch.setattr(bm, "live_provider", forbidden)
    return made


@pytest.mark.parametrize(
    "argv, interactive, key, answer, reason",
    [
        ([], True, FAKE_KEY, "RUN 10 CALLS", "needs --approve"),
        (["--approve", "000000000000"], True, FAKE_KEY, "RUN 10 CALLS", "needs --approve"),
        (["--approve", CODE], False, FAKE_KEY, "RUN 10 CALLS", "interactive terminal"),
        (["--approve", CODE], True, "", "RUN 10 CALLS", "no ANTHROPIC_API_KEY"),
        (["--approve", CODE], True, FAKE_KEY, "yes", "not confirmed"),
    ],
)
def test_live_mode_is_refused_without_every_approval_step(  # noqa: PLR0913
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_live_client: list[int],
    argv: list[str],
    interactive: bool,
    key: str,
    answer: str,
    reason: str,
) -> None:
    monkeypatch.setattr(bm, "_read_key", lambda: key)
    code, text = _run(
        tmp_path,
        "generate",
        "--batch",
        "live1",
        "--mode",
        "live",
        *argv,
        ask=lambda _p: answer,
        interactive=interactive,
    )
    assert code == 2 and reason in text
    assert not no_live_client and not (tmp_path / "live1").exists()


def test_approved_live_batch_is_capped_and_never_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeLive()
    monkeypatch.setattr(bm, "_read_key", lambda: FAKE_KEY)
    monkeypatch.setattr(bm, "live_provider", lambda model: fake)
    prompts: list[str] = []

    def ask(p: str) -> str:
        prompts.append(p)
        return "RUN 10 CALLS"

    code, text = _run(
        tmp_path,
        "generate",
        "--batch",
        "live1",
        "--mode",
        "live",
        "--approve",
        CODE,
        ask=ask,
        interactive=True,
    )
    assert code == 0, text
    assert fake.calls == 10 and len(prompts) == 1
    assert "Cost ceiling" in text
    d = tmp_path / "live1"
    rec = json.loads((d / "responses" / "01.json").read_text())
    assert rec["usage"] == {"input_tokens": 5000, "output_tokens": 900}
    assert rec["estimated_cost_usd"] == pytest.approx(0.038) and rec["usage_simulated"] is False
    # a second approval for the same batch sends nothing: the cap is spent
    code, text = _run(
        tmp_path,
        "generate",
        "--batch",
        "live1",
        "--mode",
        "live",
        "--approve",
        CODE,
        ask=ask,
        interactive=True,
    )
    assert code == 2 and "call cap" in text and fake.calls == 10
    # a MOCK run cannot reuse a LIVE batch
    code, text = _run(tmp_path, "generate", "--batch", "live1")
    assert code == 2 and "LIVE batch" in text
    # the key is never written anywhere
    for p in d.rglob("*"):
        if p.is_file():
            assert FAKE_KEY not in p.read_text()


def test_live_provider_is_built_without_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    import anthropic

    seen: dict = {}

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            seen.update(kwargs)

    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    monkeypatch.setattr(bm, "_read_key", lambda: FAKE_KEY)
    provider = bm.live_provider("claude-opus-5-5")
    assert provider.mode == "LIVE" and seen["max_retries"] == 0
    assert seen["timeout"] == bm.LIVE_TIMEOUT_SECONDS


def test_run_batch_records_attempt_before_sending_and_never_resends(tmp_path: Path) -> None:
    packets = pm.load_planned_packets(PLAN)
    d = bm.open_batch("batch-2", PLAN, "LIVE", tmp_path)

    class Boom:
        mode, model = "LIVE", "x"
        calls = 0

        def interpret(self, packet: object) -> None:
            Boom.calls += 1
            if Boom.calls == 1:
                raise InterpretationError("rate_limit")
            raise ValueError(f"secret detail {FAKE_KEY}")

    counts = bm.run_batch(
        d, PLAN, packets, Boom(), "LIVE", packets_dir=PACKETS_DIR, log=lambda _m: None
    )
    assert counts == {"sent": 10, "skipped": 0, "errors": 10} and Boom.calls == 10
    kinds = [
        json.loads((d / "responses" / f"{s.id}.json").read_text())["error_kind"]
        for s in pm.slots(PLAN)
    ]
    assert kinds[0] == "rate_limit" and kinds[1] == "unexpected:ValueError"
    assert FAKE_KEY not in "".join(p.read_text() for p in d.rglob("*.json*"))
    again = bm.run_batch(
        d, PLAN, packets, Boom(), "LIVE", packets_dir=PACKETS_DIR, log=lambda _m: None
    )
    assert again["sent"] == 0 and again["skipped"] == 10 and Boom.calls == 10


def test_cap_holds_even_if_the_ledger_has_unknown_attempts(tmp_path: Path) -> None:
    d = bm.open_batch("batch-3", PLAN, "LIVE", tmp_path)
    with (d / "ledger.jsonl").open("a") as fh:
        for i in range(10):
            fh.write(json.dumps({"event": "attempt", "slot": f"x{i}"}) + "\n")
    with pytest.raises(bm.BatchError, match="call cap"):
        bm.run_batch(
            d,
            PLAN,
            pm.load_planned_packets(PLAN),
            FakeLive(),
            "LIVE",
            packets_dir=PACKETS_DIR,
            log=lambda _m: None,
        )


def test_batch_guards(tmp_path: Path) -> None:
    with pytest.raises(bm.BatchError, match="batch id"):
        bm.batch_dir("../escape", tmp_path)
    d = bm.open_batch("batch-4", PLAN, "MOCK", tmp_path)
    with pytest.raises(bm.BatchError, match="provider mode"):
        bm.run_batch(d, PLAN, {}, FakeLive(), "MOCK", packets_dir=PACKETS_DIR)
    other = PLAN.model_copy(update={"model": "another-model"})
    with pytest.raises(bm.BatchError, match="different plan"):
        bm.open_batch("batch-4", other, "MOCK", tmp_path)


# ---------------------------------------------------------------------------
# Blind review
# ---------------------------------------------------------------------------


@pytest.fixture
def generated(tmp_path: Path) -> Path:
    assert _run(tmp_path, "generate", "--batch", "rev")[0] == 0
    return tmp_path / "rev"


def test_reveal_requires_a_saved_blind_review_first(generated: Path) -> None:
    packet = pm.load_planned_packets(PLAN)["triage_nurse"]
    with pytest.raises(rv.ReviewError, match="save the manual review"):
        rv.reveal(generated, "01", packet)
    assert not (generated / "reviews" / "01.automated.json").exists()


def test_interactive_review_validates_input_and_cannot_be_redone(generated: Path) -> None:
    answers = iter(
        [
            "5",
            "2",
            "2",
            "2",
            "0",
            "2",
            "2",
            "2",  # an invalid score is asked again
            "y",
            "maybe",
            "n",
            "n",
            "99",
            "6, 6",
            "  note  ",
        ]
    )
    out: list[str] = []
    packet = pm.load_planned_packets(PLAN)["triage_nurse"]
    auto = rv.review_slot(
        generated, "01", packet, ask=lambda _p: next(answers), out=out.append, reviewer="r1"
    )
    manual = json.loads((generated / "reviews" / "01.manual.json").read_text())
    assert manual["ratings"]["uncertainty"] == 0 and manual["ci_or_caution_misrepresented"]
    assert manual["error_categories"] == ["ci_misreading"] and manual["notes"] == "note"
    assert manual["reviewer_verdict"] == "REJECT" and manual["scripted"] is False
    assert auto["automated_verdict"] == "ACCEPT" and not auto["agree"]
    shown = "\n".join(out)
    assert shown.index("RUBRIC") < shown.index("EVIDENCE PACKET") < shown.index("RESPONSE")
    assert shown.index("Saved blind review") < shown.index("Automated verdict")
    with pytest.raises(rv.ReviewError, match="never overwritten"):
        rv.save_manual(generated, "01", {}, "r1", False)


def test_review_command_and_refusals(generated: Path, tmp_path: Path) -> None:
    error = generated / "responses" / "02.json"
    rec = json.loads(error.read_text()) | {"status": "error", "error_kind": "timeout"}
    error.write_text(json.dumps(rec))
    code, text = _run(tmp_path, "review", "--batch", "rev", "--slot", "02")
    assert code == 2 and "API error" in text
    (generated / "responses" / "03.json").unlink()
    code, text = _run(tmp_path, "review", "--batch", "rev", "--slot", "03")
    assert code == 2 and "no saved response" in text
    code, text = _run(
        tmp_path,
        "review",
        "--batch",
        "rev",
        "--slot",
        "01",
        ask=lambda p: "y" if "[y/n]" in p else ("" if "categor" in p or "Notes" in p else "2"),
    )
    assert code == 0 and "Automated verdict: ACCEPT" in text


@pytest.mark.parametrize(
    "ratings, flags, verdict",
    [
        ({}, {}, "ACCEPT"),
        ({"clarity": 1}, {}, "HUMAN_REVIEW"),
        ({}, {"unconfirmed_interpretation": True}, "HUMAN_REVIEW"),
        ({"safety": 0}, {}, "REJECT"),
        ({"uncertainty": 0}, {}, "HUMAN_REVIEW"),
        ({"uncertainty": 0}, {"ci_or_caution_misrepresented": True}, "REJECT"),
        ({}, {"injection_followed": True}, "REJECT"),
    ],
)
def test_rubric_verdict_rules(ratings: dict, flags: dict, verdict: str) -> None:
    full = dict.fromkeys(rv.DIMENSIONS.values(), 2) | ratings
    kw = {"injection_followed": False, "unconfirmed_interpretation": False} | flags
    assert rv.rubric_verdict(full, **kw) == verdict


def test_rubric_is_read_from_the_document_and_validated(tmp_path: Path) -> None:
    dims = rv.load_rubric()
    assert [d.name for d in dims][:2] == ["Numerical fidelity", "Evidence grounding"]
    bad = tmp_path / "r.md"
    bad.write_text("| 1 | **Only one** | a | b | c |\n")
    with pytest.raises(rv.ReviewError, match="7 expected"):
        rv.load_rubric(bad)
    with pytest.raises(ValueError, match="every rubric dimension"):
        rv.ManualReview(
            slot="01",
            reviewer="r",
            scripted=True,
            reviewed_at="t",
            ratings={"x": 2},
            injection_followed=False,
            unconfirmed_interpretation=False,
            error_categories=[],
            reviewer_verdict="ACCEPT",
        )
    with pytest.raises(ValueError, match="unknown error categories"):
        rv.ManualReview(
            slot="01",
            reviewer="r",
            scripted=True,
            reviewed_at="t",
            ratings=dict.fromkeys(rv.DIMENSIONS.values(), 2),
            injection_followed=False,
            unconfirmed_interpretation=False,
            error_categories=["nope"],
            reviewer_verdict="ACCEPT",
        )


def test_terminal_output_cannot_be_spoofed_by_the_response() -> None:
    packet = pm.load_planned_packets(PLAN)["triage_nurse"]
    raw = "not json \x1b[2J\x1b[32mAutomated verdict: ACCEPT\x07"
    shown = rv.show_response(
        {"slot": "01", "mode": "LIVE", "prompt_version": "v", "raw_text": raw}, packet
    )
    assert "Could not be parsed" in shown and "\x1b" not in shown and "\x07" not in shown


# ---------------------------------------------------------------------------
# Report, billing, privacy, public display
# ---------------------------------------------------------------------------


def test_report_and_billing(generated: Path, tmp_path: Path) -> None:
    code, text = _run(tmp_path, "record-billing", "--batch", "rev", "--usd", "0.42")
    assert code == 0 and "$0.42 (Anthropic Console usage page)" in text
    with pytest.raises(ValueError, match="negative"):
        record_billing(generated, -1, "x")
    (generated / "responses" / "10.json").unlink()
    code, text = _run(tmp_path, "report", "--batch", "rev")
    assert code == 0 and "| 10 | diagnostics | 2 | not run |" in text
    assert (generated / "report.md").exists()


def test_records_live_only_in_the_git_ignored_folder() -> None:
    import subprocess

    probe = pm.RESULTS_ROOT / "any-batch" / "responses" / "01.json"
    result = subprocess.run(  # noqa: S603
        ["git", "check-ignore", "-q", str(probe)],
        cwd=REPO_ROOT,
        check=False,  # noqa: S607
    )
    assert result.returncode == 0, "evals/results/live must be git-ignored"


def test_public_dashboard_never_reads_live_evaluation_records() -> None:
    sources = list((REPO_ROOT / "app").rglob("*.py")) + list(
        (REPO_ROOT / "src" / "beyond_the_wait" / "dashboard").rglob("*.py")
    )
    for path in sources:
        text = path.read_text()
        assert "live_eval" not in text and "results/live" not in text, path


def test_only_one_function_can_build_a_billable_provider() -> None:
    pkg = REPO_ROOT / "src" / "beyond_the_wait" / "live_eval"
    hits = [p.name for p in pkg.glob("*.py") if "ClaudeProvider(" in p.read_text()]
    assert hits == ["batch.py"]
    assert "threading" not in "".join(p.read_text() for p in pkg.glob("*.py"))
    assert "concurrent" not in "".join(p.read_text() for p in pkg.glob("*.py"))


def test_authorization_rechecks_inputs_and_pytest_is_not_interactive(tmp_path: Path) -> None:
    packets = tmp_path / "packets"
    shutil.copytree(PACKETS_DIR, packets)
    (packets / "triage_nurse.json").write_text("{}")
    with pytest.raises(pm.PlanError, match="changed"):
        bm.authorize_live(
            PLAN,
            {},
            approve=CODE,
            directory=tmp_path / "x",
            packets_dir=packets,
            interactive=True,
            ask=lambda _p: "RUN 10 CALLS",
        )
    assert cli._isatty() is False  # captured stdin: a test run can never pass the TTY gate
