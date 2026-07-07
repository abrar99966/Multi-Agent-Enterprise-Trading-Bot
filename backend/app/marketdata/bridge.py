"""Bridge: legacy durable bar store -> event-pipeline Bars.

The Part-1 bar store (`learning/bar_store.py`, ~2.3M real OHLCV rows across
~2,900 NSE symbols, epoch-second timestamps, interval strings) predates the
event-sourced pipeline (`core.events.Bar`, nanosecond timestamps, interval in
seconds). This module converts between the two so real market history can
drive PaperSession -- the same deterministic journaled pipeline that runs on
synthetic data, now on actual bars.

Pure conversion + reads; no writes to either store.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from app.core.events import NS_PER_SEC, Bar
from app.learning.bar import Bar as LegacyBar

#: legacy interval label -> seconds
INTERVAL_SECONDS: Dict[str, int] = {
    "minute": 60,
    "3minute": 180,
    "5minute": 300,
    "10minute": 600,
    "15minute": 900,
    "30minute": 1800,
    "60minute": 3600,
    "hour": 3600,
    "day": 86_400,
    "week": 7 * 86_400,
}


def to_core_bar(symbol: str, legacy: LegacyBar, interval: str) -> Bar:
    """Convert one legacy bar. Legacy `t` is the bar's epoch-second timestamp;
    o/h/l/c may be 0.0 for index rows -- fall back to close, mirroring how the
    strategy library treats missing fields."""
    interval_s = INTERVAL_SECONDS.get(interval)
    if interval_s is None:
        raise ValueError(f"unknown legacy interval {interval!r}")
    close = legacy.c
    open_ = legacy.o if legacy.o else close
    high = legacy.h if legacy.h else max(open_, close)
    low = legacy.l if legacy.l else min(open_, close)
    return Bar(
        symbol=symbol.upper(),
        ts_open=legacy.t * NS_PER_SEC,
        interval_s=interval_s,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=max(legacy.v or 0.0, 0.0),
    )


def load_store_bars(
    symbols: List[str],
    interval: str = "day",
    last_n: Optional[int] = 500,
    from_t: Optional[int] = None,
    to_t: Optional[int] = None,
) -> List[Bar]:
    """Read real bars for `symbols` from the legacy store and convert.

    `last_n` keeps only the most recent N bars per symbol (None = all).
    Returns all symbols' bars concatenated; ReplaySource does the global
    (ts, symbol) sort. Raises ValueError when a symbol has no stored data,
    so a typo fails loudly instead of producing an empty backtest.
    """
    from app.learning import bar_store

    out: List[Bar] = []
    for symbol in symbols:
        legacy = bar_store.get_bars(symbol.upper(), interval, from_t=from_t, to_t=to_t)
        if not legacy:
            raise ValueError(
                f"no stored {interval!r} bars for {symbol!r} -- ingest first "
                "(POST /api/v1/learning/data/ingest)"
            )
        if last_n is not None:
            legacy = legacy[-last_n:]
        out.extend(to_core_bar(symbol, b, interval) for b in legacy)
    return out
