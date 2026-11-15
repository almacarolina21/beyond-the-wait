"""Tests for loading configuration files from disk.

`tmp_path` is a built-in pytest fixture: a fresh temporary folder per test,
deleted afterwards, so we can write deliberately broken files safely.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from beyond_the_wait.config import ConfigError, load_scenario, load_site_profile
from tests.paths import EXAMPLE_SCENARIO_PATH, EXAMPLE_SITE_PATH, SCENARIOS_DIR, SITES_DIR


def test_load_example_site() -> None:
    site = load_site_profile(EXAMPLE_SITE_PATH)
    assert site.site.display_name.endswith("(fictional)")


def test_load_example_scenario() -> None:
    assert load_scenario(EXAMPLE_SCENARIO_PATH).scenario_id == "add_evening_physician"


# Every committed config file must be valid -- this catches a broken file the
# moment someone adds or edits one, without writing a new test.
@pytest.mark.parametrize("path", sorted(SITES_DIR.glob("*.yaml")), ids=lambda p: p.name)
def test_every_committed_site_profile_is_valid(path: Path) -> None:
    load_site_profile(path)


@pytest.mark.parametrize("path", sorted(SCENARIOS_DIR.glob("*.yaml")), ids=lambda p: p.name)
def test_every_committed_scenario_is_valid(path: Path) -> None:
    load_scenario(path)


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_site_profile(tmp_path / "nope.yaml")


def test_malformed_yaml(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("site: [unclosed\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_site_profile(bad)


@pytest.mark.parametrize("content", ["", "- just\n- a list\n", "42\n"])
def test_top_level_must_be_mapping(tmp_path: Path, content: str) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(content, encoding="utf-8")
    with pytest.raises(ConfigError, match="key: value pairs"):
        load_site_profile(bad)


def test_duplicate_keys_rejected(tmp_path: Path) -> None:
    text = EXAMPLE_SITE_PATH.read_text(encoding="utf-8").replace(
        "  acute_beds: 36\n", "  acute_beds: 36\n  acute_beds: 3600\n"
    )
    dup = tmp_path / "dup.yaml"
    dup.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match="duplicate key 'acute_beds'"):
        load_site_profile(dup)


def test_python_object_tags_are_not_executed(tmp_path: Path) -> None:
    # The unsafe YAML loader would *run* this command. The safe loader refuses.
    evil = tmp_path / "evil.yaml"
    evil.write_text("site: !!python/object/apply:os.system ['echo pwned']\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_site_profile(evil)


def test_validation_errors_name_the_file_and_field(tmp_path: Path) -> None:
    text = EXAMPLE_SITE_PATH.read_text(encoding="utf-8").replace("acute_beds: 36", "acute_beds: -5")
    bad = tmp_path / "negative_beds.yaml"
    bad.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError) as excinfo:
        load_site_profile(bad)
    message = str(excinfo.value)
    assert "negative_beds.yaml" in message
    assert "treatment_spaces.acute_beds" in message
