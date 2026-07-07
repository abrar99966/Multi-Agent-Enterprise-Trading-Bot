"""Shared test helpers.

SyncTestBus is a minimal, immediate-dispatch EventBus so module unit
tests don't depend on bus/memory.py (built in parallel). Semantics
differ from MemoryBus in one way: delivery happens inside publish()
rather than at run_until_idle(), which is a no-op here. Unit tests must
not rely on cascade ordering subtleties -- the e2e test covers those
against the real MemoryBus.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel

from app.bus.base import EventBus, Handler, stream_matches
from app.core.clock import Clock, SimClock
from app.core.events import Event


class SyncTestBus(EventBus):
    def __init__(self, clock: Optional[Clock] = None) -> None:
        self.clock: Clock = clock or SimClock()
        self.events: List[Event] = []
        self._seqs: Dict[str, int] = {}
        self._subs: List[Tuple[str, Handler]] = []

    def publish(self, stream: str, payload: BaseModel, ts_event: int) -> Event:
        seq = self._seqs.get(stream, 0)
        self._seqs[stream] = seq + 1
        event = Event(
            stream=stream,
            seq=seq,
            ts_event=ts_event,
            ts_recorded=self.clock.now_ns(),
            type=type(payload).__name__,
            payload=payload.model_dump(mode="json"),
        )
        self.events.append(event)
        for pattern, handler in list(self._subs):
            if stream_matches(pattern, stream):
                handler(event)
        return event

    def subscribe(self, pattern: str, handler: Handler) -> None:
        self._subs.append((pattern, handler))

    def run_until_idle(self) -> int:
        return 0

    def stream(self, name: str) -> List[Event]:
        """All captured events for one stream, in publish order."""
        return [e for e in self.events if e.stream == name]
