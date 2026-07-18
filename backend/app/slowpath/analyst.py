"""LLM news/macro analyst (slow path), provider-agnostic.

A configurable LLM (any provider in slowpath/providers.py -- OpenAI, Anthropic,
Gemini, Groq, local Llama/Ollama, ...) reads a news or macro item and returns a
structured EventImpactAssessment, which is mapped to a bounded
ParameterChangeProposal. The model is the *source* of a proposal; it is fully
contained by the ParameterController -- a bearish/high-severity read proposes a
TIGHTENING (auto-applies, fail-safe), while a bullish read proposes a LOOSENING
(held for human approval). The model can never emit an order or set a value
directly (docs/ARCHITECTURE.md sections 5-6).

The model choice is pure configuration (which provider, which model) -- nothing
here is wired to a specific vendor. This call is non-deterministic and external,
so it is OFF the replay path: it runs only when explicitly invoked (a news
event, a schedule), never inside the deterministic bus dispatch.
"""
from __future__ import annotations

from typing import List, Optional

from app.bus.base import EventBus
from app.core.clock import Clock
from app.core.events import ParameterChangeProposal, Streams
from app.slowpath.base import SlowPathAgent
from app.slowpath.providers import LLMConfig, LLMProvider, build_provider

# Structured-output schema (the supported JSON-schema subset; also embedded in
# the prompt for providers without native schema enforcement).
ASSESSMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "direction": {"type": "string", "enum": ["bullish", "bearish", "neutral"]},
        "severity": {"type": "string", "enum": ["low", "medium", "high"]},
        "confidence": {"type": "number"},
        "affected": {"type": "array", "items": {"type": "string"}},
        "rationale": {"type": "string"},
    },
    "required": ["direction", "severity", "confidence", "affected", "rationale"],
    "additionalProperties": False,
}

_SYSTEM = (
    "You are a market-risk analyst for an automated trading platform. Read the "
    "news item and assess its near-term impact on the named instruments. You do "
    "NOT place trades; your assessment only adjusts bounded risk parameters. Be "
    "conservative: when uncertain, prefer a bearish/high-severity read, since "
    "that only tightens risk."
)

# Map a bearish assessment to how hard to tighten gross exposure.
_SEVERITY_FACTOR = {"low": 0.85, "medium": 0.6, "high": 0.4}


class LLMAnalyst(SlowPathAgent):
    def __init__(
        self,
        bus: EventBus,
        clock: Clock,
        provider: LLMProvider,
        baseline_gross: float = 2_000_000.0,
        source: Optional[str] = None,
        ttl_s: int = 21_600,
    ) -> None:
        super().__init__(bus)
        self._clock = clock
        self._provider = provider
        self._baseline_gross = baseline_gross
        # Tag proposals with the model that produced them (auditability).
        self._source = source or f"llm:{provider.name}:{provider.model}"
        self._ttl_s = ttl_s

    @classmethod
    def from_config(
        cls,
        bus: EventBus,
        clock: Clock,
        config: LLMConfig,
        baseline_gross: float = 2_000_000.0,
        ttl_s: int = 21_600,
    ) -> "LLMAnalyst":
        """Build an analyst whose model is chosen entirely by config/env."""
        return cls(bus, clock, build_provider(config), baseline_gross=baseline_gross,
                   ttl_s=ttl_s)

    @property
    def provider_name(self) -> str:
        return self._provider.name

    def assess(self, headline: str, symbols: List[str]) -> dict:
        """Ask the configured model for a structured impact assessment.
        Non-deterministic; never call inside the deterministic bus loop."""
        prompt = (
            f"News item: {headline}\n"
            f"Instruments in scope: {', '.join(symbols) or 'broad market'}\n"
            "Return your impact assessment as a JSON object with EXACTLY these "
            "keys: direction (one of: bullish, bearish, neutral), severity "
            '(one of: low, medium, high), confidence (0.0-1.0), affected '
            '(list of symbols), rationale (one sentence). Example: '
            '{"direction": "bearish", "severity": "medium", "confidence": 0.7, '
            '"affected": ["HDFCBANK"], "rationale": "Rate hikes compress bank margins."}'
        )
        return self._provider.assess(_SYSTEM, prompt, ASSESSMENT_SCHEMA)

    def assess_and_propose(
        self, headline: str, symbols: List[str]
    ) -> Optional[ParameterChangeProposal]:
        """Assess a headline and, if it warrants a risk change, publish a
        bounded proposal. Bearish -> tighten (auto); bullish -> loosen (held).
        Returns the proposal (or None for a neutral/invalid read)."""
        assessment = self.assess(headline, symbols)
        direction = str(assessment.get("direction", "")).lower()
        severity = str(assessment.get("severity", "low")).lower()
        if direction not in ("bullish", "bearish"):
            # neutral, missing, or malformed: a model that cannot produce a
            # valid assessment must change NOTHING (fail to no-op, never to
            # a directional bet).
            return None
        if direction == "bearish":
            proposed = self._baseline_gross * _SEVERITY_FACTOR.get(severity, 0.85)
        else:  # bullish -> propose loosening above baseline (controller holds it)
            proposed = self._baseline_gross * 1.1
        now = self._clock.now_ns()
        proposal = ParameterChangeProposal(
            proposal_id=f"{self._source}:risk.max_gross_exposure:{now}",
            parameter="risk.max_gross_exposure",
            proposed_value=proposed,
            source=self._source,
            ttl_s=self._ttl_s,
            rationale=str(assessment.get("rationale", ""))[:500],
            evidence=[f"direction={direction}", f"severity={severity}",
                      f"confidence={assessment.get('confidence')}",
                      f"model={self._provider.name}:{self._provider.model}"],
        )
        self._bus.publish(Streams.CTL_PARAM_PROPOSALS, proposal, ts_event=now)
        return proposal
