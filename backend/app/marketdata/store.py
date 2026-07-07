"""Durable bar storage.

BarStore is the persistence boundary for historical OHLCV bars: replay
sources read from it, ingest paths write to it. Implementations must be
idempotent on (symbol, interval_s, ts_open) so re-ingesting a window is
safe (upsert semantics).
"""
from __future__ import annotations

import sqlite3
from abc import ABC, abstractmethod
from pathlib import Path
from types import TracebackType
from typing import Iterable, Optional

from app.core.events import Bar

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bars (
    symbol     TEXT    NOT NULL,
    interval_s INTEGER NOT NULL,
    ts_open    INTEGER NOT NULL,
    open       REAL    NOT NULL,
    high       REAL    NOT NULL,
    low        REAL    NOT NULL,
    close      REAL    NOT NULL,
    volume     REAL    NOT NULL,
    PRIMARY KEY (symbol, interval_s, ts_open)
)
"""


class BarStore(ABC):
    """Abstract bar store. Context-manager support closes on exit."""

    @abstractmethod
    def upsert_bars(self, bars: Iterable[Bar]) -> int:
        """Insert-or-replace bars keyed on (symbol, interval_s, ts_open).
        Returns the number of rows written."""

    @abstractmethod
    def get_bars(
        self,
        symbol: str,
        interval_s: int,
        start_ns: Optional[int] = None,
        end_ns: Optional[int] = None,
    ) -> list[Bar]:
        """Bars for one symbol/interval ordered by ts_open ascending.
        start_ns/end_ns bound ts_open and are both inclusive."""

    @abstractmethod
    def symbols(self) -> list[str]:
        """Distinct symbols present, sorted ascending."""

    @abstractmethod
    def close(self) -> None: ...

    def __enter__(self) -> "BarStore":
        return self

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        self.close()


class SqliteBarStore(BarStore):
    """SQLite-backed store (WAL mode). The Phase 0 default: zero-ops,
    single-file, good enough for daily/minute history at NSE scale."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def upsert_bars(self, bars: Iterable[Bar]) -> int:
        rows = [
            (
                bar.symbol,
                bar.interval_s,
                bar.ts_open,
                bar.open,
                bar.high,
                bar.low,
                bar.close,
                bar.volume,
            )
            for bar in bars
        ]
        self._conn.executemany(
            "INSERT OR REPLACE INTO bars"
            " (symbol, interval_s, ts_open, open, high, low, close, volume)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        self._conn.commit()
        return len(rows)

    def get_bars(
        self,
        symbol: str,
        interval_s: int,
        start_ns: Optional[int] = None,
        end_ns: Optional[int] = None,
    ) -> list[Bar]:
        query = (
            "SELECT symbol, interval_s, ts_open, open, high, low, close, volume"
            " FROM bars WHERE symbol = ? AND interval_s = ?"
        )
        params: list[object] = [symbol, interval_s]
        if start_ns is not None:
            query += " AND ts_open >= ?"
            params.append(start_ns)
        if end_ns is not None:
            query += " AND ts_open <= ?"
            params.append(end_ns)
        query += " ORDER BY ts_open"
        cursor = self._conn.execute(query, params)
        return [
            Bar(
                symbol=row[0],
                interval_s=row[1],
                ts_open=row[2],
                open=row[3],
                high=row[4],
                low=row[5],
                close=row[6],
                volume=row[7],
            )
            for row in cursor.fetchall()
        ]

    def symbols(self) -> list[str]:
        cursor = self._conn.execute(
            "SELECT DISTINCT symbol FROM bars ORDER BY symbol"
        )
        return [row[0] for row in cursor.fetchall()]

    def close(self) -> None:
        self._conn.close()
