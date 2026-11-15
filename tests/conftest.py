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
