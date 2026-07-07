"""Unit tests for the risk gateway (SyncTestBus + SimClock)."""
from __future__ import annotations

import itertools
from typing import Optional, Tuple

import pytest
from pydantic import ValidationError

from app.core.clock import SimClock
from app.core.events import (
    NS_PER_SEC,
    Bar,
    Fill,
    KillSwitch,
    Order,
    OrderIntent,
    OrderType,
    RiskCheck,
    RiskVerdict,
    Side,
    Streams,
    Tick,
)
from app.risk.gateway import RiskGateway
from app.risk.limits import RiskLimits
from tests.helpers import SyncTestBus

T0 = 1_000 * NS_PER_SEC

CHECK_ORDER = [
    "kill_switch",
    "market_data",
    "signal_age",
    "order_qty",
    "order_notional",
    "price_collar",
    "position_limit",
    "gross_exposure",
    "rate_limit",
]

_ids = itertools.count(1)


def make_env(
    limits: Optional[RiskLimits] = None,
) -> Tuple[SyncTestBus, SimClock, RiskGateway]:
    clock = SimClock(T0)
    bus = SyncTestBus(clock)
    # auto_release_max_tier=3 releases approved orders immediately so these
    # tests exercise the check/reservation layer, not Phase 2 tier routing.
    gateway = RiskGateway(
        bus=bus, clock=clock, limits=limits or RiskLimits(), auto_release_max_tier=3
    )
    return bus, clock, gateway


def set_price(
    bus: SyncTestBus, clock: SimClock, symbol: str = "INFY", price: float = 100.0
) -> None:
    bus.publish(Streams.MD_TICKS, Tick(symbol=symbol, ltp=price), ts_event=clock.now_ns())


def send_intent(
    bus: SyncTestBus,
    clock: SimClock,
    *,
    intent_id: Optional[str] = None,
    strategy_id: str = "strat-1",
    symbol: str = "INFY",
    side: Side = Side.BUY,
    qty: float = 10.0,
    order_type: OrderType = OrderType.MARKET,
    limit_price: Optional[float] = None,
    ts_signal: Optional[int] = None,
) -> RiskVerdict:
    if intent_id is None:
        intent_id = f"i{next(_ids)}"
    intent = OrderIntent(
        intent_id=intent_id,
        strategy_id=strategy_id,
        symbol=symbol,
        side=side,
        qty=qty,
        order_type=order_type,
        limit_price=limit_price,
        ts_signal=clock.now_ns() if ts_signal is None else ts_signal,
    )
    bus.publish(Streams.SIGNAL_INTENTS, intent, ts_event=clock.now_ns())
    verdict = RiskVerdict.model_validate(bus.stream(Streams.RISK_VERDICTS)[-1].payload)
    assert verdict.intent_id == intent_id
    return verdict


def send_fill(
    bus: SyncTestBus,
    clock: SimClock,
    *,
    symbol: str = "INFY",
    side: Side = Side.BUY,
    qty: float,
    price: float = 100.0,
) -> None:
    fill = Fill(
        fill_id=f"f{next(_ids)}",
        order_id="ord-x",
        intent_id="x",
        strategy_id="strat-1",
        symbol=symbol,
        side=side,
        qty=qty,
        price=price,
        ts_fill=clock.now_ns(),
    )
    bus.publish(Streams.EXEC_FILLS, fill, ts_event=clock.now_ns())


def send_kill(
    bus: SyncTestBus, clock: SimClock, *, level: int, engaged: bool, scope: str = "*"
) -> None:
    switch = KillSwitch(level=level, engaged=engaged, scope=scope)
    bus.publish(Streams.CTL_KILL, switch, ts_event=clock.now_ns())


def check_by_name(verdict: RiskVerdict, name: str) -> RiskCheck:
    matches = [c for c in verdict.checks if c.name == name]
    assert len(matches) == 1, f"expected exactly one {name!r} check, got {verdict.checks}"
    return matches[0]


def failed_names(verdict: RiskVerdict) -> list[str]:
    return [c.name for c in verdict.checks if not c.passed]


# -- happy path / verdict + order emission ------------------------------


def test_clean_intent_approved_releases_single_order() -> None:
    bus, clock, gateway = make_env()
    set_price(bus, clock)
    verdict = send_intent(bus, clock, intent_id="i-1")
    assert verdict.approved is True
    # Untrusted strategy -> Tier 3 by policy; still released here because the
    # env uses auto_release_max_tier=3 (tier routing is tested in test_tiers).
    assert verdict.tier == 3
    assert verdict.reject_reason is None
    assert [c.name for c in verdict.checks] == CHECK_ORDER
    assert all(c.passed for c in verdict.checks)
    verdict_event = bus.stream(Streams.RISK_VERDICTS)[-1]
    assert verdict_event.ts_event == clock.now_ns()
    orders = bus.stream(Streams.EXEC_ORDERS)
    assert len(orders) == 1
    order = Order.model_validate(orders[0].payload)
    assert order.order_id == "ord-i-1"
    assert order.intent_id == "i-1"


def test_approved_limit_order_copies_intent_fields() -> None:
    bus, clock, gateway = make_env()
    set_price(bus, clock, "TCS", 200.0)
    verdict = send_intent(
        bus,
        clock,
        intent_id="abc",
        strategy_id="s9",
        symbol="TCS",
        side=Side.SELL,
        qty=7.0,
        order_type=OrderType.LIMIT,
        limit_price=201.0,
    )
    assert verdict.approved
    orders = bus.stream(Streams.EXEC_ORDERS)
    assert len(orders) == 1
    assert orders[0].ts_event == clock.now_ns()
    order = Order.model_validate(orders[0].payload)
    assert order.order_id == "ord-abc"
    assert order.intent_id == "abc"
    assert order.strategy_id == "s9"
    assert order.symbol == "TCS"
    assert order.side is Side.SELL
    assert order.qty == 7.0
    assert order.order_type is OrderType.LIMIT
    assert order.limit_price == 201.0


def test_missing_market_data_rejects_with_full_check_list() -> None:
    bus, clock, gateway = make_env()
    verdict = send_intent(bus, clock, symbol="UNKNOWN")
    assert verdict.approved is False
    assert verdict.tier == 3
    assert [c.name for c in verdict.checks] == CHECK_ORDER  # full audit list
    md = check_by_name(verdict, "market_data")
    assert md.passed is False
    assert md.detail == "no market data"
    assert verdict.reject_reason == "market_data: no market data"
    assert bus.stream(Streams.EXEC_ORDERS) == []


def test_bar_close_sets_last_price() -> None:
    bus, clock, gateway = make_env()
    bar = Bar(
        symbol="INFY",
        ts_open=clock.now_ns() - 60 * NS_PER_SEC,
        interval_s=60,
        open=99.0,
        high=101.0,
        low=98.0,
        close=100.0,
        volume=1_000.0,
    )
    bus.publish(Streams.MD_BARS, bar, ts_event=bar.ts_close)
    verdict = send_intent(bus, clock)
    assert check_by_name(verdict, "market_data").passed
    assert verdict.approved


# -- kill switch ---------------------------------------------------------


def test_kill_switch_k2_blocks_all_until_cleared() -> None:
    bus, clock, gateway = make_env()
    set_price(bus, clock)
    send_kill(bus, clock, level=2, engaged=True, scope="*")
    verdict = send_intent(bus, clock)
    assert not verdict.approved
    assert not check_by_name(verdict, "kill_switch").passed
    assert verdict.reject_reason is not None
    assert verdict.reject_reason.startswith("kill_switch")
    # disengage at a LOWER level does not clear
    send_kill(bus, clock, level=1, engaged=False, scope="*")
    assert not send_intent(bus, clock).approved
    # disengage at the SAME level clears
    send_kill(bus, clock, level=2, engaged=False, scope="*")
    assert send_intent(bus, clock).approved
    # re-engage; disengage at a HIGHER level also clears
    send_kill(bus, clock, level=2, engaged=True, scope="*")
    assert not send_intent(bus, clock).approved
    send_kill(bus, clock, level=3, engaged=False, scope="*")
    assert send_intent(bus, clock).approved


def test_kill_switch_k1_scoped_blocks_only_that_strategy() -> None:
    bus, clock, gateway = make_env()
    set_price(bus, clock)
    send_kill(bus, clock, level=1, engaged=True, scope="strat-1")
    blocked = send_intent(bus, clock, strategy_id="strat-1")
    assert not blocked.approved
    assert failed_names(blocked) == ["kill_switch"]
    other = send_intent(bus, clock, strategy_id="strat-2")
    assert other.approved
    send_kill(bus, clock, level=1, engaged=False, scope="strat-1")
    assert send_intent(bus, clock, strategy_id="strat-1").approved


# -- per-check boundaries -------------------------------------------------


def test_signal_age_boundary() -> None:
    bus, clock, gateway = make_env()  # max_signal_age_ms = 5000
    set_price(bus, clock)
    limit_ns = 5_000 * 1_000_000
    at_limit = send_intent(bus, clock, ts_signal=clock.now_ns() - limit_ns)
    assert check_by_name(at_limit, "signal_age").passed
    assert at_limit.approved
    one_over = send_intent(bus, clock, ts_signal=clock.now_ns() - limit_ns - 1)
    assert not one_over.approved
    assert failed_names(one_over) == ["signal_age"]


def test_order_qty_boundary() -> None:
    bus, clock, gateway = make_env(RiskLimits(max_order_qty=10.0))
    set_price(bus, clock, price=1.0)
    assert send_intent(bus, clock, qty=10.0).approved  # exactly at limit
    one_over = send_intent(bus, clock, qty=11.0)
    assert not one_over.approved
    assert failed_names(one_over) == ["order_qty"]


def test_order_notional_boundary() -> None:
    bus, clock, gateway = make_env(RiskLimits(max_order_notional=1_000.0))
    set_price(bus, clock, price=100.0)
    assert send_intent(bus, clock, qty=10.0).approved  # 1000.0 exactly
    one_over = send_intent(bus, clock, qty=11.0)  # 1100.0
    assert not one_over.approved
    assert failed_names(one_over) == ["order_notional"]


def test_order_notional_uses_limit_price_as_ref() -> None:
    bus, clock, gateway = make_env(
        RiskLimits(max_order_notional=1_000.0, price_collar_pct=100.0)
    )
    set_price(bus, clock, price=100.0)
    # 20 * limit 50 = 1000 exactly; at last price it would be 2000 and fail.
    at_limit = send_intent(
        bus, clock, qty=20.0, order_type=OrderType.LIMIT, limit_price=50.0
    )
    assert at_limit.approved
    one_over = send_intent(
        bus, clock, qty=21.0, order_type=OrderType.LIMIT, limit_price=50.0
    )
    assert not one_over.approved
    assert failed_names(one_over) == ["order_notional"]


def test_price_collar_boundary_and_market_orders_pass() -> None:
    bus, clock, gateway = make_env(RiskLimits(price_collar_pct=25.0))
    set_price(bus, clock, price=128.0)
    # |160 - 128| / 128 * 100 = 25.0 exactly (exact in binary floats)
    at_limit = send_intent(
        bus, clock, qty=10.0, order_type=OrderType.LIMIT, limit_price=160.0
    )
    assert check_by_name(at_limit, "price_collar").passed
    assert at_limit.approved
    one_over = send_intent(
        bus, clock, qty=10.0, order_type=OrderType.LIMIT, limit_price=161.0
    )
    assert not one_over.approved
    assert failed_names(one_over) == ["price_collar"]
    market = send_intent(bus, clock, qty=10.0, order_type=OrderType.MARKET)
    assert check_by_name(market, "price_collar").passed
    assert market.approved


def test_position_limit_with_fills() -> None:
    bus, clock, gateway = make_env(RiskLimits(max_position_qty=100.0))
    set_price(bus, clock, price=1.0)
    assert gateway.position("INFY") == 0.0
    assert send_intent(bus, clock, qty=100.0).approved  # projected exactly 100
    send_fill(bus, clock, qty=100.0, price=1.0)
    assert gateway.position("INFY") == 100.0
    one_over = send_intent(bus, clock, qty=1.0)  # projected 101
    assert not one_over.approved
    assert failed_names(one_over) == ["position_limit"]
    # sells are signed negative: full flip to -100 stays within |limit|
    flip = send_intent(bus, clock, side=Side.SELL, qty=200.0)
    assert flip.approved
    send_fill(bus, clock, side=Side.SELL, qty=30.0, price=1.0)
    assert gateway.position("INFY") == 70.0


def test_gross_exposure_boundary_across_symbols() -> None:
    bus, clock, gateway = make_env(RiskLimits(max_gross_exposure=10_000.0))
    set_price(bus, clock, "AAA", 100.0)
    set_price(bus, clock, "BBB", 100.0)
    send_fill(bus, clock, symbol="AAA", qty=50.0, price=100.0)  # 5000 held
    at_limit = send_intent(bus, clock, symbol="BBB", qty=50.0)  # 10000 exactly
    assert check_by_name(at_limit, "gross_exposure").passed
    assert at_limit.approved
    one_over = send_intent(bus, clock, symbol="BBB", qty=51.0)  # 10100
    assert not one_over.approved
    assert failed_names(one_over) == ["gross_exposure"]


def test_gross_exposure_fails_closed_when_held_symbol_lacks_price() -> None:
    bus, clock, gateway = make_env()
    set_price(bus, clock, "AAA", 100.0)
    send_fill(bus, clock, symbol="ZZZ", qty=10.0, price=5.0)  # no md for ZZZ
    verdict = send_intent(bus, clock, symbol="AAA", qty=1.0)
    assert check_by_name(verdict, "market_data").passed  # AAA itself is fine
    gross = check_by_name(verdict, "gross_exposure")
    assert not gross.passed
    assert "ZZZ" in gross.detail
    assert not verdict.approved


def test_rate_limit_uses_event_time_window() -> None:
    bus, clock, gateway = make_env(RiskLimits(max_orders_per_min_per_strategy=2))
    set_price(bus, clock)
    t0 = clock.now_ns()
    assert send_intent(bus, clock).approved  # approval ts = t0
    clock.advance_to(t0 + 1 * NS_PER_SEC)
    assert send_intent(bus, clock).approved  # approval ts = t0 + 1s
    clock.advance_to(t0 + 2 * NS_PER_SEC)
    third = send_intent(bus, clock)
    assert not third.approved
    assert failed_names(third) == ["rate_limit"]
    assert gateway.approvals_count("strat-1") == 2
    # other strategies have their own window
    assert send_intent(bus, clock, strategy_id="strat-2").approved
    assert gateway.approvals_count("strat-2") == 1
    assert gateway.approvals_count("never-seen") == 0
    # at exactly t0 + 60s the t0 approval leaves the (now-60s, now] window
    clock.advance_to(t0 + 60 * NS_PER_SEC)
    slid = send_intent(bus, clock)
    assert slid.approved
    assert gateway.approvals_count("strat-1") == 2  # t0+1s and t0+60s
    again = send_intent(bus, clock)  # window full again at the same instant
    assert not again.approved
    assert failed_names(again) == ["rate_limit"]


# -- fail-closed paths ----------------------------------------------------


def test_internal_error_rejects_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus, clock, gateway = make_env()
    set_price(bus, clock)

    def boom(self: RiskGateway, intent: OrderIntent, now: int) -> RiskCheck:
        raise RuntimeError("boom")

    monkeypatch.setattr(RiskGateway, "_check_order_qty", boom)
    verdict = send_intent(bus, clock)  # must not raise
    assert not verdict.approved
    assert verdict.tier == 3
    assert len(verdict.checks) == len(CHECK_ORDER)
    internal = check_by_name(verdict, "internal_error")
    assert "boom" in internal.detail
    assert verdict.reject_reason is not None
    assert verdict.reject_reason.startswith("internal_error")
    assert bus.stream(Streams.EXEC_ORDERS) == []


def test_malformed_intent_payload_rejects_not_raises() -> None:
    bus, clock, gateway = make_env()
    # wrong payload type on the intents stream: reject, never raise
    bus.publish(Streams.SIGNAL_INTENTS, Tick(symbol="INFY", ltp=1.0), ts_event=clock.now_ns())
    verdicts = bus.stream(Streams.RISK_VERDICTS)
    assert len(verdicts) == 1
    verdict = RiskVerdict.model_validate(verdicts[0].payload)
    assert not verdict.approved
    assert verdict.checks[0].name == "internal_error"
    assert bus.stream(Streams.EXEC_ORDERS) == []


# -- limits model ---------------------------------------------------------


def test_limits_frozen() -> None:
    limits = RiskLimits()
    with pytest.raises(ValidationError):
        limits.max_order_qty = 1.0  # type: ignore[misc]


def test_conservative_limits_are_tighter_than_defaults() -> None:
    base = RiskLimits()
    tight = RiskLimits.conservative()
    assert tight.max_order_qty < base.max_order_qty
    assert tight.max_order_notional < base.max_order_notional
    assert tight.max_position_qty < base.max_position_qty
    assert tight.max_gross_exposure < base.max_gross_exposure
    assert tight.price_collar_pct < base.price_collar_pct
    assert tight.max_orders_per_min_per_strategy < base.max_orders_per_min_per_strategy
    assert tight.max_signal_age_ms < base.max_signal_age_ms
