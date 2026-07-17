"""Finnhub adapter -- market-data failover + news sentiment (slow path).

Two independent uses, both OFF the deterministic fast path:

1. **Market-data failover.** A keyed backup quote/candle source for the product
   surface (services/market_providers.py) when the active broker feed and the
   Yahoo fallback are unavailable. Never a source of fast-path bars -- those must
   stay deterministic and come from the bar store / replay.

2. **News sentiment.** ``company_news`` / ``news_sentiment`` give the slow-path
   LLM analyst (slowpath/analyst.py) an extra evidence signal. Advisory only:
   it can adjust bounded risk parameters, never place an order.

Contract (matches services/openbb_adapter.py):
  * Requires a free key (``ETB_FINNHUB_API_KEY``). No key -> ``enabled`` is False,
    every method returns empty, and the app behaves exactly as before.
  * Degrades gracefully: any failure returns an empty result and never raises.

Response shaping is done by pure functions (``parse_quote`` / ``sentiment_score``)
so they can be unit-tested offline with fixtures, no network.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import get_settings

log = logging.getLogger(__name__)

_BASE = "https://finnhub.io/api/v1"


def parse_quote(payload: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """Shape Finnhub's /quote response into a small normalized dict.

    Pure function. Finnhub returns ``{"c":current,"h":high,"l":low,"o":open,
    "pc":prev_close,"t":epoch}``. A current price of 0 means 'unknown symbol' ->
    treated as a miss (None).
    """
    try:
        current = float(payload.get("c", 0) or 0)
    except (TypeError, ValueError):
        return None
    if current <= 0:
        return None
    def _f(key: str) -> float:
        try:
            return float(payload.get(key, 0) or 0)
        except (TypeError, ValueError):
            return 0.0
    return {
        "price": current,
        "high": _f("h"),
        "low": _f("l"),
        "open": _f("o"),
        "prev_close": _f("pc"),
        "ts": _f("t"),
    }


def sentiment_score(articles: List[Dict[str, Any]]) -> float:
    """Naive polarity score in [-1, 1] from article sentiment fields.

    Pure function. Uses each article's numeric ``sentiment`` if present, else
    neutral (0). Averaged. Kept deliberately simple -- it is only slow-path
    evidence, and a bearish bias only ever tightens risk (the fail-safe way).
    """
    if not articles:
        return 0.0
    total, n = 0.0, 0
    for a in articles:
        s = a.get("sentiment")
        try:
            total += max(-1.0, min(1.0, float(s)))
            n += 1
        except (TypeError, ValueError):
            continue
    return total / n if n else 0.0


class FinnhubProvider:
    """Keyed Finnhub client. Disabled (empty results) when no key is configured."""

    def __init__(self, cache_ttl: float = 30.0, timeout_s: float = 10.0) -> None:
        self._timeout = timeout_s
        self._cache_ttl = cache_ttl
        self._cache: Dict[str, tuple] = {}

    @property
    def enabled(self) -> bool:
        return bool(get_settings().finnhub_api_key.strip())

    def _key(self) -> str:
        return get_settings().finnhub_api_key.strip()

    def _cache_get(self, k: str) -> Optional[Any]:
        entry = self._cache.get(k)
        if entry is None or time.monotonic() > entry[1]:
            return None
        return entry[0]

    def _cache_set(self, k: str, v: Any, ttl: Optional[float] = None) -> None:
        self._cache[k] = (v, time.monotonic() + (ttl or self._cache_ttl))

    async def _get(self, path: str, params: Dict[str, Any]) -> Any:
        params = {**params, "token": self._key()}
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(f"{_BASE}{path}", params=params)
            resp.raise_for_status()
            return resp.json()

    async def quote(self, symbol: str) -> Optional[Dict[str, float]]:
        """Latest quote for a symbol, or None (disabled / unknown / failure)."""
        if not self.enabled:
            return None
        ck = f"quote:{symbol}"
        cached = self._cache_get(ck)
        if cached is not None:
            return cached
        try:
            data = await self._get("/quote", {"symbol": symbol.upper()})
            shaped = parse_quote(data)
        except Exception as exc:
            log.debug("Finnhub quote(%s) failed: %s", symbol, exc)
            return None
        if shaped is not None:
            self._cache_set(ck, shaped)
        return shaped

    async def company_news(
        self, symbol: str, frm: str, to: str, limit: int = 20
    ) -> List[Dict[str, Any]]:
        """Company news between two ``YYYY-MM-DD`` dates. Empty when disabled."""
        if not self.enabled:
            return []
        ck = f"news:{symbol}:{frm}:{to}:{limit}"
        cached = self._cache_get(ck)
        if cached is not None:
            return cached
        try:
            data = await self._get(
                "/company-news", {"symbol": symbol.upper(), "from": frm, "to": to}
            )
            articles = data if isinstance(data, list) else []
            articles = articles[:limit]
        except Exception as exc:
            log.debug("Finnhub company_news(%s) failed: %s", symbol, exc)
            return []
        self._cache_set(ck, articles, ttl=180)
        return articles

    async def news_sentiment(self, symbol: str, frm: str, to: str) -> float:
        """Aggregate polarity in [-1, 1] over a symbol's recent news."""
        articles = await self.company_news(symbol, frm, to)
        return sentiment_score(articles)


# Module-level singleton (mirrors services/openbb_adapter.py::openbb_data).
finnhub = FinnhubProvider()
