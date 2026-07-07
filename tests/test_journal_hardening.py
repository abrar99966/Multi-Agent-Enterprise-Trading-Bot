"""Regression tests for Phase 0 audit-trail hardening.

Covers the fixes applied after the adversarial journal review:
- canonical_json rejects non-finite floats (allow_nan=False),
- the head anchor detects tail truncation and wholesale forgery,
- JournalWriter refuses to resume onto a corrupt-middle chain,
- JournalReader and audit.chain agree on the trailing-line rule.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.audit.chain import verify_journal
from app.bus.journal import JournalIntegrityError, JournalReader, JournalWriter
from app.core.events import Bar, Event, Streams
from app.core.hashing import canonical_json


def _bar_event(seq: int, close: float = 100.0) -> tuple[Event, Bar]:
    bar = Bar(
        symbol="TEST",
        ts_open=1_750_000_000_000_000_000 + seq * 60_000_000_000,
        interval_s=60,
        open=close,
        high=close + 1,
        low=close - 1,
        close=close,
        volume=1000.0,
    )
    event = Event(
        stream=Streams.MD_BARS,
        seq=seq,
        ts_event=bar.ts_close,
        ts_recorded=bar.ts_close,
        type="Bar",
        payload=bar.model_dump(mode="json"),
    )
    return event, bar


def _write_journal(path: Path, n: int) -> JournalWriter:
    writer = JournalWriter(path)
    for i in range(n):
        writer.append(_bar_event(i)[0])
    writer.close()
    return writer


# ----------------------------------------------------------- canonical_json


def test_canonical_json_rejects_nan_and_inf() -> None:
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError):
            canonical_json({"x": bad})


def test_canonical_json_finite_unchanged() -> None:
    # The exact bytes the chain depends on must not drift.
    assert canonical_json({"b": 1, "a": 0.1 + 0.2}) == b'{"a":0.30000000000000004,"b":1}'


# ------------------------------------------------------------- head anchor


def test_head_anchor_written_on_close(tmp_path: Path) -> None:
    path = tmp_path / "j.jsonl"
    writer = _write_journal(path, 3)
    head = path.with_suffix(".jsonl.head")
    assert head.is_file()
    data = json.loads(head.read_text(encoding="ascii"))
    assert data == {"hash": writer.tip[0], "count": 3}


def test_anchor_detects_tail_truncation(tmp_path: Path) -> None:
    path = tmp_path / "j.jsonl"
    _write_journal(path, 4)
    # Bare chain still verifies after dropping the last record...
    lines = path.read_bytes().splitlines(keepends=True)
    path.write_bytes(b"".join(lines[:-1]))
    bare = verify_journal(path)  # sidecar present -> auto-checked
    assert bare.ok is False and "truncation" in (bare.reason or "")


def test_anchor_detects_forgery(tmp_path: Path) -> None:
    path = tmp_path / "j.jsonl"
    writer = _write_journal(path, 3)
    real_tip = writer.tip[0]
    # Forge an entirely fresh, self-consistent chain of the same length.
    forged = tmp_path / "forged.jsonl"
    fw = JournalWriter(forged)
    for i in range(3):
        fw.append(_bar_event(i, close=999.0)[0])
    fw.close()
    report = verify_journal(forged, expected_head=real_tip, expected_count=3)
    assert report.ok is False and "forgery" in (report.reason or "")


def test_anchor_match_passes(tmp_path: Path) -> None:
    path = tmp_path / "j.jsonl"
    writer = _write_journal(path, 3)
    report = verify_journal(
        path, expected_head=writer.tip[0], expected_count=writer.tip[1]
    )
    assert report.ok is True


# --------------------------------------------------------- resume contiguity


def test_resume_refuses_corrupt_middle(tmp_path: Path) -> None:
    path = tmp_path / "j.jsonl"
    _write_journal(path, 3)
    lines = path.read_bytes().splitlines(keepends=True)
    corrupted = lines[0] + b'{"prev_hash": "garb\n' + b"".join(lines[1:])
    path.write_bytes(corrupted)
    with pytest.raises(JournalIntegrityError):
        JournalWriter(path)  # must refuse to extend a damaged chain


def test_resume_truncates_torn_tail_and_continues(tmp_path: Path) -> None:
    path = tmp_path / "j.jsonl"
    _write_journal(path, 2)
    with path.open("ab") as fh:  # torn final line (crash mid-append)
        fh.write(b'{"prev_hash": "partial')
    writer = JournalWriter(path)  # tolerates + truncates the torn tail
    assert writer.tip[1] == 2
    writer.append(_bar_event(2)[0])
    writer.close()
    assert verify_journal(path).ok is True


# ------------------------------------------------ reader/auditor agreement


def test_reader_and_auditor_agree_on_nonrecord_tail(tmp_path: Path) -> None:
    path = tmp_path / "j.jsonl"
    _write_journal(path, 2)
    with path.open("ab") as fh:
        fh.write(b"42\n")  # parseable JSON, not a record, as final line
    # auditor fails it...
    assert verify_journal(path).ok is False
    # ...and the live reader raises rather than silently dropping it.
    with pytest.raises(JournalIntegrityError):
        list(JournalReader(path).iter_records())
