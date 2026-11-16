"""Multi-seed what-if demonstration (SIMULATED, fictional ED, model-internal evidence).

    python -m beyond_the_wait.analysis.whatif_demo --seeds 30 --sensitivity-seeds 20 --workers 8

Writes CSVs for Milestone 7 to data/synthetic/experiments/ (git-ignored).
"""

from __future__ import annotations

import argparse
import sys
import textwrap
from datetime import date
from pathlib import Path

import pandas as pd

from beyond_the_wait.analysis.experiments import (
    MODEL_EVIDENCE_NOTE,
    Arm,
    ComparisonReport,
    ExperimentSettings,
    binding_constraint_evidence,
    compare,
    comparison_table,
    congestion_shift,
    run_arms,
    sensitivity_analysis,
    stability,
)
from beyond_the_wait.analysis.validation import SAFETY_LIMITATIONS, GateStatus
from beyond_the_wait.config import ConfigError, Scenario, load_scenario, load_site_profile

REPO_ROOT = Path(__file__).resolve().parents[3]
SITE = REPO_ROOT / "config" / "sites" / "fictional_bronx_community_ed.yaml"
SCENARIOS = REPO_ROOT / "config" / "scenarios"
OUT = REPO_ROOT / "data" / "synthetic" / "experiments"

INTERVENTIONS = (
    "add_evening_physician",
    "add_night_physician",
    "add_night_triage_nurse",
    "increase_bed_releases",
    "faster_diagnostics",
)
ARCHETYPES = {  # condition -> intervention that relieves it, and the outcome to judge it by
    "archetype_boarding_heavy": ("increase_bed_releases", "los_median_admitted"),
    "archetype_physician_mismatch": (
        "rebalance_physician_day_to_evening",
        "arrival_to_provider_p90",
    ),
    "archetype_triage_mismatch": ("add_day_triage_nurse", "arrival_to_provider_p90"),
    "archetype_diagnostic_delay": ("faster_diagnostics", "los_median_discharged"),
}
MAIN_METRICS = (
    "arrival_to_provider_median",
    "arrival_to_provider_p90",
    "triage_wait_p90",
    "provider_wait_p90",
    "los_median_discharged",
    "los_p90_discharged",
    "los_median_admitted",
    "los_p90_admitted",
    "boarding_median",
    "boarding_p90",
    "boarding_share_over_4h",
    "physician_utilization",
    "triage_utilization",
)
KEY_OUTCOMES = (
    "arrival_to_provider_p90",
    "los_median_discharged",
    "los_median_admitted",
    "los_median_lower_bound_all",
)
SYMBOL = {
    "improves": "better",
    "worsens": "WORSE",
    "changes": "changed",
    "inconclusive": "n.s.",
    "no_change": "same",
    "unavailable": "n/a",
}


def _scenario(name: str) -> Scenario:
    return load_scenario(SCENARIOS / f"{name}.yaml")


def _wrap(text: str, indent: str = "    ") -> str:
    return textwrap.fill(text, width=104, initial_indent=indent, subsequent_indent=indent)


def _print_comparison(rep: ComparisonReport, metrics: tuple[str, ...]) -> None:
    eff = ", ".join(rep.effects.get("simulated", []) + rep.effects.get("assumptions", []))
    print(f"\n  {rep.scenario_id}  [{rep.scenario_kind}: {eff}]  comparison status: {rep.status}")
    if rep.status is GateStatus.BLOCKED:
        for c in rep.checks:
            print(_wrap(f"[{c.status}] {c.check}: {c.detail}"))
        return
    print(
        f"    {'metric':42} {'baseline':>9} {'scenario':>9} {'diff':>8}   "
        f"{'95% CI of mean diff':>20}  verdict"
    )
    for name in metrics:
        mc = rep.metrics[name]
        e = mc.estimate
        if e is None:
            print(f"    {mc.label:42} {'n/a':>9}")
            continue
        scale, fmt = (100, "{:8.1f}%") if mc.unit == "fraction" else (1, "{:9.1f}")
        b, s, d = (
            fmt.format(v * scale) for v in (e.baseline_mean, e.scenario_mean, e.mean_difference)
        )
        ci = f"[{e.ci_low * scale:+7.1f}, {e.ci_high * scale:+7.1f}]"
        print(f"    {mc.label:42} {b:>9} {s:>9} {d:>8}   {ci:>20}  {SYMBOL[mc.direction]}")
    flags = [
        c for c in rep.checks if c.status is not GateStatus.PASS and c.check != "carried_cautions"
    ]
    print("    replication gate status:", rep.replication_status)
    print(
        "    carried CAUTION checks:",
        ", ".join(f"{k} x{v}" for k, v in sorted(rep.carried_cautions.items())),
    )
    for c in flags:
        print(_wrap(f"[{c.status}] {c.check}: {c.detail}", "      "))
    shift = congestion_shift(rep)
    if shift["moves_congestion"] == ["True"]:
        print(
            _wrap(f"CONGESTION MOVES: better {shift['improves']}; worse {shift['worsens']}", "    ")
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Multi-seed what-if analysis (fictional ED).")
    parser.add_argument("--seeds", type=int, default=30)
    parser.add_argument("--sensitivity-seeds", type=int, default=20)
    parser.add_argument("--days", type=int, default=8)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--skip-archetypes", action="store_true")
    parser.add_argument("--skip-sensitivity", action="store_true")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    try:
        site = load_site_profile(SITE)
        settings = ExperimentSettings(
            start_date=date(2026, 11, 16),
            arrival_days=args.days,
            seeds=tuple(range(1, args.seeds + 1)),
            workers=args.workers,
        )
    except (ConfigError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    args.out.mkdir(parents=True, exist_ok=True)

    bar = "=" * 104
    print(bar)
    print(
        " WHAT-IF ANALYSIS -- SIMULATED, fictional ED, hypothetical interventions, "
        "model-internal evidence"
    )
    print(
        f" {args.seeds} paired seeds x {args.days} days (1 warm-up day) per arm; every "
        "replication validated"
    )
    print(bar)
    print(_wrap(MODEL_EVIDENCE_NOTE, " "))

    # --- 1. Main comparison ---------------------------------------------------------------
    base = Arm("baseline", site)
    arms = [base] + [Arm.from_scenario(base, _scenario(n)) for n in INTERVENTIONS]
    reps = run_arms(arms, settings)
    reports = [compare(base, arm, reps) for arm in arms[1:]]
    print(
        "\n1) BASELINE vs INTERVENTIONS (mean over seeds; diff = scenario - baseline; n.s. = "
        "interval includes 0)"
    )
    for rep in reports:
        _print_comparison(rep, MAIN_METRICS)
    comparison_table(reports, MAIN_METRICS + KEY_OUTCOMES[-1:]).to_csv(
        args.out / "main_comparison.csv", index=False
    )

    # --- 2. Archetypes ---------------------------------------------------------------------
    if not args.skip_archetypes:
        print(
            "\n2) FICTIONAL ARCHETYPES: does relieving the suspected constraint improve the "
            "outcome?"
        )
        arch_rows = []
        for cond, (fix, outcome) in ARCHETYPES.items():
            cond_arm = Arm.from_scenario(base, _scenario(cond))
            cond_arm = Arm(cond, cond_arm.site, "archetype", "baseline", cond_arm.effects)
            fix_arm = Arm.from_scenario(cond_arm, _scenario(fix))
            r = run_arms([base, cond_arm, fix_arm], settings)
            harm = compare(base, cond_arm, r)
            relief = compare(cond_arm, fix_arm, r)
            stages = {
                m: relief.metrics[m].estimate.baseline_mean
                for m in (
                    "stage_triage_wait_mean",
                    "stage_provider_wait_mean",
                    "stage_results_mean",
                    "stage_boarding_mean",
                )
                if relief.metrics[m].estimate
            }
            largest = max(stages, key=stages.get)
            print(f"\n  {cond} (status {harm.status}) -> relief: {fix} (status {relief.status})")
            h = harm.metrics[outcome].estimate
            print(
                _wrap(
                    f"condition vs baseline, {harm.metrics[outcome].label}: "
                    f"{h.mean_difference:+.1f} min "
                    f"[{h.ci_low:+.1f}, {h.ci_high:+.1f}]"
                )
            )
            print(_wrap(binding_constraint_evidence(relief, outcome)))
            print(
                _wrap(
                    f"largest mean stage under the condition: {largest} ({stages[largest]:.0f} "
                    "min). "
                    "Largest is not the same as binding; only the relief test above supports "
                    "a claim."
                )
            )
            shift = congestion_shift(relief)
            if shift["moves_congestion"] == ["True"]:
                print(
                    _wrap(f"CONGESTION MOVES: better {shift['improves']}; worse {shift['worsens']}")
                )
            arch_rows.append(
                {
                    "archetype": cond,
                    "relief": fix,
                    "outcome": outcome,
                    "condition_effect": h.mean_difference,
                    "relief_effect": relief.metrics[outcome].estimate.mean_difference,
                    "relief_ci_low": relief.metrics[outcome].estimate.ci_low,
                    "relief_ci_high": relief.metrics[outcome].estimate.ci_high,
                    "relief_direction": relief.metrics[outcome].direction,
                    "largest_stage": largest,
                    "provenance": "SIMULATED",
                }
            )
        pd.DataFrame(arch_rows).to_csv(args.out / "archetypes.csv", index=False)

    # --- 3. Sensitivity ----------------------------------------------------------------------
    if not args.skip_sensitivity:
        print(
            f"\n3) SENSITIVITY to fictional assumptions ({args.sensitivity_seeds} paired seeds "
            "per setting):"
        )
        print(
            "   physician concurrency in {1, 2, 3} x bed releases/day in {38, 42, 46}; "
            "concurrency 1 = stress test"
        )
        sens_settings = settings.model_copy(
            update={"seeds": tuple(range(1, args.sensitivity_seeds + 1))}
        )
        chosen = [
            _scenario(n)
            for n in ("add_night_physician", "increase_bed_releases", "faster_diagnostics")
        ]
        sens = sensitivity_analysis(site, chosen, sens_settings)
        sens.to_csv(args.out / "sensitivity.csv", index=False)
        pivot = sens.pivot_table(
            index=["intervention", "metric"],
            columns=["concurrency", "releases_per_day"],
            values="direction",
            aggfunc="first",
        ).map(lambda d: SYMBOL[d])
        print(pivot.to_string())
        stab = stability(sens)
        print("\n   Stability across OPERATING settings (concurrency 2-3; stress tests excluded):")
        for row in stab.itertuples():
            flag = "  <-- FLAG" if row.verdict in ("unstable", "reverses") else ""
            print(
                f"   {row.intervention:24} {row.metric:28} {row.verdict:18} {row.directions}{flag}"
            )
        stab.to_csv(args.out / "stability.csv", index=False)

    # --- 4. Summary ---------------------------------------------------------------------------
    print("\n4) INSIDE THE FICTIONAL MODEL (not a recommendation; see limitations):")
    for rep in reports:
        better = [
            rep.metrics[m].label for m in KEY_OUTCOMES if rep.metrics[m].direction == "improves"
        ]
        worse = [
            rep.metrics[m].label
            for m in MAIN_METRICS
            if rep.metrics[m].direction == "worsens" and rep.metrics[m].better != "neutral"
        ]
        text = (
            f"{rep.scenario_id}: supported improvements in {better or 'no key outcome'}; "
            f"supported worsening in {worse or 'nothing measured'}."
        )
        print(textwrap.fill(text, width=104, initial_indent="  - ", subsequent_indent="    "))
    print("\nLimitations:")
    for item in SAFETY_LIMITATIONS:
        print(textwrap.fill(item, width=104, initial_indent="  - ", subsequent_indent="    "))
    print(f"\nCSV outputs for Milestone 7: {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
