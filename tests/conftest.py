"""Shared pytest fixtures.

A *fixture* is a function pytest runs to set up something a test needs.
Tests ask for a fixture by naming it as a parameter. Each test gets a FRESH
copy, so one test's edits can never leak into another test.
"""

from __future__ import annotations

from typing import Any

import pytest
import yaml

from beyond_the_wait.config import Scenario, SiteProfile
from tests.paths import EXAMPLE_SCENARIO_PATH, EXAMPLE_SITE_PATH


@pytest.fixture
def site_dict() -> dict[str, Any]:
    """The example site profile as a plain, editable dictionary."""
    return yaml.safe_load(EXAMPLE_SITE_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def site(site_dict: dict[str, Any]) -> SiteProfile:
    """The example site profile as a validated model."""
    return SiteProfile.model_validate(site_dict)


@pytest.fixture
def scenario_dict() -> dict[str, Any]:
    """The example scenario as a plain, editable dictionary."""
    return yaml.safe_load(EXAMPLE_SCENARIO_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def scenario(scenario_dict: dict[str, Any]) -> Scenario:
    """The example scenario as a validated model."""
    return Scenario.model_validate(scenario_dict)


@pytest.fixture(scope="session")
def small_bundle_dir(tmp_path_factory: pytest.TempPathFactory):  # noqa: ANN201
    """A small but real results bundle (2 seeds, 2 days), built once per test session
    by the same code path as the full 30-seed build."""
    from beyond_the_wait.analysis import whatif_demo

    out = tmp_path_factory.mktemp("bundle")
    code = whatif_demo.main(
        [
            "--seeds",
            "2",
            "--sensitivity-seeds",
            "2",
            "--days",
            "2",
            "--workers",
            "1",
            "--out",
            str(out),
        ]
    )
    assert code == 0
    return out
