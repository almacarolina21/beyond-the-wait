"""Evidence packets: the ONLY information Claude is allowed to see.

Python calculates. Claude explains. Humans decide.

An evidence packet is a compact, typed, deterministic summary of ONE
validated comparison (one hypothetical change vs. the fictional baseline)
taken from the fingerprint-verified results bundle. Every fact has a stable
ID (for example ``M:los_median_admitted`` or ``CK:overload_regime``) so that
Claude can cite it and Python can check the citation.

Rules enforced here:
  * Only a bundle that ``load_bundle()`` verified (status "ok") is accepted.
  * A BLOCKED comparison never produces a packet (``EvidenceRefused``).
  * Every number is copied from the bundle and rounded by Python; nothing is
    computed from Claude's output and nothing is typed in by hand.
  * No individual synthetic patient records, no real hospital data, no
    personal data, and no secrets are ever included.
  * Free text that came from files (scenario names, check details) is
    cleaned and length-limited; text that looks like an instruction is
    removed, because packet text is data, never instructions.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from beyond_the_wait.analysis.check_descriptions import check_title, describe_check
from beyond_the_wait.analysis.experiments import MODEL_EVIDENCE_NOTE
from beyond_the_wait.analysis.results_bundle import BundleLoad
from beyond_the_wait.analysis.validation import SAFETY_LIMITATIONS

PACKET_VERSION = 1
SITE_NAME = "Riverside Commons Community ED (fictional)"
MAX_TEXT_CHARS = 400
MAX_FACTS = 60
MAX_PACKET_CHARS = 24_000

# The outcomes a packet describes, in display order, with a plain definition.
# Labels come from the bundle; definitions are written once here.
METRIC_DEFINITIONS: dict[str, str] = {
    "arrival_to_provider_median": "Median time from arrival until a physician first sees the "
    "patient, among patients seen during the run.",
    "arrival_to_provider_p90": "90th percentile of time from arrival until a physician first "
    "sees the patient: 9 in 10 patients were seen within this time.",
    "triage_wait_p90": "90th percentile of the wait from arrival until triage starts.",
    "provider_wait_p90": "90th percentile of the wait from being in a treatment space until a "
    "physician is free.",
    "los_median_discharged": "Median ED length of stay for patients who went home.",
    "los_median_admitted": "Median ED length of stay for patients admitted to the hospital, "
    "including boarding.",
    "los_median_lower_bound_all": "Median ED length of stay for all patients; unfinished visits "
    "count with their time so far, so this is a lower bound.",
    "boarding_p90": "90th percentile of boarding: the wait in the ED after the decision to admit "
    "until an inpatient bed opens.",
    "boarding_share_over_4h": "Share of admitted patients whose boarding lasted more than 4 hours.",
    "physician_utilization": "Share of physician evaluation slots in use. Workload, not quality.",
    "triage_utilization": "Share of triage-nurse time in use. Workload, not quality.",
    "stage_triage_wait_mean": "Average minutes per patient spent waiting for triage.",
    "stage_space_wait_mean": "Average minutes per patient spent waiting for a treatment space.",
    "stage_provider_wait_mean": "Average minutes per patient spent waiting for a physician.",
    "stage_results_mean": "Average minutes per patient spent waiting for lab or imaging results.",
    "stage_boarding_mean": "Average boarding minutes per patient (admitted patients only).",
}
METRICS = tuple(METRIC_DEFINITIONS)

_INJECTION = re.compile(
    r"ignore (all |any |the )?(previous|prior|above|earlier)|disregard|system prompt|"
    r"you are now|new instructions|</?\s*(system|assistant|instructions?|evidence_packet)\b|"
    r"\bassistant\s*:|\buser\s*:|jailbreak",
    re.IGNORECASE,
)
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


class EvidenceRefused(ValueError):
    """Raised when no packet may be built (missing/invalid bundle, BLOCKED, unknown)."""


def clean_text(text: object, max_chars: int = MAX_TEXT_CHARS) -> str:
    """Make untrusted file text safe to place in a packet as DATA."""
    s = _CONTROL.sub(" ", str(text))
    s = re.sub(r"\s+", " ", s).strip()
    if _INJECTION.search(s):
        return "[text removed: it looked like an instruction, not data]"
    if len(s) > max_chars:
        s = s[: max_chars - 1].rstrip() + "…"
    return s


# ---------------------------------------------------------------------------
# Typed packet
# ---------------------------------------------------------------------------


class MetricFact(BaseModel):
    """One paired comparison of one outcome. All numbers are display-rounded by Python."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    kind: Literal["paired_comparison"] = "paired_comparison"
    metric: str
    label: str
    definition: str
    is_focus: bool
    level_unit: str  # unit of baseline / intervention values ("min" or "%")
    change_unit: str  # unit of the difference and its CI ("min" or "percentage points")
    baseline: float
    intervention: float
    difference: float  # intervention minus baseline
    ci95_low: float
    ci95_high: float
    n_pairs: int
    direction: Literal[
        "lower in the model",
        "higher in the model",
        "no detectable change (95% CI includes 0)",
        "identical in both arms",
    ]
    provenance: Literal["SIMULATED"] = "SIMULATED"


class TextFact(BaseModel):
    """A non-numeric fact: status, a caution, a limitation, a sensitivity verdict."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    kind: Literal["status", "caution", "limitation", "sensitivity", "design", "note"]
    text: str
    provenance: Literal["SIMULATED"] = "SIMULATED"


Fact = Annotated[MetricFact | TextFact, Field(discriminator="kind")]


class EvidencePacket(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    packet_version: Literal[1] = PACKET_VERSION
    packet_id: str
    provenance: Literal["SIMULATED"] = "SIMULATED"
    site: str = SITE_NAME
    scenario_id: str
    scenario_name: str
    baseline_name: str = "Baseline"
    focus_metric: str
    what_changes: list[str]
    n_pairs: int
    validation_status: Literal["PASS", "CAUTION"]
    bundle_fingerprint: str
    facts: list[Fact]

    def fact(self, fact_id: str) -> MetricFact | TextFact | None:
        return next((f for f in self.facts if f.id == fact_id), None)

    @property
    def ids(self) -> set[str]:
        return {f.id for f in self.facts}

    @property
    def metric_facts(self) -> list[MetricFact]:
        return [f for f in self.facts if isinstance(f, MetricFact)]

    def to_prompt_json(self) -> str:
        """Compact JSON sent to Claude (stable key order)."""
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def _r(x: float) -> float:
    """Display rounding: 1 decimal, or 2 below 1 so tiny effects don't read as 0.0."""
    x = float(x)
    v = round(x, 2) if abs(x) < 1 else round(x, 1)
    return 0.0 if v == 0 else v  # never "-0.0"


def _direction(direction: str, diff: float) -> str:
    if direction == "no_change":
        return "identical in both arms"
    if direction in ("improves", "worsens", "changes"):
        return "lower in the model" if diff < 0 else "higher in the model"
    return "no detectable change (95% CI includes 0)"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _bundle_fingerprint(manifest: dict) -> str:
    shas = sorted(f"{k}:{v.get('sha256')}" for k, v in manifest.get("files", {}).items())
    return hashlib.sha256("|".join(shas).encode()).hexdigest()[:16]


LIMITATION_IDS = {f"LIM:{_slug(t.split(':', 1)[0])}": t for t in SAFETY_LIMITATIONS}

VERDICTS = {
    "unstable": "depends on the uncertain assumptions (direction changes across settings)",
    "reverses": "reverses direction across settings",
    "no_support": "shows no detectable change in any setting",
    "stable_improves": "is lower in the model in every setting",
    "stable_worsens": "is higher in the model in every setting",
}


def build_evidence_packet(
    load: BundleLoad,
    *,
    scenario_id: str,
    metric: str,
    scenario_name: str | None = None,
) -> EvidencePacket:
    """Build the packet for one main comparison. Refuses anything not verified/valid."""
    if load.status != "ok" or load.bundle is None:
        raise EvidenceRefused(f"results bundle is not verified (status {load.status!r})")
    bundle = load.bundle
    if metric not in METRIC_DEFINITIONS:
        raise EvidenceRefused(f"unknown outcome {metric!r}")
    if any(b["scope"] == "main" and b["scenario_id"] == scenario_id for b in bundle.blocked):
        raise EvidenceRefused(f"{scenario_id} is BLOCKED by the validation gate")
    meta = next(
        (
            c
            for c in bundle.manifest.get("comparisons", [])
            if c["scope"] == "main" and c["scenario_id"] == scenario_id
        ),
        None,
    )
    if meta is None:
        raise EvidenceRefused(f"no comparison for {scenario_id!r}")
    if meta["status"] not in ("PASS", "CAUTION"):
        raise EvidenceRefused(f"{scenario_id} has status {meta['status']}; no packet")

    rows = bundle.comparisons[
        (bundle.comparisons["scope"] == "main") & (bundle.comparisons["scenario_id"] == scenario_id)
    ]
    if rows.empty:
        raise EvidenceRefused(f"no verified rows for {scenario_id!r}")
    if (rows["comparison_status"] == "BLOCKED").any() or (rows["provenance"] != "SIMULATED").any():
        raise EvidenceRefused("rows are BLOCKED or not labeled SIMULATED")  # defense in depth
    by_metric = {r.metric: r for r in rows.itertuples()}
    if metric not in by_metric:
        raise EvidenceRefused(f"outcome {metric!r} is not available for {scenario_id!r}")

    facts: list[MetricFact | TextFact] = []
    n_pairs = int(by_metric[metric].n_pairs)
    order = [metric] + [m for m in METRICS if m != metric]
    for m in order:
        r = by_metric.get(m)
        if r is None:
            continue
        values = [r.baseline_mean, r.scenario_mean, r.mean_difference, r.ci95_low, r.ci95_high]
        if any(not math.isfinite(float(v)) for v in values):
            continue  # an outcome the model could not estimate is left out, never invented
        frac = r.unit == "fraction"
        scale = 100.0 if frac else 1.0
        facts.append(
            MetricFact(
                id=f"M:{m}",
                metric=m,
                label=clean_text(r.label, 120),
                definition=METRIC_DEFINITIONS[m],
                is_focus=m == metric,
                level_unit="%" if frac else str(r.unit),
                change_unit="percentage points" if frac else str(r.unit),
                baseline=_r(r.baseline_mean * scale),
                intervention=_r(r.scenario_mean * scale),
                difference=_r(r.mean_difference * scale),
                ci95_low=_r(r.ci95_low * scale),
                ci95_high=_r(r.ci95_high * scale),
                n_pairs=int(r.n_pairs),
                direction=_direction(str(r.direction), float(r.mean_difference)),
            )
        )

    status = meta["status"]
    facts.append(
        TextFact(
            id="ST:status",
            kind="status",
            text=(
                f"Validation status {status}. The numbers passed the integrity checks "
                "(internally consistent); CAUTION means they can be interpreted only with the "
                "cautions below. No status, including PASS, is a clinical or real-world validation."
            ),
        )
    )
    checks = bundle.checks[
        (bundle.checks["scope"] == "main")
        & (bundle.checks["scenario_id"] == scenario_id)
        & (bundle.checks["status"] != "PASS")
    ]
    for c in checks.drop_duplicates("check").itertuples():
        facts.append(
            TextFact(
                id=f"CK:{_slug(str(c.check))}",
                kind="caution",
                text=f"{c.status} {describe_check(str(c.check))} Detail: {clean_text(c.detail)}",
            )
        )
    for code, count in sorted(meta.get("carried_cautions", {}).items()):
        facts.append(
            TextFact(
                id=f"CK:week_{_slug(code)}",
                kind="caution",
                text=f"{check_title(code)} (in {count} simulated weeks across both arms): "
                f"{describe_check(code).split(': ', 1)[-1]}",
            )
        )

    if bundle.stability is not None:
        stab = bundle.stability[bundle.stability["intervention"] == scenario_id]
        for s in stab.itertuples():
            label = by_metric[s.metric].label if s.metric in by_metric else s.metric
            facts.append(
                TextFact(
                    id=f"SENS:{_slug(str(s.metric))}",
                    kind="sensitivity",
                    text=(
                        f"Sensitivity: across {int(s.settings)} operating settings of the two "
                        "uncertain assumptions (physician concurrency, bed releases per day), "
                        f"{clean_text(label, 120)} "
                        f"{VERDICTS.get(str(s.verdict), clean_text(s.verdict))}."
                    ),
                )
            )

    settings = bundle.manifest.get("settings", {})
    facts.append(
        TextFact(
            id="RUN:design",
            kind="design",
            text=(
                f"Each arm was simulated on the same {n_pairs} random seeds (paired weeks of "
                f"{settings.get('arrival_days')} arrival days, {settings.get('warmup_days')} "
                "warm-up day). Pairing removes much, but not all, of the week-to-week random "
                "variation from the comparison; the 95% confidence interval describes the "
                "remaining uncertainty about the average change across simulated weeks."
            ),
        )
    )
    facts.append(TextFact(id="NOTE:model_evidence", kind="note", text=MODEL_EVIDENCE_NOTE))
    for fid, text in LIMITATION_IDS.items():
        facts.append(TextFact(id=fid, kind="limitation", text=text))

    if len(facts) > MAX_FACTS:  # pragma: no cover - bounded by construction
        raise EvidenceRefused("packet exceeds the maximum number of facts")
    body = {
        "scenario_id": scenario_id,
        "metric": metric,
        "facts": [f.model_dump(mode="json") for f in facts],
        "bundle": _bundle_fingerprint(bundle.manifest),
    }
    packet_id = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:12]
    packet = EvidencePacket(
        packet_id=packet_id,
        scenario_id=scenario_id,
        scenario_name=clean_text(scenario_name or scenario_id, 120),
        focus_metric=metric,
        what_changes=[clean_text(x, 160) for x in meta.get("effects", {}).get("simulated", [])],
        n_pairs=n_pairs,
        validation_status=status,
        bundle_fingerprint=_bundle_fingerprint(bundle.manifest),
        facts=facts,
    )
    if len(packet.to_prompt_json()) > MAX_PACKET_CHARS:
        raise EvidenceRefused("packet is larger than the input limit")
    return packet
