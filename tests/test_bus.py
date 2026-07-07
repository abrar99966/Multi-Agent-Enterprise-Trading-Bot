"""MemoryBus dispatch semantics and the RedpandaPublisher import guard."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from app.bus.journal import JournalReader, JournalWriter
from app.bus.memory import MemoryBus
from app.core.clock import SimClock
from app.core.events import Bar, Event, OrderIntent, Side, Streams, Tick

NS = 1_000_000_000


def make_bar(symbol: str = "RELIANCE", ts_open: int = 100 * NS, close: float = 100.0) -> Bar:
    return Bar(
        symbol=symbol,
        ts_open=ts_open,
        interval_s=60,
        open=99.0,
        high=101.0,
        low=98.5,
        close=close,
        volume=1000.0,
    )


def make_intent(intent_id: str, ts_signal: int = 200 * NS) -> OrderIntent:
    return OrderIntent(
        intent_id=intent_id,
        strategy_id="s1",
        symbol="RELIANCE",
        side=Side.BUY,
        qty=1.0,
        ts_signal=ts_signal,
    )


def test_publish_enqueues_without_dispatch() -> None:
    bus = MemoryBus(SimClock(5))
    received: list[Event] = []
    bus.subscribe("*", received.append)

    event = bus.publish(Streams.MD_BARS, make_bar(), ts_event=123)

    assert received == []  # nothing delivered until run_until_idle
    assert event.stream == Streams.MD_BARS
    assert event.seq == 0
    assert event.ts_event == 123
    assert event.ts_recorded == 5
    assert event.type == "Bar"
    assert bus.events == [event]

    assert bus.run_until_idle() == 1
    assert received == [event]
    assert bus.run_until_idle() == 0  # queue drained, idempotent


def test_fifo_cascade_and_subscription_order() -> None:
    bus = MemoryBus(SimClock())
    log: list[tuple[str, str, int]] = []

    bus.subscribe("*", lambda e: log.append(("a", e.stream, e.seq)))

    def on_bar(event: Event) -> None:
        if event.stream == Streams.MD_BARS:
            bus.publish(
                Streams.SIGNAL_INTENTS,
                make_intent(f"intent-{event.seq}", ts_signal=event.ts_event),
                ts_event=event.ts_event,
            )

    bus.subscribe(Streams.MD_BARS, on_bar)
    bus.subscribe("*", lambda e: log.append(("b", e.stream, e.seq)))

    bus.publish(Streams.MD_BARS, make_bar(close=1.0), ts_event=1)
    bus.publish(Streams.MD_BARS, make_bar(close=2.0), ts_event=2)

    assert bus.run_until_idle() == 4

    # FIFO: both bars dispatch before either cascaded intent; per event,
    # subscribers fire in subscription order (a before b).
    assert log == [
        ("a", Streams.MD_BARS, 0),
        ("b", Streams.MD_BARS, 0),
        ("a", Streams.MD_BARS, 1),
        ("b", Streams.MD_BARS, 1),
        ("a", Streams.SIGNAL_INTENTS, 0),
        ("b", Streams.SIGNAL_INTENTS, 0),
        ("a", Streams.SIGNAL_INTENTS, 1),
        ("b", Streams.SIGNAL_INTENTS, 1),
    ]
    # .events records publish order, cascades included.
    assert [(e.stream, e.seq) for e in bus.events] == [
        (Streams.MD_BARS, 0),
        (Streams.MD_BARS, 1),
        (Streams.SIGNAL_INTENTS, 0),
        (Streams.SIGNAL_INTENTS, 1),
    ]


def test_seq_is_isolated_per_stream() -> None:
    bus = MemoryBus(SimClock())
    bus.publish(Streams.MD_BARS, make_bar(), ts_event=1)
    bus.publish(Streams.SIGNAL_INTENTS, make_intent("i0"), ts_event=2)
    bus.publish(Streams.MD_BARS, make_bar(), ts_event=3)
    bus.publish(Streams.SIGNAL_INTENTS, make_intent("i1"), ts_event=4)
    bus.publish(Streams.MD_BARS, make_bar(), ts_event=5)

    seqs = [(e.stream, e.seq) for e in bus.events]
    assert seqs == [
        (Streams.MD_BARS, 0),
        (Streams.SIGNAL_INTENTS, 0),
        (Streams.MD_BARS, 1),
        (Streams.SIGNAL_INTENTS, 1),
        (Streams.MD_BARS, 2),
    ]


def test_pattern_matching_routes_correctly() -> None:
    bus = MemoryBus(SimClock())
    exact_bars: list[Event] = []
    md_wild: list[Event] = []
    everything: list[Event] = []
    exact_intents: list[Event] = []
    bus.subscribe(Streams.MD_BARS, exact_bars.append)
    bus.subscribe("md.*", md_wild.append)
    bus.subscribe("*", everything.append)
    bus.subscribe(Streams.SIGNAL_INTENTS, exact_intents.append)

    bus.publish(Streams.MD_BARS, make_bar(), ts_event=1)
    bus.publish(Streams.MD_TICKS, Tick(symbol="RELIANCE", ltp=100.5), ts_event=2)
    bus.publish(Streams.SIGNAL_INTENTS, make_intent("i0"), ts_event=3)
    # 'md.*' must not match a sibling prefix like 'mdx.bars'.
    bus.publish("mdx.bars", make_bar(symbol="XX"), ts_event=4)
    bus.run_until_idle()

    assert [e.stream for e in exact_bars] == [Streams.MD_BARS]
    assert [e.stream for e in md_wild] == [Streams.MD_BARS, Streams.MD_TICKS]
    assert [e.stream for e in everything] == [
        Streams.MD_BARS,
        Streams.MD_TICKS,
        Streams.SIGNAL_INTENTS,
        "mdx.bars",
    ]
    assert [e.stream for e in exact_intents] == [Streams.SIGNAL_INTENTS]


def test_ts_recorded_comes_from_injected_clock() -> None:
    clock = SimClock(100)
    bus = MemoryBus(clock)
    first = bus.publish(Streams.MD_BARS, make_bar(), ts_event=1)
    clock.advance_to(250)
    second = bus.publish(Streams.MD_BARS, make_bar(), ts_event=2)
    assert (first.ts_recorded, second.ts_recorded) == (100, 250)


def test_run_until_idle_guard_raises_on_publish_loop() -> None:
    bus = MemoryBus(SimClock())

    def feedback(event: Event) -> None:
        bus.publish(Streams.MD_TICKS, Tick(symbol="X", ltp=1.0), ts_event=event.ts_event + 1)

    bus.subscribe(Streams.MD_TICKS, feedback)
    bus.publish(Streams.MD_TICKS, Tick(symbol="X", ltp=1.0), ts_event=0)

    with pytest.raises(RuntimeError, match="max_events"):
        bus.run_until_idle(max_events=25)


def test_journal_tee_writes_at_publish_time(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    with JournalWriter(path) as journal:
        bus = MemoryBus(SimClock(7), journal=journal)
        bus.publish(Streams.MD_BARS, make_bar(), ts_event=1)
        bus.publish(Streams.SIGNAL_INTENTS, make_intent("i0"), ts_event=2)
        # Durable before any dispatch has happened.
        journaled = list(JournalReader(path).iter_events(verify=True))
        assert journaled == bus.events
    assert bus.run_until_idle() == 2


def test_journal_failure_blocks_enqueue(tmp_path: Path) -> None:
    class ExplodingJournal(JournalWriter):
        def append(self, event: Event) -> str:
            raise OSError("disk full")

    bus = MemoryBus(SimClock(), journal=ExplodingJournal(tmp_path / "j.jsonl"))
    with pytest.raises(OSError, match="disk full"):
        bus.publish(Streams.MD_BARS, make_bar(), ts_event=1)
    assert bus.events == []
    assert bus.run_until_idle() == 0


@pytest.mark.skipif(
    importlib.util.find_spec("confluent_kafka") is not None,
    reason="confluent_kafka is installed; missing-dependency path unreachable",
)
def test_redpanda_publisher_raises_without_confluent_kafka() -> None:
    from app.bus.redpanda import RedpandaPublisher  # module import needs no kafka

    with pytest.raises(RuntimeError, match="confluent-kafka"):
        RedpandaPublisher("localhost:9092")
