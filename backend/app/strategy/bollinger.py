"""Bollinger Bands momentum strategy (v2).

Instead of pure mean-reversion (buy low, sell high), this version uses
Bollinger Bands as a BREAKOUT indicator in trend-following mode:
- BUY when price breaks ABOVE the upper band in an uptrend (momentum breakout)
- SELL when price falls back to the middle band (SMA) or trend reverses
- Uses bandwidth expansion as a confirmation signal (widening bands = momentum)
"""
from __future__ import annotations

import math
from collections import deque

from app.bus.base import EventBus
from app.core.clock import Clock
from app.core.events import Bar, Event, OrderIntent, OrderType, Side, Streams


class BollingerBandsStrategy:
    def __init__(
        self,
        bus: EventBus,
        clock: Clock,
        strategy_id: str = "bbands-v0",
        period: int = 20,
        num_std: float = 2.0,
        trend_period: int = 50,
        qty: float = 100.0,
    ) -> None:
        self._bus = bus
        self._clock = clock
        self.strategy_id = strategy_id
        self._period = period
        self._num_std = num_std
        self._trend_period = trend_period
        self._qty = qty
        self._closes: dict[str, deque[float]] = {}
        self._long: set[str] = set()
        self._prev_bandwidth: dict[str, float] = {}
        bus.subscribe(Streams.MD_BARS, self._on_bar)

    def is_long(self, symbol: str) -> bool:
        return symbol in self._long

    def _on_bar(self, event: Event) -> None:
        payload = event.decode()
        if not isinstance(payload, Bar):
            return
        sym = payload.symbol
        max_len = max(self._period, self._trend_period) + 5
        closes = self._closes.setdefault(sym, deque(maxlen=max_len))
        closes.append(payload.close)
        close_list = list(closes)

        if len(close_list) < max(self._period, self._trend_period):
            return

        # Compute Bollinger Bands
        bb_window = close_list[-self._period:]
        sma = sum(bb_window) / self._period
        variance = sum((c - sma) ** 2 for c in bb_window) / self._period
        std = math.sqrt(variance)
        upper = sma + self._num_std * std
        lower = sma - self._num_std * std
        bandwidth = (upper - lower) / sma if sma > 0 else 0

        # Trend filter
        trend_sma = sum(close_list[-self._trend_period:]) / self._trend_period
        uptrend = payload.close > trend_sma and sma > trend_sma

        # Bandwidth expansion: bands widening = increasing momentum
        prev_bw = self._prev_bandwidth.get(sym, bandwidth)
        self._prev_bandwidth[sym] = bandwidth
        bw_expanding = bandwidth > prev_bw

        if sym not in self._long:
            # BUY: price near or above upper band in uptrend with expanding bandwidth
            # This catches momentum breakouts, not mean reversion
            if payload.close >= sma + self._num_std * std * 0.5 and uptrend:
                self._long.add(sym)
                self._emit(payload, Side.BUY, "bb_momentum_breakout", sma, upper, lower)
            # Also: price bouncing off SMA (middle band) in uptrend
            elif uptrend and payload.close <= sma * 1.002 and payload.close >= lower:
                prev_close = close_list[-2] if len(close_list) >= 2 else payload.close
                if payload.close > prev_close:  # bouncing up
                    self._long.add(sym)
                    self._emit(payload, Side.BUY, "bb_sma_bounce", sma, upper, lower)
        else:
            # EXIT: price drops below SMA or trend reverses
            if payload.close < sma and payload.close < trend_sma:
                self._long.discard(sym)
                self._emit(payload, Side.SELL, "bb_trend_break", sma, upper, lower)
            elif payload.close < lower:
                self._long.discard(sym)
                self._emit(payload, Side.SELL, "bb_lower_stop", sma, upper, lower)

    def _emit(
        self, bar: Bar, side: Side, reason: str,
        sma: float, upper: float, lower: float,
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
            attributions={"sma": sma, "upper": upper, "lower": lower},
        )
        self._bus.publish(
            Streams.SIGNAL_INTENTS, intent, ts_event=self._clock.now_ns()
        )
