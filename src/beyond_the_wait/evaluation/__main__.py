"""Command line: offline evaluation of the AI interpretation guardrails.

    python -m beyond_the_wait.evaluation run [--split dev|heldout|heldout2|all] [--out FILE]
    python -m beyond_the_wait.evaluation lock-heldout --split heldout2   # once, before any run
    python -m beyond_the_wait.evaluation freeze-packets    # rebuild evals/packets from the bundle

No command makes a network call. `run` reads only files under evals/.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from beyond_the_wait.evaluation.dataset import (
    HELDOUT_SPLITS,
    LOCK_FILES,
    PACKETS_DIR,
    SPLIT_FILES,
    SPLITS,
    file_sha256,
    load_dataset,
)
from beyond_the_wait.evaluation.report import to_markdown
from beyond_the_wait.evaluation.runner import run_suite, summary_dict

# name -> (scenario_id, focus outcome). Chosen to cover a focus outcome that is
# lower, higher, and inconclusive, with and without sensitivity facts.
PACKET_SPECS: dict[str, tuple[str, str]] = {
    "triage_nurse": ("add_night_triage_nurse", "triage_wait_p90"),
    "night_physician": ("add_night_physician", "boarding_p90"),
    "bed_releases": ("increase_bed_releases", "boarding_p90"),
    "evening_physician": ("add_evening_physician", "boarding_p90"),
    "diagnostics": ("faster_diagnostics", "los_median_discharged"),
}


def freeze_packets(out_dir: Path = PACKETS_DIR) -> list[Path]:  # pragma: no cover - one-off
    """Copy packets from the verified bundle into evals/packets (deliberate, manual)."""
    from beyond_the_wait.analysis.results_bundle import load_bundle
    from beyond_the_wait.dashboard.data import DEFAULT_RESULTS_DIR, scenario_names
    from beyond_the_wait.interpretation.evidence import build_evidence_packet

    load = load_bundle(DEFAULT_RESULTS_DIR)
    names = scenario_names()
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, (scenario, metric) in PACKET_SPECS.items():
        p = build_evidence_packet(
            load, scenario_id=scenario, metric=metric, scenario_name=names.get(scenario)
        )
        path = out_dir / f"{name}.json"
        path.write_text(json.dumps(p.model_dump(mode="json"), indent=1) + "\n")
        written.append(path)
    return written


def lock_heldout(split: str = "heldout", force: bool = False) -> dict:
    """Record a held-out file's hash and its FIRST (blind) run. Refuses to overwrite."""
    path = LOCK_FILES[split]
    if path.exists() and not force:
        raise SystemExit(
            f"{path.name} already exists. Re-locking would replace the blind first-run "
            "result; pass --force only if the held-out set was deliberately replaced."
        )
    report = run_suite(load_dataset((split,)))  # type: ignore[arg-type]
    lock = {
        "sha256": file_sha256(SPLIT_FILES[split]),
        "locked_on": date.today().isoformat(),
        "note": "First run of the held-out set, recorded before any rule was changed in "
        "response to it. Later held-out scores are only independent if the file is unchanged "
        "AND no rule was tuned on these cases; see docs/AI_EVALUATION.md.",
        "first_run": summary_dict(report.splits[split]),
    }
    path.write_text(json.dumps(lock, indent=1) + "\n")
    return lock


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m beyond_the_wait.evaluation")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run the offline evaluation and print a report")
    run.add_argument("--split", choices=[*SPLITS, "all"], default="all")
    run.add_argument("--out", type=Path, help="also write the Markdown report to this file")
    lock = sub.add_parser("lock-heldout", help="hash the held-out set and record its first run")
    lock.add_argument("--split", choices=list(HELDOUT_SPLITS), default="heldout")
    lock.add_argument("--force", action="store_true")
    sub.add_parser("freeze-packets", help="rebuild evals/packets from the results bundle")
    args = parser.parse_args(argv)

    if args.command == "freeze-packets":  # pragma: no cover - one-off maintenance
        for path in freeze_packets():
            print(f"wrote {path}")
        return 0
    if args.command == "lock-heldout":
        lock = lock_heldout(args.split, args.force)
        print(json.dumps(lock["first_run"], indent=1))
        return 0
    splits = SPLITS if args.split == "all" else (args.split,)
    text = to_markdown(run_suite(load_dataset(splits)))
    if args.out:
        args.out.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
