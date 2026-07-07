"""Bulk historical-data ingestion into the durable bar store.

Downloads OHLC for a whole universe ONCE and persists it, so every later
backtest runs offline against stored data. Built for the realities of free
data sources:
  • Resumable — symbols already in the store are skipped, so a re-run only
    fills gaps (safe to stop and restart).
  • Throttled — a delay between network fetches, widened after failures, to
    stay under Yahoo's aggressive rate limiting (HTTP 429).
  • Seeded — optionally imports the legacy JSON cache first, for free.

Run it in the background (see the /learning/data/ingest API) and poll progress.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Callable, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from . import bar_store
from .historical import _UPSTOX_INTERVAL, fetch_bars, migrate_cache_to_store

log = logging.getLogger(__name__)


def _store_interval(interval: str) -> str:
    """The interval key the store uses (matches what fetch_bars persists under)."""
    return _UPSTOX_INTERVAL.get(interval, "30minute")


async def ingest_universe(
    db: AsyncSession, symbols: List[str], *,
    interval: str = "day", lookback_days: int = 1095,
    throttle: float = 0.4, skip_existing: bool = True, min_bars: int = 30,
    seed_from_cache: bool = True,
    progress_cb: Optional[Callable] = None,
) -> dict:
    """Download + persist history for every symbol; return run stats.

    Defaults to DAILY bars over ~3 years — one request per symbol and not
    subject to Yahoo's intraday lookback caps, ideal for a durable research store.
    """
    started = datetime.utcnow()
    store_iv = _store_interval(interval)

    if seed_from_cache:
        try:
            seeded = await migrate_cache_to_store()
            log.info("Ingest seeded from cache: %s", seeded)
        except Exception as exc:
            log.warning("Cache seed failed (continuing): %s", exc)

    stats = {
        "requested": len(symbols), "interval": store_iv, "lookback_days": lookback_days,
        "ingested": 0, "skipped": 0, "failed": 0, "bars_added": 0,
    }
    total = len(symbols)
    consecutive_fail = 0

    for i, sym in enumerate(symbols):
        if progress_cb:
            try:
                progress_cb(i, total, sym, stats)
            except Exception:
                pass
        try:
            if skip_existing:
                cov = await asyncio.to_thread(bar_store.symbol_coverage, sym, store_iv)
                if cov["count"] >= min_bars:
                    stats["skipped"] += 1
                    if progress_cb:
                        try:
                            progress_cb(i + 1, total, sym, stats)
                        except Exception:
                            pass
                    continue

            bars = await fetch_bars(db, sym, interval=interval,
                                    lookback_days=lookback_days, refresh=True)
            if bars:
                stats["ingested"] += 1
                stats["bars_added"] += len(bars)
                consecutive_fail = 0
                await asyncio.sleep(throttle)
            else:
                stats["failed"] += 1
                consecutive_fail += 1
                # Likely rate-limited / no data — back off progressively.
                await asyncio.sleep(min(throttle * (1 + consecutive_fail), 8.0))
        except Exception as exc:
            stats["failed"] += 1
            consecutive_fail += 1
            log.warning("Ingest failed for %s: %s", sym, exc)
            await asyncio.sleep(min(throttle * (1 + consecutive_fail), 8.0))

        if progress_cb:
            try:
                progress_cb(i + 1, total, sym, stats)
            except Exception:
                pass

    stats["duration_seconds"] = (datetime.utcnow() - started).total_seconds()
    log.info("Ingest complete: %s", stats)
    return stats
