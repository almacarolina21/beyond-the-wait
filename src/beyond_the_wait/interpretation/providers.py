"""Interpretation providers: MOCK (default, free, offline) and LIVE (Claude API).

Both return raw JSON text that goes through exactly the same verification.

Live calls are opt-in twice: the environment must set BTW_AI_MODE=live AND an
API key must be found (ANTHROPIC_API_KEY in the environment, a local
git-ignored .env file, or Streamlit secrets). Having a key alone never turns
live mode on, so a key set for another tool cannot trigger billable calls.
The key is never logged, displayed, or stored by this module.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from beyond_the_wait.interpretation.evidence import EvidencePacket, MetricFact
from beyond_the_wait.interpretation.prompt import SYSTEM_PROMPT, user_message
from beyond_the_wait.interpretation.schema import Interpretation

Mode = Literal["MOCK", "LIVE"]
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MODEL = "claude-opus-5-5"
MOCK_MODEL = "mock-interpreter-v1 (demonstration, not Claude)"
MAX_INPUT_CHARS = 30_000  # packet + prompt; checked before any request is sent
MAX_OUTPUT_TOKENS = 8_000  # thinking + JSON; bounds the cost of one explanation
TIMEOUT_SECONDS = 120.0
MAX_RETRIES = 2  # SDK retries 429/5xx/connection errors with backoff; then we stop

ErrorKind = Literal[
    "missing_key",
    "authentication",
    "permission",
    "rate_limit",
    "timeout",
    "connection",
    "overloaded",
    "bad_request",
    "api_error",
    "refusal",
    "truncated",
    "empty",
    "input_too_large",
]

ERROR_MESSAGES: dict[str, str] = {
    "missing_key": "No Anthropic API key was found, so live explanations are unavailable.",
    "authentication": "The API key was rejected. Check the key in your local settings.",
    "permission": "The API key is not allowed to use this model.",
    "rate_limit": "The Claude API rate limit was reached. Wait a minute and try again.",
    "timeout": "Claude did not answer in time. Try again later.",
    "connection": "Could not reach the Claude API (network problem).",
    "overloaded": "The Claude API is temporarily overloaded. Try again later.",
    "bad_request": "The request was not accepted by the Claude API.",
    "api_error": "The Claude API returned an error.",
    "refusal": "Claude declined to answer this request.",
    "truncated": "Claude's answer was cut off before it was complete, so it was discarded.",
    "empty": "Claude returned no usable text.",
    "input_too_large": "The evidence packet is larger than the input limit; nothing was sent.",
}


class InterpretationError(RuntimeError):
    def __init__(self, kind: ErrorKind, detail: str = "") -> None:
        super().__init__(ERROR_MESSAGES[kind] + (f" ({detail})" if detail else ""))
        self.kind = kind
        self.user_message = ERROR_MESSAGES[kind]


@dataclass(frozen=True)
class ProviderResponse:
    text: str
    mode: Mode
    model: str
    stop_reason: str | None = None
    usage: dict[str, int] = field(default_factory=dict)


class Provider(Protocol):
    mode: Mode
    model: str

    def interpret(self, packet: EvidencePacket) -> ProviderResponse: ...  # pragma: no cover


# ---------------------------------------------------------------------------
# MOCK: deterministic, clearly labeled demonstration output
# ---------------------------------------------------------------------------


class MockProvider:
    """Builds a schema-valid demonstration response from the packet. No network."""

    mode: Mode = "MOCK"
    model: str = MOCK_MODEL

    def interpret(self, packet: EvidencePacket) -> ProviderResponse:
        return ProviderResponse(
            text=json.dumps(mock_interpretation(packet).model_dump()),
            mode="MOCK",
            model=self.model,
            stop_reason="end_turn",
        )


def mock_interpretation(packet: EvidencePacket) -> Interpretation:
    focus = packet.fact(f"M:{packet.focus_metric}")
    assert isinstance(focus, MetricFact)
    fid = focus.id
    others = [
        f
        for f in packet.metric_facts
        if f.id != fid and f.direction.startswith(("lower", "higher"))
    ]
    stages = [f for f in others if f.metric.startswith("stage_")]
    lower = [f for f in stages if f.direction.startswith("lower")]
    higher = [f for f in stages if f.direction.startswith("higher")]
    cautions = [f.id for f in packet.facts if f.id.startswith("CK:")][:3]
    # The focus outcome's own sensitivity verdict first: omitting an unstable verdict
    # for the focus would hide the most important uncertainty.
    sens_ids = [f.id for f in packet.facts if f.id.startswith("SENS:")]
    focus_sens = f"SENS:{packet.focus_metric}"
    sens = ([focus_sens] if focus_sens in sens_ids else []) + [
        i for i in sens_ids if i != focus_sens
    ][: 2 - (focus_sens in sens_ids)]

    findings = [
        {
            "text": f"{focus.label}: {{{{{fid}.change}}}}. Baseline {{{{{fid}.baseline}}}}, "
            f"with the change {{{{{fid}.intervention}}}}.",
            "evidence_ids": [fid, "RUN:design"],
        }
    ]
    for f in others[:2]:
        findings.append({"text": f"{f.label}: {{{{{f.id}.change}}}}.", "evidence_ids": [f.id]})
    tradeoffs = []
    if lower and higher:
        a, b = lower[0], higher[0]
        tradeoffs.append(
            {
                "text": f"Inside the model, waiting moved between stages: {a.label} was "
                f"{{{{{a.id}.change}}}}, while {b.label} was {{{{{b.id}.change}}}}.",
                "evidence_ids": [a.id, b.id],
            }
        )
    uncertainties = [
        {
            "text": f"Validation status is {packet.validation_status}; the cautions listed "
            "for this comparison apply to every number here.",
            "evidence_ids": ["ST:status", *cautions],
        },
        {
            "text": "The interval describes the average change across paired simulated weeks; "
            "a single simulated week can differ more.",
            "evidence_ids": ["RUN:design"],
        },
    ]
    if sens:
        uncertainties.append(
            {
                "text": "Some conclusions were re-tested under other values of the two "
                "uncertain assumptions; see the sensitivity verdicts.",
                "evidence_ids": sens,
            }
        )
    text = (
        f"DEMONSTRATION (mock output, not written by Claude). In the fictional model, "
        f"'{packet.scenario_name}' compared with the baseline: {focus.label} was "
        f"{{{{{fid}.change}}}}. Validation status: {packet.validation_status}."
    )
    return Interpretation(
        summary=text,
        key_findings=findings,
        tradeoffs=tradeoffs,
        uncertainties=uncertainties,
        questions_for_humans=[
            "Which real, local data would be needed to check whether a similar pattern exists?",
            "Which assumptions in the fictional model differ most from the setting being "
            "considered?",
        ],
        limitations=[
            {
                "text": "These are fictional model results under illustrative assumptions, "
                "not evidence about any real ED.",
                "evidence_ids": ["LIM:fictional_model", "NOTE:model_evidence"],
            },
            {
                "text": "No simulated result establishes that a staffing or capacity change "
                "would be appropriate in a real ED.",
                "evidence_ids": ["LIM:not_a_staffing_recommendation"],
            },
        ],
        disclaimer="Demonstration output for a fictional simulation. Decision support for "
        "human review only; not a recommendation or clinical guidance.",
    )


# ---------------------------------------------------------------------------
# LIVE: Claude via the official Anthropic SDK (Messages API, structured output)
# ---------------------------------------------------------------------------


def response_schema() -> dict:
    """JSON schema for output_config.format, prepared by the SDK's transform_schema()."""
    from anthropic import transform_schema

    return transform_schema(Interpretation)


class ClaudeProvider:
    """One explicit request per call. Never constructed during page load or tests."""

    mode: Mode = "LIVE"

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        *,
        client: object | None = None,
        timeout: float = TIMEOUT_SECONDS,
        max_retries: int = MAX_RETRIES,
        max_tokens: int = MAX_OUTPUT_TOKENS,
    ) -> None:
        if not api_key:
            raise InterpretationError("missing_key")
        self.model = model
        self.max_tokens = max_tokens
        if client is None:
            import anthropic

            client = anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=max_retries)
        self._client = client

    def interpret(self, packet: EvidencePacket) -> ProviderResponse:
        import anthropic

        message = user_message(packet)
        if len(SYSTEM_PROMPT) + len(message) > MAX_INPUT_CHARS:
            raise InterpretationError("input_too_large")
        try:
            response = self._client.messages.create(  # type: ignore[attr-defined]
                model=self.model,
                max_tokens=self.max_tokens,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": message}],
                output_config={"format": {"type": "json_schema", "schema": response_schema()}},
            )
        except anthropic.AuthenticationError as exc:
            raise InterpretationError("authentication") from exc
        except anthropic.PermissionDeniedError as exc:
            raise InterpretationError("permission") from exc
        except anthropic.RateLimitError as exc:
            raise InterpretationError("rate_limit") from exc
        except anthropic.APITimeoutError as exc:  # subclass of APIConnectionError: first
            raise InterpretationError("timeout") from exc
        except anthropic.APIConnectionError as exc:
            raise InterpretationError("connection") from exc
        except anthropic.BadRequestError as exc:
            raise InterpretationError("bad_request") from exc
        except anthropic.APIStatusError as exc:
            kind: ErrorKind = "overloaded" if exc.status_code in (503, 529) else "api_error"
            raise InterpretationError(kind, f"HTTP {exc.status_code}") from exc

        stop = getattr(response, "stop_reason", None)
        if stop == "refusal":
            raise InterpretationError("refusal")
        if stop == "max_tokens":
            raise InterpretationError("truncated")
        text = "".join(
            getattr(b, "text", "") for b in response.content if getattr(b, "type", "") == "text"
        ).strip()
        if not text:
            raise InterpretationError("empty")
        usage = getattr(response, "usage", None)
        return ProviderResponse(
            text=text,
            mode="LIVE",
            model=str(getattr(response, "model", self.model)),
            stop_reason=stop,
            usage={
                "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
                "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
            },
        )


# ---------------------------------------------------------------------------
# Configuration (environment only; the key is never returned to callers)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AIConfig:
    requested: Literal["mock", "live"]
    model: str
    has_key: bool

    @property
    def mode(self) -> Mode:
        return "LIVE" if self.requested == "live" and self.has_key else "MOCK"

    @property
    def note(self) -> str:
        if self.requested == "live" and not self.has_key:
            return (
                "Live mode was requested, but no API key was found. Showing DEMO "
                "(mock) explanations instead."
            )
        if self.mode == "MOCK":
            return (
                "DEMO mode: explanations come from a built-in mock, not from Claude. No API "
                "calls are made and no key is needed."
            )
        return f"LIVE mode: each click sends one request to the Claude API ({self.model})."


def _read_key(secrets: object | None = None) -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        try:
            from dotenv import dotenv_values

            key = str(dotenv_values(REPO_ROOT / ".env").get("ANTHROPIC_API_KEY") or "")
        except Exception:  # pragma: no cover - a missing/unreadable .env just means "no key"
            key = ""
    if not key and secrets is not None:
        try:
            key = str(secrets["ANTHROPIC_API_KEY"])  # type: ignore[index]
        except Exception:
            key = ""
    return key.strip()


def ai_config(secrets: object | None = None) -> AIConfig:
    requested = (
        "live" if os.environ.get("BTW_AI_MODE", "mock").strip().lower() == "live" else "mock"
    )
    has_key = requested == "live" and bool(_read_key(secrets))  # only look when asked
    return AIConfig(requested, os.environ.get("BTW_CLAUDE_MODEL", DEFAULT_MODEL), has_key)


def make_provider(config: AIConfig, secrets: object | None = None) -> Provider:
    """Create the provider for an explicit button click (never at page load)."""
    if config.mode == "LIVE":
        return ClaudeProvider(_read_key(secrets), config.model)
    return MockProvider()
