"""Command line for the supervised live evaluation.

    python -m beyond_the_wait.live_eval plan                    # offline: plan, order, cost ceiling
    python -m beyond_the_wait.live_eval dry-run [--batch ID]    # offline: full MOCK workflow
    python -m beyond_the_wait.live_eval generate --batch ID     # MOCK unless --mode live
    python -m beyond_the_wait.live_eval generate --batch ID --mode live --approve CODE
    python -m beyond_the_wait.live_eval review --batch ID [--slot NN]
    python -m beyond_the_wait.live_eval report --batch ID
    python -m beyond_the_wait.live_eval record-billing --batch ID --usd X --source TEXT

Only `generate --mode live` can make a billable request, and only after
`authorize_live` passes (approval code, key, interactive terminal, typed phrase).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path

from beyond_the_wait.evaluation.dataset import EVAL_DIR, PACKETS_DIR
from beyond_the_wait.interpretation.providers import MAX_OUTPUT_TOKENS
from beyond_the_wait.live_eval import batch as batch_mod
from beyond_the_wait.live_eval.dry_run import DryRunProvider
from beyond_the_wait.live_eval.plan import (
    PLAN_FILE,
    RESULTS_ROOT,
    approval_code,
    cost_ceiling,
    input_problems,
    input_token_bound,
    load_plan,
    load_planned_packets,
    slots,
)
from beyond_the_wait.live_eval.report import record_billing, to_markdown
from beyond_the_wait.live_eval.review import ReviewError, pending_slots, review_slot

DRY_RUN_ANSWERS = EVAL_DIR / "live" / "dry_run_answers.json"


def _isatty() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def cmd_plan(out: Callable[[str], None], plan_file: Path, packets_dir: Path) -> int:
    plan = load_plan(plan_file)
    problems = input_problems(plan, packets_dir)
    out(f"Plan {plan.protocol_version}: {plan.model}, prompt {plan.prompt_version}")
    out(
        f"{len(plan.packets)} frozen packets x {plan.generations_per_packet} generations = "
        f"{plan.max_calls} calls, in this fixed order:"
    )
    if problems:
        out("INPUTS DO NOT MATCH THE PLAN (live mode is refused): " + "; ".join(problems))
        return 1
    packets = load_planned_packets(plan, packets_dir)
    for s in slots(plan):
        bound = input_token_bound(packets[s.packet])
        out(
            f"  slot {s.id}: {s.packet} generation {s.generation} "
            f"(<= {bound:,} input tokens, <= {MAX_OUTPUT_TOKENS:,} output tokens)"
        )
    out(
        f"Cost ceiling at list price: ${cost_ceiling(plan, packets):.2f} "
        f"(${plan.pricing.input_usd_per_mtok}/M in, ${plan.pricing.output_usd_per_mtok}/M out, "
        f"checked {plan.pricing.checked_on}). Expected cost is far lower."
    )
    out(f"Approval code for exactly this plan: {approval_code(plan)}")
    return 0


def cmd_generate(
    args: argparse.Namespace,
    out: Callable[[str], None],
    ask: Callable[[str], str],
    interactive: bool,
) -> int:
    plan = load_plan(args.plan)
    packets = load_planned_packets(plan, args.packets)
    mode = "LIVE" if args.mode == "live" else "MOCK"
    directory = batch_mod.batch_dir(args.batch, args.root)
    if mode == "LIVE":
        # Authorize BEFORE creating anything, so a refused run leaves no trace.
        batch_mod.authorize_live(
            plan,
            packets,
            approve=args.approve,
            directory=directory,
            packets_dir=args.packets,
            interactive=interactive,
            ask=ask,
            log=out,
        )
        provider = batch_mod.live_provider(plan.model)
    else:
        provider = DryRunProvider({p.packet_id: n for n, p in packets.items()})
    directory = batch_mod.open_batch(args.batch, plan, mode, args.root)
    counts = batch_mod.run_batch(
        directory, plan, packets, provider, mode, packets_dir=args.packets, log=out
    )
    out(
        f"{counts['sent']} sent, {counts['errors']} errors, {counts['skipped']} skipped. "
        f"Records: {directory}. No verdicts were computed; run `review` next."
    )
    return 0


def cmd_review(
    args: argparse.Namespace,
    out: Callable[[str], None],
    ask: Callable[[str], str],
    answers_by_slot: dict | None = None,
    reviewer: str | None = None,
) -> int:
    plan = load_plan(args.plan)
    packets = load_planned_packets(plan, args.packets)
    directory = batch_mod.batch_dir(args.batch, args.root)
    meta = json.loads((directory / "batch.json").read_text())
    by_slot = {s["slot"]: s["packet"] for s in meta["slots"]}
    todo = [args.slot] if getattr(args, "slot", None) else pending_slots(directory)
    if not todo:
        out("Nothing left to review in this batch.")
    for slot in todo:
        out(f"\n===== Reviewing slot {slot} =====")
        review_slot(
            directory,
            slot,
            packets[by_slot[slot]],
            ask=ask,
            out=out,
            reviewer=reviewer or args.reviewer,
            answers=(answers_by_slot or {}).get(slot),
        )
    return 0


def cmd_dry_run(args: argparse.Namespace, out: Callable[[str], None]) -> int:
    """Generate (MOCK) -> blind review with pre-written answers -> reveal -> report."""
    args.mode, args.approve = "mock", None
    cmd_generate(args, out, ask=lambda _p: "", interactive=False)
    scripted = json.loads(args.answers.read_text())
    cmd_review(
        args,
        out,
        ask=lambda _p: "",
        answers_by_slot=scripted["slots"],
        reviewer=scripted["reviewer"],
    )
    report = to_markdown(batch_mod.batch_dir(args.batch, args.root))
    (batch_mod.batch_dir(args.batch, args.root) / "report.md").write_text(report + "\n")
    out("\n" + report)
    return 0


def main(
    argv: list[str] | None = None,
    *,
    out: Callable[[str], None] = print,
    ask: Callable[[str], str] = input,
    interactive: Callable[[], bool] = _isatty,
) -> int:
    parser = argparse.ArgumentParser(prog="python -m beyond_the_wait.live_eval")
    parser.add_argument("--plan", type=Path, default=PLAN_FILE, help=argparse.SUPPRESS)
    parser.add_argument("--packets", type=Path, default=PACKETS_DIR, help=argparse.SUPPRESS)
    parser.add_argument("--root", type=Path, default=RESULTS_ROOT, help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("plan", help="show the plan, order, cost ceiling, and approval code")
    gen = sub.add_parser("generate", help="run the batch (MOCK unless --mode live)")
    gen.add_argument("--batch", required=True)
    gen.add_argument("--mode", choices=["mock", "live"], default="mock")
    gen.add_argument("--approve", help="approval code from `plan` (live mode only)")
    rev = sub.add_parser("review", help="blind review of saved responses")
    rev.add_argument("--batch", required=True)
    rev.add_argument("--slot")
    rev.add_argument("--reviewer", default="reviewer-1", help="a label, not a name")
    rep = sub.add_parser("report", help="summarize a batch")
    rep.add_argument("--batch", required=True)
    bill = sub.add_parser("record-billing", help="store the charge shown in the Console")
    bill.add_argument("--batch", required=True)
    bill.add_argument("--usd", type=float, required=True)
    bill.add_argument("--source", default="Anthropic Console usage page")
    dry = sub.add_parser("dry-run", help="offline MOCK run of the whole workflow")
    dry.add_argument("--batch", default="dry-run")
    dry.add_argument("--answers", type=Path, default=DRY_RUN_ANSWERS, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    try:
        if args.command == "plan":
            return cmd_plan(out, args.plan, args.packets)
        if args.command == "generate":
            return cmd_generate(args, out, ask, interactive())
        if args.command == "review":
            return cmd_review(args, out, ask)
        if args.command == "dry-run":
            return cmd_dry_run(args, out)
        directory = batch_mod.batch_dir(args.batch, args.root)
        if args.command == "record-billing":
            record_billing(directory, args.usd, args.source)
        report = to_markdown(directory)
        (directory / "report.md").write_text(report + "\n")
        out(report)
        return 0
    except (batch_mod.BatchError, ReviewError, ValueError) as exc:
        out(f"Refused: {exc}")
        return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
