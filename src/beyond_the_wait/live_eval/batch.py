"""Generate one batch of responses under hard spending safeguards.

Safeguards (each one is tested):

* MOCK by default. A live provider is created only by `live_provider`, which the
  CLI calls only after `authorize_live` passes.
* At most MAX_CALLS_PER_BATCH provider calls per batch, counted from an
  append-only ledger that records each attempt BEFORE the request is sent, so a
  crash or a re-run can never push the batch over the cap.
* No retries: the SDK client is built with max_retries=0, and a slot that has any
  recorded attempt (even a failed one) is never sent again in that batch.
* Calls run one at a time in the foreground: no threads, no background jobs.
* Records hold model output and simulated aggregates only; the API key is never
  written, logged, or printed.
* No automated verdict is computed here, so the reviewer stays blind to it.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from beyond_the_wait.interpretation.evidence import EvidencePacket
from beyond_the_wait.interpretation.prompt import PROMPT_VERSION, SYSTEM_PROMPT, user_message
from beyond_the_wait.interpretation.providers import (
    ClaudeProvider,
    InterpretationError,
    Provider,
    _read_key,
)
from beyond_the_wait.live_eval.plan import (
    MAX_CALLS_PER_BATCH,
    RESULTS_ROOT,
    Plan,
    PlanError,
    Slot,
    approval_code,
    cost_ceiling,
    input_problems,
    plan_fingerprint,
    prompt_fingerprint,
    sha256_file,
    slots,
)

BatchMode = Literal["MOCK", "LIVE"]
BATCH_ID = re.compile(r"[a-z0-9][a-z0-9-]{2,39}")
LIVE_TIMEOUT_SECONDS = 600.0  # one slow answer must not time out after it was billed
CONFIRM_PHRASE = "RUN {n} CALLS"


class BatchError(RuntimeError):
    """The batch cannot run (cap reached, wrong mode, not approved, ...)."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def batch_dir(batch_id: str, root: Path = RESULTS_ROOT) -> Path:
    if not BATCH_ID.fullmatch(batch_id):
        raise BatchError("batch id must be 3-40 characters: lowercase letters, digits, '-'")
    return root / batch_id


def read_ledger(directory: Path) -> list[dict]:
    path = directory / "ledger.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _append(directory: Path, event: dict) -> None:
    with (directory / "ledger.jsonl").open("a") as fh:
        fh.write(json.dumps(event) + "\n")
        fh.flush()


def attempted_slots(directory: Path) -> set[str]:
    return {e["slot"] for e in read_ledger(directory) if e["event"] == "attempt"}


def open_batch(batch_id: str, plan: Plan, mode: BatchMode, root: Path = RESULTS_ROOT) -> Path:
    """Create the batch folder, or check that an existing one matches this plan and mode."""
    directory = batch_dir(batch_id, root)
    meta_path = directory / "batch.json"
    fingerprint = plan_fingerprint(plan)
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
        if meta["mode"] != mode:
            raise BatchError(f"batch {batch_id} is a {meta['mode']} batch; use a new batch id")
        if meta["plan_sha256"] != fingerprint:
            raise BatchError(f"batch {batch_id} was created for a different plan")
        return directory
    (directory / "responses").mkdir(parents=True, exist_ok=True)
    (directory / "reviews").mkdir(exist_ok=True)
    meta = {
        "batch_id": batch_id,
        "mode": mode,
        "created_at": _now(),
        "plan_sha256": fingerprint,
        "protocol_version": plan.protocol_version,
        "model": plan.model,
        "prompt_version": plan.prompt_version,
        "prompt_sha256": prompt_fingerprint(),
        "max_calls": plan.max_calls,
        "pricing": plan.pricing.model_dump(),
        "slots": [
            {"slot": s.id, "packet": s.packet, "generation": s.generation} for s in slots(plan)
        ],
        "billing": None,  # filled by `record-billing` from the Console, if ever
    }
    meta_path.write_text(json.dumps(meta, indent=1) + "\n")
    return directory


def _record(
    slot: Slot,
    packet: EvidencePacket,
    packet_sha: str,
    plan: Plan,
    mode: BatchMode,
    model: str,
    started: float,
    requested_at: str,
) -> dict:
    return {
        "slot": slot.id,
        "packet_name": slot.packet,
        "generation": slot.generation,
        "scenario_id": packet.scenario_id,
        "scenario_name": packet.scenario_name,
        "focus_metric": packet.focus_metric,
        "packet_id": packet.packet_id,
        "packet_sha256": packet_sha,
        "bundle_fingerprint": packet.bundle_fingerprint,
        "mode": mode,
        "model_requested": plan.model if mode == "LIVE" else model,
        "prompt_version": PROMPT_VERSION,
        "prompt_sha256": prompt_fingerprint(),
        "plan_sha256": plan_fingerprint(plan),
        "requested_at": requested_at,
        "completed_at": _now(),
        "latency_seconds": round(time.monotonic() - started, 2),
    }


def run_batch(
    directory: Path,
    plan: Plan,
    packets: dict[str, EvidencePacket],
    provider: Provider,
    mode: BatchMode,
    *,
    packets_dir: Path,
    log: Callable[[str], None] = print,
) -> dict[str, int]:
    """Run every not-yet-attempted slot once, in the fixed order. Never retries."""
    if provider.mode != ("LIVE" if mode == "LIVE" else "MOCK"):
        raise BatchError("provider mode does not match the batch mode")
    cap = min(plan.max_calls, MAX_CALLS_PER_BATCH)
    done = attempted_slots(directory)
    counts = {"sent": 0, "skipped": 0, "errors": 0}
    for slot in slots(plan):
        if slot.id in done:
            log(f"slot {slot.id}: already attempted, not sent again (no retries)")
            counts["skipped"] += 1
            continue
        if len(attempted_slots(directory)) >= cap:  # re-read: the ledger is the truth
            raise BatchError(f"call cap reached ({cap} per batch); nothing more is sent")
        packet = packets[slot.packet]
        packet_sha = sha256_file(packets_dir / f"{slot.packet}.json")
        requested_at, started = _now(), time.monotonic()
        _append(directory, {"event": "attempt", "slot": slot.id, "at": requested_at})
        counts["sent"] += 1
        record = _record(
            slot, packet, packet_sha, plan, mode, provider.model, started, requested_at
        )
        try:
            response = provider.interpret(packet)
        except InterpretationError as exc:
            record |= {"status": "error", "error_kind": exc.kind, "raw_text": None}
            record |= _usage({}, plan, simulated=mode == "MOCK")
            counts["errors"] += 1
        except Exception as exc:  # unexpected: record the type only, never the message
            record |= {"status": "error", "error_kind": f"unexpected:{type(exc).__name__}"}
            record |= {"raw_text": None} | _usage({}, plan, simulated=mode == "MOCK")
            counts["errors"] += 1
        else:
            usage = response.usage
            simulated = mode == "MOCK"
            if simulated:  # rough, clearly labeled stand-in so the cost math is exercised
                usage = {
                    "input_tokens": (len(SYSTEM_PROMPT) + len(user_message(packet))) // 4,
                    "output_tokens": len(response.text) // 4,
                }
            record |= {
                "status": "response",
                "error_kind": None,
                "model_returned": response.model,
                "stop_reason": response.stop_reason,
                "raw_text": response.text,
            }
            record |= _usage(usage, plan, simulated=simulated)
            record["latency_seconds"] = round(time.monotonic() - started, 2)
        record["completed_at"] = _now()
        (directory / "responses" / f"{slot.id}.json").write_text(
            json.dumps(record, indent=1) + "\n"
        )
        _append(directory, {"event": "done", "slot": slot.id, "status": record["status"]})
        log(
            f"slot {slot.id}: {slot.packet} (generation {slot.generation}) -> {record['status']}"
            + (f" [{record['error_kind']}]" if record["status"] == "error" else "")
        )
    return counts


def _usage(usage: dict[str, int], plan: Plan, *, simulated: bool) -> dict:
    tin, tout = int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))
    return {
        "usage": {"input_tokens": tin, "output_tokens": tout},
        "usage_simulated": simulated,
        "estimated_cost_usd": round(plan.pricing.cost(tin, tout), 4),
        # Errors can still be billed (for example a truncated answer); the Console
        # usage page is the only source of the actual charge.
        "billed_usd": None,
    }


# ---------------------------------------------------------------------------
# Live authorization: every condition must hold, in this order
# ---------------------------------------------------------------------------


def authorize_live(
    plan: Plan,
    packets: dict[str, EvidencePacket],
    *,
    approve: str | None,
    directory: Path,
    packets_dir: Path,
    interactive: bool,
    ask: Callable[[str], str],
    log: Callable[[str], None] = print,
) -> None:
    """Raise BatchError unless a person has explicitly approved exactly this batch."""
    problems = input_problems(plan, packets_dir)
    if problems:
        raise PlanError("; ".join(problems))
    code = approval_code(plan)
    if approve != code:
        raise BatchError(
            "live mode needs --approve with this plan's approval code "
            "(shown by `python -m beyond_the_wait.live_eval plan`)"
        )
    if not interactive:
        raise BatchError("live mode must be started by a person in an interactive terminal")
    if not _read_key():
        raise BatchError(
            "no ANTHROPIC_API_KEY found in the environment or the local .env; nothing was sent"
        )
    remaining = min(plan.max_calls, MAX_CALLS_PER_BATCH) - len(attempted_slots(directory))
    if remaining <= 0:
        raise BatchError("this batch has already used its call cap; nothing more is sent")
    ceiling = cost_ceiling(plan, packets)
    log(
        f"About to send up to {remaining} request(s) to {plan.model}, one at a time, no retries.\n"
        f"Cost ceiling at list price for the full plan: ${ceiling:.2f} "
        f"(prices checked {plan.pricing.checked_on}; the Console is authoritative)."
    )
    phrase = CONFIRM_PHRASE.format(n=remaining)
    if ask(f"Type '{phrase}' to approve, anything else to cancel: ").strip() != phrase:
        raise BatchError("not confirmed; nothing was sent")


def live_provider(model: str) -> ClaudeProvider:
    """The only constructor of a billable provider in this package: no retries."""
    return ClaudeProvider(_read_key(), model, max_retries=0, timeout=LIVE_TIMEOUT_SECONDS)
