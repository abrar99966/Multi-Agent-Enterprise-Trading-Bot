"""PositionTracker unit tests: signed average-cost book, realized PnL."""
from __future__ import annotations

import pytest

from app.core.clock import SimClock
from app.core.events import NS_PER_SEC, Fill, PositionSnapshot, Side, Streams
from app.oms.positions import PositionTracker
from tests.helpers import SyncTestBus

T0 = 1_700_000_000 * NS_PER_SEC


@pytest.fixture()
def env() -> tuple[SyncTestBus, PositionTracker]:
    bus = SyncTestBus(clock=SimClock(start_ns=T0))
    tracker = PositionTracker(bus)
    return bus, tracker


_fill_counter = {"n": 0}


def publish_fill(
    bus: SyncTestBus,
    *,
    symbol: str = "RELIANCE",
    side: Side,
    qty: float,
    price: float,
    fees: float = 0.0,
    ts: int = T0,
) -> None:
    _fill_counter["n"] += 1
    n = _fill_counter["n"]
    fill = Fill(
        fill_id=f"fill-ord-{n}-1",
        order_id=f"ord-{n}",
        intent_id=f"int-{n}",
        strategy_id="strat-1",
        symbol=symbol,
        side=side,
        qty=qty,
        price=price,
        fees=fees,
        ts_fill=ts,
    )
    bus.publish(Streams.EXEC_FILLS, fill, ts_event=ts)


def snapshots(bus: SyncTestBus) -> list[PositionSnapshot]:
    return [e.decode() for e in bus.stream(Streams.OMS_POSITIONS)]


# ------------------------------------------------------------ longs


def test_long_open(env) -> None:
    bus, tracker = env
    publish_fill(bus, side=Side.BUY, qty=10.0, price=100.0)

    assert tracker.position("RELIANCE") == (10.0, 100.0)
    assert tracker.realized_pnl("RELIANCE") == 0.0


def test_long_add_reaverages(env) -> None:
    bus, tracker = env
    publish_fill(bus, side=Side.BUY, qty=10.0, price=100.0)
    publish_fill(bus, side=Side.BUY, qty=10.0, price=110.0)

    # (10*100 + 10*110) / 20 = 105
    assert tracker.position("RELIANCE") == (20.0, 105.0)
    assert tracker.realized_pnl("RELIANCE") == 0.0


def test_long_partial_close_realizes_pnl_keeps_avg(env) -> None:
    bus, tracker = env
    publish_fill(bus, side=Side.BUY, qty=10.0, price=100.0)
    publish_fill(bus, side=Side.BUY, qty=10.0, price=110.0)
    publish_fill(bus, side=Side.SELL, qty=4.0, price=120.0)

    # realized = (120 - 105) * 4 = 60; avg cost untouched by the close
    assert tracker.position("RELIANCE") == (16.0, 105.0)
    assert tracker.realized_pnl("RELIANCE") == 60.0


def test_long_full_close_flattens(env) -> None:
    bus, tracker = env
    publish_fill(bus, side=Side.BUY, qty=10.0, price=100.0)
    publish_fill(bus, side=Side.BUY, qty=10.0, price=110.0)
    publish_fill(bus, side=Side.SELL, qty=4.0, price=120.0)
    publish_fill(bus, side=Side.SELL, qty=16.0, price=95.0)

    # 60 + (95 - 105) * 16 = 60 - 160 = -100
    assert tracker.position("RELIANCE") == (0.0, 0.0)
    assert tracker.realized_pnl("RELIANCE") == -100.0
    assert tracker.total_realized_pnl() == -100.0


def test_flip_long_to_short_through_zero(env) -> None:
    bus, tracker = env
    publish_fill(bus, side=Side.BUY, qty=10.0, price=100.0)
    publish_fill(bus, side=Side.SELL, qty=15.0, price=120.0)

    # Old long closed fully: realized = (120 - 100) * 10 = 200.
    # Remainder of 5 opens short at the fill price.
    assert tracker.position("RELIANCE") == (-5.0, 120.0)
    assert tracker.realized_pnl("RELIANCE") == 200.0

    publish_fill(bus, side=Side.BUY, qty=5.0, price=110.0)
    # Short covered: realized += (110 - 120) * 5 * (-1) = +50
    assert tracker.position("RELIANCE") == (0.0, 0.0)
    assert tracker.realized_pnl("RELIANCE") == 250.0


# ----------------------------------------------------------- shorts


def test_short_open_add_and_cover(env) -> None:
    bus, tracker = env
    publish_fill(bus, side=Side.SELL, qty=8.0, price=50.0)
    assert tracker.position("RELIANCE") == (-8.0, 50.0)

    publish_fill(bus, side=Side.SELL, qty=2.0, price=40.0)
    # (-8*50 + -2*40) / -10 = 48
    assert tracker.position("RELIANCE") == (-10.0, 48.0)
    assert tracker.realized_pnl("RELIANCE") == 0.0

    publish_fill(bus, side=Side.BUY, qty=10.0, price=45.0)
    # realized = (45 - 48) * 10 * sign(-10) = +30
    assert tracker.position("RELIANCE") == (0.0, 0.0)
    assert tracker.realized_pnl("RELIANCE") == 30.0


def test_flip_short_to_long_through_zero(env) -> None:
    bus, tracker = env
    publish_fill(bus, side=Side.SELL, qty=5.0, price=100.0)
    publish_fill(bus, side=Side.BUY, qty=12.0, price=90.0)

    # realized = (90 - 100) * 5 * (-1) = +50; remainder long 7 @ 90
    assert tracker.position("RELIANCE") == (7.0, 90.0)
    assert tracker.realized_pnl("RELIANCE") == 50.0


# ------------------------------------------------------------- fees


def test_fees_reduce_realized_even_when_opening(env) -> None:
    bus, tracker = env
    publish_fill(bus, side=Side.BUY, qty=10.0, price=100.0, fees=2.5)
    assert tracker.realized_pnl("RELIANCE") == -2.5

    publish_fill(bus, side=Side.SELL, qty=10.0, price=100.0, fees=2.5)
    # Price PnL is zero; both fee legs hit realized.
    assert tracker.realized_pnl("RELIANCE") == -5.0
    assert tracker.position("RELIANCE") == (0.0, 0.0)


def test_fees_subtract_from_closing_pnl(env) -> None:
    bus, tracker = env
    publish_fill(bus, side=Side.BUY, qty=10.0, price=100.0, fees=1.0)
    publish_fill(bus, side=Side.SELL, qty=10.0, price=110.0, fees=1.1)

    # (110 - 100) * 10 - 1.0 - 1.1 = 97.9
    assert tracker.realized_pnl("RELIANCE") == pytest.approx(97.9)


# -------------------------------------------------------- snapshots


def test_snapshot_published_after_each_fill(env) -> None:
    bus, tracker = env
    t1, t2 = T0, T0 + 60 * NS_PER_SEC
    publish_fill(bus, side=Side.BUY, qty=10.0, price=100.0, ts=t1)
    publish_fill(bus, side=Side.SELL, qty=10.0, price=110.0, ts=t2)

    events = bus.stream(Streams.OMS_POSITIONS)
    assert len(events) == 2
    snaps = snapshots(bus)

    assert snaps[0].symbol == "RELIANCE"
    assert snaps[0].qty == 10.0
    assert snaps[0].avg_price == 100.0
    assert snaps[0].realized_pnl == 0.0
    assert snaps[0].ts == t1
    assert events[0].ts_event == t1

    assert snaps[1].qty == 0.0
    assert snaps[1].avg_price == 0.0
    assert snaps[1].realized_pnl == 100.0
    assert snaps[1].ts == t2
    assert events[1].ts_event == t2


# ------------------------------------------------- accessors / m2m


def test_unknown_symbol_accessors(env) -> None:
    _, tracker = env
    assert tracker.position("NOPE") == (0.0, 0.0)
    assert tracker.realized_pnl("NOPE") == 0.0
    assert tracker.total_realized_pnl() == 0.0
    assert tracker.mark_to_market({}) == {
        "unrealized": {},
        "unrealized_pnl": 0.0,
        "realized_pnl": 0.0,
        "total_pnl": 0.0,
    }


def test_total_realized_sums_across_symbols(env) -> None:
    bus, tracker = env
    publish_fill(bus, symbol="RELIANCE", side=Side.BUY, qty=10.0, price=100.0)
    publish_fill(bus, symbol="RELIANCE", side=Side.SELL, qty=10.0, price=105.0)
    publish_fill(bus, symbol="TCS", side=Side.SELL, qty=5.0, price=200.0)
    publish_fill(bus, symbol="TCS", side=Side.BUY, qty=5.0, price=210.0)

    assert tracker.realized_pnl("RELIANCE") == 50.0
    assert tracker.realized_pnl("TCS") == -50.0
    assert tracker.total_realized_pnl() == 0.0


def test_mark_to_market(env) -> None:
    bus, tracker = env
    publish_fill(bus, symbol="RELIANCE", side=Side.BUY, qty=10.0, price=100.0)
    publish_fill(bus, symbol="TCS", side=Side.SELL, qty=5.0, price=200.0)
    # INFY round trip: flat with realized +4, must not appear in unrealized.
    publish_fill(bus, symbol="INFY", side=Side.BUY, qty=2.0, price=10.0)
    publish_fill(bus, symbol="INFY", side=Side.SELL, qty=2.0, price=12.0)

    m2m = tracker.mark_to_market({"RELIANCE": 110.0, "TCS": 190.0})
    # RELIANCE: (110 - 100) * 10 = +100; TCS: (190 - 200) * -5 = +50
    assert m2m["unrealized"] == {"RELIANCE": 100.0, "TCS": 50.0}
    assert m2m["unrealized_pnl"] == 150.0
    assert m2m["realized_pnl"] == 4.0
    assert m2m["total_pnl"] == 154.0


def test_mark_to_market_missing_price_raises(env) -> None:
    bus, tracker = env
    publish_fill(bus, side=Side.BUY, qty=10.0, price=100.0)
    with pytest.raises(KeyError):
        tracker.mark_to_market({})
