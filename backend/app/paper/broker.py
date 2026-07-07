"""Paper trading broker: deterministic, bar-driven fill simulation.

Consumes approved Orders from exec.orders and simulates an exchange
against md.bars, publishing Fills and OrderUpdates. Every timestamp
comes from injected sources (the event envelope and the Clock), so
replaying the same event stream reproduces identical fills.
"""
from __future__ import annotations

from app.bus.base import EventBus
from app.core.clock import Clock
from app.core.events import (
    Bar,
    Event,
    Fill,
    Order,
    OrderStatus,
    OrderType,
    OrderUpdate,
    Side,
    Streams,
)


class PaperBroker:
    """Phase 0 fill model.

    - No partial fills: an order fills its entire quantity in a single
      Fill on one bar, or not at all on that bar.
    - Orders are GTC within the session: unfilled orders remain in
      ``pending`` indefinitely (no expiry in Phase 0).
    - Anti-lookahead: an order placed at ``placed_ts`` (the bus
      ``ts_recorded`` of its Order event) may only fill on a bar with
      ``bar.ts_open >= placed_ts``. The signal bar whose close produced
      the order has ``ts_open < placed_ts``, so a replayed signal bar
      can never fill the order it caused.
    - MARKET fills at the bar open adjusted for slippage: BUY at
      ``open * (1 + slippage_bps / 1e4)``, SELL at
      ``open * (1 - slippage_bps / 1e4)``.
    - LIMIT BUY fills at the open on a favorable gap (``open <= limit``),
      else at the limit if the bar traded through it (``low <= limit``);
      LIMIT SELL is the mirror (``open >= limit`` / ``high >= limit``).
    - A LIMIT order without a limit_price is REJECTED on receipt (it
      could never price a fill).
    """

    def __init__(
        self,
        bus: EventBus,
        clock: Clock,
        slippage_bps: float = 2.0,
        fee_bps: float = 1.0,
    ) -> None:
        self._bus = bus
        self._clock = clock
        self._slippage_bps = slippage_bps
        self._fee_bps = fee_bps
        # Open orders by order_id, exposed for inspection.
        self.pending: dict[str, Order] = {}
        self._placed_ts: dict[str, int] = {}
        bus.subscribe(Streams.EXEC_ORDERS, self._on_order)
        bus.subscribe(Streams.MD_BARS, self._on_bar)

    def _on_order(self, event: Event) -> None:
        payload = event.decode()
        if not isinstance(payload, Order):
            return
        if payload.order_type is OrderType.LIMIT and payload.limit_price is None:
            self._publish_update(
                payload.order_id,
                OrderStatus.REJECTED,
                ts_event=self._clock.now_ns(),
                detail="LIMIT order without limit_price",
            )
            return
        self.pending[payload.order_id] = payload
        self._placed_ts[payload.order_id] = event.ts_recorded
        self._publish_update(
            payload.order_id, OrderStatus.ACKED, ts_event=self._clock.now_ns()
        )

    def _on_bar(self, event: Event) -> None:
        payload = event.decode()
        if not isinstance(payload, Bar):
            return
        for order_id, order in list(self.pending.items()):
            if order.symbol != payload.symbol:
                continue
            if payload.ts_open < self._placed_ts[order_id]:
                # Anti-lookahead: the bar predates the order.
                continue
            price = self._fill_price(order, payload)
            if price is not None:
                self._fill(order, price, payload.ts_open)

    def _fill_price(self, order: Order, bar: Bar) -> float | None:
        """Fill price on this bar, or None if the order stays pending."""
        if order.order_type is OrderType.MARKET:
            slip = self._slippage_bps / 1e4
            if order.side is Side.BUY:
                return bar.open * (1 + slip)
            return bar.open * (1 - slip)
        limit = order.limit_price
        if limit is None:  # unreachable: rejected at acceptance
            return None
        if order.side is Side.BUY:
            if bar.open <= limit:
                return bar.open
            if bar.low <= limit:
                return limit
            return None
        if bar.open >= limit:
            return bar.open
        if bar.high >= limit:
            return limit
        return None

    def _fill(self, order: Order, price: float, ts_fill: int) -> None:
        fees = order.qty * price * self._fee_bps / 1e4
        fill = Fill(
            fill_id=f"fill-{order.order_id}-1",
            order_id=order.order_id,
            intent_id=order.intent_id,
            strategy_id=order.strategy_id,
            symbol=order.symbol,
            side=order.side,
            qty=order.qty,
            price=price,
            fees=fees,
            ts_fill=ts_fill,
        )
        del self.pending[order.order_id]
        del self._placed_ts[order.order_id]
        self._bus.publish(Streams.EXEC_FILLS, fill, ts_event=ts_fill)
        self._publish_update(order.order_id, OrderStatus.FILLED, ts_event=ts_fill)

    def _publish_update(
        self, order_id: str, status: OrderStatus, ts_event: int, detail: str = ""
    ) -> None:
        self._bus.publish(
            Streams.EXEC_ORDER_UPDATES,
            OrderUpdate(order_id=order_id, status=status, detail=detail),
            ts_event=ts_event,
        )
