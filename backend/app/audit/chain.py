"""Audit chain verification.

Independent verifier for the hash-chained JSONL journal format specified
in core/hashing.py. It works purely on the journaled bytes -- no pydantic
models and no bus/journal.py imports -- so verifying historical journals
can never break under schema evolution or writer refactors.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.hashing import GENESIS_HASH, canonical_json, chain_hash

_REQUIRED_KEYS = ("prev_hash", "hash", "event")


@dataclass
class VerifyReport:
    ok: bool
    records: int
    first_bad_line: int | None = None  # 1-based
    reason: str | None = None
    tip: str | None = None  # hash of the last verified record (or None if empty)


def _check_record(record: Any, prev_hash: str) -> str | None:
    """Return a failure reason, or None if the record validly extends the
    chain whose tip is prev_hash."""
    if not isinstance(record, dict):
        return "record is not a JSON object"
    missing = [key for key in _REQUIRED_KEYS if key not in record]
    if missing:
        return f"record missing key(s): {', '.join(missing)}"
    if record["prev_hash"] != prev_hash:
        return (
            f"prev_hash mismatch: expected {prev_hash}, "
            f"found {record['prev_hash']!r}"
        )
    expected = chain_hash(prev_hash, canonical_json(record["event"]))
    if record["hash"] != expected:
        return f"hash mismatch: computed {expected}, recorded {record['hash']!r}"
    return None


def _load_head_anchor(path: Path) -> tuple[str, int] | None:
    """Read a co-located ``<journal>.head`` anchor {hash, count} if present
    and well-formed, else None."""
    head_path = path.with_suffix(path.suffix + ".head")
    if not head_path.is_file():
        return None
    try:
        data = json.loads(head_path.read_text(encoding="ascii"))
        return (str(data["hash"]), int(data["count"]))
    except (ValueError, KeyError, TypeError):
        return None


def verify_journal(
    path: Path,
    expected_head: str | None = None,
    expected_count: int | None = None,
) -> VerifyReport:
    """Verify one journal file against the core/hashing.py format spec.

    Every record's hash is recomputed from its embedded event dict using
    the tracked chain tip (GENESIS_HASH before the first record), and its
    prev_hash must equal that tip. A line that does not parse as JSON is
    a failure unless it is the final line, in which case it is tolerated
    as a crash-truncated tail and the intact prefix verifies as ok.

    Anchor check (detects tail truncation / wholesale forgery, which a
    bare hash chain cannot): if expected_head/expected_count are given --
    or a co-located ``<journal>.head`` sidecar exists -- the verified tip
    hash and record count must match them. Explicit arguments win over the
    sidecar. A sidecar is only a tripwire (same trust domain as the
    journal); an externally retained or signed anchor is the real control.
    """
    prev_hash = GENESIS_HASH
    records = 0
    with path.open("rb") as f:
        lineno = 0
        raw = f.readline()
        while raw:
            lineno += 1
            nxt = f.readline()
            try:
                # UnicodeDecodeError and JSONDecodeError are ValueErrors.
                record = json.loads(raw)
            except ValueError:
                if not nxt:
                    return _with_anchor(
                        VerifyReport(
                            ok=True,
                            records=records,
                            first_bad_line=None,
                            reason="truncated tail tolerated",
                            tip=None if records == 0 else prev_hash,
                        ),
                        path,
                        expected_head,
                        expected_count,
                    )
                return VerifyReport(
                    ok=False,
                    records=records,
                    first_bad_line=lineno,
                    reason="malformed JSON",
                    tip=None if records == 0 else prev_hash,
                )
            reason = _check_record(record, prev_hash)
            if reason is not None:
                return VerifyReport(
                    ok=False,
                    records=records,
                    first_bad_line=lineno,
                    reason=reason,
                    tip=None if records == 0 else prev_hash,
                )
            prev_hash = record["hash"]
            records += 1
            raw = nxt
    return _with_anchor(
        VerifyReport(
            ok=True,
            records=records,
            first_bad_line=None,
            reason=None,
            tip=None if records == 0 else prev_hash,
        ),
        path,
        expected_head,
        expected_count,
    )


def _with_anchor(
    report: VerifyReport,
    path: Path,
    expected_head: str | None,
    expected_count: int | None,
) -> VerifyReport:
    """Apply the trust-anchor comparison to an otherwise-ok report."""
    if expected_head is None and expected_count is None:
        anchor = _load_head_anchor(path)
        if anchor is None:
            return report
        expected_head, expected_count = anchor
    if expected_count is not None and report.records != expected_count:
        report.ok = False
        report.reason = (
            f"anchor mismatch: {report.records} records on disk, anchor "
            f"expects {expected_count} (possible tail truncation)"
        )
        return report
    if expected_head is not None and report.tip != expected_head:
        report.ok = False
        report.reason = (
            f"anchor mismatch: tip {report.tip!r} != expected "
            f"{expected_head!r} (possible forgery)"
        )
    return report


def verify_directory(dir: Path, glob: str = "*.jsonl") -> dict[str, VerifyReport]:
    """Verify every journal under dir matching glob.

    Keys are paths relative to dir in posix form, inserted in sorted
    order, so iteration over the result is deterministic.
    """
    reports: dict[str, VerifyReport] = {}
    for path in sorted(p for p in dir.glob(glob) if p.is_file()):
        reports[path.relative_to(dir).as_posix()] = verify_journal(path)
    return reports
