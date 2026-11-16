"""Tests for the validated-analysis demonstration CLI."""

from __future__ import annotations

import dataclasses

import pytest

from beyond_the_wait.analysis import demo


def test_demo_prints_status_tables_findings_and_limits(capsys: pytest.CaptureFixture[str]) -> None:
    assert demo.main(["--days", "3", "--warmup-days", "1", "--seed", "5", "--gate-examples"]) == 0
    out = capsys.readouterr().out
    for text in (
        "VALIDATION STATUS: CAUTION",
        "Arrival to provider by ESI",
        "Staff utilization by shift",
        "Boarding distribution",
        "Extreme waits",
        "DESCRIPTIVE, one run",
        "DO THE MILESTONE 4 FINDINGS SURVIVE",
        "left_without_being_seen_rate",
        "NOT A STAFFING RECOMMENDATION",
        "STRESS TEST",
        "status BLOCKED",
        "REFUSED",
    ):
        assert text in out, text


def test_demo_shows_no_numbers_for_blocked_run(
    monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:  # noqa: ANN001
    real_run = demo.run_simulation

    def corrupted_run(site, settings):  # noqa: ANN001, ANN202
        r = real_run(site, settings)
        return dataclasses.replace(r, patients=r.patients.assign(provenance="PUBLIC"))

    monkeypatch.setattr(demo, "run_simulation", corrupted_run)
    assert demo.main(["--days", "3", "--warmup-days", "1", "--seed", "5"]) == 2
    out = capsys.readouterr().out
    assert "VALIDATION STATUS: BLOCKED" in out and "no metrics are shown" in out
    assert "Arrival to provider by ESI" not in out


def test_demo_rejects_bad_settings(capsys: pytest.CaptureFixture[str]) -> None:
    assert demo.main(["--days", "1", "--warmup-days", "1"]) == 1
    assert "ERROR" in capsys.readouterr().err
