"""TCA store: SQLite round-trip + ClickHouse optional-dependency guard."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.events import Side
from app.tca.engine import TcaResult
from app.tca.shortfall import implementation_shortfall
from app.tca.store import ClickHouseTcaStore, SqliteTcaStore


def _result(fill_id: str, markouts: dict[int, float]) -> TcaResult:
    b = implementation_shortfall(Side.BUY, 10, 100.0, 101.0, 101.2, fees=1.0)
    return TcaResult(
        fill_id=fill_id, order_id=f"ord-{fill_id}", intent_id=f"int-{fill_id}",
        strategy_id="s1", symbol="X", side=Side.BUY, qty=10,
        ts_decision=1, ts_fill=2, breakdown=b, markouts_bps=markouts,
    )


def test_sqlite_round_trip(tmp_path: Path) -> None:
    with SqliteTcaStore(tmp_path / "tca.db") as store:
        n = store.insert([_result("f1", {1: 5.0, 5: -2.0}), _result("f2", {1: 1.0})])
        assert n == 2
        rows = store.all()
    assert len(rows) == 2
    r0 = rows[0]
    assert r0["fill_id"] == "f1"
    assert abs(r0["delay_bps"] - 100.0) < 1e-9
    assert json.loads(r0["markouts_bps"]) == {"1": 5.0, "5": -2.0}


def test_sqlite_insert_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "tca.db"
    with SqliteTcaStore(path) as store:
        store.insert([_result("f1", {1: 5.0})])
        store.insert([_result("f1", {1: 9.0})])  # same fill_id -> replace
        rows = store.all()
    assert len(rows) == 1
    assert json.loads(rows[0]["markouts_bps"]) == {"1": 9.0}


def test_clickhouse_requires_dependency() -> None:
    # clickhouse-connect is not installed here: construction must raise clearly.
    with pytest.raises(RuntimeError, match="clickhouse-connect"):
        ClickHouseTcaStore()
