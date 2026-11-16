"""Data access for the dashboard. Thin wrappers over existing, tested APIs.

Every loader validates what it reads and returns a status instead of raising,
so a missing or invalid file becomes a clear message on the page, not a crash.

    PUBLIC     load_public_data()   -> data/public/processed (Milestone 2)
    SIMULATED  run_flow()            -> one live single-seed run through the gate
    SIMULATED  load_results()        -> precomputed Milestone 6 bundle, verified
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Literal

import pandas as pd

from beyond_the_wait.analysis import detailed_metrics as dm
from beyond_the_wait.analysis.results_bundle import Bundle, BundleLoad, load_bundle
from beyond_the_wait.analysis.validation import (
    BlockedResultError,
    GateStatus,
    ValidationReport,
    release_for_interpretation,
    validate_run,
)
from beyond_the_wait.config import ConfigError, SiteProfile, load_scenario, load_site_profile
from beyond_the_wait.data.ed_measures import MEASURES
from beyond_the_wait.data.errors import PublicDataError
from beyond_the_wait.data.provenance import Provenance, require_provenance
from beyond_the_wait.data.public_pipeline import (
    BUILD_MANIFEST_FILE,
    DEFAULT_OUT_DIR,
    FACILITIES_FILE,
    MEASURES_FILE,
    load_processed_measures,
)
from beyond_the_wait.data.synthetic_generator import generate_patients
from beyond_the_wait.simulation.engine import SimulationSettings, run_simulation

REPO_ROOT = Path(__file__).resolve().parents[3]
SITE_PATH = REPO_ROOT / "config" / "sites" / "fictional_bronx_community_ed.yaml"
SCENARIOS_DIR = REPO_ROOT / "config" / "scenarios"
DEFAULT_RESULTS_DIR = REPO_ROOT / "data" / "synthetic" / "experiments"
BUILD_COMMAND = (
    "python -m beyond_the_wait.analysis.whatif_demo --seeds 30 --sensitivity-seeds 20 --workers 8"
)
START = date(2026, 11, 16)
MAX_FLOW_DAYS = 14  # cap for the live single-run view
CMS_MEASURE_ORDER = ["OP_18b", "OP_18a", "OP_18c", "OP_18d", "OP_22", "EDV"]
NYS_MEASURE_ORDER = [
    "NYS_ED_TOTAL",
    "NYS_ED_TREAT_AND_RELEASE",
    "NYS_ED_ADMITTED",
    "NYS_ED_AMBULATORY_SURGERY",
]


def results_dir() -> Path:
    return Path(os.environ.get("BTW_RESULTS_DIR", DEFAULT_RESULTS_DIR))


def public_dir() -> Path:
    return Path(os.environ.get("BTW_PUBLIC_DIR", DEFAULT_OUT_DIR))


# ---------------------------------------------------------------------------
# PUBLIC data
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PublicData:
    measures: pd.DataFrame
    facilities: pd.DataFrame
    manifest: dict


@dataclass(frozen=True)
class PublicLoad:
    status: Literal["ok", "missing", "invalid"]
    problems: list[str]
    data: PublicData | None = None


def load_public_data(directory: Path | None = None) -> PublicLoad:
    """Load committed Bronx reference tables, verifying PUBLIC provenance."""
    d = directory or public_dir()
    paths = [d / MEASURES_FILE, d / FACILITIES_FILE, d / BUILD_MANIFEST_FILE]
    missing = [p.name for p in paths if not p.is_file()]
    if missing:
        return PublicLoad("missing", [f"Missing public data files in {d}: {missing}"])
    try:
        measures = load_processed_measures(d / MEASURES_FILE)
        facilities = pd.read_csv(d / FACILITIES_FILE, dtype=str, keep_default_na=False)
        require_provenance(facilities, Provenance.PUBLIC)
        manifest = json.loads((d / BUILD_MANIFEST_FILE).read_text())
    except (ValueError, PublicDataError, json.JSONDecodeError) as exc:
        return PublicLoad("invalid", [f"Public data failed verification: {exc}"])
    return PublicLoad("ok", [], PublicData(measures, facilities, manifest))


def period_text(start: str, end: str) -> str:
    s, e = pd.Timestamp(start), pd.Timestamp(end)
    if s.month == 1 and s.day == 1 and e.month == 12 and e.day == 31 and s.year == e.year:
        return f"CY {s.year}"
    return f"{s:%b %d, %Y} – {e:%b %d, %Y}"


def _display_value(row: pd.Series) -> str:
    if row["status"] == "suppressed":
        return "Suppressed (S)"
    if row["status"] != "reported":
        codes = f" [footnote {row['footnote_codes']}]" if row.get("footnote_codes") else ""
        return f"Not available{codes}"
    if row["unit"] == "category":
        return str(row["value_category"])
    if row["unit"] == "percent":
        return f"{row['value']:.0f}%"
    if row["unit"] == "minutes":
        return f"{row['value']:.0f} min"
    return f"{row['value']:,.0f}"


def cms_measure_rows(measures: pd.DataFrame) -> pd.DataFrame:
    """CMS measures for the matched hospitals, one row per hospital x measure."""
    cms = measures[measures["facility_id_type"] == "cms_ccn"].copy()
    cms["display"] = cms.apply(_display_value, axis=1)
    cms["period"] = [
        period_text(a, b) for a, b in zip(cms["period_start"], cms["period_end"], strict=True)
    ]
    cms["short_name"] = cms["measure_id"].map(lambda m: MEASURES[m].short_name)
    cms["order"] = cms["measure_id"].map({m: i for i, m in enumerate(CMS_MEASURE_ORDER)})
    return cms.sort_values(["order", "facility_name"]).reset_index(drop=True)


def cms_wide_table(measures: pd.DataFrame) -> pd.DataFrame:
    """Hospitals x CMS measures, preserving 'Not available' text and periods in headers."""
    rows = cms_measure_rows(measures)
    headers = {
        m: f"{m} · {MEASURES[m].short_name} ({p})"
        for m, p in rows.groupby("measure_id")["period"].first().items()
    }
    wide = rows.pivot_table(
        index=["facility_name", "facility_id"],
        columns="measure_id",
        values="display",
        aggfunc="first",
    )
    wide = wide[[m for m in CMS_MEASURE_ORDER if m in wide.columns]].rename(columns=headers)
    return wide.reset_index().rename(
        columns={"facility_name": "Hospital (CMS name)", "facility_id": "CMS CCN"}
    )


def reporting_periods(measures: pd.DataFrame) -> pd.DataFrame:
    """Reporting period of each measure, so mismatches are explicit."""
    cms = cms_measure_rows(measures)
    out = cms.groupby("measure_id").agg(period=("period", "first")).reset_index()
    out["measure"] = out["measure_id"].map(lambda m: MEASURES[m].short_name)
    out["order"] = out["measure_id"].map({m: i for i, m in enumerate(CMS_MEASURE_ORDER)})
    return out.sort_values("order")[["measure_id", "measure", "period"]].reset_index(drop=True)


def nys_rows(
    measures: pd.DataFrame, *, year: int, period_type: str = "calendar_year"
) -> pd.DataFrame:
    nys = measures[
        (measures["facility_id_type"] == "nys_pfi") & (measures["period_type"] == period_type)
    ]
    nys = nys[nys["period_start"].str.startswith(str(year))].copy()
    nys["display"] = nys.apply(_display_value, axis=1)
    nys["short_name"] = nys["measure_id"].map(lambda m: MEASURES[m].short_name)
    return nys.reset_index(drop=True)


def measure_definitions(measure_ids: list[str]) -> pd.DataFrame:
    rows = []
    for m in measure_ids:
        d = MEASURES[m]
        rows.append(
            {
                "Measure": m,
                "Name": d.short_name,
                "Unit": d.unit.value,
                "Definition": d.definition,
                "Population": d.population,
                "Does NOT measure": "; ".join(d.does_not_measure),
                "Source": d.definition_source,
            }
        )
    return pd.DataFrame(rows)


def crosswalk_view(facilities: pd.DataFrame) -> pd.DataFrame:
    cols = {
        "status": "Status",
        "cms_ccn": "CMS CCN",
        "cms_facility_name": "CMS name",
        "nys_pfi": "NYS PFI",
        "nys_facility_name": "NYS name",
        "match_method": "Match",
        "nys_sites_listing_this_as_main_site": "NYS sites listing it as main site",
        "note": "Note",
    }
    return facilities[list(cols)].rename(columns=cols)


# ---------------------------------------------------------------------------
# Fictional site and one live SIMULATED run
# ---------------------------------------------------------------------------


def load_site() -> SiteProfile:
    return load_site_profile(SITE_PATH)


def site_summary(site: SiteProfile) -> dict[str, pd.DataFrame]:
    shifts = [(s.name, f"{s.start_hour:02d}:00", s.duration_hours) for s in site.staffing.shifts]
    staffing = pd.DataFrame(
        [
            {
                "Shift": n,
                "Starts": start,
                "Hours": h,
                "Physicians": site.staffing.physicians_on_duty[n],
                "Physician slots (x concurrency)": site.staffing.physicians_on_duty[n]
                * site.operations.physician_concurrent_patients,
                "Triage nurses": site.operations.triage_nurses_on_duty[n],
                "Nurses on duty": site.staffing.nurses_on_duty[n],
            }
            for n, start, h in shifts
        ]
    )
    spaces = pd.DataFrame(
        [
            {"Space type": k.replace("_", " "), "Count": getattr(site.treatment_spaces, k)}
            for k in ("acute_beds", "resuscitation_bays", "fast_track_chairs", "hallway_spaces")
        ]
    )
    mix = site.patient_mix
    acuity = pd.DataFrame(
        [
            {
                "ESI": a,
                "Share of arrivals": mix.acuity_distribution[a],
                "P(lab)": mix.lab_probability_by_acuity[a],
                "P(imaging)": mix.imaging_probability_by_acuity[a],
                "P(admission)": mix.admission_probability_by_acuity[a],
            }
            for a in range(1, 6)
        ]
    )
    beds = site.operations.inpatient_bed_availability
    assumptions = pd.DataFrame(
        [
            {
                "Assumption": "Physician concurrency (patients per physician at once)",
                "Value": site.operations.physician_concurrent_patients,
                "Type": "Uncertain assumption",
            },
            {
                "Assumption": "Inpatient bed releases to the ED per day (simplified model)",
                "Value": beds.releases_per_day,
                "Type": "Uncertain assumption",
            },
            {
                "Assumption": "Expected arrivals on an average day",
                "Value": site.arrivals.expected_daily_arrivals,
                "Type": "Illustrative input",
            },
            {
                "Assumption": "Overall admission share (implied by acuity mix)",
                "Value": round(mix.implied_admission_fraction, 4),
                "Type": "Illustrative input",
            },
            {
                "Assumption": "ESI 1 bypasses triage",
                "Value": site.operations.esi1_bypasses_triage,
                "Type": "Modeling rule",
            },
        ]
    )
    assumptions["Value"] = assumptions["Value"].astype(str)
    return {"staffing": staffing, "spaces": spaces, "acuity": acuity, "assumptions": assumptions}


@dataclass(frozen=True)
class FlowSnapshot:
    """Everything the patient-flow page needs from one run: plain DataFrames only."""

    seed: int
    days: int
    report: ValidationReport
    tables: dict[str, pd.DataFrame] = field(default_factory=dict)  # empty when BLOCKED
    arrivals: pd.DataFrame | None = None  # SYNTHETIC generated patients
    boarding: pd.DataFrame | None = None  # SIMULATED per-patient boarding

    @property
    def blocked(self) -> bool:
        return self.report.status is GateStatus.BLOCKED


def run_flow(seed: int, days: int = 8, site: SiteProfile | None = None) -> FlowSnapshot:
    """Run ONE simulation, validate it, and release tables only if not BLOCKED."""
    if not 2 <= days <= MAX_FLOW_DAYS:
        raise ValueError(f"days must be between 2 and {MAX_FLOW_DAYS}")
    site = site or load_site()
    settings = SimulationSettings(start_date=START, arrival_days=days, warmup_days=1, seed=seed)
    result = run_simulation(site, settings)
    report = validate_run(result)
    if report.status is GateStatus.BLOCKED:
        return FlowSnapshot(seed, days, report)
    try:
        released = release_for_interpretation(result, report)
    except BlockedResultError:  # pragma: no cover - defensive; status checked above
        return FlowSnapshot(seed, days, report)
    arrivals = generate_patients(site, start=START, days=days, seed=seed)
    require_provenance(arrivals, Provenance.SYNTHETIC)
    return FlowSnapshot(seed, days, report, released.tables, arrivals, dm.boarding_values(result))


# ---------------------------------------------------------------------------
# Precomputed Milestone 6 results (SIMULATED)
# ---------------------------------------------------------------------------


def load_results(directory: Path | None = None) -> BundleLoad:
    return load_bundle(directory or results_dir())


def scenario_names() -> dict[str, str]:
    """scenario_id -> human-readable name, from the committed scenario files."""
    names = {"baseline": "Baseline"}
    for path in sorted(SCENARIOS_DIR.glob("*.yaml")):
        try:
            sc = load_scenario(path)
        except ConfigError:  # pragma: no cover - committed files are validated by tests
            continue
        names[sc.scenario_id] = sc.name
    return names


def comparison_rows(
    bundle: Bundle,
    *,
    scope: str = "main",
    metric: str | None = None,
    scenario_id: str | None = None,
) -> pd.DataFrame:
    df = bundle.comparisons[bundle.comparisons["scope"] == scope]
    if metric is not None:
        df = df[df["metric"] == metric]
    if scenario_id is not None:
        df = df[df["scenario_id"] == scenario_id]
    df = df.copy()
    names = scenario_names()
    df["scenario_name"] = df["scenario_id"].map(lambda s: names.get(s, s))
    df["baseline_name"] = df["baseline_id"].map(lambda s: names.get(s, s))
    return df.reset_index(drop=True)


def display_scale(unit: str) -> tuple[float, str]:
    """Fractions are shown as percentage points."""
    return (100.0, "percentage points") if unit == "fraction" else (1.0, unit)


def comparison_meta(
    bundle: Bundle, *, scope: str, scenario_id: str, baseline_id: str | None = None
) -> dict:
    for c in bundle.manifest["comparisons"]:
        if (
            c["scope"] == scope
            and c["scenario_id"] == scenario_id
            and (baseline_id is None or c["baseline_id"] == baseline_id)
        ):
            return c
    return {}


def comparison_checks(
    bundle: Bundle, *, scope: str, scenario_id: str, include_pass: bool = False
) -> pd.DataFrame:
    df = bundle.checks[
        (bundle.checks["scope"] == scope) & (bundle.checks["scenario_id"] == scenario_id)
    ]
    if not include_pass:
        df = df[df["status"] != "PASS"]
    return df[["check", "status", "detail"]].reset_index(drop=True)


STAGE_METRICS = {
    "stage_triage_wait_mean": "Waiting for triage",
    "stage_space_wait_mean": "Waiting for a space",
    "stage_provider_wait_mean": "Waiting for a physician",
    "stage_results_mean": "Lab/imaging results",
    "stage_boarding_mean": "Boarding (admitted)",
}


def stage_effects(bundle: Bundle, *, scope: str, scenario_id: str) -> pd.DataFrame:
    df = comparison_rows(bundle, scope=scope, scenario_id=scenario_id)
    df = df[df["metric"].isin(STAGE_METRICS)].copy()
    df["stage"] = df["metric"].map(STAGE_METRICS)
    df["order"] = df["metric"].map({m: i for i, m in enumerate(STAGE_METRICS)})
    return df.sort_values("order").reset_index(drop=True)


def moves_congestion(stages: pd.DataFrame) -> bool:
    d = set(stages["direction"])
    return "improves" in d and "worsens" in d


def sensitivity_grid(bundle: Bundle, *, intervention: str, metric: str) -> pd.DataFrame:
    if bundle.sensitivity is None:
        return pd.DataFrame()
    s = bundle.sensitivity
    return s[(s["intervention"] == intervention) & (s["metric"] == metric)].reset_index(drop=True)
