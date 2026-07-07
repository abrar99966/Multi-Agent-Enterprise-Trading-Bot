"""Replay bars through the bus under a simulated clock.

This is the determinism backbone of backtests: bars are published in
global (ts_close, symbol) order, the SimClock is advanced to each bar's
close before publishing, and the bus is drained after every publish so
the full downstream cascade (signal -> risk -> order -> fill) completes
before the next bar exists. A session is therefore a pure function of
the bar sequence.
"""
from __future__ import annotations

from typing import Iterable

from app.bus.base import EventBus
from app.core.clock import SimClock
from app.core.events import Bar, Streams

from app.marketdata.store import BarStore


class ReplaySource:
    def __init__(self, bus: EventBus, clock: SimClock, bars: Iterable[Bar]) -> None:
        self._bus = bus
        self._clock = clock
        self._bars: list[Bar] = list(bars)

    def run(self) -> int:
        """Publish every bar to md.bars in (ts_close, symbol) order,
        draining the bus after each publish. Returns bars published."""
        published = 0
        for bar in sorted(self._bars, key=lambda b: (b.ts_close, b.symbol)):
            self._clock.advance_to(bar.ts_close)
            self._bus.publish(Streams.MD_BARS, bar, ts_event=bar.ts_close)
            self._bus.run_until_idle()
            published += 1
        return published


class StoreReplaySource(ReplaySource):
    """ReplaySource builder that pulls its bars from a BarStore."""

    @classmethod
    def from_store(
        cls,
        bus: EventBus,
        clock: SimClock,
        store: BarStore,
        symbols: list[str],
        interval_s: int,
    ) -> "StoreReplaySource":
        merged: list[Bar] = []
        for symbol in symbols:
            merged.extend(store.get_bars(symbol, interval_s))
        return cls(bus, clock, merged)
