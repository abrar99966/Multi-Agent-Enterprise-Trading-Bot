"""OBB-powered historical bar ingestion for AI agent training.

Replaces / supplements Yahoo Finance as the training data source.
Uses openbb.obb.equity.price.historical as the primary source,
falls back to the existing Yahoo/Upstox pipeline on any failure.

Design:
  • Zero hard dependency — graceful degradation if openbb not installed.
  • Same Bar dataclass as the rest of the training pipeline (no schema change).
  • Plugs straight into ingest_universe() via the `fetcher` override.
  • Writes to the same bar_store so backtests & the tuner pick it up automatically.
  • Concurrency-safe: uses asyncio.to_thread() for blocking OBB SDK calls.

Usage::
    from app.learning.obb_ingest import ingest_universe_obb
    stats = await ingest_universe_obb(db, symbols, lookback_days=365)

Or as a standalone script::
    python -m app.learning.obb_ingest
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Callable, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from .bar import Bar
from . import bar_store
from .ingest import ingest_universe          # fallback for symbols OBB can't serve
from .historical import _UPSTOX_INTERVAL

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# OBB bar fetcher
# ---------------------------------------------------------------------------

def _nse_obb_symbol(symbol: str) -> str:
    """Normalise a bare NSE ticker for OBB's yfinance provider.

    yfinance inside OBB uses the same Yahoo convention: ``RELIANCE.NS``.
    Already-qualified tickers (contain `.` or `^`) are passed through.
    """
    s = (symbol or "").upper().strip()
    if not s or "." in s or "^" in s:
        return s
    return s + ".NS"


async def fetch_bars_obb(
    symbol: str,
    lookback_days: int = 365,
    provider: str = "yfinance",
) -> List[Bar]:
    """Fetch OHLCV bars from OBB for a single symbol.

    Returns an oldest-first list of Bar objects, or [] if OBB is unavailable
    or the fetch fails (caller should fall back to Yahoo/Upstox).

    Args:
        symbol:        NSE ticker (bare ``RELIANCE`` or already qualified).
        lookback_days: How many calendar days of history to pull.
        provider:      OBB data provider key — ``"yfinance"`` (free),
                       ``"fmp"`` (FMP key required), ``"polygon"`` (key required).
    """
    try:
        from openbb import obb  # lazy import — no crash if not installed
    except ImportError:
        log.debug("OBB not installed — fetch_bars_obb returning []")
        return []

    ysym = _nse_obb_symbol(symbol)
    to_dt = datetime.utcnow().date()
    from_dt = to_dt - timedelta(days=lookback_days)

    try:
        result = await asyncio.to_thread(
            obb.equity.price.historical,
            ysym,
            start_date=from_dt.isoformat(),
            end_date=to_dt.isoformat(),
            provider=provider,
        )
        df = result.to_dataframe().reset_index()
    except Exception as exc:
        log.debug("OBB fetch failed for %s (%s): %s", symbol, provider, exc)
        return []

    if df is None or df.empty:
        return []

    bars: List[Bar] = []
    for row in df.itertuples(index=False):
        try:
            # OBB returns a `date` column (date object) or `datetime`
            raw_date = getattr(row, "date", None) or getattr(row, "datetime", None)
            if raw_date is None:
                continue
            if hasattr(raw_date, "timestamp"):
                ts = int(raw_date.timestamp())
            else:
                # It's a plain date — convert to epoch at midnight UTC
                ts = int(datetime.combine(raw_date, datetime.min.time()).timestamp())

            bars.append(Bar(
                t=ts,
                o=float(getattr(row, "open", 0) or 0),
                h=float(getattr(row, "high", 0) or 0),
                l=float(getattr(row, "low", 0) or 0),
                c=float(getattr(row, "close", 0)),
                v=float(getattr(row, "volume", 0) or 0),
            ))
        except Exception:
            continue

    bars.sort(key=lambda b: b.t)
    log.info("OBB fetched %d bars for %s via %s", len(bars), symbol, provider)
    return bars


# ---------------------------------------------------------------------------
# Universe ingestion
# ---------------------------------------------------------------------------

async def ingest_universe_obb(
    db: AsyncSession,
    symbols: List[str],
    *,
    lookback_days: int = 365,
    provider: str = "yfinance",
    throttle: float = 0.3,
    skip_existing: bool = True,
    min_bars: int = 30,
    fallback_to_yahoo: bool = True,
    progress_cb: Optional[Callable] = None,
) -> dict:
    """Download OBB history for every symbol and persist to the bar store.

    Mirrors the signature and behaviour of ``ingest_universe()`` so it can be
    used as a drop-in replacement. Symbols already in the store (≥ min_bars)
    are skipped. OBB failures fall back to the existing Yahoo/Upstox pipeline
    when ``fallback_to_yahoo=True``.

    Args:
        db:               SQLAlchemy async session (passed to fallback pipeline).
        symbols:          List of NSE tickers.
        lookback_days:    Calendar days of OHLCV history to fetch.
        provider:         OBB provider (``"yfinance"``, ``"fmp"``, etc.).
        throttle:         Seconds between API calls (avoid rate-limiting).
        skip_existing:    Skip symbols that already have ≥ min_bars in store.
        min_bars:         Threshold for "already ingested" check.
        fallback_to_yahoo: On OBB failure, attempt Yahoo/Upstox fetch.
        progress_cb:      Optional ``(done, total, symbol, stats)`` callback.

    Returns:
        Stats dict: requested, ingested_obb, ingested_fallback, skipped, failed, bars_added.
    """
    store_iv = _UPSTOX_INTERVAL.get("day", "day")   # OBB daily bars → "day" key
    started = datetime.utcnow()

    stats = {
        "requested": len(symbols),
        "provider": provider,
        "lookback_days": lookback_days,
        "ingested_obb": 0,
        "ingested_fallback": 0,
        "skipped": 0,
        "failed": 0,
        "bars_added": 0,
    }
    total = len(symbols)
    consecutive_fail = 0

    for i, sym in enumerate(symbols):
        _cb(progress_cb, i, total, sym, stats)

        # --- skip check ---------------------------------------------------
        if skip_existing:
            cov = await asyncio.to_thread(bar_store.symbol_coverage, sym, store_iv)
            if cov.get("count", 0) >= min_bars:
                stats["skipped"] += 1
                _cb(progress_cb, i + 1, total, sym, stats)
                continue

        # --- primary: OBB -------------------------------------------------
        try:
            bars = await fetch_bars_obb(sym, lookback_days=lookback_days, provider=provider)
        except Exception as exc:
            log.warning("OBB ingest error for %s: %s", sym, exc)
            bars = []

        source = "obb"

        # --- fallback: existing Yahoo/Upstox pipeline ---------------------
        if not bars and fallback_to_yahoo:
            log.debug("OBB returned nothing for %s — falling back to Yahoo/Upstox", sym)
            try:
                from .historical import fetch_bars as _fetch_yahoo
                bars = await _fetch_yahoo(db, sym, interval="day",
                                          lookback_days=lookback_days, refresh=True)
                source = "fallback"
            except Exception as exc:
                log.warning("Fallback fetch failed for %s: %s", sym, exc)
                bars = []

        # --- persist to bar store -----------------------------------------
        if bars:
            n = await asyncio.to_thread(bar_store.save_bars, sym, store_iv, bars)
            stats["bars_added"] += n
            if source == "obb":
                stats["ingested_obb"] += 1
            else:
                stats["ingested_fallback"] += 1
            consecutive_fail = 0
            await asyncio.sleep(throttle)
        else:
            stats["failed"] += 1
            consecutive_fail += 1
            back_off = min(throttle * (1 + consecutive_fail), 8.0)
            await asyncio.sleep(back_off)

        _cb(progress_cb, i + 1, total, sym, stats)

    stats["duration_seconds"] = (datetime.utcnow() - started).total_seconds()
    log.info("OBB ingest complete: %s", stats)
    return stats


def _cb(fn, done, total, sym, stats):
    """Safe progress callback invocation."""
    if fn:
        try:
            fn(done, total, sym, stats)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# CLI entry-point  (python -m app.learning.obb_ingest)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    async def _main():
        # Quick smoke-test: fetch 5 Nifty 50 blue chips.
        symbols = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK"]
        print(f"[OBB ingest] fetching {len(symbols)} symbols …")

        # We don't have a real DB session here — skip skip_existing check.
        class _FakeDB:
            pass

        stats = await ingest_universe_obb(
            _FakeDB(),  # type: ignore[arg-type]
            symbols,
            lookback_days=365,
            provider="yfinance",
            skip_existing=False,
            fallback_to_yahoo=False,
            progress_cb=lambda d, t, s, st: print(f"  [{d}/{t}] {s}"),
        )
        print("\nStats:", stats)

    asyncio.run(_main())
