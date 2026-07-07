"""Clock abstraction.

Decision components never read the wall clock directly -- they take a
Clock. Live trading injects LiveClock; replay/backtest injects SimClock
advanced from event timestamps, which is what makes a replay reproduce
live decisions exactly. All times are integer nanoseconds UTC.
"""
from __future__ import annotations

import time
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    def now_ns(self) -> int: ...


class LiveClock:
    def now_ns(self) -> int:
        return time.time_ns()


class SimClock:
    """Deterministic clock driven by the event source. Monotonic:
    advance_to() with an older timestamp is a no-op."""

    def __init__(self, start_ns: int = 0) -> None:
        self._now_ns = start_ns

    def advance_to(self, ts_ns: int) -> None:
        if ts_ns > self._now_ns:
            self._now_ns = ts_ns

    def now_ns(self) -> int:
        return self._now_ns
