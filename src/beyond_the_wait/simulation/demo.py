"""Command-line demonstration of the baseline simulation (SIMULATED, fictional ED).

python -m beyond_the_wait.simulation.demo --days 8 --warmup-days 1 --seed 42
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import pandas as pd

from beyond_the_wait.analysis.metrics import (
    patient_summary,
    resource_summary,
    throughput_summary,
)
from beyond_the_wait.config import ConfigError, load_site_profile
from beyond_the_wait.simulation.engine import SimulationSettings, run_simulation
from beyond_the_wait.simulation.validity import offered_load_checks, offered_loads, run_checks

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SITE = REPO_ROOT / "config" / "sites" / "fictional_bronx_community_ed.yaml"


def _clock(minutes: float, start: date) -> str:
    if pd.isna(minutes):
        return "      --"
    t = pd.Timestamp(start) + pd.Timedelta(minutes=float(minutes))
    return t.strftime("%a %H:%M")


def _print_journeys(result, k: int) -> None:  # noqa: ANN001
    p = result.patients
    m = p[(p["cohort"] == "measured") & (p["status"] == "completed")]
    picks = pd.concat(
        [
            m[m["disposition"] == "discharge"].head(max(1, k // 2)),
            m[m["disposition"] == "admit"].head(max(1, k - k // 2)),
            m[m["acuity_esi"] == 1].head(1),
        ]
    ).drop_duplicates("synthetic_patient_id")
    start = result.settings.start_date
    print(f"\nSample patient journeys ({len(picks)}; clock times are fictional):\n")
    times = " ".join(f"{h:>9}" for h in ("arrive", "triaged", "space", "provider", "left"))
    header = f"{'patient':15} ESI {'dispo':9} {times}  space type        LOS(min) board(min)"
    print(header)
    for r in picks.itertuples():
        board = f"{r.boarding_min:9.0f}" if r.disposition == "admit" else "        -"
        print(
            f"{r.synthetic_patient_id:15} {r.acuity_esi:>3} {r.disposition:9} "
            f"{_clock(r.arrival_min, start):>9} {_clock(r.triage_end_min, start):>9} "
            f"{_clock(r.space_start_min, start):>9} {_clock(r.provider_start_min, start):>9} "
            f"{_clock(r.departure_min, start):>9}  {r.space_type:17} {r.los_min:8.0f} {board}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the baseline ED simulation (SIMULATED).")
    parser.add_argument("--site", type=Path, default=DEFAULT_SITE)
    parser.add_argument("--start", type=date.fromisoformat, default=date(2026, 11, 16))
    parser.add_argument("--days", type=int, default=8, help="days with arrivals")
    parser.add_argument("--warmup-days", type=int, default=1)
    parser.add_argument("--drain-hours", type=int, default=72)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--sample", type=int, default=4)
    args = parser.parse_args(argv)
    try:
        site = load_site_profile(args.site)
        settings = SimulationSettings(
            start_date=args.start,
            arrival_days=args.days,
            warmup_days=args.warmup_days,
            drain_hours=args.drain_hours,
            seed=args.seed,
        )
    except (ConfigError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    bar = "=" * 96
    print(bar)
    print(" SIMULATED OUTCOMES -- fictional ED, fictional patients, illustrative assumptions.")
    print(" Not a description of any real hospital. Not comparable to CMS/NYS public measures.")
    print(
        f" Site: {site.site.display_name} | seed {settings.seed} | arrivals {settings.start_date} "
        f"+{settings.arrival_days} days | warm-up {settings.warmup_days} day(s) excluded"
    )
    print(bar)

    print("\nPre-run capacity check (average day / busiest hour; load = work arriving / capacity):")
    for key, value in offered_loads(site).items():
        print(f"  {key:24} {value:5.2f}")
    pre = offered_load_checks(site)
    for f in pre:
        print(f"  [{f.level.upper()}] {f.message}")

    result = run_simulation(site, settings)
    t = throughput_summary(result)
    print(
        f"\nRun: {len(result.patients)} patients simulated; stop = {result.stop_reason} at "
        f"{_clock(result.stop_time, settings.start_date)} (day {result.stop_time / 1440:.2f})."
    )
    print(
        f"Measured window: {t['measured_days']} days, {t['arrivals_measured']} arrivals "
        f"({t['arrivals_per_day']:.1f}/day), {t['completed_measured']} completed, "
        f"{t['unfinished_measured']} unfinished; "
        f"mean patients in ED {t['mean_census_in_window']:.1f}."
    )
    print(
        f"Inpatient beds (simplified model): {t['beds_released']} released to ED, "
        f"{t['beds_claimed']} claimed, {t['beds_lost_to_other_demand']} went to other demand."
    )

    _print_journeys(result, args.sample)

    print("\nPatient outcomes, measured cohort, completed visits (minutes):")
    ps = patient_summary(result)
    shown = ps[["measure", "group", "patients", "median_min", "p90_min", "mean_min"]]
    print(shown.to_string(index=False, float_format=lambda v: f"{v:7.1f}"))

    print("\nResources (time-weighted over the measured window):")
    rs = resource_summary(result)[
        ["resource", "average_capacity", "utilization", "mean_queue", "max_queue"]
    ]
    print(rs.to_string(index=False, float_format=lambda v: f"{v:6.2f}", na_rep="     -"))

    print("\nValidity flags:")
    for f in [*pre, *run_checks(result)]:
        print(f"  [{f.level.upper():8}] {f.code}: {f.message}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
