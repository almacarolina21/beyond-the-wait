"""The committed demo results bundle: a fresh clone must be able to run the dashboard.

These tests read the real files in data/synthetic/experiments/ (SIMULATED,
fictional). They pin the three findings quoted in README.md to the bundle, so
the README cannot drift from what the app shows.
"""

from __future__ import annotations

import json
import subprocess

from beyond_the_wait.analysis.results_bundle import load_bundle
from beyond_the_wait.dashboard.data import DEFAULT_RESULTS_DIR, REPO_ROOT, key_findings
from beyond_the_wait.evaluation.dataset import PACKETS_DIR
from beyond_the_wait.interpretation.evidence import _bundle_fingerprint

README_FINDINGS = [
    "98.4 min lower within the fictional model (95% CI 66.0 to 131 min lower)",
    "13.9 min lower within the fictional model (95% CI 13.5 to 14.3 min lower)",
    "4.8 min lower within the fictional model (95% CI 3.8 to 5.9 min lower)",
    "3.2 min higher within the fictional model (95% CI 2.4 to 4.0 min higher)",
]


def test_committed_bundle_loads_and_passes_fingerprint_verification():
    loaded = load_bundle(DEFAULT_RESULTS_DIR)
    assert loaded.status == "ok", loaded.problems
    assert loaded.bundle.n_seeds == 30
    assert loaded.bundle.manifest["provenance"] == "SIMULATED"


def test_frozen_evaluation_packets_come_from_the_committed_bundle():
    bundle = load_bundle(DEFAULT_RESULTS_DIR).bundle
    fingerprint = _bundle_fingerprint(bundle.manifest)
    for path in sorted(PACKETS_DIR.glob("*.json")):
        assert json.loads(path.read_text())["bundle_fingerprint"] == fingerprint, path.name


def test_readme_findings_match_the_bundle_exactly():
    texts = " ".join(f["text"] for f in key_findings(load_bundle(DEFAULT_RESULTS_DIR).bundle))
    readme = (REPO_ROOT / "README.md").read_text()
    for finding in README_FINDINGS:
        assert finding in texts, finding
        assert finding in readme, finding


def test_bundle_is_tracked_by_git_and_holds_no_local_paths():
    tracked = subprocess.run(
        ["git", "ls-files", str(DEFAULT_RESULTS_DIR.relative_to(REPO_ROOT))],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if tracked.returncode != 0:  # not a git checkout (e.g. a source archive)
        return
    names = {line.rsplit("/", 1)[-1] for line in tracked.stdout.splitlines()}
    assert {"manifest.json", "comparisons.csv"} <= names
    for path in DEFAULT_RESULTS_DIR.iterdir():
        text = path.read_text()
        assert "/Users/" not in text and "/home/" not in text and "C:\\" not in text
