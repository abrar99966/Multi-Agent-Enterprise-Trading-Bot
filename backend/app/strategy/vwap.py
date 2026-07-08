"""VWAP trend-following strategy (v2).

Instead of buying BELOW VWAP (which fails in trending markets), this
version follows the trend:
- BUY when price crosses ABOVE VWAP with momentum confirmation
- SELL when price crosses BELOW VWAP (trend flip)
- VWAP acts as a dynamic support/resistance level
"""
from __future__ import annotations

from collections import deque

from app.bus.base import EventBus
from app.core.clock import Clock
from app.core.events import Bar, Event, OrderIntent, OrderType, Side, Streams


class VWAPStrategy:
    def __init__(
        self,
        bus: EventBus,
        clock: Clock,
        strategy_id: str = "vwap-v0",
        period: int = 30,
        trend_sma: int = 40,
        cross_confirm_bars: int = 2,
        qty: float = 100.0,
    ) -> None:
        self._bus = bus
        self._clock = clock
        self.strategy_id = strategy_id
        self._period = period
        self._trend_sma = trend_sma
        self._confirm_bars = cross_confirm_bars
        self._qty = qty
        self._bars_buf: dict[str, deque[Bar]] = {}
        self._closes: dict[str, deque[float]] = {}
        self._above_vwap_count: dict[str, int] = {}
        self._below_vwap_count: dict[str, int] = {}
        self._long: set[str] = set()
        bus.subscribe(Streams.MD_BARS, self._on_bar)

    def is_long(self, symbol: str) -> bool:
        return symbol in self._long

    def _compute_vwap(self, bars: list[Bar]) -> float:
        total_pv = sum(b.close * b.volume for b in bars)
        total_vol = sum(b.volume for b in bars)
        return total_pv / total_vol if total_vol > 0 else bars[-1].close

    def _on_bar(self, event: Event) -> None:
        payload = event.decode()
        if not isinstance(payload, Bar):
            return
        sym = payload.symbol
        max_close_len = max(self._period, self._trend_sma) + 5
        bar_buf = self._bars_buf.setdefault(sym, deque(maxlen=self._period))
        bar_buf.append(payload)
        closes = self._closes.setdefault(sym, deque(maxlen=max_close_len))
        closes.append(payload.close)
        close_list = list(closes)

        if len(bar_buf) < self._period or len(close_list) < self._trend_sma:
            return

        vwap = self._compute_vwap(list(bar_buf))
        trend_sma_val = sum(close_list[-self._trend_sma:]) / self._trend_sma

        # Track consecutive bars above/below VWAP
        if payload.close > vwap:
            self._above_vwap_count[sym] = self._above_vwap_count.get(sym, 0) + 1
            self._below_vwap_count[sym] = 0
        else:
            self._below_vwap_count[sym] = self._below_vwap_count.get(sym, 0) + 1
            self._above_vwap_count[sym] = 0

        above_count = self._above_vwap_count.get(sym, 0)
        below_count = self._below_vwap_count.get(sym, 0)
        uptrend = payload.close > trend_sma_val

        if sym not in self._long:
            # BUY: price confirmed above VWAP for N bars + uptrend
            if above_count >= self._confirm_bars and uptrend:
                self._long.add(sym)
                deviation = (payload.close - vwap) / vwap
                self._emit(payload, Side.BUY, "vwap_trend_follow", vwap, deviation)
        else:
            # SELL: price drops below VWAP for N bars or trend reversal
            if below_count >= self._confirm_bars:
                deviation = (payload.close - vwap) / vwap
                self._long.discard(sym)
                self._emit(payload, Side.SELL, "vwap_trend_flip", vwap, deviation)
            elif not uptrend and payload.close < vwap:
                deviation = (payload.close - vwap) / vwap
                self._long.discard(sym)
                self._emit(payload, Side.SELL, "vwap_trend_break", vwap, deviation)

    def _emit(
        self, bar: Bar, side: Side, reason: str,
        vwap: float, deviation: float,
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
            attributions={"vwap": vwap, "deviation": deviation},
        )
        self._bus.publish(
            Streams.SIGNAL_INTENTS, intent, ts_event=self._clock.now_ns()
        )
