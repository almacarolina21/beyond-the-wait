"""The evaluation dataset: frozen packets, reference responses, and labeled cases.

Layout (all version-controlled, all fictional/simulated):

    evals/packets/<name>.json              frozen evidence packets
    evals/cases/reference_responses.toml   one legitimate response per packet
    evals/cases/dev.toml                   development cases (rules may be tuned on these)
    evals/cases/heldout.toml               held-out round 1 (locked, then used for fixes)
    evals/cases/heldout2.toml              held-out round 2 (written after those fixes)
    evals/cases/heldout3.toml              held-out round 3 (after the vocabulary gate)
    evals/cases/<split>.lock.json          SHA-256 of a held-out file + its first, blind run

A case is either a complete raw response (``raw``) or a reference response
with small edits (``patch``), so each case shows exactly what it changes.
Packets are frozen copies: the evaluation stays reproducible even if the
simulation bundle is regenerated, and it needs no data outside ``evals/``.
"""

from __future__ import annotations

import copy
import hashlib
import json
import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from beyond_the_wait.interpretation.evidence import EvidencePacket
from beyond_the_wait.interpretation.faithfulness import ClaimClass, Verdict
from beyond_the_wait.interpretation.verify import CHECKS

REPO_ROOT = Path(__file__).resolve().parents[3]
EVAL_DIR = REPO_ROOT / "evals"
PACKETS_DIR = EVAL_DIR / "packets"
CASES_DIR = EVAL_DIR / "cases"
REFERENCE_FILE = CASES_DIR / "reference_responses.toml"
SPLITS: tuple[str, ...] = ("dev", "heldout", "heldout2", "heldout3")
SPLIT_FILES = {name: CASES_DIR / f"{name}.toml" for name in SPLITS}
HELDOUT_SPLITS = ("heldout", "heldout2", "heldout3")
LOCK_FILES = {name: CASES_DIR / f"{name}.lock.json" for name in HELDOUT_SPLITS}

Split = Literal["dev", "heldout", "heldout2", "heldout3"]
Kind = Literal["legitimate", "borderline", "adversarial"]

# Risk categories (the threat model in docs/AI_EVALUATION.md, section 2).
CATEGORIES: dict[str, str] = {
    "legitimate": "Faithful, descriptive explanation",
    "legitimate_nuanced": "Faithful explanation with careful hedges or negations "
    "(over-blocking probe)",
    "interpretation": "Plausible but unproven mechanism or inference",
    "magnitude_judgment": "Judges how big a change is",
    "cherry_picking": "Favorable outcome reported while an opposite movement is hidden",
    "unsupported_conclusion": "Correct citation with a conclusion the facts do not support",
    "causal_overclaim": "Causal or certain language the evidence cannot give",
    "ci_misreading": "Confidence interval read as a prediction, probability, or proof",
    "safety_claim": "Claims of clinical safety or benefit",
    "real_world_generalization": "Generalizes the fictional model to real hospitals or places",
    "fabricated_citation": "Invented, unknown, or misattributed evidence or sources",
    "missing_uncertainty": "Drops cautions, limitations, or uncertainty",
    "prompt_injection": "Follows or echoes instructions hidden in data",
    "numeric_fidelity": "Wrong numbers, units, rounding, or arithmetic",
    "recommendation": "Direct or indirect advice to act",
    "mislabeling": "A correct value attached to the wrong outcome or statistic",
    "contradiction": "Contradicts the cited sensitivity, status, or interval",
    "malformed": "Not valid JSON or not the required structure",
}
DIMENSIONS: dict[str, tuple[str, ...]] = {
    "Evidence ID validity": ("evidence_ids", "value_tokens"),
    "Numerical and unit fidelity": ("numbers_and_units", "direction"),
    "Uncertainty preservation": ("uncertainty_wording", "required_content"),
    "Claim alignment": ("claim_alignment",),
    "Real-world separation": ("real_world_claims",),
    "Safety and recommendations": ("recommendations_and_safety",),
    "Sources, markup, and injection echoes": ("sources_and_markup",),
    "Structure": ("structure",),
}
CHECK_NAMES = tuple(name for name, _ in CHECKS)
FLAG_KINDS = (
    "interpretation",
    "magnitude",
    "omitted_tradeoff",
    "unnamed_value",
    "uncited_robustness",
    "omitted_sensitivity",
    "unvetted_wording",
    "rhetorical_question",
)


class DatasetError(ValueError):
    """The dataset files are inconsistent (unknown packet, bad path, bad label...)."""


class Patch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: Literal["set", "append", "remove"] = "set"
    path: str
    value: Any = None


class ClaimLabel(BaseModel):
    """An independently written label for one statement of the candidate."""

    model_config = ConfigDict(extra="forbid")

    loc: str  # "summary", "key_findings[0]", ...
    label: ClaimClass


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    split: Split
    packet: str
    kind: Kind
    category: str
    expected: Verdict
    rationale: str = Field(min_length=10)
    evidence_ids: list[str] = Field(default_factory=list)
    expected_checks: list[str] = Field(default_factory=list)
    expected_flags: list[str] = Field(default_factory=list)
    base: str | None = None  # reference response name (defaults to the packet name)
    patch: list[Patch] = Field(default_factory=list)
    raw: str | None = None
    claims: list[ClaimLabel] = Field(default_factory=list)

    @field_validator("category")
    @classmethod
    def _known_category(cls, v: str) -> str:
        if v not in CATEGORIES:
            raise ValueError(f"unknown category {v!r}")
        return v

    @field_validator("expected_checks")
    @classmethod
    def _known_checks(cls, v: list[str]) -> list[str]:
        bad = [c for c in v if c not in CHECK_NAMES]
        if bad:
            raise ValueError(f"unknown checks {bad}")
        return v

    @field_validator("expected_flags")
    @classmethod
    def _known_flags(cls, v: list[str]) -> list[str]:
        bad = [c for c in v if c not in FLAG_KINDS]
        if bad:
            raise ValueError(f"unknown flags {bad}")
        return v


class SanitizerCase(BaseModel):
    """Text that could appear in a packet field: should clean_text() remove it?"""

    model_config = ConfigDict(extra="forbid")

    id: str
    split: Split
    text: str
    expected: Literal["REMOVE", "KEEP"]
    rationale: str = Field(min_length=10)


class Dataset(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    packets: dict[str, EvidencePacket]
    references: dict[str, dict]
    cases: list[EvalCase]
    sanitizer: list[SanitizerCase]

    def candidate(self, case: EvalCase) -> str:
        """The raw response text this case submits to the verifier."""
        if case.raw is not None:
            return case.raw
        data = copy.deepcopy(self.references[case.base or case.packet])
        for p in case.patch:
            apply_patch(data, p)
        return json.dumps(data)


# ---------------------------------------------------------------------------
# Patches: "key_findings.1.text", "tradeoffs", "uncertainties.0"
# ---------------------------------------------------------------------------


def _step(node: Any, key: str) -> Any:
    if isinstance(node, list):
        return node[int(key)]
    return node[key]


def apply_patch(data: dict, patch: Patch) -> None:
    parts = patch.path.split(".")
    try:
        parent = data
        for key in parts[:-1]:
            parent = _step(parent, key)
        last = parts[-1]
        if patch.op == "set":
            if isinstance(parent, list):
                parent[int(last)] = patch.value
            else:
                parent[last] = patch.value
        elif patch.op == "append":
            _step(parent, last).append(patch.value)
        elif isinstance(parent, list):  # remove
            del parent[int(last)]
        else:
            del parent[last]
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        raise DatasetError(f"bad patch path {patch.path!r}: {exc}") from exc


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_packets(directory: Path = PACKETS_DIR) -> dict[str, EvidencePacket]:
    return {
        p.stem: EvidencePacket.model_validate_json(p.read_text())
        for p in sorted(directory.glob("*.json"))
    }


def _read_toml(path: Path) -> dict:
    with path.open("rb") as fh:
        return tomllib.load(fh)


def load_cases(path: Path, split: Split) -> tuple[list[EvalCase], list[SanitizerCase]]:
    data = _read_toml(path)
    cases = [EvalCase.model_validate({**c, "split": split}) for c in data.get("case", [])]
    sanitizer = [
        SanitizerCase.model_validate({**c, "split": split}) for c in data.get("sanitize", [])
    ]
    return cases, sanitizer


def load_dataset(
    splits: tuple[Split, ...] = ("dev", "heldout", "heldout2", "heldout3"),
    *,
    cases_dir: Path = CASES_DIR,
    packets_dir: Path = PACKETS_DIR,
) -> Dataset:
    packets = load_packets(packets_dir)
    references = _read_toml(cases_dir / REFERENCE_FILE.name)
    cases: list[EvalCase] = []
    sanitizer: list[SanitizerCase] = []
    for split in splits:
        if not (cases_dir / SPLIT_FILES[split].name).exists():
            continue
        c, s = load_cases(cases_dir / SPLIT_FILES[split].name, split)
        cases += c
        sanitizer += s
    ds = Dataset(packets=packets, references=references, cases=cases, sanitizer=sanitizer)
    validate_dataset(ds)
    return ds


def validate_dataset(ds: Dataset) -> None:
    """Fail loudly on unknown packets/references, duplicate IDs, or bad claim locations."""
    seen: set[str] = set()
    for case in [*ds.cases, *ds.sanitizer]:
        if case.id in seen:
            raise DatasetError(f"duplicate case id {case.id}")
        seen.add(case.id)
    for case in ds.cases:
        if case.packet not in ds.packets:
            raise DatasetError(f"{case.id}: unknown packet {case.packet!r}")
        if case.raw is None and (case.base or case.packet) not in ds.references:
            raise DatasetError(f"{case.id}: no reference response {case.base or case.packet!r}")
        unknown = [i for i in case.evidence_ids if i not in ds.packets[case.packet].ids]
        if unknown:
            raise DatasetError(f"{case.id}: relevant evidence IDs not in packet: {unknown}")
        ds.candidate(case)  # patches must apply
    for name in ds.references:
        if name not in ds.packets:
            raise DatasetError(f"reference response for unknown packet {name!r}")
