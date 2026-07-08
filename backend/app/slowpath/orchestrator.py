"""Slow-path orchestrator — wires OpenBB + personas + governor together.

This is the single entry point for triggering slow-path analysis.
It coordinates:
  1. OpenBB adapter → fetches enriched context (fundamentals, news, macro)
  2. Persona analysts → run domain-specific LLM assessments
  3. Agent governor → enforces rate limits, budgets, health monitoring
  4. Event bus → publishes any ParameterChangeProposals

Usage::
    from app.slowpath.orchestrator import slowpath
    result = await slowpath.analyze("RELIANCE.NS", headline="Q3 earnings miss")
    agents = slowpath.list_agents()
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

from app.bus.memory import MemoryBus
from app.core.clock import LiveClock
from app.core.config import llm_config
from app.slowpath.governance import AgentGovernor, AgentConfig, AgentStatus
from app.slowpath.providers import build_provider, LLMProvider

log = logging.getLogger(__name__)

# Persona agent IDs
_PERSONA_IDS = {
    "fundamentals": "fundamentals-analyst",
    "sentiment": "sentiment-analyst",
    "technical": "technical-analyst",
    "macro": "macro-analyst",
}


class SlowPathOrchestrator:
    """Top-level coordinator for the slow-path intelligence plane."""

    def __init__(self) -> None:
        self._bus = MemoryBus(LiveClock())
        self._governor = AgentGovernor()
        self._provider: Optional[LLMProvider] = None
        self._analysts: Dict[str, Any] = {}
        self._initialized = False

    def initialize(self) -> Dict[str, str]:
        """Build the LLM provider and register all persona agents.
        Safe to call multiple times (idempotent)."""
        if self._initialized:
            return {"status": "already_initialized",
                    "provider": self._provider.name if self._provider else "none"}

        from app.slowpath.personas import (
            FundamentalsAnalyst, SentimentAnalyst,
            TechnicalAnalyst, MacroAnalyst,
        )

        # Build provider from env config
        cfg = llm_config()
        self._provider = build_provider(cfg)
        provider_name = self._provider.name

        log.info("Slow-path initializing with provider: %s (model: %s)",
                 provider_name, self._provider.model)

        # Create analyst instances
        persona_classes = {
            "fundamentals": FundamentalsAnalyst,
            "sentiment": SentimentAnalyst,
            "technical": TechnicalAnalyst,
            "macro": MacroAnalyst,
        }

        for key, cls in persona_classes.items():
            agent_id = _PERSONA_IDS[key]
            analyst = cls(self._bus, self._bus._clock, self._provider)
            self._analysts[key] = analyst

            # Register with governor
            self._governor.register(agent_id, key, config=AgentConfig(
                max_errors_before_pause=5,
                max_invocations_per_hour=60,
                max_token_budget=500_000,
                cooldown_after_error_s=30.0,
            ))

        self._initialized = True
        log.info("Slow-path initialized: 4 persona analysts registered (%s)", provider_name)
        return {"status": "initialized", "provider": provider_name,
                "model": self._provider.model,
                "agents": list(_PERSONA_IDS.values())}

    async def analyze(
        self,
        symbol: str,
        headline: Optional[str] = None,
        personas: Optional[List[str]] = None,
        include_openbb: bool = True,
    ) -> Dict[str, Any]:
        """Run one or more persona analysts on a symbol.

        Args:
            symbol: Stock ticker (e.g. "RELIANCE", "AAPL")
            headline: Optional news/event to analyze. If not provided,
                      a generic "Current market analysis" prompt is used.
            personas: Which analysts to run. Default: all four.
            include_openbb: Whether to fetch enriched context from OpenBB.

        Returns:
            Dict with assessments, proposals, and enrichment context.
        """
        if not self._initialized:
            self.initialize()

        headline = headline or f"Provide a current market analysis for {symbol}"
        run_personas = personas or ["fundamentals", "sentiment", "technical", "macro"]
        results: Dict[str, Any] = {
            "symbol": symbol,
            "headline": headline,
            "assessments": {},
            "proposals": [],
            "openbb_context": {},
            "provider": self._provider.name if self._provider else "stub",
            "model": self._provider.model if self._provider else "stub",
        }

        # Step 1: Fetch OpenBB enrichment (if available)
        if include_openbb:
            try:
                from app.services.openbb_adapter import openbb_data
                ctx = await openbb_data.analyst_context(
                    symbol,
                    include_news="sentiment" in run_personas,
                    include_fundamentals="fundamentals" in run_personas,
                    include_macro="macro" in run_personas,
                )
                results["openbb_context"] = ctx
                # Enrich headline with OpenBB data if we got anything
                enrichments = []
                if ctx.get("profile"):
                    p = ctx["profile"]
                    sector = p.get("sector", "")
                    mktcap = p.get("market_cap", "")
                    if sector:
                        enrichments.append(f"Sector: {sector}")
                    if mktcap:
                        enrichments.append(f"Market Cap: {mktcap}")
                if ctx.get("metrics"):
                    m = ctx["metrics"]
                    pe = m.get("pe_ratio", m.get("pe_ttm", ""))
                    if pe:
                        enrichments.append(f"P/E: {pe}")
                if ctx.get("recent_news") and len(ctx["recent_news"]) > 0:
                    top_news = ctx["recent_news"][0]
                    title = top_news.get("title", "")
                    if title:
                        enrichments.append(f"Latest: {title[:100]}")
                if enrichments:
                    headline = f"{headline}. Context: {'; '.join(enrichments)}"

            except Exception as exc:
                log.debug("OpenBB enrichment skipped: %s", exc)

        # Step 2: Run each requested persona through the governor
        for persona_key in run_personas:
            agent_id = _PERSONA_IDS.get(persona_key)
            analyst = self._analysts.get(persona_key)
            if not agent_id or not analyst:
                results["assessments"][persona_key] = {
                    "status": "not_found", "error": f"Unknown persona: {persona_key}"
                }
                continue

            if not self._governor.can_invoke(agent_id):
                agent = self._governor.get_agent(agent_id)
                results["assessments"][persona_key] = {
                    "status": "blocked",
                    "reason": f"Agent {agent.status.value}" if agent else "unknown",
                }
                continue

            # Run the analyst
            start = time.monotonic()
            try:
                assessment = await asyncio.to_thread(
                    analyst.assess, headline, [symbol]
                )
                elapsed_ms = (time.monotonic() - start) * 1000
                est_tokens = max(len(headline) // 4, 50) + 200  # rough estimate

                self._governor.record_invocation(
                    agent_id, latency_ms=elapsed_ms,
                    tokens=est_tokens, error=False,
                )

                results["assessments"][persona_key] = {
                    "status": "ok",
                    "assessment": assessment,
                    "latency_ms": round(elapsed_ms, 1),
                }

                # Generate proposal if bearish/bullish
                direction = str(assessment.get("direction", "")).lower()
                if direction in ("bearish", "bullish"):
                    try:
                        proposal = analyst.assess_and_propose(headline, [symbol])
                        if proposal:
                            results["proposals"].append({
                                "persona": persona_key,
                                "parameter": proposal.parameter,
                                "proposed_value": proposal.proposed_value,
                                "direction": direction,
                                "severity": assessment.get("severity"),
                                "rationale": proposal.rationale,
                                "auto_applies": direction == "bearish",
                            })
                    except Exception as exc:
                        log.debug("Proposal generation failed for %s: %s", persona_key, exc)

            except Exception as exc:
                elapsed_ms = (time.monotonic() - start) * 1000
                self._governor.record_invocation(
                    agent_id, latency_ms=elapsed_ms, error=True,
                )
                results["assessments"][persona_key] = {
                    "status": "error",
                    "error": str(exc),
                    "latency_ms": round(elapsed_ms, 1),
                }

        return results

    def list_agents(self, status: Optional[str] = None) -> List[Dict]:
        """List all registered agents with their metrics."""
        filter_status = AgentStatus(status) if status else None
        return [a.to_dict() for a in self._governor.list_agents(status=filter_status)]

    def agent_dashboard(self) -> Dict:
        """Full governance dashboard summary."""
        summary = self._governor.dashboard_summary()
        summary["provider"] = self._provider.name if self._provider else "not_initialized"
        summary["model"] = self._provider.model if self._provider else ""
        summary["initialized"] = self._initialized
        return summary

    def pause_agent(self, agent_id: str, reason: str = "manual") -> bool:
        return self._governor.pause(agent_id, reason=reason)

    def resume_agent(self, agent_id: str) -> bool:
        return self._governor.resume(agent_id)

    def reset_agent_metrics(self, agent_id: str) -> bool:
        return self._governor.reset_metrics(agent_id)


# Module-level singleton
slowpath = SlowPathOrchestrator()
