"""Tests for the market data store, synthetic generator, and replay source."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterator

import pytest

from app.core.clock import SimClock
from app.core.events import NS_PER_SEC, Bar, Event, Streams
from app.marketdata.questdb import QuestDbBarStore
from app.marketdata.replay import ReplaySource, StoreReplaySource
from app.marketdata.store import SqliteBarStore
from app.marketdata.synthetic import generate_bars
from tests.helpers import SyncTestBus

START_TS = 1_700_000_000 * NS_PER_SEC


@pytest.fixture()
def store(tmp_path: Path) -> Iterator[SqliteBarStore]:
    s = SqliteBarStore(tmp_path / "nested" / "dir" / "bars.db")
    yield s
    s.close()


# ---------------------------------------------------------------- sqlite


def test_sqlite_creates_parent_dirs_and_file(tmp_path: Path) -> None:
    path = tmp_path / "a" / "b" / "bars.db"
    with SqliteBarStore(path) as s:
        assert path.exists()
        assert s.symbols() == []


def test_sqlite_round_trip_ordered_by_ts_open(store: SqliteBarStore) -> None:
    bars = generate_bars("AAA", 50, START_TS)
    written = store.upsert_bars(reversed(bars))  # insertion order scrambled
    assert written == 50
    assert store.get_bars("AAA", 60) == bars  # sorted ascending by ts_open


def test_sqlite_upsert_idempotent_and_replaces(store: SqliteBarStore) -> None:
    bars = generate_bars("AAA", 20, START_TS)
    assert store.upsert_bars(bars) == 20
    assert store.upsert_bars(bars) == 20  # re-ingest is a no-op replace
    assert store.get_bars("AAA", 60) == bars

    revised = bars[7].model_copy(update={"close": 999.0})
    store.upsert_bars([revised])
    got = store.get_bars("AAA", 60)
    assert len(got) == 20  # still one row per (symbol, interval_s, ts_open)
    assert got[7].close == 999.0


def test_sqlite_range_filters_inclusive(store: SqliteBarStore) -> None:
    bars = generate_bars("AAA", 10, START_TS)
    store.upsert_bars(bars)
    ts = lambda k: bars[k].ts_open  # noqa: E731

    assert store.get_bars("AAA", 60, start_ns=ts(3)) == bars[3:]
    assert store.get_bars("AAA", 60, end_ns=ts(6)) == bars[: 6 + 1]
    assert store.get_bars("AAA", 60, start_ns=ts(3), end_ns=ts(6)) == bars[3 : 6 + 1]
    assert store.get_bars("AAA", 60, start_ns=ts(9) + 1) == []


def test_sqlite_separates_symbols_and_intervals(store: SqliteBarStore) -> None:
    a60 = generate_bars("AAA", 5, START_TS, interval_s=60)
    a300 = generate_bars("AAA", 5, START_TS, interval_s=300)
    b60 = generate_bars("BBB", 5, START_TS, interval_s=60)
    store.upsert_bars(a60 + a300 + b60)

    assert store.symbols() == ["AAA", "BBB"]
    assert store.get_bars("AAA", 60) == a60
    assert store.get_bars("AAA", 300) == a300
    assert store.get_bars("CCC", 60) == []


def test_sqlite_context_manager_persists_then_closes(tmp_path: Path) -> None:
    path = tmp_path / "bars.db"
    bars = generate_bars("AAA", 5, START_TS)
    with SqliteBarStore(path) as s:
        s.upsert_bars(bars)
    with pytest.raises(sqlite3.ProgrammingError):
        s.symbols()  # connection closed on exit
    with SqliteBarStore(path) as reopened:
        assert reopened.get_bars("AAA", 60) == bars


# -------------------------------------------------------------- questdb


def test_questdb_construction_raises_with_install_hint() -> None:
    with pytest.raises(RuntimeError, match=r"pip install questdb"):
        QuestDbBarStore(ilp_host="localhost")


# ------------------------------------------------------------ synthetic


def test_synthetic_deterministic() -> None:
    a = generate_bars("RELIANCE", 200, START_TS)
    b = generate_bars("RELIANCE", 200, START_TS)
    assert a == b
    assert generate_bars("TCS", 200, START_TS) != a  # symbol changes the path
    assert generate_bars("RELIANCE", 200, START_TS, seed=7) != a


def test_synthetic_ohlc_invariants() -> None:
    bars = generate_bars("SYM", 500, START_TS, interval_s=60)
    assert len(bars) == 500
    for k, bar in enumerate(bars):
        assert bar.symbol == "SYM"
        assert bar.interval_s == 60
        assert bar.ts_open == START_TS + k * 60 * NS_PER_SEC
        assert bar.ts_close == bar.ts_open + 60 * NS_PER_SEC
        assert bar.high >= max(bar.open, bar.close)
        assert bar.low <= min(bar.open, bar.close)
        assert bar.low > 0.0
        assert bar.volume > 0.0
    for prev, nxt in zip(bars, bars[1:]):
        assert nxt.open == prev.close


def test_synthetic_sma_crossovers_occur() -> None:
    closes = [bar.close for bar in generate_bars("SYM", 500, START_TS)]

    def sma(i: int, w: int) -> float:
        return sum(closes[i - w + 1 : i + 1]) / w

    diffs = [sma(i, 10) - sma(i, 30) for i in range(29, len(closes))]
    signs = [d > 0 for d in diffs if d != 0.0]
    crossings = sum(1 for a, b in zip(signs, signs[1:]) if a != b)
    assert crossings > 0
    assert crossings >= 3  # "several" regime flips per 500 bars


# --------------------------------------------------------------- replay


def test_replay_publishes_in_global_order_with_clock_advanced() -> None:
    clock = SimClock()
    bus = SyncTestBus(clock)
    clock_at_delivery: list[tuple[int, int]] = []  # (ts_event, clock.now_ns())

    def handler(event: Event) -> None:
        clock_at_delivery.append((event.ts_event, clock.now_ns()))

    bus.subscribe(Streams.MD_BARS, handler)
    bars_a = generate_bars("AAA", 20, START_TS)
    bars_b = generate_bars("BBB", 20, START_TS)
    source = ReplaySource(bus, clock, bars_b + bars_a)  # unsorted input

    assert source.run() == 40

    events = bus.stream(Streams.MD_BARS)
    assert len(events) == 40
    keys = []
    for event in events:
        bar = event.decode()
        assert isinstance(bar, Bar)
        assert event.ts_event == bar.ts_close  # published at close
        assert event.ts_recorded == event.ts_event  # clock advanced first
        keys.append((event.ts_event, bar.symbol))
    assert keys == sorted(keys)  # global (ts_close, symbol) order
    for ts_event, now_ns in clock_at_delivery:
        assert now_ns == ts_event  # handlers see the bar-close clock
    assert clock.now_ns() == max(b.ts_close for b in bars_a + bars_b)


def test_store_replay_source_merges_symbols_from_store(
    store: SqliteBarStore,
) -> None:
    bars_a = generate_bars("AAA", 10, START_TS)
    bars_b = generate_bars("BBB", 10, START_TS)
    other_interval = generate_bars("AAA", 10, START_TS, interval_s=300)
    store.upsert_bars(bars_a + bars_b + other_interval)

    clock = SimClock()
    bus = SyncTestBus(clock)
    source = StoreReplaySource.from_store(bus, clock, store, ["AAA", "BBB"], 60)

    assert source.run() == 20  # interval-300 bars excluded

    events = bus.stream(Streams.MD_BARS)
    keys = [(e.ts_event, e.decode().symbol) for e in events]
    assert keys == sorted(keys)
    assert {e.decode().interval_s for e in events} == {60}
    assert {e.decode().symbol for e in events} == {"AAA", "BBB"}
