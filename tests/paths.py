"""File locations used by the tests (kept in one place so tests never hard-code paths)."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SITES_DIR = REPO_ROOT / "config" / "sites"
SCENARIOS_DIR = REPO_ROOT / "config" / "scenarios"
EXAMPLE_SITE_PATH = SITES_DIR / "fictional_bronx_community_ed.yaml"
EXAMPLE_SCENARIO_PATH = SCENARIOS_DIR / "add_evening_physician.yaml"
