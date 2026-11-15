"""Generate FICTIONAL emergency department patients from a site profile.

Every row is invented. No real person, visit, or hospital is described, and
the generator is NOT calibrated to any real hospital's operations or to the
public CMS / NYS measures (see docs/SYNTHETIC_DATA_ASSUMPTIONS.md).

What it produces -- patient ATTRIBUTES only:
    synthetic ID, arrival time, ESI acuity, needs lab, needs imaging, and a
    hypothetical admission flag ("would be admitted if this were real").
What it does NOT produce -- OUTCOMES:
    waits, lengths of stay, departure times, boarding hours. Those emerge
    from the Milestone 4 simulation, when these patients meet limited beds
    and staff.

How arrivals are modeled (a time-varying Poisson process)
    Each clock hour has an average rate, e.g. 12 patients/hour at noon,
    scaled by a day-of-week multiplier. A Poisson process is the standard
    model for "independent arrivals at a known average rate": the number
    arriving in an hour is a Poisson random count with that average, and
    given that count, the arrival moments are spread uniformly at random
    across the hour. Because our rate is constant within each hour and
    changes only on the hour, drawing hour by hour is EXACT for this model
    (no approximation needed). Each hour looks up its own date, so the
    23:00 hour on Sunday uses Sunday's multiplier and 00:00 the next
    morning uses Monday's -- that is how midnight boundaries are handled.

How randomness is controlled (seeds)
    All randomness comes from NumPy's Generator seeded with the `seed` you
    pass. Same seed + same site profile + same dates -> identical patients,
    every time, on any machine running the same NumPy version.
    We split the seed into two independent streams: one for arrivals and
    one for attributes. Changing an attribute probability (e.g. lab use)
    therefore leaves arrival times unchanged, which keeps later scenario
    comparisons fair ("common random numbers").

Usage:
    python -m beyond_the_wait.data.synthetic_generator --seed 42 --date 2026-11-16
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from beyond_the_wait.config import ConfigError, SiteProfile, load_site_profile
from beyond_the_wait.config.models import ESI_LEVELS, HOURS_PER_DAY, WEEKDAYS
from beyond_the_wait.data.errors import SyntheticDataError
from beyond_the_wait.data.provenance import (
    PROVENANCE_COLUMNS,
    Provenance,
    require_provenance,
    tag_dataframe,
)

GENERATOR_VERSION = "1"
GENERATOR_ID = f"beyond_the_wait.data.synthetic_generator@v{GENERATOR_VERSION}"
MAX_DAYS = 3660  # ten years; a guard against accidental huge requests
SECONDS_PER_HOUR = 3600

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SITE = REPO_ROOT / "config" / "sites" / "fictional_bronx_community_ed.yaml"

ATTRIBUTE_COLUMNS: list[str] = [
    "synthetic_patient_id",
    "arrival_time",
    "arrival_date",
    "arrival_hour",
    "day_of_week",
    "acuity_esi",
    "needs_lab",
    "needs_imaging",
    "hypothetical_admission",
    "site_id",
    "generator_seed",
    "generator_version",
]
GENERATED_COLUMNS: list[str] = [*ATTRIBUTE_COLUMNS, *PROVENANCE_COLUMNS]


# ---------------------------------------------------------------------------
# Input checks
# ---------------------------------------------------------------------------


def _check_request(start: object, days: object, seed: object) -> None:
    if isinstance(start, datetime) or not isinstance(start, date):
        raise SyntheticDataError(f"start must be a calendar date (no time), got {start!r}")
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= MAX_DAYS:
        raise SyntheticDataError(f"days must be a whole number from 1 to {MAX_DAYS}, got {days!r}")
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise SyntheticDataError(f"seed must be a non-negative whole number, got {seed!r}")


# ---------------------------------------------------------------------------
# Arrival rates
# ---------------------------------------------------------------------------


def hourly_rate_schedule(site: SiteProfile, start: date, days: int) -> np.ndarray:
    """Average arrivals for every hour in the horizon (length days x 24).

    rate(day d, hour h) = hourly_rates[h] x multiplier(weekday of day d)
    """
    _check_request(start, days, 0)
    base = np.asarray(site.arrivals.hourly_rates, dtype=float)
    day_multipliers = np.array(
        [site.arrivals.multiplier_for((start + timedelta(days=d)).weekday()) for d in range(days)]
    )
    return (day_multipliers[:, None] * base[None, :]).ravel()


def expected_arrivals(site: SiteProfile, start: date, days: int) -> float:
    """Expected (average) number of arrivals over the horizon."""
    return float(hourly_rate_schedule(site, start, days).sum())


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "synthetic_patient_id": pd.Series(dtype=str),
            "arrival_time": pd.Series(dtype="datetime64[s]"),
            "arrival_date": pd.Series(dtype=object),
            "arrival_hour": pd.Series(dtype="int64"),
            "day_of_week": pd.Series(dtype=str),
            "acuity_esi": pd.Series(dtype="int64"),
            "needs_lab": pd.Series(dtype=bool),
            "needs_imaging": pd.Series(dtype=bool),
            "hypothetical_admission": pd.Series(dtype=bool),
            "site_id": pd.Series(dtype=str),
            "generator_seed": pd.Series(dtype="int64"),
            "generator_version": pd.Series(dtype=str),
        }
    )


def generate_patients(site: SiteProfile, *, start: date, days: int = 1, seed: int) -> pd.DataFrame:
    """Generate fictional ED arrivals and their attributes.

    Args:
        site: validated fictional site profile.
        start: first calendar day (arrivals begin at 00:00 on this date).
        days: number of whole days to generate.
        seed: non-negative integer controlling all randomness.

    Returns:
        One row per fictional patient, sorted by arrival time, tagged
        SYNTHETIC. Empty (with all columns) if no one arrives.
    """
    _check_request(start, days, seed)
    rates = hourly_rate_schedule(site, start, days)

    # Two independent random streams derived from one seed (see module docstring).
    arrival_stream, attribute_stream = np.random.SeedSequence(seed).spawn(2)
    arrival_rng = np.random.default_rng(arrival_stream)
    attribute_rng = np.random.default_rng(attribute_stream)

    # 1. How many arrive in each hour: Poisson(rate of that hour).
    counts = arrival_rng.poisson(rates)
    n = int(counts.sum())

    # 2. When within the hour: uniformly random, then sorted by time.
    hour_index = np.repeat(np.arange(rates.size), counts)
    seconds = np.floor((hour_index + arrival_rng.random(n)) * SECONDS_PER_HOUR).astype("int64")
    seconds.sort()

    # 3. Who they are: acuity first, then conditional probabilities given acuity.
    mix = site.patient_mix
    levels = np.array(ESI_LEVELS)
    acuity_probs = np.array([mix.acuity_distribution[a] for a in ESI_LEVELS], dtype=float)
    acuity = attribute_rng.choice(levels, size=n, p=acuity_probs / acuity_probs.sum())

    def by_acuity(table: dict[int, float]) -> np.ndarray:
        lookup = np.array([table[a] for a in ESI_LEVELS], dtype=float)
        return lookup[acuity - 1]  # each patient's probability for their own level

    draws = attribute_rng.random((n, 3))
    needs_lab = draws[:, 0] < by_acuity(mix.lab_probability_by_acuity)
    needs_imaging = draws[:, 1] < by_acuity(mix.imaging_probability_by_acuity)
    admitted = draws[:, 2] < by_acuity(mix.admission_probability_by_acuity)

    if n == 0:
        frame = _empty_frame()
    else:
        arrival_time = pd.Timestamp(start) + pd.to_timedelta(seconds, unit="s")
        arrival = pd.Series(arrival_time.astype("datetime64[s]"))
        frame = pd.DataFrame(
            {
                "synthetic_patient_id": [f"SYN-{seed}-{i:06d}" for i in range(1, n + 1)],
                "arrival_time": arrival,
                "arrival_date": arrival.dt.date,
                "arrival_hour": arrival.dt.hour.astype("int64"),
                "day_of_week": [WEEKDAYS[d] for d in arrival.dt.day_of_week],
                "acuity_esi": acuity.astype("int64"),
                "needs_lab": needs_lab,
                "needs_imaging": needs_imaging,
                "hypothetical_admission": admitted,
                "site_id": site.site.site_id,
                "generator_seed": seed,
                "generator_version": GENERATOR_VERSION,
            }
        )
    return tag_dataframe(
        frame,
        label=Provenance.SYNTHETIC,
        dataset_id=f"site_profile:{site.site.site_id}",
        source_url=GENERATOR_ID,
        retrieved_on=None,
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_synthetic_patients(df: pd.DataFrame, *, start: date, days: int) -> None:
    """Check a generated table is complete, well-formed, and labeled SYNTHETIC.

    Raises SyntheticDataError describing the first problem found.
    """
    missing = [c for c in GENERATED_COLUMNS if c not in df.columns]
    if missing:
        raise SyntheticDataError(f"missing columns: {missing}")
    try:
        require_provenance(df, Provenance.SYNTHETIC)
    except ValueError as exc:
        raise SyntheticDataError(str(exc)) from exc
    if df.empty:
        return
    if df[ATTRIBUTE_COLUMNS].isna().any().any():
        raise SyntheticDataError("required attribute values are missing")
    if not df["synthetic_patient_id"].is_unique:
        raise SyntheticDataError("synthetic_patient_id values must be unique")
    if not df["synthetic_patient_id"].str.startswith("SYN-").all():
        raise SyntheticDataError("synthetic IDs must start with 'SYN-'")
    if not df["acuity_esi"].isin(ESI_LEVELS).all():
        raise SyntheticDataError(f"acuity_esi must be one of {list(ESI_LEVELS)}")
    for col in ("needs_lab", "needs_imaging", "hypothetical_admission"):
        if df[col].dtype != bool:
            raise SyntheticDataError(f"{col} must be true/false")
    times = pd.to_datetime(df["arrival_time"])
    begin = pd.Timestamp(start)
    end = begin + pd.Timedelta(days=days)
    if (times < begin).any() or (times >= end).any():
        raise SyntheticDataError("arrival times fall outside the requested dates")
    if not times.is_monotonic_increasing:
        raise SyntheticDataError("rows must be sorted by arrival time")
    consistent = (
        (times.dt.date == df["arrival_date"])
        & (times.dt.hour == df["arrival_hour"])
        & (times.dt.day_of_week.map(lambda d: WEEKDAYS[d]) == df["day_of_week"])
    )
    if not consistent.all():
        raise SyntheticDataError("arrival_date/hour/day_of_week disagree with arrival_time")


# ---------------------------------------------------------------------------
# Summary and command-line demonstration
# ---------------------------------------------------------------------------


def summarize(df: pd.DataFrame, site: SiteProfile, *, start: date, days: int) -> dict[str, object]:
    """Observed vs configured values. Configured values are assumptions, not facts."""
    n = len(df)
    mix = site.patient_mix

    def share(mask: pd.Series) -> float:
        return float(mask.mean()) if n else float("nan")

    return {
        "patients": n,
        "expected_patients": expected_arrivals(site, start, days),
        "acuity_share": {
            a: (share(df["acuity_esi"] == a), mix.acuity_distribution[a]) for a in ESI_LEVELS
        },
        "needs_lab": (share(df["needs_lab"]), mix.implied_lab_fraction),
        "needs_imaging": (share(df["needs_imaging"]), mix.implied_imaging_fraction),
        "hypothetical_admission": (
            share(df["hypothetical_admission"]),
            mix.implied_admission_fraction,
        ),
        "arrivals_by_hour": df.groupby("arrival_hour")
        .size()
        .reindex(range(HOURS_PER_DAY), fill_value=0)
        .tolist(),
    }


def _print_demo(
    df: pd.DataFrame, site: SiteProfile, start: date, days: int, seed: int, k: int
) -> None:
    s = summarize(df, site, start=start, days=days)
    bar = "=" * 78
    print(bar)
    print(" FICTIONAL / SYNTHETIC DATA -- no real patients, visits, or hospitals")
    print(f" Site: {site.site.display_name}")
    print(f" Dates: {start.isoformat()} ({start.strftime('%A')}) for {days} day(s) | seed {seed}")
    print(bar)
    print(f"\nSample of {min(k, len(df))} generated patients (attributes only, no outcomes):\n")
    cols = ["synthetic_patient_id", "arrival_time", "acuity_esi", "needs_lab",
            "needs_imaging", "hypothetical_admission", "provenance"]  # fmt: skip
    print(df[cols].head(k).to_string(index=False) if len(df) else "  (no arrivals)")
    expected = s["expected_patients"]
    print(f"\nPatients generated: {s['patients']}  (expected on average: {expected:.1f})")
    print("\nAcuity mix            generated   configured assumption")
    for level, (obs, cfg) in s["acuity_share"].items():  # type: ignore[union-attr]
        print(f"  ESI {level}               {obs:6.1%}       {cfg:6.1%}")
    print("\nAttribute             generated   implied by assumptions")
    for key in ("needs_lab", "needs_imaging", "hypothetical_admission"):
        obs, cfg = s[key]  # type: ignore[misc]
        print(f"  {key:<22}{obs:6.1%}       {cfg:6.1%}")
    hours = s["arrivals_by_hour"]
    print("\nArrivals by hour (00-23):", " ".join(str(h) for h in hours))  # type: ignore[union-attr]
    print(
        "\nNote: one day is a small sample, so generated shares differ from the assumptions "
        "by chance.\nNo waits or lengths of stay are generated here; those come from the "
        "Milestone 4 simulation."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate fictional ED patients (SYNTHETIC).")
    parser.add_argument("--site", type=Path, default=DEFAULT_SITE, help="site profile YAML")
    parser.add_argument("--date", type=date.fromisoformat, default=date(2026, 11, 16))
    parser.add_argument("--days", type=int, default=1)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--sample", type=int, default=10, help="rows to print")
    parser.add_argument("--out", type=Path, help="optional CSV path (e.g. data/synthetic/x.csv)")
    args = parser.parse_args(argv)
    try:
        site = load_site_profile(args.site)
        df = generate_patients(site, start=args.date, days=args.days, seed=args.seed)
        validate_synthetic_patients(df, start=args.date, days=args.days)
    except (ConfigError, SyntheticDataError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    _print_demo(df, site, args.date, args.days, args.seed, args.sample)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(args.out, index=False)
        print(f"\nWrote {len(df)} rows to {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
