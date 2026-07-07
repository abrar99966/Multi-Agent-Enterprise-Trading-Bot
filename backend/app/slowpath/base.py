"""Base class for slow-path agents.

The slow path (regime classifier, LLM analysts) is BEST-EFFORT and must never
take down the fast path. SlowPathAgent guards every bus callback in a
try/except: an exception inside an agent's logic is swallowed and counted, not
propagated to the bus dispatch loop. So a hung or crashing analyst (an LLM
outage, a bad model output, a bug) is harmless -- trading continues on the
last-known-good / TTL-decayed parameters, exactly as the design requires
(docs/TARGET_ARCHITECTURE.md section 6: "the fast path never waits on the
slow path").

Determinism note: the guard does not read the clock or randomize; on success
it is a plain dispatch, so deterministic agents stay replay-deterministic.
"""
from __future__ import annotations

from typing import Callable

from app.bus.base import EventBus
from app.core.events import Event


class SlowPathAgent:
    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        #: count of swallowed handler exceptions (observability/tests).
        self.errors = 0

    def _guarded(self, handler: Callable[[Event], None]) -> Callable[[Event], None]:
        def wrapper(event: Event) -> None:
            try:
                handler(event)
            except Exception:  # slow path is best-effort: never break the bus
                self.errors += 1

        return wrapper

    def subscribe(self, pattern: str, handler: Callable[[Event], None]) -> None:
        """Subscribe with failure isolation. Use this instead of bus.subscribe
        for every slow-path handler."""
        self._bus.subscribe(pattern, self._guarded(handler))
