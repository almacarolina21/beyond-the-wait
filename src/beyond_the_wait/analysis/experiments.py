"""Multi-seed, paired what-if experiments on the FICTIONAL ED (all outputs SIMULATED).

How a comparison works
    1. An ARM is a site profile (baseline, or baseline + scenario).
    2. Each arm is simulated once per seed. The SAME seeds are used for every
       arm, so each scenario run is PAIRED with a baseline run that saw the
       same patients, task durations, and bed-release randomness (common
       random numbers; see simulation/randomness.py).
    3. Every replication goes through the Milestone 5 validation gate.
       BLOCKED replications are never measured; a comparison containing one is
       itself BLOCKED and reports no numbers. CAUTION reasons are carried
       forward and counted.
    4. For each metric, paired differences d_i = scenario_i - baseline_i are
       summarized with a paired t 95% confidence interval
       (analysis/statistics.py), plus a separately labeled prediction interval.
    5. Comparison-level checks add their own PASS/CAUTION/BLOCKED verdicts.

What a result means
    "Improves" means the 95% CI for the mean paired difference excludes zero
    in the better direction, INSIDE THE FICTIONAL MODEL under its stated
    assumptions. It is model-internal evidence: it says nothing about any real
    hospital and does not show that an intervention would be safe.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from beyond_the_wait.analysis.statistics import PairedEstimate, paired_estimate
from beyond_the_wait.analysis.validation import (
    SAFETY_LIMITATIONS,
    GateStatus,
    ReleasedResults,
    release_for_interpretation,
    validate_run,
)
from beyond_the_wait.config import Scenario, SiteProfile, apply_scenario, scenario_effects
from beyond_the_wait.simulation.engine import SimulationResult, SimulationSettings, run_simulation

Intent = Literal["baseline", "stress_test"]
Direction = Literal["improves", "worsens", "changes", "inconclusive", "no_change", "unavailable"]
MIN_SEEDS_FOR_ESTIMATE = 2
RECOMMENDED_SEEDS = 30
NOISE_RELATIVE_HALF_WIDTH = 0.25  # CI half-width above 25% of the baseline level = too noisy
SKEW_LIMIT = 2.0

MODEL_EVIDENCE_NOTE = (
    "Model-internal evidence only: confidence intervals describe the fictional model's "
    "behavior across random seeds under its stated assumptions. They are not evidence about "
    "any real hospital, and no intervention is shown to be clinically safe."
)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class ExperimentSettings(BaseModel):
    """Run settings shared by every arm of an experiment."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    start_date: date
    arrival_days: StrictInt = Field(8, ge=2, le=60)
    warmup_days: StrictInt = Field(1, ge=0)
    drain_hours: StrictInt = Field(72, ge=0, le=336)
    seeds: tuple[int, ...] = Field(min_length=MIN_SEEDS_FOR_ESTIMATE)
    workers: StrictInt = Field(1, ge=1, le=64)

    @field_validator("seeds")
    @classmethod
    def _unique_nonnegative(cls, seeds: tuple[int, ...]) -> tuple[int, ...]:
        if len(set(seeds)) != len(seeds):
            raise ValueError("seeds must be unique (each pair needs its own random draw)")
        if any(s < 0 for s in seeds):
            raise ValueError("seeds must be non-negative")
        return seeds

    @model_validator(mode="after")
    def _warmup(self) -> ExperimentSettings:
        if self.warmup_days >= self.arrival_days:
            raise ValueError("warmup_days must be smaller than arrival_days")
        return self

    def simulation_settings(self, seed: int) -> SimulationSettings:
        return SimulationSettings(
            start_date=self.start_date,
            arrival_days=self.arrival_days,
            warmup_days=self.warmup_days,
            drain_hours=self.drain_hours,
            seed=seed,
        )


# ---------------------------------------------------------------------------
# Metrics extracted from RELEASED results only
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MetricSpec:
    name: str
    label: str
    unit: str
    better: Literal["lower", "higher", "neutral"]
    fn: Callable[[SimulationResult, ReleasedResults], float]


def _done(r: SimulationResult) -> pd.DataFrame:
    p = r.patients
    return p[(p["cohort"] == "measured") & (p["status"] == "completed")]


def _q(series: pd.Series, q: float) -> float:
    s = series.dropna()
    return float(s.quantile(q)) if len(s) else float("nan")


def _mean(series: pd.Series) -> float:
    s = series.dropna()
    return float(s.mean()) if len(s) else float("nan")


def _disp(r: SimulationResult, d: str) -> pd.DataFrame:
    done = _done(r)
    return done[done["disposition"] == d]


def _resource(rel: ReleasedResults, name: str) -> float:
    rs = rel.tables["resource_summary"].set_index("resource")
    return float(rs.loc[name, "utilization"])


def _boarding(rel: ReleasedResults, column: str) -> float:
    bd = rel.tables["boarding_distribution"].set_index("group")
    return float(bd.loc["all", column])


METRICS: tuple[MetricSpec, ...] = (
    MetricSpec(
        "arrival_to_provider_median",
        "Arrival to provider, median",
        "min",
        "lower",
        lambda r, rel: _q(_done(r)["arrival_to_provider_min"], 0.5),
    ),
    MetricSpec(
        "arrival_to_provider_p90",
        "Arrival to provider, p90",
        "min",
        "lower",
        lambda r, rel: _q(_done(r)["arrival_to_provider_min"], 0.9),
    ),
    MetricSpec(
        "triage_wait_p90",
        "Waiting for triage, p90",
        "min",
        "lower",
        lambda r, rel: _q(_done(r)["triage_wait_min"], 0.9),
    ),
    MetricSpec(
        "provider_wait_p90",
        "Waiting for a physician, p90",
        "min",
        "lower",
        lambda r, rel: _q(_done(r)["provider_wait_min"], 0.9),
    ),
    MetricSpec(
        "los_median_discharged",
        "LOS discharged, median",
        "min",
        "lower",
        lambda r, rel: _q(_disp(r, "discharge")["los_min"], 0.5),
    ),
    MetricSpec(
        "los_p90_discharged",
        "LOS discharged, p90",
        "min",
        "lower",
        lambda r, rel: _q(_disp(r, "discharge")["los_min"], 0.9),
    ),
    MetricSpec(
        "los_median_admitted",
        "LOS admitted, median",
        "min",
        "lower",
        lambda r, rel: _q(_disp(r, "admit")["los_min"], 0.5),
    ),
    MetricSpec(
        "los_p90_admitted",
        "LOS admitted, p90",
        "min",
        "lower",
        lambda r, rel: _q(_disp(r, "admit")["los_min"], 0.9),
    ),
    MetricSpec(
        "los_median_lower_bound_all",
        "LOS all, median incl. unfinished (lower bound)",
        "min",
        "lower",
        lambda r, rel: float(rel.tables["censoring_summary"].iloc[0]["los_median_lower_bound_all"]),
    ),
    MetricSpec(
        "boarding_median",
        "Boarding, median",
        "min",
        "lower",
        lambda r, rel: _boarding(rel, "median"),
    ),
    MetricSpec(
        "boarding_p90", "Boarding, p90", "min", "lower", lambda r, rel: _boarding(rel, "p90")
    ),
    MetricSpec(
        "boarding_share_over_4h",
        "Boarding > 4 h, share of admitted",
        "fraction",
        "lower",
        lambda r, rel: _boarding(rel, "share_over_4h"),
    ),
    MetricSpec(
        "physician_utilization",
        "Physician-slot utilization",
        "fraction",
        "neutral",
        lambda r, rel: _resource(rel, "physician_slots"),
    ),
    MetricSpec(
        "triage_utilization",
        "Triage-nurse utilization",
        "fraction",
        "neutral",
        lambda r, rel: _resource(rel, "triage_nurses"),
    ),
    MetricSpec(
        "stage_triage_wait_mean",
        "Stage: waiting for triage (mean)",
        "min",
        "lower",
        lambda r, rel: _mean(_done(r)["triage_wait_min"]),
    ),
    MetricSpec(
        "stage_space_wait_mean",
        "Stage: waiting for a space (mean)",
        "min",
        "lower",
        lambda r, rel: _mean(_done(r)["space_wait_min"]),
    ),
    MetricSpec(
        "stage_provider_wait_mean",
        "Stage: waiting for a physician (mean)",
        "min",
        "lower",
        lambda r, rel: _mean(_done(r)["provider_wait_min"]),
    ),
    MetricSpec(
        "stage_results_mean",
        "Stage: lab/imaging results (mean)",
        "min",
        "lower",
        lambda r, rel: _mean(_done(r)["disposition_min"] - _done(r)["provider_end_min"]),
    ),
    MetricSpec(
        "stage_boarding_mean",
        "Stage: boarding, admitted (mean)",
        "min",
        "lower",
        lambda r, rel: _mean(_disp(r, "admit")["boarding_min"]),
    ),
    MetricSpec(
        "unfinished_measured",
        "Unfinished measured visits",
        "patients",
        "lower",
        lambda r, rel: float(
            ((r.patients["cohort"] == "measured") & (r.patients["status"] == "unfinished")).sum()
        ),
    ),
    MetricSpec(
        "measured_patients",
        "Measured patients",
        "patients",
        "neutral",
        lambda r, rel: float((r.patients["cohort"] == "measured").sum()),
    ),
)
METRIC_BY_NAME = {m.name: m for m in METRICS}
WAIT_STAGES = (
    "stage_triage_wait_mean",
    "stage_space_wait_mean",
    "stage_provider_wait_mean",
    "stage_boarding_mean",
)
COMPLETED_ONLY = (
    "los_median_discharged",
    "los_p90_discharged",
    "los_median_admitted",
    "los_p90_admitted",
    "arrival_to_provider_median",
    "arrival_to_provider_p90",
)


# ---------------------------------------------------------------------------
# Replications
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Arm:
    """One configuration to simulate: the baseline or a scenario applied to it."""

    arm_id: str
    site: SiteProfile
    kind: str = "baseline"
    intent: Intent = "baseline"
    effects: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def from_scenario(cls, base: Arm, scenario: Scenario, *, intent: Intent | None = None) -> Arm:
        return cls(
            arm_id=scenario.scenario_id,
            site=apply_scenario(base.site, scenario),
            kind=scenario.kind,
            intent=intent or ("stress_test" if scenario.kind == "stress_test" else base.intent),
            effects=scenario_effects(scenario),
        )


@dataclass(frozen=True)
class Replication:
    arm_id: str
    seed: int
    status: GateStatus
    overloaded: bool
    caution_checks: tuple[str, ...]
    blocked_reasons: tuple[str, ...]
    metrics: dict[str, float] | None  # None when BLOCKED: no numbers from blocked runs


def _replicate(args: tuple[Arm, ExperimentSettings, int]) -> Replication:
    arm, settings, seed = args
    result = run_simulation(arm.site, settings.simulation_settings(seed))
    report = validate_run(result, intent=arm.intent)
    caution = tuple(c.check for c in report.checks if c.status is GateStatus.CAUTION)
    if report.status is GateStatus.BLOCKED:
        return Replication(
            arm.arm_id, seed, report.status, report.overloaded, caution, tuple(report.reasons), None
        )
    released = release_for_interpretation(result, report)
    values = {m.name: m.fn(result, released) for m in METRICS}
    return Replication(arm.arm_id, seed, report.status, report.overloaded, caution, (), values)


def run_arms(arms: Sequence[Arm], settings: ExperimentSettings) -> dict[str, list[Replication]]:
    """Simulate and validate every arm on every seed (optionally in parallel)."""
    jobs = [(arm, settings, seed) for arm in arms for seed in settings.seeds]
    if settings.workers > 1:
        with ProcessPoolExecutor(max_workers=settings.workers) as pool:
            reps = list(pool.map(_replicate, jobs, chunksize=4))
    else:
        reps = [_replicate(job) for job in jobs]
    out: dict[str, list[Replication]] = {arm.arm_id: [] for arm in arms}
    for rep in reps:
        out[rep.arm_id].append(rep)
    return out


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ComparisonCheck:
    check: str
    status: GateStatus
    detail: str


@dataclass(frozen=True)
class MetricComparison:
    metric: str
    label: str
    unit: str
    better: str
    estimate: PairedEstimate | None
    n_missing: int
    direction: Direction


@dataclass(frozen=True)
class ComparisonReport:
    baseline_id: str
    scenario_id: str
    scenario_kind: str
    status: GateStatus
    checks: tuple[ComparisonCheck, ...]
    metrics: dict[str, MetricComparison]
    replication_status: dict[str, dict[str, int]]
    carried_cautions: dict[str, int]
    overloaded_replications: dict[str, int]
    effects: dict[str, list[str]]
    limitations: tuple[str, ...] = SAFETY_LIMITATIONS
    evidence_note: str = MODEL_EVIDENCE_NOTE


def _direction(spec: MetricSpec, est: PairedEstimate) -> Direction:
    if est.sd_difference == 0 and est.mean_difference == 0:
        return "no_change"
    if not est.ci_excludes_zero:
        return "inconclusive"
    if spec.better == "neutral":  # e.g., utilization: a change is neither good nor bad by itself
        return "changes"
    lower = est.mean_difference < 0
    return "improves" if lower == (spec.better == "lower") else "worsens"


def compare(
    baseline: Arm,
    scenario: Arm,
    replications: dict[str, list[Replication]],
) -> ComparisonReport:
    """Paired comparison of `scenario` against `baseline` with gate integration."""
    base = {r.seed: r for r in replications[baseline.arm_id]}
    scen = {r.seed: r for r in replications[scenario.arm_id]}
    checks: list[ComparisonCheck] = []
    status_counts = {
        arm: dict(Counter(r.status.value for r in reps))
        for arm, reps in ((baseline.arm_id, base.values()), (scenario.arm_id, scen.values()))
    }
    overloaded = {
        baseline.arm_id: sum(r.overloaded for r in base.values()),
        scenario.arm_id: sum(r.overloaded for r in scen.values()),
    }
    carried = Counter(c for reps in (base, scen) for r in reps.values() for c in r.caution_checks)

    blocked = [r for reps in (base, scen) for r in reps.values() if r.status is GateStatus.BLOCKED]
    if blocked:
        checks.append(
            ComparisonCheck(
                "blocked_replications",
                GateStatus.BLOCKED,
                f"{len(blocked)} replication(s) BLOCKED by the validation gate "
                f"(e.g., {blocked[0].arm_id} seed {blocked[0].seed}: "
                f"{blocked[0].blocked_reasons[:1]}). "
                "The comparison is invalid and reports no numbers.",
            )
        )
    if set(base) != set(scen):
        checks.append(
            ComparisonCheck(
                "seed_pairing",
                GateStatus.BLOCKED,
                f"seeds differ between arms (only in baseline: {sorted(set(base) - set(scen))}, "
                f"only in scenario: {sorted(set(scen) - set(base))}); pairs cannot be formed.",
            )
        )
    if any(c.status is GateStatus.BLOCKED for c in checks):
        return ComparisonReport(
            baseline.arm_id,
            scenario.arm_id,
            scenario.kind,
            GateStatus.BLOCKED,
            tuple(checks),
            {},
            status_counts,
            dict(carried),
            overloaded,
            scenario.effects,
        )

    seeds = sorted(base)
    n = len(seeds)
    checks.append(
        ComparisonCheck(
            "seed_coverage",
            GateStatus.PASS if n >= RECOMMENDED_SEEDS else GateStatus.CAUTION,
            f"{n} paired seeds"
            + (
                ""
                if n >= RECOMMENDED_SEEDS
                else f" (fewer than the recommended {RECOMMENDED_SEEDS}; intervals are wide)"
            ),
        )
    )

    metrics: dict[str, MetricComparison] = {}
    missing, noisy, skewed = {}, [], []
    for spec in METRICS:
        pairs = [(base[s].metrics[spec.name], scen[s].metrics[spec.name]) for s in seeds]
        ok = [(b, s) for b, s in pairs if math.isfinite(b) and math.isfinite(s)]
        n_missing = n - len(ok)
        if n_missing:
            missing[spec.name] = n_missing
        if len(ok) < MIN_SEEDS_FOR_ESTIMATE:
            metrics[spec.name] = MetricComparison(
                spec.name, spec.label, spec.unit, spec.better, None, n_missing, "unavailable"
            )
            continue
        est = paired_estimate([b for b, _ in ok], [s for _, s in ok])
        if abs(est.baseline_mean) > 0 and est.ci_half_width > NOISE_RELATIVE_HALF_WIDTH * abs(
            est.baseline_mean
        ):
            noisy.append(spec.name)
        if abs(est.skewness) > SKEW_LIMIT:
            skewed.append(spec.name)
        metrics[spec.name] = MetricComparison(
            spec.name, spec.label, spec.unit, spec.better, est, n_missing, _direction(spec, est)
        )

    checks.append(
        ComparisonCheck(
            "missing_data",
            GateStatus.CAUTION if missing else GateStatus.PASS,
            (
                "Pairs with an undefined metric (e.g., no admitted patients) were excluded "
                "and counted: " + ", ".join(f"{k}: {v}" for k, v in missing.items())
            )
            if missing
            else "No missing metric values.",
        )
    )
    checks.append(
        ComparisonCheck(
            "excessive_variability",
            GateStatus.CAUTION if noisy else GateStatus.PASS,
            (
                "CI half-width exceeds 25% of the baseline level for: "
                + ", ".join(noisy)
                + ". Estimates are too imprecise to support conclusions."
            )
            if noisy
            else "Intervals are reasonably narrow relative to baseline levels.",
        )
    )
    checks.append(
        ComparisonCheck(
            "skewed_differences",
            GateStatus.CAUTION if skewed else GateStatus.PASS,
            (
                "Paired differences are strongly skewed (|skew| > 2) for: "
                + ", ".join(skewed)
                + "; the normal approximation behind the t interval is less reliable."
            )
            if skewed
            else "No strongly skewed paired differences.",
        )
    )
    unfinished = [
        r
        for reps in (base, scen)
        for r in reps.values()
        if r.metrics and r.metrics["unfinished_measured"] > 0
    ]
    checks.append(
        ComparisonCheck(
            "censoring",
            GateStatus.CAUTION if unfinished else GateStatus.PASS,
            (
                f"{len(unfinished)} replication(s) had unfinished measured visits. "
                "Completed-only metrics "
                f"({', '.join(COMPLETED_ONLY)}) are biased low there; prefer "
                "los_median_lower_bound_all "
                "and unfinished_measured."
            )
            if unfinished
            else "No unfinished measured visits in any replication.",
        )
    )
    over = sum(overloaded.values())
    checks.append(
        ComparisonCheck(
            "overload_regime",
            GateStatus.CAUTION if over else GateStatus.PASS,
            (
                f"Overloaded replications: {overloaded}. Effects here describe a STRESS-TEST "
                "regime "
                "(queues growing with run length), not ordinary operation."
            )
            if over
            else "No replication was overloaded.",
        )
    )
    checks.append(
        ComparisonCheck(
            "carried_cautions",
            GateStatus.CAUTION if carried else GateStatus.PASS,
            "Replication-level CAUTION checks (count across both arms): "
            + ", ".join(f"{k} x{v}" for k, v in sorted(carried.items())),
        )
    )
    claims = []
    if scenario.kind in ("sensitivity", "stress_test"):
        claims.append(
            f"this is a {scenario.kind} scenario (changes assumptions), so differences "
            "are not intervention effects"
        )
    if (
        scenario.effects
        and not scenario.effects.get("simulated")
        and not scenario.effects.get("assumptions")
    ):
        claims.append(
            "the scenario only changes inputs with no simulation effect "
            f"({scenario.effects.get('not_simulated')})"
        )
    if all(m.direction in ("no_change", "unavailable") for m in metrics.values()):
        claims.append(
            "no metric changed at all: the changed resource may not be a binding constraint"
        )
    claims.append("evidence is internal to the fictional model")
    checks.append(
        ComparisonCheck("unsupported_conclusions", GateStatus.CAUTION, "; ".join(claims) + ".")
    )

    status = max(
        (c.status for c in checks), key=lambda s: {"PASS": 0, "CAUTION": 1, "BLOCKED": 2}[s]
    )
    return ComparisonReport(
        baseline.arm_id,
        scenario.arm_id,
        scenario.kind,
        status,
        tuple(checks),
        metrics,
        status_counts,
        dict(carried),
        overloaded,
        scenario.effects,
    )


# ---------------------------------------------------------------------------
# Interpretation helpers (structured, model-internal)
# ---------------------------------------------------------------------------


def congestion_shift(report: ComparisonReport) -> dict[str, list[str]]:
    """Which waiting stages got CI-supported better or worse.

    `moves_congestion` is true when at least one stage improves while another
    worsens: the intervention relocated waiting rather than only removing it.
    """
    better = [
        m for m in WAIT_STAGES if m in report.metrics and report.metrics[m].direction == "improves"
    ]
    worse = [
        m for m in WAIT_STAGES if m in report.metrics and report.metrics[m].direction == "worsens"
    ]
    return {"improves": better, "worsens": worse, "moves_congestion": [str(bool(better and worse))]}


def binding_constraint_evidence(report: ComparisonReport, outcome: str) -> str:
    """Model-internal statement about whether relieving the scenario's constraint
    improved `outcome`. Never names a cause for a real hospital."""
    if report.status is GateStatus.BLOCKED:
        return "No conclusion: the comparison is BLOCKED."
    mc = report.metrics.get(outcome)
    if mc is None or mc.estimate is None:
        return f"No conclusion: {outcome} is unavailable."
    e = mc.estimate
    changed = ", ".join(report.effects.get("simulated", [])) or "the scenario's changes"
    ci = (
        f"mean paired difference {e.mean_difference:+.1f} {mc.unit}, "
        f"95% CI {e.ci_low:+.1f} to {e.ci_high:+.1f}, n={e.n}"
    )
    if mc.direction == "improves":
        return (
            f"Within the fictional model, changing {changed} improved {mc.label} ({ci}): "
            "evidence that this constraint was binding for this outcome in the model."
        )
    if mc.direction == "worsens":
        return f"Within the fictional model, changing {changed} worsened {mc.label} ({ci})."
    return (
        f"Within the fictional model, no supported effect of changing {changed} on "
        f"{mc.label} ({ci}); a binding-constraint claim is not supported."
    )


def describe(report: ComparisonReport, metric: str) -> str:
    """One plain-language sentence, always scoped to the fictional model."""
    if report.status is GateStatus.BLOCKED:
        return f"{report.scenario_id}: comparison BLOCKED; no result is reported."
    mc = report.metrics[metric]
    if mc.estimate is None:
        return f"Within the fictional model, {mc.label} is unavailable for {report.scenario_id}."
    e = mc.estimate
    verdict = {
        "improves": "an improvement",
        "worsens": "a worsening",
        "inconclusive": "no conclusion (the interval includes zero)",
        "changes": "a change (neither better nor worse by itself)",
        "no_change": "no change",
        "unavailable": "unavailable",
    }[mc.direction]
    return (
        f"Within the fictional model, {report.scenario_id} changed {mc.label} by "
        f"{e.mean_difference:+.2f} {mc.unit} on average (95% CI {e.ci_low:+.2f} to "
        f"{e.ci_high:+.2f}; {e.n} paired seeds): {verdict}. A single future run could differ "
        f"by {e.prediction_low:+.2f} to {e.prediction_high:+.2f} (95% prediction interval)."
    )


def comparison_table(reports: Sequence[ComparisonReport], metrics: Sequence[str]) -> pd.DataFrame:
    """Long table of estimates for display or export (labeled SIMULATED)."""
    rows = []
    for rep in reports:
        for name in metrics:
            mc = rep.metrics.get(name)
            e = mc.estimate if mc else None
            rows.append(
                {
                    "scenario": rep.scenario_id,
                    "kind": rep.scenario_kind,
                    "metric": name,
                    "unit": mc.unit if mc else "",
                    "n_pairs": e.n if e else 0,
                    "baseline_mean": e.baseline_mean if e else np.nan,
                    "scenario_mean": e.scenario_mean if e else np.nan,
                    "mean_difference": e.mean_difference if e else np.nan,
                    "ci95_low": e.ci_low if e else np.nan,
                    "ci95_high": e.ci_high if e else np.nan,
                    "direction": mc.direction if mc else "unavailable",
                    "comparison_status": rep.status.value,
                    "provenance": "SIMULATED",
                }
            )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Sensitivity analysis
# ---------------------------------------------------------------------------


def assumption_variant(base: SiteProfile, *, concurrency: int, releases_per_day: float) -> Arm:
    """The baseline under a different (hypothetical) assumption setting."""
    current = base.operations.inpatient_bed_availability.releases_per_day
    changes: dict[str, object] = {}
    if concurrency != base.operations.physician_concurrent_patients:
        changes["physician_concurrency_override"] = concurrency
    if releases_per_day != current:
        changes["bed_release_change_per_day"] = releases_per_day - current
    arm_id = f"assume_c{concurrency}_r{releases_per_day:g}"
    if not changes:
        return Arm(arm_id, base, kind="baseline")
    sc = Scenario.model_validate(
        {
            "schema_version": 2,
            "scenario_id": arm_id,
            "kind": "sensitivity",
            "name": f"Assumption: concurrency {concurrency}, {releases_per_day:g} bed releases/day",
            "description": "Hypothetical assumption setting for sensitivity analysis only.",
            "applies_to_site": base.site.site_id,
            **changes,
        }
    )
    return Arm(arm_id, apply_scenario(base, sc), kind="sensitivity", effects=scenario_effects(sc))


def sensitivity_analysis(
    base: SiteProfile,
    interventions: Sequence[Scenario],
    settings: ExperimentSettings,
    *,
    concurrency_values: Sequence[int] = (1, 2, 3),
    release_values: Sequence[float] = (38.0, 42.0, 46.0),
    metrics: Sequence[str] = (
        "arrival_to_provider_p90",
        "los_median_lower_bound_all",
        "boarding_p90",
    ),
) -> pd.DataFrame:
    """Re-run each intervention comparison under every assumption setting.

    Concurrency 1 makes the fictional ED overloaded; those comparisons are
    analyzed as stress tests (intent='stress_test') and labeled as such.
    """
    arms: list[Arm] = []
    pairs: list[tuple[Arm, Arm, int, float]] = []
    for c in concurrency_values:
        for rel in release_values:
            variant = assumption_variant(base, concurrency=c, releases_per_day=rel)
            intent: Intent = "stress_test" if c == 1 else "baseline"
            variant = Arm(variant.arm_id, variant.site, variant.kind, intent, variant.effects)
            arms.append(variant)
            for sc in interventions:
                scen = Arm.from_scenario(variant, sc, intent=intent)
                scen = Arm(
                    f"{variant.arm_id}__{sc.scenario_id}",
                    scen.site,
                    scen.kind,
                    intent,
                    scen.effects,
                )
                arms.append(scen)
                pairs.append((variant, scen, c, rel))
    reps = run_arms(arms, settings)
    rows = []
    for variant, scen, c, rel in pairs:
        rep = compare(variant, scen, reps)
        for name in metrics:
            mc = rep.metrics.get(name)
            e = mc.estimate if mc else None
            rows.append(
                {
                    "concurrency": c,
                    "releases_per_day": rel,
                    "intervention": scen.arm_id.split("__", 1)[1],
                    "metric": name,
                    "mean_difference": e.mean_difference if e else np.nan,
                    "ci95_low": e.ci_low if e else np.nan,
                    "ci95_high": e.ci_high if e else np.nan,
                    "direction": mc.direction if mc else "unavailable",
                    "regime": "stress_test"
                    if (c == 1 or sum(rep.overloaded_replications.values()))
                    else "operating",
                    "comparison_status": rep.status.value,
                    "provenance": "SIMULATED",
                }
            )
    return pd.DataFrame(rows)


def stability(sensitivity: pd.DataFrame, *, regime: str | None = "operating") -> pd.DataFrame:
    """Is each intervention's effect on each metric stable across assumptions?

    stable        same CI-supported direction everywhere
    unstable      supported in some settings, inconclusive in others
    reverses      improves under some assumptions and worsens under others
    no_support    never CI-supported
    """
    df = sensitivity if regime is None else sensitivity[sensitivity["regime"] == regime]
    rows = []
    for (intervention, metric), g in df.groupby(["intervention", "metric"], sort=True):
        dirs = set(g["direction"])
        if {"improves", "worsens"} <= dirs:
            verdict = "reverses"
        elif dirs <= {"improves"} or dirs <= {"worsens"}:
            verdict = f"stable_{next(iter(dirs))}"
        elif dirs & {"improves", "worsens"}:
            verdict = "unstable"
        else:
            verdict = "no_support"
        rows.append(
            {
                "intervention": intervention,
                "metric": metric,
                "settings": len(g),
                "directions": dict(Counter(g["direction"])),
                "verdict": verdict,
            }
        )
    return pd.DataFrame(rows)
