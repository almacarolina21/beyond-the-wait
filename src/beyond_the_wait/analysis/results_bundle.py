"""Precomputed what-if results: write once, verify on every load.

The dashboard must load quickly, so it reads results that
`python -m beyond_the_wait.analysis.whatif_demo` computed in advance
(30 paired seeds take minutes; nothing that slow should run on a widget
change). Because those files are read later, possibly after someone edited
them, the bundle carries a manifest with:

  * a SHA-256 fingerprint of every file (tampering or truncation is detected),
  * the experiment settings and seeds (reproducibility),
  * per-comparison status, carried CAUTIONs, overload counts, and lever effects.

`load_bundle()` refuses a bundle whose files are missing, altered, mislabeled
(provenance must be SIMULATED), or malformed. It separates BLOCKED
comparisons so no BLOCKED number can be displayed as valid comparison data.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from importlib.metadata import version
from pathlib import Path
from typing import Literal

import pandas as pd

from beyond_the_wait.analysis.experiments import (
    MODEL_EVIDENCE_NOTE,
    ComparisonReport,
    ExperimentSettings,
)
from beyond_the_wait.analysis.validation import SAFETY_LIMITATIONS

BUNDLE_VERSION = 1
MANIFEST = "manifest.json"
FILES = {
    "comparisons": "comparisons.csv",
    "checks": "comparison_checks.csv",
    "archetypes": "archetypes.csv",
    "sensitivity": "sensitivity.csv",
    "stability": "stability.csv",
}
OPTIONAL = {"sensitivity", "stability"}
STATUSES = {"PASS", "CAUTION", "BLOCKED"}
COMPARISON_COLUMNS = [
    "scope",
    "baseline_id",
    "scenario_id",
    "kind",
    "metric",
    "label",
    "unit",
    "better",
    "n_pairs",
    "n_missing",
    "baseline_mean",
    "scenario_mean",
    "mean_difference",
    "sd_difference",
    "ci95_low",
    "ci95_high",
    "pred95_low",
    "pred95_high",
    "skewness",
    "direction",
    "comparison_status",
    "provenance",
]
NUMERIC = [
    "baseline_mean",
    "scenario_mean",
    "mean_difference",
    "sd_difference",
    "ci95_low",
    "ci95_high",
    "pred95_low",
    "pred95_high",
]
REQUIRED_COLUMNS = {
    "comparisons": COMPARISON_COLUMNS,
    "checks": ["scope", "baseline_id", "scenario_id", "check", "status", "detail", "provenance"],
    "archetypes": ["archetype", "relief", "outcome", "provenance"],
    "sensitivity": [
        "concurrency",
        "releases_per_day",
        "intervention",
        "metric",
        "direction",
        "regime",
        "comparison_status",
        "provenance",
    ],
    "stability": ["intervention", "metric", "verdict", "provenance"],
}

Scope = Literal["main", "archetype_condition", "archetype_relief"]


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def comparisons_frame(reports: Sequence[tuple[Scope, ComparisonReport]]) -> pd.DataFrame:
    """Every metric of every comparison as one long table (SIMULATED)."""
    rows = []
    for scope, rep in reports:
        for mc in rep.metrics.values():
            e = mc.estimate
            nan = float("nan")
            rows.append(
                {
                    "scope": scope,
                    "baseline_id": rep.baseline_id,
                    "scenario_id": rep.scenario_id,
                    "kind": rep.scenario_kind,
                    "metric": mc.metric,
                    "label": mc.label,
                    "unit": mc.unit,
                    "better": mc.better,
                    "n_pairs": e.n if e else 0,
                    "n_missing": mc.n_missing,
                    "baseline_mean": e.baseline_mean if e else nan,
                    "scenario_mean": e.scenario_mean if e else nan,
                    "mean_difference": e.mean_difference if e else nan,
                    "sd_difference": e.sd_difference if e else nan,
                    "ci95_low": e.ci_low if e else nan,
                    "ci95_high": e.ci_high if e else nan,
                    "pred95_low": e.prediction_low if e else nan,
                    "pred95_high": e.prediction_high if e else nan,
                    "skewness": e.skewness if e else nan,
                    "direction": mc.direction,
                    "comparison_status": rep.status.value,
                    "provenance": "SIMULATED",
                }
            )
        if not rep.metrics:  # BLOCKED: one placeholder row, no numbers
            rows.append(
                {c: float("nan") for c in NUMERIC}
                | {
                    "scope": scope,
                    "baseline_id": rep.baseline_id,
                    "scenario_id": rep.scenario_id,
                    "kind": rep.scenario_kind,
                    "metric": "",
                    "label": "",
                    "unit": "",
                    "better": "",
                    "n_pairs": 0,
                    "n_missing": 0,
                    "skewness": float("nan"),
                    "direction": "blocked",
                    "comparison_status": rep.status.value,
                    "provenance": "SIMULATED",
                }
            )
    return pd.DataFrame(rows, columns=COMPARISON_COLUMNS)


def checks_frame(reports: Sequence[tuple[Scope, ComparisonReport]]) -> pd.DataFrame:
    rows = [
        {
            "scope": scope,
            "baseline_id": rep.baseline_id,
            "scenario_id": rep.scenario_id,
            "check": c.check,
            "status": c.status.value,
            "detail": c.detail,
            "provenance": "SIMULATED",
        }
        for scope, rep in reports
        for c in rep.checks
    ]
    return pd.DataFrame(rows, columns=REQUIRED_COLUMNS["checks"])


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_bundle(
    out_dir: Path,
    *,
    settings: ExperimentSettings,
    reports: Sequence[tuple[Scope, ComparisonReport]],
    archetypes: pd.DataFrame,
    sensitivity: pd.DataFrame | None = None,
    stability: pd.DataFrame | None = None,
    sensitivity_seeds: int | None = None,
) -> dict[str, object]:
    """Write the bundle CSVs and a fingerprinted manifest. Returns the manifest."""
    out_dir.mkdir(parents=True, exist_ok=True)
    frames: dict[str, pd.DataFrame | None] = {
        "comparisons": comparisons_frame(reports),
        "checks": checks_frame(reports),
        "archetypes": archetypes.assign(provenance="SIMULATED"),
        "sensitivity": sensitivity,
        "stability": None if stability is None else stability.assign(provenance="SIMULATED"),
    }
    files = {}
    for key, df in frames.items():
        path = out_dir / FILES[key]
        if df is None:
            path.unlink(missing_ok=True)
            continue
        df.to_csv(path, index=False, lineterminator="\n")
        files[key] = {"path": FILES[key], "sha256": _sha(path), "rows": len(df)}
    manifest = {
        "bundle_version": BUNDLE_VERSION,
        "created_by": "beyond_the_wait.analysis.whatif_demo",
        "package_version": version("beyond-the-wait"),
        "provenance": "SIMULATED",
        "settings": settings.model_dump(mode="json", exclude={"workers"}),
        "sensitivity_seeds": sensitivity_seeds,
        "evidence_note": MODEL_EVIDENCE_NOTE,
        "limitations": list(SAFETY_LIMITATIONS),
        "comparisons": [
            {
                "scope": scope,
                "baseline_id": rep.baseline_id,
                "scenario_id": rep.scenario_id,
                "kind": rep.scenario_kind,
                "status": rep.status.value,
                "effects": rep.effects,
                "replication_status": rep.replication_status,
                "carried_cautions": rep.carried_cautions,
                "overloaded_replications": rep.overloaded_replications,
            }
            for scope, rep in reports
        ],
        "files": files,
    }
    (out_dir / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


# ---------------------------------------------------------------------------
# Loading and verification
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Bundle:
    manifest: dict
    comparisons: pd.DataFrame  # valid (non-BLOCKED) comparisons only
    checks: pd.DataFrame
    archetypes: pd.DataFrame
    sensitivity: pd.DataFrame | None
    stability: pd.DataFrame | None
    blocked: list[dict] = field(
        default_factory=list
    )  # (scope, baseline, scenario) that were BLOCKED

    @property
    def n_seeds(self) -> int:
        return len(self.manifest["settings"]["seeds"])


@dataclass(frozen=True)
class BundleLoad:
    status: Literal["ok", "missing", "invalid"]
    problems: list[str]
    bundle: Bundle | None = None


def load_bundle(directory: Path) -> BundleLoad:
    """Load and verify a results bundle. Never raises on bad files."""
    manifest_path = directory / MANIFEST
    if not manifest_path.is_file():
        return BundleLoad("missing", [f"No results bundle at {directory} (no {MANIFEST})."])
    try:
        manifest = json.loads(manifest_path.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return BundleLoad("invalid", [f"{MANIFEST} is not valid JSON: {exc}"])
    problems: list[str] = []
    if manifest.get("bundle_version") != BUNDLE_VERSION:
        problems.append(f"unsupported bundle_version {manifest.get('bundle_version')!r}")
    if manifest.get("provenance") != "SIMULATED":
        problems.append("manifest provenance must be SIMULATED")
    files = manifest.get("files", {})
    frames: dict[str, pd.DataFrame | None] = {}
    for key, name in FILES.items():
        entry = files.get(key)
        path = directory / name
        if entry is None:
            if key not in OPTIONAL:
                problems.append(f"manifest does not list required file {name}")
            frames[key] = None
            continue
        if not path.is_file():
            problems.append(f"missing file {name}")
            continue
        if _sha(path) != entry.get("sha256"):
            problems.append(
                f"{name} does not match its manifest fingerprint (altered or truncated)"
            )
            continue
        df = pd.read_csv(path)
        missing = [c for c in REQUIRED_COLUMNS[key] if c not in df.columns]
        if missing:
            problems.append(f"{name} is missing columns {missing}")
            continue
        if not (df["provenance"] == "SIMULATED").all():
            problems.append(f"{name} contains rows not labeled SIMULATED")
            continue
        frames[key] = df
    if problems:
        return BundleLoad("invalid", problems)

    comps = frames["comparisons"]
    bad_status = set(comps["comparison_status"]) - STATUSES
    if bad_status:
        return BundleLoad("invalid", [f"unknown comparison statuses {sorted(bad_status)}"])
    is_blocked = comps["comparison_status"] == "BLOCKED"
    leaked = comps.loc[is_blocked, NUMERIC].notna().any(axis=None)
    if leaked:
        return BundleLoad("invalid", ["a BLOCKED comparison contains numbers; refusing the bundle"])
    blocked = (
        comps.loc[is_blocked, ["scope", "baseline_id", "scenario_id"]]
        .drop_duplicates()
        .to_dict("records")
    )
    sens = frames["sensitivity"]
    if sens is not None:
        sens = sens.copy()
        b = sens["comparison_status"] == "BLOCKED"
        sens.loc[b, "direction"] = "blocked"
        for col in ("mean_difference", "ci95_low", "ci95_high"):
            if col in sens:
                sens.loc[b, col] = math.nan
    return BundleLoad(
        "ok",
        [],
        Bundle(
            manifest=manifest,
            comparisons=comps[~is_blocked].reset_index(drop=True),
            checks=frames["checks"],
            archetypes=frames["archetypes"],
            sensitivity=sens,
            stability=frames["stability"],
            blocked=blocked,
        ),
    )
