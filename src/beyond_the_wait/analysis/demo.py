"""Validated analysis of one simulation run (SIMULATED, fictional ED).

python -m beyond_the_wait.analysis.demo --seed 42
python -m beyond_the_wait.analysis.demo --seed 42 --gate-examples
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
import textwrap
from datetime import date
from pathlib import Path

import pandas as pd

from beyond_the_wait.analysis.validation import (
    BlockedResultError,
    GateStatus,
    ReleasedResults,
    release_for_interpretation,
    validate_run,
)
from beyond_the_wait.config import ConfigError, SiteProfile, load_site_profile
from beyond_the_wait.simulation.engine import SimulationSettings, run_simulation

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SITE = REPO_ROOT / "config" / "sites" / "fictional_bronx_community_ed.yaml"


def _fmt(df: pd.DataFrame) -> str:
    # Two decimals: shares and utilization are fractions (0.86 must not print as 0.9).
    return df.to_string(index=False, float_format=lambda v: f"{v:8.2f}", na_rep="       -")


def _wrap(text: str, indent: str = "      ") -> str:
    return textwrap.fill(text, width=100, initial_indent=indent, subsequent_indent=indent)


def _print_report(report) -> None:  # noqa: ANN001
    print(
        f"\nVALIDATION STATUS: {report.status}  (intent: {report.run_intent}; "
        f"overloaded: {report.overloaded})"
    )
    for c in report.checks:
        print(f"  [{c.status:7}] {c.category:14} {c.check}")
        if c.status is not GateStatus.PASS:
            print(_wrap(c.detail))


def _print_tables(released: ReleasedResults) -> None:
    t = released.tables
    am = t["acuity_metrics"]
    cols = ["acuity_esi", "patients", "median", "p90", "p95", "max", "over_4h", "over_24h"]
    for measure, title in (
        ("arrival_to_provider_min", "Arrival to provider"),
        ("los_min", "ED length of stay"),
    ):
        print(f"\n{title} by ESI (completed visits, minutes):")
        print(_fmt(am[(am["disposition"] == "all") & (am["measure"] == measure)][cols]))
    print("\nED length of stay by disposition (minutes):")
    los = am[
        (am["acuity_esi"] == "all") & (am["measure"] == "los_min") & (am["disposition"] != "all")
    ]
    print(_fmt(los[["disposition", "patients", "median", "p90", "p95", "max"]]))

    cs = t["censoring_summary"].iloc[0]
    print(
        f"\nCensoring: {cs['completed']} completed, {cs['unfinished']} unfinished. Median LOS "
        f"completed-only {cs['los_median_completed_only']:.1f} vs lower bound incl. unfinished "
        f"{cs['los_median_lower_bound_all']:.1f} min."
    )

    print("\nStaff utilization by shift (measured window):")
    su = t["shift_utilization"]
    print(
        _fmt(
            su[
                [
                    "resource",
                    "shift",
                    "people_on_duty",
                    "utilization",
                    "share_time_at_or_above_90pct",
                    "patients_per_staffed_hour",
                ]
            ]
        )
    )

    print("\nBoarding distribution, admitted patients (minutes; shares are of admitted patients):")
    bd = t["boarding_distribution"]
    print(
        _fmt(
            bd[
                [
                    "group",
                    "admitted_patients",
                    "median",
                    "p90",
                    "max",
                    "share_over_2h",
                    "share_over_4h",
                    "share_over_8h",
                ]
            ]
        )
    )

    print("\nExtreme waits (all ESI levels; unfinished visits counted at time so far):")
    ew = t["extreme_waits"]
    print(
        _fmt(
            ew[ew["acuity_esi"] == "all"][
                [
                    "measure",
                    "threshold_min",
                    "patients_over",
                    "denominator",
                    "share_over",
                    "max_min",
                ]
            ]
        )
    )

    print(
        "\nWhere simulated time goes (mean minutes per stage;"
        " DESCRIPTIVE, one run, not a bottleneck claim):"
    )
    sd = t["stage_decomposition"]
    print(_fmt(sd[["disposition", "stage", "mean_min", "share_of_mean_los"]]))


def _print_findings(released: ReleasedResults) -> None:
    t = released.tables
    print("\nDO THE MILESTONE 4 FINDINGS SURVIVE? (one seed; suggestive, not established)")
    bd = t["boarding_distribution"].set_index("group")
    night = bd.loc["decision during night shift"]
    other = bd.drop(index=["all", "decision during night shift"])
    print(
        f"  Boarding: admission decided on the night shift -> median {night['median']:.0f} min "
        f"(n={int(night['admitted_patients'])}); other shifts -> medians "
        + ", ".join(
            f"{g.replace('decision during ', '')}: {r['median']:.0f}" for g, r in other.iterrows()
        )
        + f" min. Share of all admitted boarding >4 h: {bd.loc['all', 'share_over_4h']:.0%}."
    )
    hp = t["hourly_profile"]
    by_queue = hp.sort_values("physician_queue_mean", ascending=False).head(4)
    by_util = hp.sort_values("physician_utilization", ascending=False).head(4)
    print("  Physician pressure by clock hour (averages over measured days):")
    print(
        "    highest mean physician queue: "
        + ", ".join(
            f"{int(r.hour):02d}:00 ({r.physician_queue_mean:.2f})" for r in by_queue.itertuples()
        )
    )
    print(
        "    highest physician utilization: "
        + ", ".join(
            f"{int(r.hour):02d}:00 ({r.physician_utilization:.2f})" for r in by_util.itertuples()
        )
    )
    h23 = hp.set_index("hour").loc[23]
    print(
        f"    23:00 -> utilization {h23['physician_utilization']:.2f}, queue "
        f"{h23['physician_queue_mean']:.2f}, median arrival-to-provider "
        f"{h23['median_arrival_to_provider_min']:.0f} min for patients arriving then."
    )


def _gate_examples(site: SiteProfile, settings: SimulationSettings) -> None:
    print("\n" + "=" * 100 + "\nGATE EXAMPLES\n" + "=" * 100)
    data = site.model_dump()
    data["operations"]["physician_concurrent_patients"] = 1
    stressed = SiteProfile.model_validate(data)
    r = run_simulation(stressed, settings)
    report = validate_run(r, intent="stress_test")
    print("\n(1) STRESS TEST: physicians limited to one patient at a time (demand ~1.4x supply).")
    print(
        f"    status {report.status}, overloaded={report.overloaded}; integrity checks all PASS: "
        f"{all(c.status is GateStatus.PASS for c in report.checks if c.category == 'integrity')}"
    )
    released = release_for_interpretation(r, report)
    ew = released.tables["extreme_waits"]
    over = ew[
        (ew["acuity_esi"] == "all")
        & (ew["measure"] == "time_in_ed")
        & (ew["threshold_min"] == 1440)
    ].iloc[0]
    print(
        f"    still released for diagnosis: {int(over['patients_over'])} of "
        f"{int(over['denominator'])} "
        f"measured patients spent >24 h in the ED."
    )

    base = run_simulation(site, settings)
    broken_log = base.patients.copy()
    i = broken_log.index[10]
    broken_log.loc[i, "provider_start_min"] = broken_log.loc[i, "arrival_min"] - 15
    broken = dataclasses.replace(base, patients=broken_log)
    report = validate_run(broken)
    print("\n(2) CORRUPTED OUTPUT: one patient 'seen by a physician' 15 min before arriving.")
    print(f"    status {report.status}")
    for line in report.reasons:
        print(_wrap(line, "    "))
    try:
        release_for_interpretation(broken, report)
    except BlockedResultError:
        print("    release_for_interpretation() REFUSED: no numbers from this run can be packaged.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validated analysis of one simulation run.")
    parser.add_argument("--site", type=Path, default=DEFAULT_SITE)
    parser.add_argument("--start", type=date.fromisoformat, default=date(2026, 11, 16))
    parser.add_argument("--days", type=int, default=8)
    parser.add_argument("--warmup-days", type=int, default=1)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--intent", choices=["baseline", "stress_test"], default="baseline")
    parser.add_argument("--gate-examples", action="store_true")
    args = parser.parse_args(argv)
    try:
        site = load_site_profile(args.site)
        settings = SimulationSettings(
            start_date=args.start,
            arrival_days=args.days,
            warmup_days=args.warmup_days,
            seed=args.seed,
        )
    except (ConfigError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print("=" * 100)
    print(" SIMULATED, VALIDATED ANALYSIS -- fictional ED, illustrative assumptions, one seed.")
    print(
        " Not clinically validated. Not a description of any real hospital."
        " Not a staffing recommendation."
    )
    print(
        f" Site: {site.site.display_name} | seed {args.seed} | {args.days} days, "
        f"first {args.warmup_days} excluded as warm-up"
    )
    print("=" * 100)

    result = run_simulation(site, settings)
    report = validate_run(result, intent=args.intent)
    _print_report(report)
    if report.status is GateStatus.BLOCKED:
        print("\nBLOCKED: no metrics are shown for this run.")
        return 2
    released = release_for_interpretation(result, report)
    _print_tables(released)
    _print_findings(released)

    print("\nUnavailable metrics:")
    for name, why in report.unavailable_metrics.items():
        print(f"  {name}:")
        print(_wrap(why))
    print("\nLimitations that apply to every number above:")
    for item in report.limitations:
        print(textwrap.fill(item, width=100, initial_indent="  - ", subsequent_indent="    "))
    if args.gate_examples:
        _gate_examples(site, settings)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
