"""OBB-powered feature augmentation for supervised model training.

Extends the base OHLCV feature vector (fabric.py) with three new groups
pulled from the OpenBB adapter:

  Group A — Fundamentals  (per-symbol, slow-changing)
      pe_ratio_z, ps_ratio_z, pb_ratio_z, roe_z, debt_eq_z
      (z-scored across the training universe so values are comparable)

  Group B — Macro signals  (market-wide, FRED series)
      fred_gdp_yoy, fred_cpi_yoy, fred_unrate, fred_dff
      (raw normalised values — no cross-symbol z-score needed)

  Group C — News sentiment (per-symbol, short-half-life)
      news_sentiment   in {-1.0 = bearish, 0.0 = neutral, +1.0 = bullish}
      news_count_3d    number of headlines in last 3 days (log-normalised)

All methods are async, return empty/zero dicts on failure (graceful
degradation), and have a configurable in-memory TTL cache so a training
run touching 50 symbols doesn't hammer the API 50 times per feature.

Usage::
    from app.learning.obb_features import OBBFeatureAugmenter, AUG_FEATURE_NAMES
    aug = OBBFeatureAugmenter()
    feats = await aug.augment("RELIANCE", existing_feature_dict)
    # feats now has the base features + OBB-derived extras
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from statistics import mean, pstdev
from typing import Any, Dict, List, Optional, Sequence

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Canonical extra-feature names (appended AFTER FEATURE_NAMES from fabric.py)
# ---------------------------------------------------------------------------
FUNDAMENTAL_FEATURE_NAMES: List[str] = [
    "pe_ratio_z",
    "ps_ratio_z",
    "pb_ratio_z",
    "roe_z",
    "debt_eq_z",
]

MACRO_FEATURE_NAMES: List[str] = [
    "fred_gdp_yoy",
    "fred_cpi_yoy",
    "fred_unrate",
    "fred_dff",
]

SENTIMENT_FEATURE_NAMES: List[str] = [
    "news_sentiment",
    "news_count_3d_log",
]

AUG_FEATURE_NAMES: List[str] = (
    FUNDAMENTAL_FEATURE_NAMES + MACRO_FEATURE_NAMES + SENTIMENT_FEATURE_NAMES
)

# Defaults used when data is unavailable — neutral / zero so they don't bias.
_ZERO_FUNDAMENTALS: Dict[str, float] = {k: 0.0 for k in FUNDAMENTAL_FEATURE_NAMES}
_ZERO_MACRO:        Dict[str, float] = {k: 0.0 for k in MACRO_FEATURE_NAMES}
_ZERO_SENTIMENT:    Dict[str, float] = {k: 0.0 for k in SENTIMENT_FEATURE_NAMES}


# ---------------------------------------------------------------------------
# Tiny TTL cache (reuse the pattern from openbb_adapter.py)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Universe-level z-score normaliser
# ---------------------------------------------------------------------------

def _zscore(values: Sequence[float]) -> List[float]:
    """Standard z-score; returns zeros if std == 0."""
    if not values:
        return []
    mu = mean(values)
    sd = pstdev(values)
    if sd == 0:
        return [0.0] * len(values)
    return [(v - mu) / sd for v in values]


# ---------------------------------------------------------------------------
# Main augmenter
# ---------------------------------------------------------------------------

class OBBFeatureAugmenter:
    """Adds OBB-derived features to a base feature dict.

    Designed for the training pipeline:
        1. Call ``prefetch_universe(symbols)`` once before the dataset loop.
        2. Call ``augment(symbol, base_feats)`` per sample inside the loop.

    Both methods degrade silently to zeros when OBB is unavailable.
    """

    def __init__(self, cache_ttl: float = 600.0):
        self._cache = _TTLCache(default_ttl=cache_ttl)
        # Universe-level z-score tables populated by prefetch_universe()
        self._fund_zscores: Dict[str, Dict[str, float]] = {}   # sym -> normed fundamentals
        self._macro_cache: Dict[str, float] = {}               # fresh after prefetch

    @property
    def available(self) -> bool:
        """True if the OpenBB SDK is installed."""
        try:
            from app.services.openbb_adapter import openbb_data
            return openbb_data.available
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Prefetch: pull all data for the universe in parallel, then z-score
    # ------------------------------------------------------------------

    async def prefetch_universe(self, symbols: List[str]) -> None:
        """Download fundamentals, macro and news for all symbols in parallel.

        Call this ONCE at the start of a training run. Subsequent calls to
        ``augment()`` will be served from the in-memory cache (no I/O).
        """
        if not self.available:
            log.warning("OBB not available — augmented features will be zeros")
            return

        log.info("OBBFeatureAugmenter: prefetching %d symbols …", len(symbols))
        from app.services.openbb_adapter import openbb_data

        # --- fundamentals (parallel) ---
        raw_fund: Dict[str, Dict] = {}
        fund_tasks = [openbb_data.key_metrics(sym) for sym in symbols]
        fund_results = await asyncio.gather(*fund_tasks, return_exceptions=True)
        for sym, res in zip(symbols, fund_results):
            raw_fund[sym] = res if isinstance(res, dict) else {}

        # z-score each fundamental across the universe
        metrics = ["pe_ratio", "ps_ratio", "pb_ratio", "return_on_equity",
                   "debt_to_equity"]
        feat_keys = FUNDAMENTAL_FEATURE_NAMES  # pe_ratio_z, ps_ratio_z, …

        raw_vals: Dict[str, List[float]] = {k: [] for k in feat_keys}
        order = list(symbols)
        for sym in order:
            m = raw_fund.get(sym) or {}
            raw_vals["pe_ratio_z"].append(self._safe(m, ["pe_ratio", "pe_ttm"]))
            raw_vals["ps_ratio_z"].append(self._safe(m, ["ps_ratio", "price_to_sales"]))
            raw_vals["pb_ratio_z"].append(self._safe(m, ["pb_ratio", "price_to_book"]))
            raw_vals["roe_z"].append(self._safe(m, ["return_on_equity", "roe"]))
            raw_vals["debt_eq_z"].append(self._safe(m, ["debt_to_equity", "total_debt_to_equity"]))

        for feat in feat_keys:
            zscored = _zscore(raw_vals[feat])
            for sym, z in zip(order, zscored):
                self._fund_zscores.setdefault(sym, {})[feat] = round(z, 6)

        # --- macro (one call, market-wide) ---
        self._macro_cache = await self._fetch_macro(openbb_data)

        # --- news sentiment (parallel) ---
        news_tasks = [openbb_data.company_news(sym, limit=10) for sym in symbols]
        news_results = await asyncio.gather(*news_tasks, return_exceptions=True)
        for sym, res in zip(symbols, news_results):
            articles = res if isinstance(res, list) else []
            self._cache.set(f"news:{sym}", articles, ttl=300)

        log.info("OBBFeatureAugmenter: prefetch complete — "
                 "fundamentals=%d, macro=%d features",
                 len(self._fund_zscores), len(self._macro_cache))

    # ------------------------------------------------------------------
    # Per-sample augmentation (called inside the dataset builder loop)
    # ------------------------------------------------------------------

    async def augment(
        self,
        symbol: str,
        base_feats: Dict[str, float],
        *,
        include_fundamentals: bool = True,
        include_macro: bool = True,
        include_sentiment: bool = True,
    ) -> Dict[str, float]:
        """Return ``base_feats`` extended with OBB-derived features.

        Always returns a dict; values are 0.0 when data unavailable.
        Does NOT mutate the input dict.
        """
        result = dict(base_feats)
        if include_fundamentals:
            result.update(self._get_fundamentals(symbol))
        if include_macro:
            result.update(self._macro_cache if self._macro_cache else _ZERO_MACRO)
        if include_sentiment:
            result.update(await self._get_sentiment(symbol))
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_fundamentals(self, symbol: str) -> Dict[str, float]:
        cached = self._fund_zscores.get(symbol)
        if cached:
            return cached
        # Not prefetched — return zeros (safe default)
        return dict(_ZERO_FUNDAMENTALS)

    async def _get_sentiment(self, symbol: str) -> Dict[str, float]:
        # Use cached articles from prefetch_universe if available
        articles = self._cache.get(f"news:{symbol}")
        if articles is None and self.available:
            try:
                from app.services.openbb_adapter import openbb_data
                articles = await openbb_data.company_news(symbol, limit=10)
                self._cache.set(f"news:{symbol}", articles, ttl=300)
            except Exception:
                articles = []

        if not articles:
            return dict(_ZERO_SENTIMENT)

        # Score each headline with a simple keyword approach
        # (replace with an LLM call or VADER if you want higher fidelity)
        score_sum = 0.0
        recent_count = 0
        cutoff = time.time() - 3 * 86400  # last 3 days

        for art in articles:
            title = str(art.get("title", "") or "").lower()
            pub = art.get("published_utc") or art.get("date") or ""
            ts = self._parse_ts(pub)

            # Sentiment score: +1 bullish keywords, -1 bearish keywords
            bull = sum(1 for w in _BULL_WORDS if w in title)
            bear = sum(1 for w in _BEAR_WORDS if w in title)
            score_sum += (bull - bear)

            if ts and ts >= cutoff:
                recent_count += 1

        n = max(len(articles), 1)
        raw_score = score_sum / n  # [-..., +...]
        sentiment = max(-1.0, min(1.0, raw_score))
        count_log = math.log1p(recent_count)

        return {
            "news_sentiment": round(sentiment, 4),
            "news_count_3d_log": round(count_log, 4),
        }

    async def _fetch_macro(self, openbb_data) -> Dict[str, float]:
        """Pull FRED macro indicators and normalise to roughly [-1, 1]."""
        # Series: GDP (quarterly %, real), CPI (YoY), UNRATE, DFF (fed funds)
        SERIES = {
            "fred_gdp_yoy":  ("A191RL1Q225SBEA", 5.0),   # real GDP growth %, ~[-5, 5]
            "fred_cpi_yoy":  ("CPIAUCSL",         10.0),  # CPI YoY ~[0, 10]
            "fred_unrate":   ("UNRATE",            10.0),  # unemployment ~[3, 10]
            "fred_dff":      ("DFF",               10.0),  # fed funds ~[0, 10]
        }
        ids = [v[0] for v in SERIES.values()]
        result: Dict[str, float] = {}
        try:
            raw = await openbb_data.economic_indicators(ids)
            if not raw:
                return dict(_ZERO_MACRO)
            # raw is a list of dicts with {series_id, date, value}
            # take the most recent value for each series
            latest: Dict[str, float] = {}
            for row in raw:
                sid = row.get("symbol") or row.get("series_id") or ""
                val = row.get("value")
                if sid and val is not None:
                    latest[sid] = float(val)

            for feat, (sid, scale) in SERIES.items():
                v = latest.get(sid, 0.0)
                result[feat] = round(max(-1.0, min(1.0, v / scale)), 4)
        except Exception as exc:
            log.debug("OBB macro fetch failed: %s", exc)
            return dict(_ZERO_MACRO)
        return result

    @staticmethod
    def _safe(d: dict, keys: List[str]) -> float:
        """First non-None numeric value from a list of candidate keys."""
        for k in keys:
            v = d.get(k)
            if v is not None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    continue
        return 0.0

    @staticmethod
    def _parse_ts(raw) -> Optional[float]:
        """Best-effort ISO-8601 → epoch float."""
        if not raw:
            return None
        try:
            from datetime import datetime as _dt
            s = str(raw).replace("Z", "+00:00")
            return _dt.fromisoformat(s).timestamp()
        except Exception:
            return None


# ---------------------------------------------------------------------------
# Simple keyword sentiment lexicon (extend as needed)
# ---------------------------------------------------------------------------
_BULL_WORDS = {
    "beat", "beats", "surges", "rises", "gains", "profit", "growth",
    "record", "strong", "upgrade", "buy", "outperform", "rally",
    "expansion", "positive", "bullish", "revenue", "dividend",
}
_BEAR_WORDS = {
    "miss", "misses", "falls", "drops", "loss", "decline", "weak",
    "downgrade", "sell", "underperform", "crash", "plunges", "bearish",
    "layoff", "lawsuit", "recall", "warning", "cut", "negative",
}


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
obb_augmenter = OBBFeatureAugmenter()
