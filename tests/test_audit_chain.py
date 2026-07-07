"""Tests for audit chain verification (app/audit/chain.py).

Journals are built by hand from plain dict events using only the
core.hashing helpers, exactly per the format spec -- deliberately
schema-independent (no pydantic models, no bus/journal.py).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from app.audit.chain import VerifyReport, verify_directory, verify_journal
from app.core.hashing import GENESIS_HASH, canonical_json, chain_hash

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "verify_audit_chain.py"


def _event(i: int) -> dict:
    return {
        "stream": "md.bars",
        "seq": i,
        "ts_event": 1_750_000_000_000_000_000 + i,
        "ts_recorded": 1_750_000_000_000_000_000 + i,
        "type": "Bar",
        "schema_version": 1,
        "payload": {"symbol": "TEST", "close": 100.0 + i},
    }


def _records(n: int) -> list[dict]:
    """n correctly chained journal records starting at GENESIS_HASH."""
    prev = GENESIS_HASH
    records: list[dict] = []
    for i in range(n):
        event = _event(i)
        digest = chain_hash(prev, canonical_json(event))
        records.append({"prev_hash": prev, "hash": digest, "event": event})
        prev = digest
    return records


def _lines(records: list[dict]) -> list[str]:
    return [canonical_json(r).decode("ascii") for r in records]


def _write(path: Path, lines: list[str]) -> None:
    text = "".join(line + "\n" for line in lines)
    path.write_bytes(text.encode("utf-8"))


# ---------------------------------------------------------------- verify_journal


def test_valid_multi_record(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    records = _records(5)
    _write(path, _lines(records))
    assert verify_journal(path) == VerifyReport(
        ok=True,
        records=5,
        first_bad_line=None,
        reason=None,
        tip=records[-1]["hash"],
    )


def test_empty_file_ok_with_zero_records(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    path.write_bytes(b"")
    assert verify_journal(path) == VerifyReport(
        ok=True, records=0, first_bad_line=None, reason=None
    )


def test_outer_line_encoding_is_irrelevant(tmp_path: Path) -> None:
    """The chain hashes canonical event bytes, not the journal line bytes:
    re-serializing a line with different key order/whitespace still verifies."""
    import json

    records = _records(3)
    lines = _lines(records)
    middle = records[1]
    lines[1] = json.dumps(
        {"event": middle["event"], "hash": middle["hash"], "prev_hash": middle["prev_hash"]},
        indent=None,
        separators=(", ", ": "),
    )
    path = tmp_path / "journal.jsonl"
    _write(path, lines)
    report = verify_journal(path)
    assert report.ok and report.records == 3


def test_tampered_event_field(tmp_path: Path) -> None:
    records = _records(4)
    records[2]["event"]["payload"]["close"] = 999.0  # flip a byte of history
    path = tmp_path / "journal.jsonl"
    _write(path, _lines(records))
    report = verify_journal(path)
    assert report.ok is False
    assert report.first_bad_line == 3
    assert report.records == 2
    assert report.reason is not None and "hash mismatch" in report.reason


def test_tampered_hash_field(tmp_path: Path) -> None:
    records = _records(3)
    records[1]["hash"] = "f" * 64
    path = tmp_path / "journal.jsonl"
    _write(path, _lines(records))
    report = verify_journal(path)
    assert report.ok is False
    assert report.first_bad_line == 2
    assert report.records == 1
    assert report.reason is not None and "hash mismatch" in report.reason


def test_broken_prev_linkage(tmp_path: Path) -> None:
    """Record 3 is internally consistent (hash matches its own prev_hash +
    event) but its prev_hash does not link to record 2's hash."""
    records = _records(4)
    fake_prev = "a" * 64
    records[2]["prev_hash"] = fake_prev
    records[2]["hash"] = chain_hash(fake_prev, canonical_json(records[2]["event"]))
    path = tmp_path / "journal.jsonl"
    _write(path, _lines(records))
    report = verify_journal(path)
    assert report.ok is False
    assert report.first_bad_line == 3
    assert report.records == 2
    assert report.reason is not None and "prev_hash mismatch" in report.reason


def test_truncated_tail_tolerated(tmp_path: Path) -> None:
    records = _records(3)
    tip = records[-1]["hash"]
    next_event = _event(3)
    full_line = canonical_json(
        {
            "prev_hash": tip,
            "hash": chain_hash(tip, canonical_json(next_event)),
            "event": next_event,
        }
    ).decode("ascii")
    path = tmp_path / "journal.jsonl"
    _write(path, _lines(records))
    with path.open("ab") as f:  # crash mid-append: partial line, no newline
        f.write(full_line[: len(full_line) // 2].encode("ascii"))
    report = verify_journal(path)
    assert report == VerifyReport(
        ok=True,
        records=3,
        first_bad_line=None,
        reason="truncated tail tolerated",
        tip=records[-1]["hash"],
    )


def test_blank_only_file_treated_as_truncated_tail(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    path.write_bytes(b"\n")
    report = verify_journal(path)
    assert report.ok is True
    assert report.records == 0
    assert report.reason == "truncated tail tolerated"


def test_malformed_middle_line_fails(tmp_path: Path) -> None:
    lines = _lines(_records(3))
    lines.insert(1, '{"prev_hash": "garb')  # partial write that was overtaken
    path = tmp_path / "journal.jsonl"
    _write(path, lines)
    report = verify_journal(path)
    assert report.ok is False
    assert report.first_bad_line == 2
    assert report.records == 1
    assert report.reason == "malformed JSON"


def test_parseable_but_invalid_last_record_is_not_tolerated(tmp_path: Path) -> None:
    """Truncation tolerance applies only to unparseable lines; well-formed
    JSON that is not a chain record fails even at the tail."""
    lines = _lines(_records(2)) + ['{"not": "a record"}']
    path = tmp_path / "journal.jsonl"
    _write(path, lines)
    report = verify_journal(path)
    assert report.ok is False
    assert report.first_bad_line == 3
    assert report.records == 2
    assert report.reason is not None and "missing key" in report.reason


def test_non_object_json_line_fails(tmp_path: Path) -> None:
    lines = _lines(_records(1)) + ["42", _lines(_records(2))[1]]
    path = tmp_path / "journal.jsonl"
    _write(path, lines)
    report = verify_journal(path)
    assert report.ok is False
    assert report.first_bad_line == 2
    assert report.reason == "record is not a JSON object"


# ------------------------------------------------------------- verify_directory


def test_verify_directory(tmp_path: Path) -> None:
    good = _records(2)
    _write(tmp_path / "a_good.jsonl", _lines(good))
    bad = _records(2)
    bad[1]["event"]["seq"] = 99
    _write(tmp_path / "b_bad.jsonl", _lines(bad))
    (tmp_path / "ignored.txt").write_text("not a journal", encoding="utf-8")

    reports = verify_directory(tmp_path)
    assert list(reports) == ["a_good.jsonl", "b_bad.jsonl"]  # sorted, .jsonl only
    assert reports["a_good.jsonl"] == VerifyReport(
        True, 2, None, None, tip=good[-1]["hash"]
    )
    assert reports["b_bad.jsonl"].ok is False
    assert reports["b_bad.jsonl"].first_bad_line == 2

    only_good = verify_directory(tmp_path, glob="a_*.jsonl")
    assert list(only_good) == ["a_good.jsonl"]


def test_verify_directory_empty(tmp_path: Path) -> None:
    assert verify_directory(tmp_path) == {}


# ------------------------------------------------------------------------- CLI


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_cli_single_file_pass(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    _write(path, _lines(_records(2)))
    proc = _run_cli(str(path))
    assert proc.returncode == 0
    line = proc.stdout.strip()
    assert line.startswith("PASS")
    assert "records=2" in line


def test_cli_directory_with_failure(tmp_path: Path) -> None:
    _write(tmp_path / "a_good.jsonl", _lines(_records(3)))
    bad = _records(3)
    bad[1]["hash"] = "f" * 64
    _write(tmp_path / "b_bad.jsonl", _lines(bad))

    proc = _run_cli(str(tmp_path))
    assert proc.returncode == 1
    out_lines = proc.stdout.strip().splitlines()
    assert any(
        l.startswith("PASS") and "a_good.jsonl" in l and "records=3" in l
        for l in out_lines
    )
    assert any(
        l.startswith("FAIL") and "b_bad.jsonl" in l and "first_bad_line=2" in l
        for l in out_lines
    )


def test_cli_missing_path_is_usage_error(tmp_path: Path) -> None:
    proc = _run_cli(str(tmp_path / "does_not_exist.jsonl"))
    assert proc.returncode == 2
    assert "no such file or directory" in proc.stderr
