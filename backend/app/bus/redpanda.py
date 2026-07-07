"""Redpanda/Kafka durable tee (Phase 0: outbound mirror only).

RedpandaPublisher mirrors already-journaled Events to per-stream topics
for external consumers; it is not an EventBus and never dispatches.
confluent-kafka is an optional dependency, imported only on construction.
"""
from __future__ import annotations

from typing import Any

from app.core.events import Event
from app.core.hashing import canonical_json


class RedpandaPublisher:
    def __init__(self, brokers: str, topic_prefix: str = "etb") -> None:
        try:
            from confluent_kafka import Producer
        except ImportError as exc:
            raise RuntimeError(
                "RedpandaPublisher requires the optional confluent-kafka "
                "package; install it with `pip install confluent-kafka`, or "
                "leave ETB_REDPANDA_BROKERS empty to disable the adapter"
            ) from exc
        self._topic_prefix = topic_prefix
        self._producer: Any = Producer({"bootstrap.servers": brokers})

    def publish_event(self, event: Event) -> None:
        """Produce to '<prefix>.<stream>', keyed by the payload's symbol
        when present (per-symbol partition ordering) else the stream name.
        Delivery is async; call flush() to drain."""
        symbol = event.payload.get("symbol")
        key = symbol if isinstance(symbol, str) and symbol else event.stream
        self._producer.produce(
            topic=f"{self._topic_prefix}.{event.stream}",
            key=key.encode("utf-8"),
            value=canonical_json(event.model_dump(mode="json")),
        )
        self._producer.poll(0)

    def flush(self) -> None:
        self._producer.flush()
