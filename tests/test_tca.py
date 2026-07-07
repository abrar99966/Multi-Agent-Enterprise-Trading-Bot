"""TCA: Implementation Shortfall math + streaming engine."""
from __future__ import annotations

from app.core.clock import SimClock
from app.core.events import Bar, Fill, OrderIntent, Side, Streams
from app.tca.engine import TcaEngine
from app.tca.shortfall import implementation_shortfall, markout_bps
from tests.helpers import SyncTestBus

_T = 1_750_000_000_000_000_000
_IVL = 60_000_000_000  # 60s in ns


# ---------------------------------------------------------------- shortfall math


def test_is_decomposition_buy() -> None:
    b = implementation_shortfall(Side.BUY, qty=10, decision_price=100.0,
                                 arrival_price=101.0, fill_price=101.2, fees=1.0)
    assert b.delay_cost == 10.0          # (101-100)*10
    assert abs(b.execution_cost - 2.0) < 1e-9  # (101.2-101)*10
    assert abs(b.total_is - 13.0) < 1e-9       # 10 + 2 + 1 fee
    assert b.notional == 1000.0
    assert abs(b.delay_bps - 100.0) < 1e-9
    assert abs(b.execution_bps - 20.0) < 1e-9
    assert abs(b.total_is_bps - 130.0) < 1e-9


def test_is_sign_for_sell() -> None:
    # Selling BELOW the decision price is a cost (positive); above is a gain.
    worse = implementation_shortfall(Side.SELL, 10, 100.0, 99.0, 98.0)
    assert worse.delay_cost > 0 and worse.execution_cost > 0
    better = implementation_shortfall(Side.SELL, 10, 100.0, 101.0, 102.0)
    assert better.delay_cost < 0 and better.execution_cost < 0


def test_markout_sign() -> None:
    # Bought at 100: price rising to 101 is favorable (positive).
    assert markout_bps(Side.BUY, 100.0, 101.0) > 0
    assert markout_bps(Side.BUY, 100.0, 99.0) < 0
    # Sold at 100: price falling is favorable.
    assert markout_bps(Side.SELL, 100.0, 99.0) > 0


# ---------------------------------------------------------------- engine


def _bar(sym: str, ts_open: int, o: float, c: float) -> Bar:
    return Bar(symbol=sym, ts_open=ts_open, interval_s=60, open=o, high=max(o, c) + 1,
               low=min(o, c) - 1, close=c, volume=1000.0)


def test_engine_computes_is_and_markout() -> None:
    clock = SimClock(_T)
    bus = SyncTestBus(clock)
    eng = TcaEngine(bus, markout_horizons=(1,))
    # Signal bar: close 100 = decision price.
    bus.publish(Streams.MD_BARS, _bar("X", _T, 100.0, 100.0), ts_event=_T)
    bus.publish(
        Streams.SIGNAL_INTENTS,
        OrderIntent(intent_id="i1", strategy_id="s1", symbol="X", side=Side.BUY,
                    qty=10, ts_signal=_T),
        ts_event=_T,
    )
    # Fill bar: opens at 101 (arrival), fill at 101.2.
    bus.publish(Streams.MD_BARS, _bar("X", _T + _IVL, 101.0, 101.5), ts_event=_T + _IVL)
    bus.publish(
        Streams.EXEC_FILLS,
        Fill(fill_id="f1", order_id="ord-i1", intent_id="i1", strategy_id="s1",
             symbol="X", side=Side.BUY, qty=10, price=101.2, fees=0.0,
             ts_fill=_T + _IVL),
        ts_event=_T + _IVL,
    )
    results = eng.results()
    assert len(results) == 1
    r = results[0]
    assert r.breakdown.decision_price == 100.0
    assert r.breakdown.arrival_price == 101.0
    assert r.breakdown.fill_price == 101.2
    assert abs(r.breakdown.delay_bps - 100.0) < 1e-6
    assert abs(r.breakdown.execution_bps - 20.0) < 1e-6
    # Markout horizon 1: bar at ts_fill + 60s, close 102.
    assert 1 not in r.markouts_bps
    bus.publish(Streams.MD_BARS, _bar("X", _T + 2 * _IVL, 102.0, 102.0), ts_event=_T + 2 * _IVL)
    assert 1 in r.markouts_bps and r.markouts_bps[1] > 0  # price rose after the buy


def test_engine_summary_keys() -> None:
    clock = SimClock(_T)
    bus = SyncTestBus(clock)
    eng = TcaEngine(bus, markout_horizons=(1,))
    assert eng.summary() == {"n_fills": 0}
    bus.publish(Streams.MD_BARS, _bar("X", _T, 100.0, 100.0), ts_event=_T)
    bus.publish(
        Streams.SIGNAL_INTENTS,
        OrderIntent(intent_id="i1", strategy_id="s1", symbol="X", side=Side.BUY,
                    qty=10, ts_signal=_T),
        ts_event=_T,
    )
    bus.publish(Streams.MD_BARS, _bar("X", _T + _IVL, 100.0, 100.0), ts_event=_T + _IVL)
    bus.publish(
        Streams.EXEC_FILLS,
        Fill(fill_id="f1", order_id="ord-i1", intent_id="i1", strategy_id="s1",
             symbol="X", side=Side.BUY, qty=10, price=100.0, ts_fill=_T + _IVL),
        ts_event=_T + _IVL,
    )
    s = eng.summary()
    assert s["n_fills"] == 1.0
    assert "total_is_bps" in s and "delay_bps" in s and "execution_bps" in s
