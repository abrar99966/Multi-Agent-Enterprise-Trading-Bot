"""RSI Momentum strategy (v3).

v1 and v2 still lost money because RSI's natural strength is detecting
momentum, not predicting reversals. This version uses RSI purely as a
MOMENTUM indicator:
- BUY when RSI crosses ABOVE 50 (bullish momentum) + price in uptrend
- SELL when RSI drops BELOW 45 (momentum fading) or trend reverses
- Uses dual SMA crossover as the regime gate
"""
from __future__ import annotations

from collections import deque

from app.bus.base import EventBus
from app.core.clock import Clock
from app.core.events import Bar, Event, OrderIntent, OrderType, Side, Streams


class RSIStrategy:
    def __init__(
        self,
        bus: EventBus,
        clock: Clock,
        strategy_id: str = "rsi-v0",
        period: int = 14,
        oversold: float = 30.0,
        overbought: float = 70.0,
        trend_sma: int = 50,
        fast_sma: int = 10,
        qty: float = 100.0,
    ) -> None:
        self._bus = bus
        self._clock = clock
        self.strategy_id = strategy_id
        self._period = period
        self._oversold = oversold
        self._overbought = overbought
        self._trend_sma = trend_sma
        self._fast_sma = fast_sma
        self._qty = qty
        self._closes: dict[str, deque[float]] = {}
        self._prev_rsi: dict[str, float | None] = {}
        self._long: set[str] = set()
        bus.subscribe(Streams.MD_BARS, self._on_bar)

    def is_long(self, symbol: str) -> bool:
        return symbol in self._long

    @staticmethod
    def _compute_rsi(closes: list[float], period: int) -> float | None:
        if len(closes) < period + 1:
            return None
        gains = losses = 0.0
        for i in range(len(closes) - period, len(closes)):
            delta = closes[i] - closes[i - 1]
            if delta > 0:
                gains += delta
            else:
                losses -= delta
        avg_gain = gains / period
        avg_loss = losses / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def _on_bar(self, event: Event) -> None:
        payload = event.decode()
        if not isinstance(payload, Bar):
            return
        sym = payload.symbol
        max_len = max(self._period + 10, self._trend_sma + 5)
        closes = self._closes.setdefault(sym, deque(maxlen=max_len))
        closes.append(payload.close)
        close_list = list(closes)

        rsi = self._compute_rsi(close_list, self._period)
        if rsi is None or len(close_list) < self._trend_sma:
            self._prev_rsi[sym] = rsi
            return

        # Regime: fast SMA vs slow SMA
        slow_sma = sum(close_list[-self._trend_sma:]) / self._trend_sma
        fast_sma_val = sum(close_list[-self._fast_sma:]) / self._fast_sma
        uptrend = fast_sma_val > slow_sma

        prev_rsi = self._prev_rsi.get(sym)
        self._prev_rsi[sym] = rsi

        if prev_rsi is None:
            return

        if sym not in self._long:
            # BUY: RSI crossing above 50 (momentum confirmed) + uptrend
            if prev_rsi < 50 and rsi >= 50 and uptrend:
                self._long.add(sym)
                self._emit(payload, Side.BUY, "rsi_momentum_up", rsi)
            # Also: RSI > 55 and strong uptrend (catch mid-trend entries)
            elif rsi > 55 and uptrend and fast_sma_val > slow_sma * 1.002:
                if prev_rsi <= 55:  # only on first cross
                    self._long.add(sym)
                    self._emit(payload, Side.BUY, "rsi_strong_momentum", rsi)
        else:
            # SELL: momentum fading or trend reversal
            if rsi < 45 and rsi < prev_rsi:
                self._long.discard(sym)
                self._emit(payload, Side.SELL, "rsi_momentum_fade", rsi)
            elif not uptrend and rsi < 50:
                self._long.discard(sym)
                self._emit(payload, Side.SELL, "rsi_trend_reversal", rsi)
            elif rsi >= self._overbought and rsi < prev_rsi:
                # Overbought and turning down — take profit
                self._long.discard(sym)
                self._emit(payload, Side.SELL, "rsi_overbought_tp", rsi)

    def _emit(self, bar: Bar, side: Side, reason: str, rsi: float) -> None:
        intent = OrderIntent(
            intent_id=f"{self.strategy_id}:{bar.symbol}:{bar.ts_open}",
            strategy_id=self.strategy_id,
            symbol=bar.symbol,
            side=side,
            qty=self._qty,
            order_type=OrderType.MARKET,
            ts_signal=bar.ts_close,
            reason=reason,
            attributions={"rsi": rsi},
        )
        self._bus.publish(
            Streams.SIGNAL_INTENTS, intent, ts_event=self._clock.now_ns()
        )
