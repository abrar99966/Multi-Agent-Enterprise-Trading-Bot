"""OpenBB data adapter — Slow Path enrichment layer.

Wraps the OpenBB SDK as an optional data provider for the slow path.
Feeds LLM analysts with richer context (macro, fundamentals, news).
OFF the deterministic fast path. Graceful degradation if not installed.

Usage::
    from app.services.openbb_adapter import openbb_data
    profile = await openbb_data.company_profile("RELIANCE.NS")
    news    = await openbb_data.world_news(limit=10)
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

_obb = None
_obb_available: Optional[bool] = None


def _ensure_openbb():
    global _obb, _obb_available
    if _obb_available is not None:
        return _obb_available
    try:
        from openbb import obb
        _obb = obb
        _obb_available = True
        log.info("OpenBB SDK loaded — slow-path data enrichment enabled")
    except ImportError:
        _obb_available = False
        log.warning("OpenBB SDK not installed. Install: pip install openbb openbb-yfinance")
    return _obb_available


class _TTLCache:
    def __init__(self, default_ttl: float = 300.0):
        self._store: Dict[str, tuple] = {}
        self._default_ttl = default_ttl

    def get(self, key: str) -> Optional[Any]:
        entry = self._store.get(key)
        if entry is None or time.monotonic() > entry[1]:
            return None
        return entry[0]

    def set(self, key: str, data: Any, ttl: Optional[float] = None) -> None:
        self._store[key] = (data, time.monotonic() + (ttl or self._default_ttl))

    def clear(self) -> None:
        self._store.clear()


class OpenBBDataAdapter:
    """Slow-path data enrichment via the OpenBB SDK.
    Every method returns empty results on failure — never raises."""

    def __init__(self, default_provider: str = "yfinance", cache_ttl: float = 300.0):
        self._default_provider = default_provider
        self._cache = _TTLCache(default_ttl=cache_ttl)

    @property
    def available(self) -> bool:
        return _ensure_openbb()

    def _run_sync(self, fn, *args, **kwargs) -> Any:
        loop = asyncio.get_event_loop()
        return loop.run_in_executor(None, lambda: fn(*args, **kwargs))

    async def historical_prices(self, symbol: str, days: int = 30,
                                 provider: Optional[str] = None) -> List[Dict]:
        if not self.available:
            return []
        ck = f"hist:{symbol}:{days}:{provider or self._default_provider}"
        cached = self._cache.get(ck)
        if cached is not None:
            return cached
        try:
            result = await self._run_sync(
                _obb.equity.price.historical, symbol,
                provider=provider or self._default_provider,
            )
            df = result.to_dataframe()
            if len(df) > days:
                df = df.tail(days)
            records = df.reset_index().to_dict(orient="records")
            self._cache.set(ck, records)
            return records
        except Exception as exc:
            log.debug("OpenBB historical_prices(%s) failed: %s", symbol, exc)
            return []

    async def company_profile(self, symbol: str,
                               provider: Optional[str] = None) -> Dict:
        if not self.available:
            return {}
        ck = f"profile:{symbol}:{provider or self._default_provider}"
        cached = self._cache.get(ck)
        if cached is not None:
            return cached
        try:
            result = await self._run_sync(
                _obb.equity.profile, symbol,
                provider=provider or self._default_provider,
            )
            df = result.to_dataframe()
            if df.empty:
                return {}
            record = df.iloc[0].to_dict()
            self._cache.set(ck, record, ttl=3600)
            return record
        except Exception as exc:
            log.debug("OpenBB company_profile(%s) failed: %s", symbol, exc)
            return {}

    async def key_metrics(self, symbol: str,
                           provider: Optional[str] = None) -> Dict:
        if not self.available:
            return {}
        ck = f"metrics:{symbol}:{provider or self._default_provider}"
        cached = self._cache.get(ck)
        if cached is not None:
            return cached
        try:
            result = await self._run_sync(
                _obb.equity.fundamental.metrics, symbol,
                provider=provider or self._default_provider,
            )
            df = result.to_dataframe()
            if df.empty:
                return {}
            record = df.iloc[0].to_dict()
            self._cache.set(ck, record, ttl=3600)
            return record
        except Exception as exc:
            log.debug("OpenBB key_metrics(%s) failed: %s", symbol, exc)
            return {}

    async def world_news(self, limit: int = 10,
                          provider: Optional[str] = None) -> List[Dict]:
        if not self.available:
            return []
        ck = f"news:world:{limit}:{provider or 'default'}"
        cached = self._cache.get(ck)
        if cached is not None:
            return cached
        try:
            result = await self._run_sync(_obb.news.world, limit=limit, provider=provider)
            df = result.to_dataframe()
            records = df.reset_index().to_dict(orient="records")
            self._cache.set(ck, records, ttl=180)
            return records
        except Exception as exc:
            log.debug("OpenBB world_news failed: %s", exc)
            return []

    async def company_news(self, symbol: str, limit: int = 8,
                            provider: Optional[str] = None) -> List[Dict]:
        if not self.available:
            return []
        ck = f"news:{symbol}:{limit}:{provider or 'default'}"
        cached = self._cache.get(ck)
        if cached is not None:
            return cached
        try:
            result = await self._run_sync(
                _obb.news.company, symbol, limit=limit, provider=provider,
            )
            df = result.to_dataframe()
            records = df.reset_index().to_dict(orient="records")
            self._cache.set(ck, records, ttl=180)
            return records
        except Exception as exc:
            log.debug("OpenBB company_news(%s) failed: %s", symbol, exc)
            return []

    async def economic_indicators(self, series_ids: List[str],
                                   provider: Optional[str] = None) -> List[Dict]:
        if not self.available or not series_ids:
            return []
        ck = f"econ:{','.join(sorted(series_ids))}:{provider or 'default'}"
        cached = self._cache.get(ck)
        if cached is not None:
            return cached
        try:
            result = await self._run_sync(
                _obb.economy.fred_series, series_ids, provider=provider,
            )
            df = result.to_dataframe()
            records = df.reset_index().to_dict(orient="records")
            self._cache.set(ck, records, ttl=1800)
            return records
        except Exception as exc:
            log.debug("OpenBB economic_indicators failed: %s", exc)
            return []

    async def analyst_context(self, symbol: str, include_news: bool = True,
                               include_fundamentals: bool = True,
                               include_macro: bool = False) -> Dict[str, Any]:
        """Build a rich context dict for the LLM analyst."""
        context: Dict[str, Any] = {"symbol": symbol}
        tasks, keys = [], []
        if include_news:
            tasks.append(self.company_news(symbol, limit=5))
            keys.append("recent_news")
        if include_fundamentals:
            tasks.append(self.company_profile(symbol))
            keys.append("profile")
            tasks.append(self.key_metrics(symbol))
            keys.append("metrics")
        if include_macro:
            tasks.append(self.economic_indicators(["GDP", "CPIAUCSL", "UNRATE", "DFF"]))
            keys.append("macro_indicators")
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for key, result in zip(keys, results):
            context[key] = {} if isinstance(result, Exception) else result
        return context

    def clear_cache(self) -> None:
        self._cache.clear()
        log.info("OpenBB data cache cleared")


# Module-level singleton
openbb_data = OpenBBDataAdapter()
