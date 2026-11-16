"""Tests for the precomputed results bundle: writing, verification, BLOCKED handling."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pandas as pd
import pytest

from beyond_the_wait.analysis.experiments import Arm, Replication, compare
from beyond_the_wait.analysis.results_bundle import (
    FILES,
    MANIFEST,
    comparisons_frame,
    load_bundle,
)
from beyond_the_wait.analysis.validation import GateStatus
from beyond_the_wait.config import SiteProfile


@pytest.fixture
def bundle_copy(small_bundle_dir: Path, tmp_path: Path) -> Path:
    dest = tmp_path / "b"
    shutil.copytree(small_bundle_dir, dest)
    return dest


def _rewrite(directory: Path, key: str, df: pd.DataFrame) -> None:
    """Rewrite one file AND its manifest fingerprint (so only content checks can catch it)."""
    path = directory / FILES[key]
    df.to_csv(path, index=False)
    manifest = json.loads((directory / MANIFEST).read_text())
    manifest["files"][key]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    (directory / MANIFEST).write_text(json.dumps(manifest))


def test_real_small_bundle_loads_and_verifies(small_bundle_dir: Path) -> None:
    load = load_bundle(small_bundle_dir)
    assert load.status == "ok" and load.problems == []
    b = load.bundle
    assert b.n_seeds == 2 and b.blocked == []
    assert set(b.comparisons["scope"]) == {"main", "archetype_condition", "archetype_relief"}
    assert (b.comparisons["provenance"] == "SIMULATED").all()
    assert b.sensitivity is not None and b.stability is not None
    assert b.manifest["provenance"] == "SIMULATED" and b.manifest["limitations"]


def test_missing_bundle(tmp_path: Path) -> None:
    load = load_bundle(tmp_path / "nothing")
    assert load.status == "missing" and load.bundle is None


def test_invalid_manifest_json(bundle_copy: Path) -> None:
    (bundle_copy / MANIFEST).write_text("{not json")
    assert load_bundle(bundle_copy).status == "invalid"


def test_tampered_file_fails_fingerprint(bundle_copy: Path) -> None:
    path = bundle_copy / FILES["comparisons"]
    path.write_text(path.read_text().replace("CAUTION", "PASS", 1))
    load = load_bundle(bundle_copy)
    assert load.status == "invalid" and "fingerprint" in load.problems[0]


def test_wrong_provenance_rejected(bundle_copy: Path) -> None:
    df = pd.read_csv(bundle_copy / FILES["comparisons"])
    _rewrite(bundle_copy, "comparisons", df.assign(provenance="PUBLIC"))
    load = load_bundle(bundle_copy)
    assert load.status == "invalid" and "not labeled SIMULATED" in load.problems[0]


def test_missing_required_file_and_column(bundle_copy: Path, tmp_path: Path) -> None:
    other = tmp_path / "other"
    shutil.copytree(bundle_copy, other)
    (bundle_copy / FILES["checks"]).unlink()
    assert "missing file" in load_bundle(bundle_copy).problems[0]
    df = pd.read_csv(other / FILES["archetypes"]).drop(columns="relief")
    _rewrite(other, "archetypes", df)
    assert "missing columns" in load_bundle(other).problems[0]


def test_optional_sensitivity_may_be_absent(bundle_copy: Path) -> None:
    manifest = json.loads((bundle_copy / MANIFEST).read_text())
    del manifest["files"]["sensitivity"], manifest["files"]["stability"]
    (bundle_copy / MANIFEST).write_text(json.dumps(manifest))
    load = load_bundle(bundle_copy)
    assert load.status == "ok" and load.bundle.sensitivity is None


def test_bad_version_and_status(bundle_copy: Path, tmp_path: Path) -> None:
    other = tmp_path / "o"
    shutil.copytree(bundle_copy, other)
    manifest = json.loads((bundle_copy / MANIFEST).read_text())
    manifest["bundle_version"] = 99
    (bundle_copy / MANIFEST).write_text(json.dumps(manifest))
    assert "bundle_version" in load_bundle(bundle_copy).problems[0]
    df = pd.read_csv(other / FILES["comparisons"])
    _rewrite(other, "comparisons", df.assign(comparison_status="GREEN"))
    assert "unknown comparison statuses" in load_bundle(other).problems[0]


def _blocked_report(site: SiteProfile):  # noqa: ANN202
    from beyond_the_wait.analysis.experiments import METRICS

    metrics = {m.name: 1.0 for m in METRICS}
    reps = {
        "baseline": [
            Replication("baseline", i, GateStatus.CAUTION, False, (), (), metrics) for i in range(2)
        ],
        "scen": [
            Replication("scen", 0, GateStatus.BLOCKED, False, (), ("bad",), None),
            Replication("scen", 1, GateStatus.CAUTION, False, (), (), metrics),
        ],
    }
    return compare(Arm("baseline", site), Arm("scen", site, kind="intervention", effects={}), reps)


def test_blocked_comparison_is_written_without_numbers_and_excluded_on_load(
    bundle_copy: Path, site: SiteProfile
) -> None:
    rep = _blocked_report(site)
    frame = comparisons_frame([("main", rep)])
    assert len(frame) == 1 and frame["direction"].iloc[0] == "blocked"
    assert frame[["mean_difference", "ci95_low", "ci95_high"]].isna().all(axis=None)
    combined = pd.concat([pd.read_csv(bundle_copy / FILES["comparisons"]), frame])
    _rewrite(bundle_copy, "comparisons", combined)
    load = load_bundle(bundle_copy)
    assert load.status == "ok"
    assert load.bundle.blocked == [
        {"scope": "main", "baseline_id": "baseline", "scenario_id": "scen"}
    ]
    assert "scen" not in set(load.bundle.comparisons["scenario_id"])


def test_blocked_comparison_with_numbers_rejects_bundle(bundle_copy: Path) -> None:
    df = pd.read_csv(bundle_copy / FILES["comparisons"])
    df.loc[0, "comparison_status"] = "BLOCKED"  # still carries numbers
    _rewrite(bundle_copy, "comparisons", df)
    load = load_bundle(bundle_copy)
    assert load.status == "invalid" and "BLOCKED comparison contains numbers" in load.problems[0]


def test_blocked_sensitivity_cells_lose_their_numbers(bundle_copy: Path) -> None:
    df = pd.read_csv(bundle_copy / FILES["sensitivity"])
    df.loc[0, "comparison_status"] = "BLOCKED"
    _rewrite(bundle_copy, "sensitivity", df)
    sens = load_bundle(bundle_copy).bundle.sensitivity
    assert sens.loc[0, "direction"] == "blocked" and pd.isna(sens.loc[0, "mean_difference"])


def test_manifest_must_be_simulated_and_list_required_files(
    bundle_copy: Path, tmp_path: Path
) -> None:
    other = tmp_path / "o2"
    shutil.copytree(bundle_copy, other)
    m = json.loads((bundle_copy / MANIFEST).read_text())
    m["provenance"] = "PUBLIC"
    (bundle_copy / MANIFEST).write_text(json.dumps(m))
    assert "provenance must be SIMULATED" in load_bundle(bundle_copy).problems[0]
    m2 = json.loads((other / MANIFEST).read_text())
    del m2["files"]["checks"]
    (other / MANIFEST).write_text(json.dumps(m2))
    assert "does not list required file" in load_bundle(other).problems[0]
