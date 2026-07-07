"""F1: working-order (approved-but-unfilled) exposure reservation.

The Phase 0 gateway tracked position from fills only, so a burst of intents
could be approved past the position limit before any fill landed. These tests
pin the v1 behavior: reservations count toward the limits and are released on
fill / cancel / reject.
"""
from __future__ import annotations

from app.core.clock import SimClock
from app.core.events import (
    Bar,
    Fill,
    OrderIntent,
    OrderStatus,
    OrderUpdate,
    RiskVerdict,
    Side,
    Streams,
)
from app.risk.gateway import RiskGateway
from app.risk.limits import RiskLimits
from tests.helpers import SyncTestBus

_T = 1_750_000_000_000_000_000
# max_position_qty=150 so a second 100-lot breaches once the first is reserved.
_LIMITS = RiskLimits(max_position_qty=150, max_order_qty=1000, max_order_notional=1e12,
                     max_gross_exposure=1e12, max_orders_per_min_per_strategy=1000)


def _setup() -> tuple[SyncTestBus, RiskGateway]:
    clock = SimClock(_T)
    bus = SyncTestBus(clock)
    # Release approved orders immediately (tier routing tested separately) so
    # this file exercises the F1 working-reservation lifecycle.
    gw = RiskGateway(bus, clock, _LIMITS, auto_release_max_tier=3)
    bus.publish(Streams.MD_BARS, _bar("RELI", 100.0), ts_event=_T)  # seed last price
    return bus, gw


def _bar(sym: str, close: float) -> Bar:
    return Bar(symbol=sym, ts_open=_T, interval_s=60, open=close, high=close,
               low=close, close=close, volume=1000.0)


def _intent(iid: str, side: Side = Side.BUY, qty: float = 100.0) -> OrderIntent:
    return OrderIntent(intent_id=iid, strategy_id="s1", symbol="RELI", side=side,
                       qty=qty, ts_signal=_T)


def _fill(iid: str, side: Side = Side.BUY, qty: float = 100.0) -> Fill:
    return Fill(fill_id=f"f-{iid}", order_id=f"ord-{iid}", intent_id=iid,
                strategy_id="s1", symbol="RELI", side=side, qty=qty, price=100.0, ts_fill=_T)


def _verdict(bus: SyncTestBus, intent_id: str) -> RiskVerdict:
    for ev in reversed(bus.stream(Streams.RISK_VERDICTS)):
        v = RiskVerdict.model_validate(ev.payload)
        if v.intent_id == intent_id:
            return v
    raise AssertionError(f"no verdict for {intent_id}")


def test_working_reservation_blocks_burst_before_fill() -> None:
    bus, gw = _setup()
    bus.publish(Streams.SIGNAL_INTENTS, _intent("i1"), ts_event=_T)
    assert _verdict(bus, "i1").approved is True
    assert gw.working("RELI") == 100.0 and gw.position("RELI") == 0.0
    # Second intent, no fill yet: 0 pos + 100 working + 100 = 200 > 150 -> reject.
    bus.publish(Streams.SIGNAL_INTENTS, _intent("i2"), ts_event=_T)
    v2 = _verdict(bus, "i2")
    assert v2.approved is False
    assert v2.reject_reason is not None and "position_limit" in v2.reject_reason
    # Exactly one order (i1) reached exec.orders -- the burst was contained.
    assert len(bus.stream(Streams.EXEC_ORDERS)) == 1


def test_fill_converts_working_to_position_no_double_count() -> None:
    bus, gw = _setup()
    bus.publish(Streams.SIGNAL_INTENTS, _intent("i1"), ts_event=_T)
    bus.publish(Streams.EXEC_FILLS, _fill("i1"), ts_event=_T)
    assert gw.position("RELI") == 100.0
    assert gw.working("RELI") == 0.0  # reservation consumed by the fill, not doubled


def test_reject_releases_working() -> None:
    bus, gw = _setup()
    bus.publish(Streams.SIGNAL_INTENTS, _intent("i1"), ts_event=_T)
    assert gw.working("RELI") == 100.0
    bus.publish(
        Streams.EXEC_ORDER_UPDATES,
        OrderUpdate(order_id="ord-i1", status=OrderStatus.REJECTED, detail="x"),
        ts_event=_T,
    )
    assert gw.working("RELI") == 0.0
    # A fresh intent now fits again.
    bus.publish(Streams.SIGNAL_INTENTS, _intent("i3"), ts_event=_T)
    assert _verdict(bus, "i3").approved is True


def test_cancel_releases_working() -> None:
    bus, gw = _setup()
    bus.publish(Streams.SIGNAL_INTENTS, _intent("i1"), ts_event=_T)
    bus.publish(
        Streams.EXEC_ORDER_UPDATES,
        OrderUpdate(order_id="ord-i1", status=OrderStatus.CANCELLED),
        ts_event=_T,
    )
    assert gw.working("RELI") == 0.0


def test_filled_status_after_fill_is_noop() -> None:
    bus, gw = _setup()
    bus.publish(Streams.SIGNAL_INTENTS, _intent("i1"), ts_event=_T)
    bus.publish(Streams.EXEC_FILLS, _fill("i1"), ts_event=_T)
    bus.publish(
        Streams.EXEC_ORDER_UPDATES,
        OrderUpdate(order_id="ord-i1", status=OrderStatus.FILLED),
        ts_event=_T,
    )
    # FILLED arriving after the fill must not double-release into negative working.
    assert gw.working("RELI") == 0.0
    assert gw.position("RELI") == 100.0
