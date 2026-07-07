"""Enhanced analyst personas — TradingAgents-inspired prompt templates.

Extracted and adapted from the TradingAgents multi-agent debate pattern
(github.com/TauricResearch/TradingAgents). Key difference: in TradingAgents,
these agents sit in the execution path and emit trade decisions. Here, they
are SLOW PATH ONLY — their output is always a bounded ParameterChangeProposal,
never an order.

Each persona focuses on a specific analytical domain:
  - FundamentalsAnalyst: earnings, valuation, financial health
  - SentimentAnalyst: news sentiment, social signals, market mood
  - TechnicalAnalyst: price action, indicators, chart patterns
  - MacroAnalyst: macro environment, central bank policy, geopolitics

All extend LLMAnalyst and override the system prompt and assessment logic.
They follow the same safety contract: bearish → tighten (auto),
bullish → loosen (held for human approval).
"""
from __future__ import annotations

from typing import Dict, List, Optional

from app.bus.base import EventBus
from app.core.clock import Clock
from app.slowpath.analyst import LLMAnalyst, ASSESSMENT_SCHEMA
from app.slowpath.providers import LLMProvider


# -- System prompts (TradingAgents-inspired, adapted for bounded output) ----

FUNDAMENTALS_SYSTEM = (
    "You are a senior fundamentals analyst for an automated trading platform. "
    "Analyze the provided financial data focusing on: earnings quality, revenue "
    "growth trajectory, profit margins, balance sheet strength (debt/equity, "
    "current ratio), cash flow generation, and valuation metrics (P/E, P/B, "
    "EV/EBITDA) relative to sector peers. "
    "Your assessment adjusts bounded risk parameters ONLY — you do NOT place "
    "trades. Be conservative: when uncertain, prefer bearish/high-severity "
    "(tightens risk). Consider both near-term catalysts and structural risks."
)

SENTIMENT_SYSTEM = (
    "You are a market sentiment analyst for an automated trading platform. "
    "Analyze news headlines, social media signals, and market narratives to "
    "gauge investor sentiment. Focus on: news flow velocity (sudden spike = "
    "caution), narrative shifts, institutional positioning signals, options "
    "flow implications, and contrarian indicators (extreme sentiment often "
    "precedes reversals). "
    "Your assessment adjusts bounded risk parameters ONLY — you do NOT place "
    "trades. Distinguish between noise and signal. A single headline is noise; "
    "a pattern across sources is signal. When in doubt, rate bearish/high-severity."
)

TECHNICAL_SYSTEM = (
    "You are a technical analysis specialist for an automated trading platform. "
    "Analyze price action and technical indicators focusing on: trend structure "
    "(higher highs/lows), key support/resistance levels, volume confirmation, "
    "momentum indicators (RSI, MACD), volatility regime (ATR, Bollinger width), "
    "and significant chart patterns. "
    "Your assessment adjusts bounded risk parameters ONLY — you do NOT place "
    "trades. Prioritize risk identification over opportunity: a broken support "
    "level is more actionable than a potential breakout. Flag divergences "
    "between price and volume/momentum as high-severity signals."
)

MACRO_SYSTEM = (
    "You are a macro-economic analyst for an automated trading platform. "
    "Analyze the macro environment focusing on: central bank policy trajectory "
    "(rate decisions, QE/QT), inflation trends (CPI, PPI, expectations), "
    "employment data, GDP growth, yield curve dynamics, currency movements, "
    "commodity prices (oil, metals), and geopolitical risks. "
    "Your assessment adjusts bounded risk parameters ONLY — you do NOT place "
    "trades. Macro shifts are slow but powerful: a hawkish pivot or yield "
    "curve inversion warrants high-severity even if equities haven't reacted "
    "yet. Be especially conservative during regime transitions."
)


class FundamentalsAnalyst(LLMAnalyst):
    """Earnings, valuation, and financial health assessment."""

    def __init__(self, bus: EventBus, clock: Clock, provider: LLMProvider,
                 baseline_gross: float = 2_000_000.0, ttl_s: int = 43_200) -> None:
        super().__init__(bus, clock, provider, baseline_gross,
                         source=f"fundamentals:{provider.name}:{provider.model}",
                         ttl_s=ttl_s)
        self._system = FUNDAMENTALS_SYSTEM

    def assess(self, headline: str, symbols: List[str]) -> dict:
        prompt = (
            f"Financial data / event: {headline}\n"
            f"Instruments: {', '.join(symbols) or 'broad market'}\n"
            "Assess the fundamental impact. Return JSON with: direction "
            "(bullish/bearish/neutral), severity (low/medium/high), "
            "confidence (0.0-1.0), affected (symbols list), rationale (1 sentence)."
        )
        return self._provider.assess(self._system, prompt, ASSESSMENT_SCHEMA)


class SentimentAnalyst(LLMAnalyst):
    """News sentiment and market mood assessment."""

    def __init__(self, bus: EventBus, clock: Clock, provider: LLMProvider,
                 baseline_gross: float = 2_000_000.0, ttl_s: int = 10_800) -> None:
        super().__init__(bus, clock, provider, baseline_gross,
                         source=f"sentiment:{provider.name}:{provider.model}",
                         ttl_s=ttl_s)
        self._system = SENTIMENT_SYSTEM

    def assess(self, headline: str, symbols: List[str]) -> dict:
        prompt = (
            f"News / sentiment signal: {headline}\n"
            f"Instruments: {', '.join(symbols) or 'broad market'}\n"
            "Assess the sentiment impact. Distinguish noise from signal. "
            "Return JSON with: direction, severity, confidence, affected, rationale."
        )
        return self._provider.assess(self._system, prompt, ASSESSMENT_SCHEMA)


class TechnicalAnalyst(LLMAnalyst):
    """Price action and technical indicator assessment."""

    def __init__(self, bus: EventBus, clock: Clock, provider: LLMProvider,
                 baseline_gross: float = 2_000_000.0, ttl_s: int = 7_200) -> None:
        super().__init__(bus, clock, provider, baseline_gross,
                         source=f"technical:{provider.name}:{provider.model}",
                         ttl_s=ttl_s)
        self._system = TECHNICAL_SYSTEM

    def assess(self, headline: str, symbols: List[str]) -> dict:
        prompt = (
            f"Technical signal / price action: {headline}\n"
            f"Instruments: {', '.join(symbols) or 'broad market'}\n"
            "Assess the technical implications. Prioritize risk signals. "
            "Return JSON with: direction, severity, confidence, affected, rationale."
        )
        return self._provider.assess(self._system, prompt, ASSESSMENT_SCHEMA)


class MacroAnalyst(LLMAnalyst):
    """Macro-economic environment assessment."""

    def __init__(self, bus: EventBus, clock: Clock, provider: LLMProvider,
                 baseline_gross: float = 2_000_000.0, ttl_s: int = 86_400) -> None:
        super().__init__(bus, clock, provider, baseline_gross,
                         source=f"macro:{provider.name}:{provider.model}",
                         ttl_s=ttl_s)
        self._system = MACRO_SYSTEM

    def assess(self, headline: str, symbols: List[str]) -> dict:
        prompt = (
            f"Macro event / data release: {headline}\n"
            f"Instruments: {', '.join(symbols) or 'broad market'}\n"
            "Assess the macro impact on risk positioning. Be conservative "
            "during regime transitions. "
            "Return JSON with: direction, severity, confidence, affected, rationale."
        )
        return self._provider.assess(self._system, prompt, ASSESSMENT_SCHEMA)
