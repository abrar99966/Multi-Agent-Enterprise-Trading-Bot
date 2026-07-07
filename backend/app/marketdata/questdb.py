"""QuestDB-backed BarStore (optional adapter).

Writes go over ILP (InfluxDB line protocol, port 9009 by default) via
the official ``questdb`` client; reads go over pg-wire (port 8812) via
``psycopg``. Both dependencies are optional and imported lazily so the
platform runs without them; construction fails fast with an install
hint when ``questdb`` is missing.

Upsert semantics require the server table to be created with
``DEDUP UPSERT KEYS(ts, symbol, interval_s)`` -- ILP itself is
append-only and QuestDB deduplication provides the replace-on-conflict
behavior the BarStore contract promises.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from app.core.events import Bar
from app.marketdata.store import BarStore

_QUESTDB_HINT = (
    "QuestDbBarStore requires the optional 'questdb' package;"
    " install it with: pip install questdb"
)
_PSYCOPG_HINT = (
    "QuestDbBarStore reads require the optional 'psycopg' package;"
    " install it with: pip install psycopg[binary]"
)


class QuestDbBarStore(BarStore):
    def __init__(
        self,
        ilp_host: str = "localhost",
        ilp_port: int = 9009,
        pg_host: Optional[str] = None,
        pg_port: int = 8812,
        pg_user: str = "admin",
        pg_password: str = "quest",
        pg_database: str = "qdb",
        table: str = "bars",
    ) -> None:
        try:
            from questdb import ingress
        except ImportError as exc:
            raise RuntimeError(_QUESTDB_HINT) from exc
        self._ingress = ingress
        self._ilp_conf = f"tcp::addr={ilp_host}:{ilp_port};"
        self._pg_host = pg_host if pg_host is not None else ilp_host
        self._pg_port = pg_port
        self._pg_user = pg_user
        self._pg_password = pg_password
        self._pg_database = pg_database
        self._table = table

    def _pg_connect(self) -> Any:
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError(_PSYCOPG_HINT) from exc
        return psycopg.connect(
            host=self._pg_host,
            port=self._pg_port,
            user=self._pg_user,
            password=self._pg_password,
            dbname=self._pg_database,
        )

    def upsert_bars(self, bars: Iterable[Bar]) -> int:
        rows = 0
        with self._ingress.Sender.from_conf(self._ilp_conf) as sender:
            for bar in bars:
                sender.row(
                    self._table,
                    symbols={"symbol": bar.symbol},
                    columns={
                        "interval_s": bar.interval_s,
                        "ts_open": bar.ts_open,
                        "open": bar.open,
                        "high": bar.high,
                        "low": bar.low,
                        "close": bar.close,
                        "volume": bar.volume,
                    },
                    at=self._ingress.TimestampNanos(bar.ts_open),
                )
                rows += 1
        return rows

    def get_bars(
        self,
        symbol: str,
        interval_s: int,
        start_ns: Optional[int] = None,
        end_ns: Optional[int] = None,
    ) -> list[Bar]:
        query = (
            "SELECT symbol, interval_s, ts_open, open, high, low, close, volume"
            f' FROM "{self._table}" WHERE symbol = %s AND interval_s = %s'
        )
        params: list[object] = [symbol, interval_s]
        if start_ns is not None:
            query += " AND ts_open >= %s"
            params.append(start_ns)
        if end_ns is not None:
            query += " AND ts_open <= %s"
            params.append(end_ns)
        query += " ORDER BY ts_open"
        with self._pg_connect() as conn, conn.cursor() as cursor:
            cursor.execute(query, params)
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
        query = f'SELECT DISTINCT symbol FROM "{self._table}" ORDER BY symbol'
        with self._pg_connect() as conn, conn.cursor() as cursor:
            cursor.execute(query)
            return [row[0] for row in cursor.fetchall()]

    def close(self) -> None:
        """Connections are opened per call; nothing persistent to close."""
