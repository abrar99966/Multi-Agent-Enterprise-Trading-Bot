"""JournalWriter/JournalReader: format, chain verification, crash tolerance."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from app.bus.journal import JournalIntegrityError, JournalReader, JournalWriter
from app.core.events import Bar, Event, OrderIntent, Side, Streams
from app.core.hashing import GENESIS_HASH, canonical_json, chain_hash

NS = 1_000_000_000


def make_event(stream: str, seq: int, payload: BaseModel, ts: int) -> Event:
    return Event(
        stream=stream,
        seq=seq,
        ts_event=ts,
        ts_recorded=ts + 1,
        type=type(payload).__name__,
        payload=payload.model_dump(mode="json"),
    )


def make_bar_event(seq: int, close: float = 100.0) -> Event:
    bar = Bar(
        symbol="TCS",
        ts_open=(1_700_000_000 + 60 * seq) * NS,
        interval_s=60,
        open=99.0,
        high=101.5,
        low=98.0,
        close=close,
        volume=2500.0,
    )
    return make_event(Streams.MD_BARS, seq, bar, ts=bar.ts_close)


def make_intent_event(seq: int) -> Event:
    intent = OrderIntent(
        intent_id=f"s1:TCS:{seq}",
        strategy_id="s1",
        symbol="TCS",
        side=Side.SELL,
        qty=3.0,
        ts_signal=1_700_000_000 * NS + seq,
        reason="unit test",
        attributions={"momentum": 0.75},
    )
    return make_event(Streams.SIGNAL_INTENTS, seq, intent, ts=intent.ts_signal)


def write_journal(path: Path, events: list[Event]) -> list[str]:
    with JournalWriter(path) as writer:
        return [writer.append(e) for e in events]


def test_round_trip_preserves_events_exactly(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "dirs" / "journal.jsonl"  # parents created
    events = [make_bar_event(0), make_intent_event(0), make_bar_event(1, close=102.25)]
    hashes = write_journal(path, events)

    reader = JournalReader(path)
    assert list(reader.iter_events(verify=True)) == events

    records = list(reader.iter_records())
    assert [r["hash"] for r in records] == hashes
    assert records[0]["prev_hash"] == GENESIS_HASH
    assert [r["prev_hash"] for r in records[1:]] == hashes[:-1]


def test_line_format_matches_spec_exactly(tmp_path: Path) -> None:
    path = tmp_path / "j.jsonl"
    event = make_bar_event(0)
    write_journal(path, [event])

    event_dict = event.model_dump(mode="json")
    expected_hash = chain_hash(GENESIS_HASH, canonical_json(event_dict))
    expected_line = (
        json.dumps(
            {"prev_hash": GENESIS_HASH, "hash": expected_hash, "event": event_dict},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        + "\n"
    )
    assert path.read_bytes() == expected_line.encode("ascii")


def test_chain_resumes_across_reopen(tmp_path: Path) -> None:
    path = tmp_path / "j.jsonl"
    first = [make_bar_event(0), make_bar_event(1)]
    second = [make_bar_event(2), make_intent_event(0)]
    write_journal(path, first)
    write_journal(path, second)  # reopen: must continue, not restart

    reader = JournalReader(path)
    assert list(reader.iter_events(verify=True)) == first + second
    records = list(reader.iter_records())
    assert records[2]["prev_hash"] == records[1]["hash"]


def test_verify_catches_tampered_event_field(tmp_path: Path) -> None:
    path = tmp_path / "j.jsonl"
    write_journal(path, [make_bar_event(0), make_bar_event(1), make_bar_event(2)])

    lines = path.read_bytes().splitlines()
    record = json.loads(lines[1])
    record["event"]["payload"]["close"] = 999.99  # one tampered field
    lines[1] = json.dumps(
        record, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    path.write_bytes(b"\n".join(lines) + b"\n")

    reader = JournalReader(path)
    with pytest.raises(JournalIntegrityError, match="line 2"):
        list(reader.iter_events(verify=True))
    # Without verification the tampered record still parses.
    unverified = list(reader.iter_events(verify=False))
    assert len(unverified) == 3
    assert unverified[1].payload["close"] == 999.99


def test_verify_catches_broken_linkage(tmp_path: Path) -> None:
    path = tmp_path / "j.jsonl"
    write_journal(path, [make_bar_event(0), make_bar_event(1)])

    lines = path.read_bytes().splitlines()
    record = json.loads(lines[1])
    record["prev_hash"] = "f" * 64
    # Re-hash consistently so only the linkage (not this line's hash) is wrong.
    record["hash"] = chain_hash(record["prev_hash"], canonical_json(record["event"]))
    lines[1] = json.dumps(
        record, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    path.write_bytes(b"\n".join(lines) + b"\n")

    with pytest.raises(JournalIntegrityError, match="line 2"):
        list(JournalReader(path).iter_events(verify=True))


def test_trailing_partial_line_is_tolerated(tmp_path: Path) -> None:
    path = tmp_path / "j.jsonl"
    events = [make_bar_event(0), make_bar_event(1), make_bar_event(2)]
    write_journal(path, events)

    lines = path.read_bytes().splitlines(keepends=True)
    path.write_bytes(lines[0] + lines[1] + lines[2][: len(lines[2]) // 2])  # torn write

    reader = JournalReader(path)
    assert len(list(reader.iter_records())) == 2
    assert list(reader.iter_events(verify=True)) == events[:2]


def test_partial_line_mid_file_raises(tmp_path: Path) -> None:
    path = tmp_path / "j.jsonl"
    write_journal(path, [make_bar_event(0), make_bar_event(1)])

    lines = path.read_bytes().splitlines(keepends=True)
    path.write_bytes(lines[0] + b'{"torn":' + b"\n" + lines[1])

    with pytest.raises(JournalIntegrityError, match="line 2"):
        list(JournalReader(path).iter_records())


def test_writer_resume_truncates_torn_tail(tmp_path: Path) -> None:
    path = tmp_path / "j.jsonl"
    events = [make_bar_event(0), make_bar_event(1), make_bar_event(2)]
    write_journal(path, events)

    lines = path.read_bytes().splitlines(keepends=True)
    path.write_bytes(lines[0] + lines[1] + lines[2][:20])  # crash mid-write

    replacement = make_intent_event(0)
    write_journal(path, [replacement])  # reopen resumes after last valid line

    reader = JournalReader(path)
    assert list(reader.iter_events(verify=True)) == [events[0], events[1], replacement]
    records = list(reader.iter_records())
    assert records[2]["prev_hash"] == records[1]["hash"]


def test_payloads_filters_by_exact_stream_and_decodes(tmp_path: Path) -> None:
    path = tmp_path / "j.jsonl"
    bar_events = [make_bar_event(0), make_bar_event(1, close=101.0)]
    write_journal(path, [bar_events[0], make_intent_event(0), bar_events[1]])

    pairs = list(JournalReader(path).payloads(Streams.MD_BARS))
    assert [event for event, _ in pairs] == bar_events
    for event, payload in pairs:
        assert isinstance(payload, Bar)
        assert payload.model_dump(mode="json") == event.payload


def test_fsync_writer_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "j.jsonl"
    event = make_bar_event(0)
    with JournalWriter(path, fsync=True) as writer:
        assert writer.append(event) == chain_hash(
            GENESIS_HASH, canonical_json(event.model_dump(mode="json"))
        )
    assert list(JournalReader(path).iter_events(verify=True)) == [event]


def test_empty_existing_file_starts_fresh(tmp_path: Path) -> None:
    path = tmp_path / "j.jsonl"
    path.touch()
    assert list(JournalReader(path).iter_records()) == []
    write_journal(path, [make_bar_event(0)])
    assert next(JournalReader(path).iter_records())["prev_hash"] == GENESIS_HASH
