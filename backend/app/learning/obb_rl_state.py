"""OBB-enriched RL state builder.

Replaces the hardcoded ``"neutral"`` sentiment in ``RLTrader.state_of()``
with real sentiment derived from:
  1. OBB company news (via obb_features sentiment scorer)
  2. Macro regime from FRED data (via obb_features macro indicators)

The RL state key format remains IDENTICAL to the existing format so the
persisted q-table (rl_qtable.json) stays fully compatible:

    "{horizon}|{regime}|{signal}|{sentiment}"

Example:
    "ST|bullish_macro|bullish|positive"    ← was always "neutral" before

Usage::
    from app.learning.obb_rl_state import OBBRLStateBuilder
    builder = OBBRLStateBuilder()
    # at startup / before grading:
    await builder.warm(["RELIANCE", "TCS", "INFY"])
    # at grading time (replaces RLTrader.get_state):
    state = await builder.state_for(symbol, agent_outputs)
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sentiment bucket thresholds
# ---------------------------------------------------------------------------
# Continuous news_sentiment in [-1, 1] → discrete label used in state key.
_SENT_LABELS = [
    (-1.0,  -0.15, "negative"),
    (-0.15,  0.15, "neutral"),
    ( 0.15,  1.01, "positive"),
]

# Macro regime from FRED indicators → discrete label.
# Uses fred_cpi_yoy and fred_dff to bucket macro environment.
def _macro_regime(cpi_norm: float, dff_norm: float, gdp_norm: float) -> str:
    """Classify macro environment into a regime string.

    Args:
        cpi_norm:  CPI YoY normalised to [-1, 1]  (scale 10 %)
        dff_norm:  Fed-funds rate normalised [-1, 1] (scale 10 %)
        gdp_norm:  Real GDP growth normalised [-1, 1] (scale 5 %)
    """
    if cpi_norm > 0.5 and dff_norm > 0.5:
        return "stagflation"       # high inflation + tight money
    if cpi_norm > 0.3 and gdp_norm > 0.2:
        return "overheating"       # strong growth + rising prices
    if gdp_norm < -0.2 and dff_norm < 0.2:
        return "recession"         # contraction + loose money
    if gdp_norm > 0.1 and cpi_norm < 0.2:
        return "goldilocks"        # good growth, tame inflation
    if dff_norm > 0.4:
        return "tightening"        # aggressive rate hikes
    if dff_norm < 0.1:
        return "easing"            # rate cuts / easy money
    return "neutral"               # everything else


def _sentiment_label(score: float) -> str:
    for lo, hi, label in _SENT_LABELS:
        if lo <= score < hi:
            return label
    return "neutral"


# ---------------------------------------------------------------------------
# OBBRLStateBuilder
# ---------------------------------------------------------------------------

class OBBRLStateBuilder:
    """Builds enriched RL state strings using OBB data.

    Thread-safe (asyncio): warm() prefetches, state_for() is fast and
    uses cached values. Falls back to plain "neutral" if OBB unavailable.
    """

    def __init__(self) -> None:
        self._sentiment: Dict[str, str] = {}    # sym  → sentiment label
        self._macro_regime: str = "neutral"      # market-wide
        self._last_warm: float = 0.0
        self._warm_ttl: float = 300.0            # re-warm at most every 5 min

    @property
    def _obb_available(self) -> bool:
        try:
            from app.services.openbb_adapter import openbb_data
            return openbb_data.available
        except Exception:
            return False

    async def warm(self, symbols: List[str]) -> None:
        """Pre-fetch sentiment + macro for a universe of symbols.

        Idempotent and throttled (won't re-fetch within ``_warm_ttl`` seconds).
        Safe to call at app startup or before a training/grading batch.
        """
        now = time.monotonic()
        if now - self._last_warm < self._warm_ttl:
            return  # still fresh

        if not self._obb_available:
            log.debug("OBBRLStateBuilder: OBB unavailable — using neutral states")
            return

        from app.services.openbb_adapter import openbb_data

        # --- sentiment (parallel) ---
        tasks = [openbb_data.company_news(sym, limit=10) for sym in symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for sym, res in zip(symbols, results):
            articles = res if isinstance(res, list) else []
            score = _score_articles(articles)
            self._sentiment[sym] = _sentiment_label(score)
            log.debug("OBB sentiment for %s: %.3f → %s", sym, score, self._sentiment[sym])

        # --- macro (one call) ---
        try:
            macro_raw = await openbb_data.economic_indicators(
                ["A191RL1Q225SBEA", "CPIAUCSL", "UNRATE", "DFF"]
            )
            latest: Dict[str, float] = {}
            for row in (macro_raw or []):
                sid = row.get("symbol") or row.get("series_id") or ""
                val = row.get("value")
                if sid and val is not None:
                    latest[sid] = float(val)

            gdp_norm  = _norm(latest.get("A191RL1Q225SBEA", 0.0), 5.0)
            cpi_norm  = _norm(latest.get("CPIAUCSL", 0.0), 10.0)
            dff_norm  = _norm(latest.get("DFF", 0.0), 10.0)
            self._macro_regime = _macro_regime(cpi_norm, dff_norm, gdp_norm)
            log.info("OBB macro regime: %s (CPI=%.2f DFF=%.2f GDP=%.2f)",
                     self._macro_regime, cpi_norm, dff_norm, gdp_norm)
        except Exception as exc:
            log.debug("OBB macro fetch failed: %s", exc)

        self._last_warm = now
        log.info("OBBRLStateBuilder warmed: %d symbols, macro=%s",
                 len(self._sentiment), self._macro_regime)

    # ------------------------------------------------------------------
    # State construction
    # ------------------------------------------------------------------

    async def state_for(
        self,
        symbol: str,
        agent_outputs: Dict[str, Any],
        *,
        refresh_if_missing: bool = True,
    ) -> str:
        """Build an RL state string enriched with real OBB sentiment & macro.

        Compatible with the existing RLTrader q-table key format:
        ``"{horizon}|{regime}|{signal}|{sentiment}"``

        Args:
            symbol:              The equity being graded.
            agent_outputs:       Same dict RLTrader.get_state() receives.
            refresh_if_missing:  If symbol not cached, try a live OBB fetch.

        Returns:
            State key string.
        """
        # --- horizon + signal from agent outputs (same as before) ---
        rationale = agent_outputs.get("rationale") or {}
        he        = agent_outputs.get("HorizonEngine") or {}
        tech      = agent_outputs.get("TechnicalAnalysis") or {}
        horizon   = rationale.get("horizon")
        signal    = he.get("signal") or tech.get("signal")

        # --- macro regime ---
        regime = self._macro_regime

        # Fallback: if OBB macro not available, check existing agent_outputs
        if regime == "neutral":
            macro_out = agent_outputs.get("MacroEconomics") or {}
            regime = macro_out.get("market_regime") or "neutral"

        # --- sentiment ---
        sentiment = self._sentiment.get(symbol)
        if sentiment is None:
            # Try existing agent output first (fast path)
            news_out  = agent_outputs.get("NewsIntelligence") or {}
            raw_sent  = news_out.get("sentiment")
            if raw_sent and raw_sent != "neutral":
                sentiment = raw_sent
            elif refresh_if_missing and self._obb_available:
                # Live single-symbol fetch (blocks ~0.5 s, only on cache miss)
                sentiment = await self._live_sentiment(symbol)
            else:
                sentiment = "neutral"

        return _fmt(horizon, regime, signal, sentiment)

    async def _live_sentiment(self, symbol: str) -> str:
        """Single-symbol live sentiment fetch (cache miss path)."""
        try:
            from app.services.openbb_adapter import openbb_data
            articles = await openbb_data.company_news(symbol, limit=10)
            score = _score_articles(articles)
            label = _sentiment_label(score)
            self._sentiment[symbol] = label
            return label
        except Exception as exc:
            log.debug("OBB live sentiment for %s failed: %s", symbol, exc)
            return "neutral"

    # ------------------------------------------------------------------
    # Convenience: warm state key (no awaiting) from cached data only
    # ------------------------------------------------------------------

    def cached_state_for(self, symbol: str, agent_outputs: Dict[str, Any]) -> str:
        """Synchronous version — uses cache only, falls back to "neutral"."""
        rationale = agent_outputs.get("rationale") or {}
        he        = agent_outputs.get("HorizonEngine") or {}
        tech      = agent_outputs.get("TechnicalAnalysis") or {}
        horizon   = rationale.get("horizon")
        signal    = he.get("signal") or tech.get("signal")
        regime    = self._macro_regime
        sentiment = self._sentiment.get(symbol, "neutral")
        return _fmt(horizon, regime, signal, sentiment)

    def sentiment_snapshot(self) -> Dict[str, str]:
        """Current cached sentiment labels keyed by symbol."""
        return dict(self._sentiment)

    def macro_regime(self) -> str:
        return self._macro_regime


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

_BULL = {"beat","beats","surges","rises","gains","profit","growth","record",
         "strong","upgrade","buy","outperform","rally","expansion","positive",
         "bullish","revenue","dividend"}
_BEAR = {"miss","misses","falls","drops","loss","decline","weak","downgrade",
         "sell","underperform","crash","plunges","bearish","layoff","lawsuit",
         "recall","warning","cut","negative"}


def _score_articles(articles: list) -> float:
    if not articles:
        return 0.0
    total = 0.0
    for art in articles:
        title = str(art.get("title", "") or "").lower()
        bull = sum(1 for w in _BULL if w in title)
        bear = sum(1 for w in _BEAR if w in title)
        total += bull - bear
    return total / max(len(articles), 1)


def _norm(value: float, scale: float) -> float:
    """Clamp value/scale to [-1, 1]."""
    if scale == 0:
        return 0.0
    return max(-1.0, min(1.0, value / scale))


def _fmt(horizon, regime, signal, sentiment) -> str:
    return (
        f"{horizon or 'ST'}|"
        f"{regime or 'neutral'}|"
        f"{signal or 'neutral'}|"
        f"{sentiment or 'neutral'}"
    )


# ---------------------------------------------------------------------------
# Module-level singleton (mirrors openbb_adapter.openbb_data pattern)
# ---------------------------------------------------------------------------
obb_rl_state = OBBRLStateBuilder()
