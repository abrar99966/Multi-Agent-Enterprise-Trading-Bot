"""In-process event bus.

MemoryBus is the single-threaded backbone for paper/live v0. publish()
journals (write-ahead, when a journal is attached) and enqueues; nothing
is delivered until run_until_idle() drains the queue strict-FIFO, so a
full session is a deterministic function of the source events.
"""
from __future__ import annotations

from collections import deque

from pydantic import BaseModel

from app.bus.base import EventBus, Handler, stream_matches
from app.bus.journal import JournalWriter
from app.core.clock import Clock
from app.core.events import Event


class MemoryBus(EventBus):
    def __init__(self, clock: Clock, journal: JournalWriter | None = None) -> None:
        self._clock = clock
        self._journal = journal
        self._queue: deque[Event] = deque()
        self._seqs: dict[str, int] = {}
        self._subs: list[tuple[str, Handler]] = []
        #: every published Event, in publish order (replay/e2e inspection).
        self.events: list[Event] = []

    def publish(self, stream: str, payload: BaseModel, ts_event: int) -> Event:
        """Journal-first write-ahead: the event hits the journal before it
        becomes visible to dispatch, so anything a handler can observe is
        already durable. If the journal append raises, the event is not
        enqueued and the stream's seq is not consumed."""
        seq = self._seqs.get(stream, 0)
        event = Event(
            stream=stream,
            seq=seq,
            ts_event=ts_event,
            ts_recorded=self._clock.now_ns(),
            type=type(payload).__name__,
            payload=payload.model_dump(mode="json"),
        )
        if self._journal is not None:
            self._journal.append(event)
        self._seqs[stream] = seq + 1
        self._queue.append(event)
        self.events.append(event)
        return event

    def subscribe(self, pattern: str, handler: Handler) -> None:
        self._subs.append((pattern, handler))

    def run_until_idle(self, max_events: int = 1_000_000) -> int:
        """Dispatch queued events FIFO until the queue is empty. Events
        published by handlers join the back of the queue. Subscribers are
        snapshotted per event, so a handler subscribing mid-dispatch only
        affects subsequent events."""
        dispatched = 0
        while self._queue:
            if dispatched >= max_events:
                raise RuntimeError(
                    f"run_until_idle dispatched {dispatched} events without "
                    f"draining the queue (max_events={max_events}); likely a "
                    "handler publish loop"
                )
            event = self._queue.popleft()
            dispatched += 1
            for pattern, handler in list(self._subs):
                if stream_matches(pattern, event.stream):
                    handler(event)
        return dispatched
