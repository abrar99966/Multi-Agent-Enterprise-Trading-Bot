"""EMA triple-crossover trend strategy.

Uses three EMA periods (fast/medium/slow). Buys when all three align
bullish (fast > medium > slow) and sells when the alignment breaks
(fast < medium). More selective than a simple dual-SMA crossover.
"""
from __future__ import annotations

from app.bus.base import EventBus
from app.core.clock import Clock
from app.core.events import Bar, Event, OrderIntent, OrderType, Side, Streams


class TripleEMAStrategy:
    def __init__(
        self,
        bus: EventBus,
        clock: Clock,
        strategy_id: str = "3ema-v0",
        fast: int = 5,
        medium: int = 13,
        slow: int = 26,
        qty: float = 100.0,
    ) -> None:
        self._bus = bus
        self._clock = clock
        self.strategy_id = strategy_id
        self._fast_p = fast
        self._med_p = medium
        self._slow_p = slow
        self._qty = qty
        self._ema_fast: dict[str, float | None] = {}
        self._ema_med: dict[str, float | None] = {}
        self._ema_slow: dict[str, float | None] = {}
        self._bar_count: dict[str, int] = {}
        self._prev_aligned: dict[str, bool | None] = {}
        self._long: set[str] = set()
        bus.subscribe(Streams.MD_BARS, self._on_bar)

    def is_long(self, symbol: str) -> bool:
        return symbol in self._long

    @staticmethod
    def _ema_update(prev: float | None, value: float, period: int) -> float:
        if prev is None:
            return value
        a = 2.0 / (period + 1.0)
        return a * value + (1 - a) * prev

    def _on_bar(self, event: Event) -> None:
        payload = event.decode()
        if not isinstance(payload, Bar):
            return
        sym = payload.symbol
        self._bar_count[sym] = self._bar_count.get(sym, 0) + 1
        c = payload.close

        self._ema_fast[sym] = self._ema_update(self._ema_fast.get(sym), c, self._fast_p)
        self._ema_med[sym] = self._ema_update(self._ema_med.get(sym), c, self._med_p)
        self._ema_slow[sym] = self._ema_update(self._ema_slow.get(sym), c, self._slow_p)

        if self._bar_count[sym] < self._slow_p:
            return

        ef = self._ema_fast[sym]
        em = self._ema_med[sym]
        es = self._ema_slow[sym]
        aligned = ef > em > es
        prev = self._prev_aligned.get(sym)
        self._prev_aligned[sym] = aligned

        if prev is None:
            return

        if not prev and aligned and sym not in self._long:
            self._long.add(sym)
            self._emit(payload, Side.BUY, "triple_ema_align", ef, em, es)
        elif prev and not aligned and sym in self._long:
            self._long.discard(sym)
            self._emit(payload, Side.SELL, "triple_ema_break", ef, em, es)

    def _emit(
        self, bar: Bar, side: Side, reason: str,
        ema_fast: float, ema_med: float, ema_slow: float,
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
                "ema_fast": ema_fast,
                "ema_med": ema_med,
                "ema_slow": ema_slow,
            },
        )
        self._bus.publish(
            Streams.SIGNAL_INTENTS, intent, ts_event=self._clock.now_ns()
        )
