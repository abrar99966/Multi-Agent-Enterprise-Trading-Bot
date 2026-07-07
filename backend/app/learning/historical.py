"""Historical OHLC fetcher backed by Upstox HistoryApi.

Pulls multi-day candles for training. Routes through the connected Upstox
broker (sandbox vs prod auto-selected by the broker's `is_paper` flag).
Caches the result to disk so re-runs of the tuner don't repeatedly hit the API.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.database import BrokerAccount, BrokerStatus
from ..services.broker_adapters import BrokerCreds, get_adapter
from ..services.broker_service import _dec
from ..services import upstox_symbols
from . import bar_store
from .bar import Bar  # re-exported: `from .historical import Bar` still works

log = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parents[2] / ".cache" / "historical"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Serve from the durable store when it holds at least this many bars in the window.
_MIN_STORED_BARS = 20

# Map our generic interval string to Upstox's literal vocabulary.
_UPSTOX_INTERVAL = {
    "1m": "1minute", "1minute": "1minute",
    "30m": "30minute", "30minute": "30minute",
    "1d": "day", "day": "day",
    "1w": "week", "week": "week",
}


def _cache_path(symbol: str, interval: str, from_date: str, to_date: str) -> Path:
    safe = symbol.replace("/", "_")
    return CACHE_DIR / f"{safe}__{interval}__{from_date}__{to_date}.json"


async def _get_upstox_creds(db: AsyncSession) -> Optional[BrokerCreds]:
    """Find the most recently connected Upstox account."""
    res = await db.execute(
        select(BrokerAccount).where(
            BrokerAccount.broker_name == "upstox",
            BrokerAccount.status == BrokerStatus.CONNECTED,
        ).order_by(BrokerAccount.created_at.desc()).limit(1)
    )
    acc = res.scalar_one_or_none()
    if acc is None:
        return None
    return BrokerCreds(
        api_key=_dec(acc.api_key_enc) or "",
        api_secret=_dec(acc.api_secret_enc) or "",
        access_token=_dec(acc.access_token_enc),
        account_id=acc.account_id,
        is_paper=bool(acc.is_paper),
    )


# Yahoo range cap per interval (Yahoo's hard limits)
_YAHOO_MAX_DAYS = {
    "1m": 7, "2m": 60, "5m": 60, "15m": 60, "30m": 60,
    "60m": 730, "90m": 730, "1h": 730,
    "1d": 25 * 365, "5d": 25 * 365, "1wk": 25 * 365,
}


def _interval_to_yahoo(interval: str) -> str:
    """Map our generic interval to Yahoo's vocabulary."""
    s = interval.lower()
    if s in ("1minute", "1m"): return "1m"
    if s in ("5minute", "5m"): return "5m"
    if s in ("15minute", "15m"): return "15m"
    if s in ("30minute", "30m"): return "30m"
    if s in ("60minute", "1h", "60m"): return "1h"
    if s in ("day", "1d"): return "1d"
    if s in ("week", "1w", "1wk"): return "1wk"
    return "1d"


def _yahoo_range(days: int, interval: str) -> str:
    """Pick the smallest Yahoo `range` covering the lookback (Yahoo capped per interval)."""
    cap = _YAHOO_MAX_DAYS.get(interval, 60)
    effective_days = min(days, cap)
    if effective_days <= 7: return "7d"
    if effective_days <= 30: return "1mo"
    if effective_days <= 60: return "60d"
    if effective_days <= 90: return "3mo"
    if effective_days <= 180: return "6mo"
    if effective_days <= 365: return "1y"
    if effective_days <= 730: return "2y"
    if effective_days <= 1825: return "5y"
    return "10y"


def _nse_yahoo_symbol(symbol: str) -> str:
    """Map a bare NSE equity ticker to its Yahoo form (`SYMBOL.NS`).

    Yahoo addresses NSE stocks as `RELIANCE.NS`; a bare `RELIANCE` 404s. The
    market_data service only suffixes a small curated hint list, so for the
    whole-market universe we suffix here. Indexes (handled by INDEX_ALIAS) and
    already-qualified tickers (`.NS`/`.BO`/`^...`) are passed through untouched.
    """
    from ..services.market_data import INDEX_ALIAS
    s = (symbol or "").upper().strip()
    if not s or s in INDEX_ALIAS or "." in s or "^" in s:
        return s
    return s + ".NS"


async def _fetch_bars_yahoo(symbol: str, interval: str, lookback_days: int) -> List[Bar]:
    """Yahoo Finance fallback — works for any symbol the market_data service knows."""
    from ..services.market_data import market_data_service
    yahoo_iv = _interval_to_yahoo(interval)
    yahoo_range = _yahoo_range(lookback_days, yahoo_iv)
    ysym = _nse_yahoo_symbol(symbol)
    try:
        data = await market_data_service.get_intraday(ysym, range_=yahoo_range, interval=yahoo_iv)
    except Exception as exc:
        log.warning("Yahoo fetch failed for %s (%s/%s): %s", symbol, yahoo_iv, yahoo_range, exc)
        return []
    series = data.get("series") or []
    bars: List[Bar] = []
    for s in series:
        try:
            bars.append(Bar(
                t=int(s["t"]),
                o=float(s["o"]) if s.get("o") is not None else 0.0,
                h=float(s["h"]) if s.get("h") is not None else 0.0,
                l=float(s["l"]) if s.get("l") is not None else 0.0,
                c=float(s["c"]),
                v=float(s["v"]) if s.get("v") is not None else 0.0,
            ))
        except Exception:
            continue
    bars.sort(key=lambda b: b.t)
    return bars


async def _fetch_bars_upstox(
    db: AsyncSession, symbol: str, interval: str, lookback_days: int,
) -> List[Bar]:
    """Try Upstox first. Returns [] if no connection or fetch fails."""
    upstox_iv = _UPSTOX_INTERVAL.get(interval, "30minute")
    to_dt = datetime.utcnow().date()
    from_dt = to_dt - timedelta(days=lookback_days)
    from_date, to_date = from_dt.isoformat(), to_dt.isoformat()

    creds = await _get_upstox_creds(db)
    if creds is None:
        return []
    ref = await upstox_symbols.resolve_async(symbol)
    if ref is None:
        return []

    adapter = get_adapter("upstox")
    try:
        # Force production host: Upstox serves historical candles only from
        # api.upstox.com, never the sandbox (paper) host, which 404s them.
        _, _, history_api = adapter._data_apis(creds)
        resp = await asyncio.to_thread(
            history_api.get_historical_candle_data1,
            instrument_key=ref.instrument_key,
            interval=upstox_iv,
            to_date=to_date,
            from_date=from_date,
            api_version="2.0",
        )
    except Exception as exc:
        log.info("Upstox historical fetch failed for %s (%s) — will try Yahoo: %s", symbol, upstox_iv, exc)
        return []

    data = getattr(resp, "data", None) or {}
    if hasattr(data, "to_dict"):
        data = data.to_dict()
    candles = (data or {}).get("candles") or []

    bars: List[Bar] = []
    for c in candles:
        if not c or len(c) < 5:
            continue
        try:
            ts_iso = c[0]
            ts = int(datetime.fromisoformat(str(ts_iso).replace("Z", "+00:00")).timestamp())
            bars.append(Bar(
                t=ts,
                o=float(c[1]) if c[1] is not None else 0.0,
                h=float(c[2]) if c[2] is not None else 0.0,
                l=float(c[3]) if c[3] is not None else 0.0,
                c=float(c[4]),
                v=float(c[5]) if len(c) > 5 and c[5] is not None else 0.0,
            ))
        except Exception:
            continue
    bars.sort(key=lambda b: b.t)
    return bars


def _window_epochs(lookback_days: int) -> tuple[int, int]:
    """UTC epoch bounds for the requested lookback window (1-day slack each side)."""
    to_dt = datetime.utcnow().date()
    from_dt = to_dt - timedelta(days=lookback_days)
    from_t = int(datetime.combine(from_dt, time.min, tzinfo=timezone.utc).timestamp()) - 86400
    to_t = int(datetime.combine(to_dt, time.max, tzinfo=timezone.utc).timestamp()) + 86400
    return from_t, to_t


async def fetch_bars(
    db: AsyncSession, symbol: str, *,
    interval: str = "30minute", lookback_days: int = 90,
    use_cache: bool = True, refresh: bool = False,
) -> List[Bar]:
    """Fetch historical OHLC for a symbol — durable store first, network on miss.

    Reads from the persistent bar store (`bar_store`) when it already covers the
    window, so a symbol is downloaded ONCE and reused for unlimited backtests
    offline. On a miss (or `refresh=True`) it fetches from Upstox (if connected)
    then Yahoo Finance, persists every bar to the store, and returns the window.

    `use_cache=False` or `refresh=True` forces a live fetch. Returns oldest-first
    Bars; empty list ⇒ neither the store nor the network had data.
    """
    upstox_iv = _UPSTOX_INTERVAL.get(interval, "30minute")
    from_t, to_t = _window_epochs(lookback_days)

    # 1. Serve from the durable store unless the caller forces a refresh.
    if use_cache and not refresh:
        stored = await asyncio.to_thread(bar_store.get_bars, symbol, upstox_iv, from_t, to_t)
        if len(stored) >= _MIN_STORED_BARS:
            return stored

    # 2. Miss → fetch from network (Upstox first, then Yahoo).
    bars = await _fetch_bars_upstox(db, symbol, interval, lookback_days)
    source_used = "upstox"
    if not bars:
        bars = await _fetch_bars_yahoo(symbol, interval, lookback_days)
        source_used = "yahoo"

    # 3. Persist and return the window from the store (merged with any prior bars).
    if bars:
        log.info("Fetched %d bars for %s via %s", len(bars), symbol, source_used)
        await asyncio.to_thread(bar_store.save_bars, symbol, upstox_iv, bars)
        merged = await asyncio.to_thread(bar_store.get_bars, symbol, upstox_iv, from_t, to_t)
        return merged or bars

    # 4. Nothing from the network — fall back to whatever the store has (even if sparse).
    log.warning("No fresh bars for %s; falling back to store", symbol)
    return await asyncio.to_thread(bar_store.get_bars, symbol, upstox_iv, from_t, to_t)


async def migrate_cache_to_store() -> dict:
    """One-time import of legacy `.cache/historical/*.json` files into the bar store.

    Filenames look like `SYMBOL__interval__from__to.json`. Idempotent (upserts),
    so running it twice is harmless. Lets pre-existing cached data seed the store
    without re-downloading.
    """
    imported = {"files": 0, "bars": 0, "symbols": set()}
    for f in CACHE_DIR.glob("*.json"):
        try:
            stem = f.stem  # SYMBOL__interval__from__to
            parts = stem.split("__")
            if len(parts) < 2:
                continue
            symbol, iv = parts[0], parts[1]
            raw = json.loads(f.read_text())
            if not raw:
                continue
            bars = [Bar(**b) for b in raw]
            n = await asyncio.to_thread(bar_store.save_bars, symbol, iv, bars)
            imported["files"] += 1
            imported["bars"] += n
            imported["symbols"].add(symbol.upper())
        except Exception as exc:
            log.debug("Skipping cache file %s: %s", f.name, exc)
    imported["symbols"] = len(imported["symbols"])
    log.info("Migrated cache → store: %s", imported)
    return imported
