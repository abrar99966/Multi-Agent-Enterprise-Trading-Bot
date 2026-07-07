"""Durable OHLC bar store (SQLite).

Replaces the old date-windowed JSON cache with a queryable, append-only store so
historical data is downloaded ONCE and reused for unlimited backtests — offline,
repeatably, across every strategy. Lives in its own SQLite file (`market_data.db`)
so the ~1M+ market rows never touch the transactional app DB.

Keyed by (symbol, interval, t); INSERT OR REPLACE makes re-ingests idempotent and
lets new bars extend an existing series without duplicates. Functions are plain
sync (sqlite3) — async callers wrap them in `asyncio.to_thread`. A fresh
connection per call keeps it thread-safe under the to_thread pool.
"""
from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path
from typing import List, Optional

from .bar import Bar

log = logging.getLogger(__name__)

# coverage_summary() scans the whole (1M+ row) table — cache it briefly so the
# Training/Screener tabs that poll it don't eat a ~2s full scan every call.
_COVERAGE_TTL = 20.0
_coverage_cache: dict = {}

DB_PATH = Path(__file__).resolve().parents[2] / ".cache" / "market_data.db"

_DDL = """
CREATE TABLE IF NOT EXISTS bars (
    symbol   TEXT    NOT NULL,
    interval TEXT    NOT NULL,
    t        INTEGER NOT NULL,
    o REAL, h REAL, l REAL, c REAL, v REAL,
    PRIMARY KEY (symbol, interval, t)
) WITHOUT ROWID;
"""

_init_done = False


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _ensure() -> None:
    global _init_done
    if _init_done:
        return
    conn = _connect()
    try:
        conn.executescript(_DDL)
        conn.commit()
    finally:
        conn.close()
    _init_done = True


def save_bars(symbol: str, interval: str, bars: List[Bar]) -> int:
    """Upsert bars for (symbol, interval). Returns the number of rows written."""
    if not bars:
        return 0
    _ensure()
    sym = (symbol or "").upper()
    rows = [
        (sym, interval, int(b.t), b.o, b.h, b.l, b.c, b.v)
        for b in bars if b.t
    ]
    if not rows:
        return 0
    conn = _connect()
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO bars(symbol, interval, t, o, h, l, c, v) "
            "VALUES (?,?,?,?,?,?,?,?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()
    _coverage_cache.clear()   # counts changed — force a fresh summary next call
    return len(rows)


def get_bars(symbol: str, interval: str,
             from_t: Optional[int] = None, to_t: Optional[int] = None) -> List[Bar]:
    """Return stored bars for (symbol, interval) within [from_t, to_t], oldest-first."""
    _ensure()
    sym = (symbol or "").upper()
    q = "SELECT t,o,h,l,c,v FROM bars WHERE symbol=? AND interval=?"
    args: list = [sym, interval]
    if from_t is not None:
        q += " AND t>=?"
        args.append(int(from_t))
    if to_t is not None:
        q += " AND t<=?"
        args.append(int(to_t))
    q += " ORDER BY t ASC"
    conn = _connect()
    try:
        cur = conn.execute(q, args)
        return [Bar(t=r[0], o=r[1], h=r[2], l=r[3], c=r[4], v=r[5]) for r in cur.fetchall()]
    finally:
        conn.close()


def symbol_coverage(symbol: str, interval: str) -> dict:
    """Per-symbol coverage: bar count + first/last epoch for one (symbol, interval)."""
    _ensure()
    sym = (symbol or "").upper()
    conn = _connect()
    try:
        cur = conn.execute(
            "SELECT COUNT(*), MIN(t), MAX(t) FROM bars WHERE symbol=? AND interval=?",
            (sym, interval),
        )
        n, mn, mx = cur.fetchone()
    finally:
        conn.close()
    return {"symbol": sym, "interval": interval, "count": n or 0, "first_t": mn, "last_t": mx}


def coverage_summary(interval: Optional[str] = None) -> dict:
    """Store-wide stats for the API: #symbols, total bars, date span, per-interval breakdown.

    Cached for `_COVERAGE_TTL`s (the underlying COUNT/MIN/MAX scans the full table).
    Writes via save_bars() invalidate the cache so post-ingest counts stay fresh.
    """
    key = interval or "__all__"
    now = time.monotonic()
    cached = _coverage_cache.get(key)
    if cached and now - cached[0] < _COVERAGE_TTL:
        return dict(cached[1])
    _ensure()
    conn = _connect()
    try:
        where = "WHERE interval=?" if interval else ""
        args = [interval] if interval else []
        total = conn.execute(f"SELECT COUNT(*) FROM bars {where}", args).fetchone()[0]
        nsyms = conn.execute(
            f"SELECT COUNT(DISTINCT symbol) FROM bars {where}", args).fetchone()[0]
        span = conn.execute(f"SELECT MIN(t), MAX(t) FROM bars {where}", args).fetchone()
        by_interval = conn.execute(
            "SELECT interval, COUNT(DISTINCT symbol), COUNT(*) FROM bars GROUP BY interval"
        ).fetchall()
    finally:
        conn.close()
    result = {
        "db_path": str(DB_PATH),
        "total_bars": total or 0,
        "symbols": nsyms or 0,
        "first_t": span[0] if span else None,
        "last_t": span[1] if span else None,
        "by_interval": [
            {"interval": iv, "symbols": s, "bars": b} for iv, s, b in by_interval
        ],
    }
    _coverage_cache[key] = (now, dict(result))
    return result


def stored_symbols(interval: str) -> List[str]:
    """Distinct symbols that already have bars for `interval` (used to resume ingests)."""
    _ensure()
    conn = _connect()
    try:
        cur = conn.execute(
            "SELECT DISTINCT symbol FROM bars WHERE interval=? ORDER BY symbol", (interval,))
        return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


def coverage_by_symbol(interval: str) -> List[dict]:
    """Per-symbol coverage for `interval` in ONE query (symbol pickers and
    freshness views need all ~3k rows; per-symbol calls would be 3k queries).
    Oldest-last_t first is the natural 'what needs refreshing' order."""
    _ensure()
    conn = _connect()
    try:
        cur = conn.execute(
            "SELECT symbol, COUNT(*), MIN(t), MAX(t) FROM bars "
            "WHERE interval=? GROUP BY symbol ORDER BY symbol",
            (interval,),
        )
        return [
            {"symbol": s, "bars": n, "first_t": f, "last_t": l}
            for s, n, f, l in cur.fetchall()
        ]
    finally:
        conn.close()
