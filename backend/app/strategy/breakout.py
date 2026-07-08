"""Breakout / channel strategy.

Uses a Donchian-like channel: buys when price breaks above the N-bar
high (breakout) and sells when price breaks below the N-bar low
(breakdown). Turtle-traders-inspired trend-capture strategy.
"""
from __future__ import annotations

from collections import deque

from app.bus.base import EventBus
from app.core.clock import Clock
from app.core.events import Bar, Event, OrderIntent, OrderType, Side, Streams


class BreakoutStrategy:
    def __init__(
        self,
        bus: EventBus,
        clock: Clock,
        strategy_id: str = "breakout-v0",
        period: int = 20,
        qty: float = 100.0,
    ) -> None:
        self._bus = bus
        self._clock = clock
        self.strategy_id = strategy_id
        self._period = period
        self._qty = qty
        self._highs: dict[str, deque[float]] = {}
        self._lows: dict[str, deque[float]] = {}
        self._long: set[str] = set()
        bus.subscribe(Streams.MD_BARS, self._on_bar)

    def is_long(self, symbol: str) -> bool:
        return symbol in self._long

    def _on_bar(self, event: Event) -> None:
        payload = event.decode()
        if not isinstance(payload, Bar):
            return
        sym = payload.symbol
        highs = self._highs.setdefault(sym, deque(maxlen=self._period))
        lows = self._lows.setdefault(sym, deque(maxlen=self._period))

        if len(highs) >= self._period:
            channel_high = max(highs)
            channel_low = min(lows)

            if payload.close > channel_high and sym not in self._long:
                self._long.add(sym)
                self._emit(payload, Side.BUY, "channel_breakout", channel_high, channel_low)
            elif payload.close < channel_low and sym in self._long:
                self._long.discard(sym)
                self._emit(payload, Side.SELL, "channel_breakdown", channel_high, channel_low)

        highs.append(payload.high)
        lows.append(payload.low)

    def _emit(
        self, bar: Bar, side: Side, reason: str,
        ch_high: float, ch_low: float,
    ) -> None:
        intent = OrderIntent(
            intent_id=f"{self.strategy_id}:{bar.symbol}:{bar.ts_open}",
            strategy_id=self.strategy_id,
            symbol=bar.symbol,
            side=side,
            qty=self._qty,
            order_type=OrderType.MARKET,
            ts_signal=bar.ts_close,
            reason=reason,
            attributions={"channel_high": ch_high, "channel_low": ch_low},
        )
        self._bus.publish(
            Streams.SIGNAL_INTENTS, intent, ts_event=self._clock.now_ns()
        )
