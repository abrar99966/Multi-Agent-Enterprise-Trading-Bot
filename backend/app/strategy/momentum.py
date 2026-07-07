"""SMA-crossover momentum strategy (Phase 0 reference strategy).

Long-only state machine per symbol: a golden cross (fast SMA crossing
strictly above the slow SMA) while flat emits a BUY OrderIntent; a
death cross while long emits a SELL intent that closes the position.

Phase 0 simplification: the internal flat/long state flips
optimistically when the intent is EMITTED, not when an order fills. If
the risk gateway rejects the intent or the order never fills, the
strategy's view diverges from the real book -- the gateway and broker
remain authoritative; the strategy merely stops re-signalling that
side until the opposite cross.
"""
from __future__ import annotations

from collections import deque

from app.bus.base import EventBus
from app.core.clock import Clock
from app.core.events import Bar, Event, OrderIntent, OrderType, Side, Streams


class MomentumStrategy:
    def __init__(
        self,
        bus: EventBus,
        clock: Clock,
        strategy_id: str = "momentum-v0",
        fast: int = 10,
        slow: int = 30,
        qty: float = 100.0,
    ) -> None:
        if not 0 < fast < slow:
            raise ValueError(f"require 0 < fast < slow, got fast={fast} slow={slow}")
        self._bus = bus
        self._clock = clock
        self.strategy_id = strategy_id
        self._fast = fast
        self._slow = slow
        self._qty = float(qty)
        self._closes: dict[str, deque[float]] = {}
        # symbol -> (sma_fast, sma_slow) as of the previous bar.
        self._prev_smas: dict[str, tuple[float, float]] = {}
        self._long: set[str] = set()
        bus.subscribe(Streams.MD_BARS, self._on_bar)

    def is_long(self, symbol: str) -> bool:
        """The strategy's optimistic view (see module docstring)."""
        return symbol in self._long

    def _on_bar(self, event: Event) -> None:
        payload = event.decode()
        if not isinstance(payload, Bar):
            return
        closes = self._closes.setdefault(
            payload.symbol, deque(maxlen=self._slow)
        )
        closes.append(payload.close)
        if len(closes) < self._slow:
            return
        window = list(closes)
        sma_fast = sum(window[-self._fast:]) / self._fast
        sma_slow = sum(window) / self._slow
        prev = self._prev_smas.get(payload.symbol)
        self._prev_smas[payload.symbol] = (sma_fast, sma_slow)
        if prev is None:
            return
        prev_fast, prev_slow = prev
        if prev_fast <= prev_slow and sma_fast > sma_slow:
            if payload.symbol not in self._long:
                self._long.add(payload.symbol)
                self._emit(payload, Side.BUY, "golden_cross", sma_fast, sma_slow)
        elif prev_fast >= prev_slow and sma_fast < sma_slow:
            if payload.symbol in self._long:
                self._long.discard(payload.symbol)
                self._emit(payload, Side.SELL, "death_cross", sma_fast, sma_slow)

    def _emit(
        self, bar: Bar, side: Side, reason: str, sma_fast: float, sma_slow: float
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
            attributions={"sma_fast": sma_fast, "sma_slow": sma_slow},
        )
        self._bus.publish(
            Streams.SIGNAL_INTENTS, intent, ts_event=self._clock.now_ns()
        )
