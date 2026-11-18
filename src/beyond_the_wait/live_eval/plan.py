"""The fixed evaluation plan: packets, slot order, fingerprints, and the cost ceiling."""

from __future__ import annotations

import hashlib
import json
import tomllib
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from beyond_the_wait.evaluation.dataset import EVAL_DIR, PACKETS_DIR
from beyond_the_wait.interpretation.evidence import EvidencePacket
from beyond_the_wait.interpretation.prompt import PROMPT_VERSION, SYSTEM_PROMPT, user_message
from beyond_the_wait.interpretation.providers import MAX_OUTPUT_TOKENS
from beyond_the_wait.interpretation.schema import Interpretation

PLAN_FILE = EVAL_DIR / "live" / "plan.toml"
RESULTS_ROOT = EVAL_DIR / "results" / "live"  # git-ignored (evals/results/*)
MAX_CALLS_PER_BATCH = 10  # hard ceiling in code, whatever the plan file says
EXCLUDED_PACKETS = {"triage_nurse_tampered"}  # adversarial test fixture, never sent


class PlanError(ValueError):
    """The plan file, a frozen packet, or the prompt does not match what was approved."""


class Pricing(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_usd_per_mtok: float = Field(gt=0)
    output_usd_per_mtok: float = Field(gt=0)
    checked_on: str
    source: str

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        return (
            input_tokens * self.input_usd_per_mtok + output_tokens * self.output_usd_per_mtok
        ) / 1_000_000


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: str
    model: str
    prompt_version: str
    generations_per_packet: int = Field(ge=1)
    max_calls: int = Field(ge=1, le=MAX_CALLS_PER_BATCH)
    packets: list[str] = Field(min_length=1)
    packet_sha256: dict[str, str]
    pricing: Pricing

    @model_validator(mode="after")
    def _consistent(self) -> Plan:
        if len(set(self.packets)) != len(self.packets):
            raise ValueError("packets must be distinct")
        if set(self.packets) & EXCLUDED_PACKETS:
            raise ValueError("the tampered test packet must never be sent to a model")
        if set(self.packet_sha256) != set(self.packets):
            raise ValueError("packet_sha256 must pin exactly the planned packets")
        if len(self.packets) * self.generations_per_packet != self.max_calls:
            raise ValueError("max_calls must equal packets x generations_per_packet")
        return self


@dataclass(frozen=True)
class Slot:
    index: int  # 1-based position in the fixed order
    packet: str
    generation: int

    @property
    def id(self) -> str:
        return f"{self.index:02d}"


def load_plan(path: Path = PLAN_FILE) -> Plan:
    return Plan.model_validate(tomllib.loads(path.read_text()))


def slots(plan: Plan) -> list[Slot]:
    """Round-robin order: generation 1 of every packet, then generation 2, ..."""
    order = [(p, g) for g in range(1, plan.generations_per_packet + 1) for p in plan.packets]
    return [Slot(i, p, g) for i, (p, g) in enumerate(order, start=1)]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prompt_fingerprint() -> str:
    """Hash of everything fixed that is sent besides the packet: system prompt + schema."""
    schema = json.dumps(Interpretation.model_json_schema(), sort_keys=True)
    return hashlib.sha256((SYSTEM_PROMPT + "\n" + schema).encode()).hexdigest()


def plan_fingerprint(plan: Plan) -> str:
    body = json.dumps(plan.model_dump(), sort_keys=True) + prompt_fingerprint()
    return hashlib.sha256(body.encode()).hexdigest()


def approval_code(plan: Plan) -> str:
    """The short code a person must type to approve exactly this plan."""
    return plan_fingerprint(plan)[:12]


def input_problems(plan: Plan, packets_dir: Path = PACKETS_DIR) -> list[str]:
    """Why the current inputs differ from the plan (empty list = they match)."""
    problems = []
    if plan.prompt_version != PROMPT_VERSION:
        problems.append(f"prompt version is {PROMPT_VERSION}, plan expects {plan.prompt_version}")
    for name, expected in plan.packet_sha256.items():
        path = packets_dir / f"{name}.json"
        if not path.exists():
            problems.append(f"frozen packet {name}.json is missing")
        elif sha256_file(path) != expected:
            problems.append(f"frozen packet {name}.json changed since the plan was written")
    return problems


def load_planned_packets(plan: Plan, packets_dir: Path = PACKETS_DIR) -> dict[str, EvidencePacket]:
    problems = input_problems(plan, packets_dir)
    if problems:
        raise PlanError("; ".join(problems))
    return {
        name: EvidencePacket.model_validate_json((packets_dir / f"{name}.json").read_text())
        for name in plan.packets
    }


def input_token_bound(packet: EvidencePacket) -> int:
    """Upper bound on input tokens: one token per character of prompt, packet, and schema.

    Real tokenization produces far fewer tokens than characters for English and
    JSON, so this overstates the cost on purpose.
    """
    schema = json.dumps(Interpretation.model_json_schema())  # sent as the output format
    return len(SYSTEM_PROMPT) + len(user_message(packet)) + len(schema)


def cost_ceiling(plan: Plan, packets: dict[str, EvidencePacket]) -> float:
    """Most the batch can cost at list price: every slot at its input bound and max output."""
    return sum(
        plan.pricing.cost(input_token_bound(packets[s.packet]), MAX_OUTPUT_TOKENS)
        for s in slots(plan)
    )
