"""MACD trend-following strategy.

Buys on MACD line crossing above the signal line (bullish momentum);
sells on MACD crossing below signal (bearish momentum). Classic
trend-following approach that responds faster than SMA crossovers.
"""
from __future__ import annotations

from collections import deque

from app.bus.base import EventBus
from app.core.clock import Clock
from app.core.events import Bar, Event, OrderIntent, OrderType, Side, Streams


class MACDStrategy:
    def __init__(
        self,
        bus: EventBus,
        clock: Clock,
        strategy_id: str = "macd-v0",
        fast_period: int = 12,
        slow_period: int = 26,
        signal_period: int = 9,
        qty: float = 100.0,
    ) -> None:
        self._bus = bus
        self._clock = clock
        self.strategy_id = strategy_id
        self._fast_period = fast_period
        self._slow_period = slow_period
        self._signal_period = signal_period
        self._qty = qty
        self._closes: dict[str, deque[float]] = {}
        self._ema_fast: dict[str, float | None] = {}
        self._ema_slow: dict[str, float | None] = {}
        self._ema_signal: dict[str, float | None] = {}
        self._prev_histogram: dict[str, float | None] = {}
        self._bar_count: dict[str, int] = {}
        self._long: set[str] = set()
        bus.subscribe(Streams.MD_BARS, self._on_bar)

    def is_long(self, symbol: str) -> bool:
        return symbol in self._long

    @staticmethod
    def _update_ema(prev: float | None, value: float, period: int) -> float:
        if prev is None:
            return value
        alpha = 2.0 / (period + 1.0)
        return alpha * value + (1 - alpha) * prev

    def _on_bar(self, event: Event) -> None:
        payload = event.decode()
        if not isinstance(payload, Bar):
            return
        sym = payload.symbol
        self._bar_count[sym] = self._bar_count.get(sym, 0) + 1

        self._ema_fast[sym] = self._update_ema(
            self._ema_fast.get(sym), payload.close, self._fast_period
        )
        self._ema_slow[sym] = self._update_ema(
            self._ema_slow.get(sym), payload.close, self._slow_period
        )
        if self._bar_count[sym] < self._slow_period:
            return

        macd_line = self._ema_fast[sym] - self._ema_slow[sym]
        self._ema_signal[sym] = self._update_ema(
            self._ema_signal.get(sym), macd_line, self._signal_period
        )
        if self._bar_count[sym] < self._slow_period + self._signal_period:
            self._prev_histogram[sym] = macd_line - self._ema_signal[sym]
            return

        histogram = macd_line - self._ema_signal[sym]
        prev_hist = self._prev_histogram.get(sym)
        self._prev_histogram[sym] = histogram

        if prev_hist is None:
            return

        # Bullish crossover: histogram flips positive
        if prev_hist <= 0 and histogram > 0 and sym not in self._long:
            self._long.add(sym)
            self._emit(payload, Side.BUY, "macd_bullish_cross", macd_line, self._ema_signal[sym], histogram)
        # Bearish crossover: histogram flips negative
        elif prev_hist >= 0 and histogram < 0 and sym in self._long:
            self._long.discard(sym)
            self._emit(payload, Side.SELL, "macd_bearish_cross", macd_line, self._ema_signal[sym], histogram)

    def _emit(
        self, bar: Bar, side: Side, reason: str,
        macd_line: float, signal_line: float, histogram: float,
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
            attributions={
                "macd": macd_line,
                "signal": signal_line,
                "histogram": histogram,
            },
        )
        self._bus.publish(
            Streams.SIGNAL_INTENTS, intent, ts_event=self._clock.now_ns()
        )
