"""TCA persistence.

TcaStore is the analytics sink for per-fill TCA (design: ClickHouse for TCA /
audit queries). Phase 2 ships a SQLite default so the pipeline runs with zero
infra, plus a ClickHouse adapter that activates when the optional dependency
is installed -- mirroring the QuestDB/Redpanda pattern.
"""
from __future__ import annotations

import json
import sqlite3
from abc import ABC, abstractmethod
from pathlib import Path
from types import TracebackType
from typing import Iterable, List

from app.tca.engine import TcaResult


def _row(result: TcaResult) -> dict:
    b = result.breakdown
    return {
        "fill_id": result.fill_id,
        "order_id": result.order_id,
        "intent_id": result.intent_id,
        "strategy_id": result.strategy_id,
        "symbol": result.symbol,
        "side": result.side.value,
        "qty": result.qty,
        "ts_fill": result.ts_fill,
        "decision_price": b.decision_price,
        "arrival_price": b.arrival_price,
        "fill_price": b.fill_price,
        "notional": b.notional,
        "delay_bps": b.delay_bps,
        "execution_bps": b.execution_bps,
        "fees_bps": b.fees_bps,
        "total_is_bps": b.total_is_bps,
        "total_is_cost": b.total_is,
        "markouts_bps": json.dumps(
            {str(k): v for k, v in sorted(result.markouts_bps.items())}
        ),
    }


# Explicit column order (kept in sync with the keys _row() produces).
COLUMNS = [
    "fill_id", "order_id", "intent_id", "strategy_id", "symbol", "side", "qty",
    "ts_fill", "decision_price", "arrival_price", "fill_price", "notional",
    "delay_bps", "execution_bps", "fees_bps", "total_is_bps", "total_is_cost",
    "markouts_bps",
]


class TcaStore(ABC):
    @abstractmethod
    def insert(self, results: Iterable[TcaResult]) -> int: ...

    @abstractmethod
    def all(self) -> List[dict]: ...

    def close(self) -> None:  # pragma: no cover - default no-op
        pass

    def __enter__(self) -> "TcaStore":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


class SqliteTcaStore(TcaStore):
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        cols = ", ".join(f"{c} {self._coltype(c)}" for c in COLUMNS)
        self._conn.execute(
            f"CREATE TABLE IF NOT EXISTS tca ({cols}, PRIMARY KEY(fill_id))"
        )
        self._conn.commit()

    @staticmethod
    def _coltype(col: str) -> str:
        if col in {"fill_id", "order_id", "intent_id", "strategy_id", "symbol",
                   "side", "markouts_bps"}:
            return "TEXT"
        if col in {"ts_fill"}:
            return "INTEGER"
        return "REAL"

    def insert(self, results: Iterable[TcaResult]) -> int:
        rows = [_row(r) for r in results]
        if not rows:
            return 0
        placeholders = ", ".join("?" for _ in COLUMNS)
        sql = f"INSERT OR REPLACE INTO tca ({', '.join(COLUMNS)}) VALUES ({placeholders})"
        self._conn.executemany(sql, [[row[c] for c in COLUMNS] for row in rows])
        self._conn.commit()
        return len(rows)

    def all(self) -> List[dict]:
        cur = self._conn.execute(f"SELECT {', '.join(COLUMNS)} FROM tca ORDER BY ts_fill, fill_id")
        return [dict(zip(COLUMNS, r)) for r in cur.fetchall()]

    def close(self) -> None:
        self._conn.close()


class ClickHouseTcaStore(TcaStore):
    """ClickHouse-backed TCA store (optional dependency). Activates only when
    ``clickhouse-connect`` is installed; otherwise construction raises with an
    install hint, matching the QuestDB/Redpanda adapters."""

    def __init__(self, host: str = "localhost", port: int = 8123, database: str = "etb") -> None:
        try:
            import clickhouse_connect  # type: ignore
        except ImportError as exc:  # pragma: no cover - exercised only without the dep
            raise RuntimeError(
                "ClickHouseTcaStore needs clickhouse-connect: pip install clickhouse-connect"
            ) from exc
        self._client = clickhouse_connect.get_client(
            host=host, port=port, database=database
        )
        self._client.command(
            "CREATE TABLE IF NOT EXISTS tca ("
            "fill_id String, order_id String, intent_id String, strategy_id String, "
            "symbol String, side String, qty Float64, ts_fill Int64, "
            "decision_price Float64, arrival_price Float64, fill_price Float64, "
            "notional Float64, delay_bps Float64, execution_bps Float64, "
            "fees_bps Float64, total_is_bps Float64, total_is_cost Float64, "
            "markouts_bps String) ENGINE = ReplacingMergeTree ORDER BY fill_id"
        )

    def insert(self, results: Iterable[TcaResult]) -> int:
        rows = [_row(r) for r in results]
        if not rows:
            return 0
        self._client.insert("tca", [[row[c] for c in COLUMNS] for row in rows], column_names=COLUMNS)
        return len(rows)

    def all(self) -> List[dict]:
        res = self._client.query(f"SELECT {', '.join(COLUMNS)} FROM tca ORDER BY ts_fill, fill_id")
        return [dict(zip(COLUMNS, r)) for r in res.result_rows]

    def close(self) -> None:
        self._client.close()
