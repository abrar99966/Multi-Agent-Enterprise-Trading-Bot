"""EventBus interface.

Implementations:
- bus/memory.py    MemoryBus: single-threaded FIFO dispatch, optional
                   journal tee (the in-process backbone for paper/live v0).
- bus/journal.py   JournalWriter / JournalReader: hash-chained JSONL
                   (format spec in core/hashing.py).
- bus/redpanda.py  RedpandaBus: durable adapter (optional dependency).

Dispatch model: publish() appends to an internal queue; nothing is
delivered until run_until_idle() drains it. Handlers may publish while
handling (cascades); ordering is strict FIFO, so a full session is a
deterministic function of the source events. Subscribers to the same
stream are invoked in subscription order.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable

from pydantic import BaseModel

from app.core.events import Event

Handler = Callable[[Event], None]


def stream_matches(pattern: str, stream: str) -> bool:
    """Exact match, '*' for everything, or prefix wildcard:
    'md.*' matches 'md.bars' but not 'mdx.bars'."""
    if pattern == stream or pattern == "*":
        return True
    if pattern.endswith(".*"):
        return stream.startswith(pattern[:-1])
    return False


class EventBus(ABC):
    @abstractmethod
    def publish(self, stream: str, payload: BaseModel, ts_event: int) -> Event:
        """Wrap payload in an Event -- assigning the per-stream seq and
        ts_recorded from the bus clock -- and enqueue it for dispatch."""

    @abstractmethod
    def subscribe(self, pattern: str, handler: Handler) -> None: ...

    @abstractmethod
    def run_until_idle(self) -> int:
        """Dispatch queued events FIFO (including cascades) until the
        queue is empty. Returns the number of events dispatched."""
