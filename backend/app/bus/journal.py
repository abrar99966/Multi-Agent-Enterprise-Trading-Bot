"""Hash-chained JSONL journal (line format spec: core/hashing.py).

JournalWriter is the append-only, write-ahead sink for MemoryBus;
JournalReader replays and verifies.

Crash / corruption model (kept identical to audit.chain so the live
reader and the independent auditor never disagree on the same bytes):

  * A record counts as committed only once its full line (newline
    included) is on disk.
  * An *unparseable* final line is a genuine torn tail (crash mid-write)
    and is tolerated -- the reader stops cleanly at it, and a reopening
    writer truncates it before continuing the chain.
  * A *parseable-but-non-record* final line (e.g. ``42``) is NOT a torn
    tail: truncating a real record line can never yield a valid
    standalone JSON value, so it is a hard failure, exactly as
    audit.chain.verify_journal reports it.
  * Any corrupt/non-record line that is *not* last is pre-tail
    corruption: the reader raises and a reopening writer refuses to
    extend the chain on top of it.

Durability: append() always flush()es to the OS page cache; pass
fsync=True (the engine's audit journal does) to also fsync the file
descriptor so a committed record survives power loss, not just a
process crash. The parent directory is fsynced once on first creation
so the file's namespace entry is durable too.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import TracebackType
from typing import IO, Any, Iterator

from pydantic import BaseModel

from app.core.events import Event
from app.core.hashing import GENESIS_HASH, canonical_json, chain_hash


class JournalIntegrityError(Exception):
    """A journal line is corrupt, mis-chained, or mis-hashed."""


# Line classification, shared by the reader and the resume scan.
_LINE_GARBAGE = "garbage"  # not valid JSON: a torn tail when it is last
_LINE_NONRECORD = "nonrecord"  # valid JSON but not a record dict: never a tail


def _classify_line(raw: bytes) -> tuple[str, dict[str, Any] | None]:
    """('ok', record) | ('nonrecord', None) | ('garbage', None)."""
    try:
        rec = json.loads(raw)
    except ValueError:
        return (_LINE_GARBAGE, None)
    if not isinstance(rec, dict):
        return (_LINE_NONRECORD, None)
    return ("ok", rec)


def _verify_chain_line(raw: bytes, lineno: int, prev_hash: str) -> tuple[str, str | None]:
    """Classify a line and, when it is a record, verify it extends the
    chain whose tip is prev_hash. Returns (kind, this_hash); this_hash is
    the record hash when kind == 'ok', else None. Raises
    JournalIntegrityError for a record that is malformed or breaks the
    chain (shape / prev linkage / recomputed hash)."""
    kind, rec = _classify_line(raw)
    if kind != "ok":
        return (kind, None)
    assert rec is not None
    recorded_prev = rec.get("prev_hash")
    recorded_hash = rec.get("hash")
    event_dict = rec.get("event")
    if not (
        isinstance(recorded_prev, str)
        and isinstance(recorded_hash, str)
        and isinstance(event_dict, dict)
    ):
        raise JournalIntegrityError(
            f"line {lineno}: record missing prev_hash/hash/event"
        )
    if recorded_prev != prev_hash:
        raise JournalIntegrityError(
            f"line {lineno}: prev_hash {recorded_prev!r} does not match "
            f"prior record hash {prev_hash!r}"
        )
    if chain_hash(prev_hash, canonical_json(event_dict)) != recorded_hash:
        raise JournalIntegrityError(f"line {lineno}: hash mismatch")
    return ("ok", recorded_hash)


def _fsync_dir(path: Path) -> None:
    """Best-effort fsync of a file's parent directory so its namespace
    entry is durable. No-op where the OS forbids opening a directory for
    fsync (e.g. Windows)."""
    try:
        fd = os.open(str(path.parent), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


class JournalWriter:
    def __init__(self, path: Path, fsync: bool = False) -> None:
        self._path = Path(path)
        self._fsync = fsync
        self._prev_hash = GENESIS_HASH
        self._count = 0
        existed = self._path.parent.exists()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if self._path.exists() and self._path.stat().st_size > 0:
            self._prev_hash, self._count = self._resume()
        file_existed = self._path.exists()
        self._fh: IO[bytes] = open(self._path, "ab")
        if not file_existed:
            # Make the new file's directory entry durable on first create.
            _fsync_dir(self._path)
            if not existed:
                _fsync_dir(self._path.parent)

    def _resume(self) -> tuple[str, int]:
        """Verify the existing chain and continue from its tip.

        Refuses (raises JournalIntegrityError) if any record before the
        final line is corrupt, a non-record, or breaks the hash chain --
        the writer will not extend a damaged journal. An unparseable
        *final* line is a torn tail and is truncated (durably); a
        parseable non-record final line is a hard failure. Returns
        (tip_hash, record_count)."""
        prev_hash = GENESIS_HASH
        count = 0
        keep = 0
        offset = 0
        lineno = 0
        pending: tuple[bytes, int, int] | None = None
        with open(self._path, "rb") as fh:
            for raw in fh:
                lineno += 1
                if pending is not None:
                    praw, poff, pno = pending
                    kind, this_hash = _verify_chain_line(praw, pno, prev_hash)
                    if kind != "ok":
                        raise JournalIntegrityError(
                            f"resume: line {pno}: corrupt or non-record line "
                            "before end of journal; refusing to extend"
                        )
                    prev_hash = this_hash  # type: ignore[assignment]
                    count += 1
                    keep = poff + len(praw)
                pending = (raw, offset, lineno)
                offset += len(raw)
            if pending is not None:
                praw, poff, pno = pending
                kind, this_hash = _verify_chain_line(praw, pno, prev_hash)
                if kind == "ok":
                    prev_hash = this_hash  # type: ignore[assignment]
                    count += 1
                    keep = poff + len(praw)
                elif kind == _LINE_NONRECORD:
                    raise JournalIntegrityError(
                        f"resume: line {pno}: final line is valid JSON but not "
                        "a record dict; not a torn tail"
                    )
                # garbage final line == torn tail: truncated below.
        if keep < offset:
            with open(self._path, "r+b") as fh:
                fh.truncate(keep)
                fh.flush()
                os.fsync(fh.fileno())
        return prev_hash, count

    @property
    def tip(self) -> tuple[str, int]:
        """Current chain head: (tip_hash, record_count). Persist this out
        of band to detect later tail-truncation or wholesale forgery
        (see core/hashing.py)."""
        return (self._prev_hash, self._count)

    def append(self, event: Event) -> str:
        event_dict = event.model_dump(mode="json")
        record_hash = chain_hash(self._prev_hash, canonical_json(event_dict))
        line = json.dumps(
            {"prev_hash": self._prev_hash, "hash": record_hash, "event": event_dict},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        self._fh.write(line.encode("ascii") + b"\n")
        self._fh.flush()
        if self._fsync:
            os.fsync(self._fh.fileno())
        self._prev_hash = record_hash
        self._count += 1
        return record_hash

    def _write_head_anchor(self) -> None:
        """Write a co-located ``<journal>.head`` anchor {hash, count}.

        NOTE: a sidecar in the same directory is only a tripwire -- an
        attacker who can rewrite the journal can rewrite this too. Real
        tamper-evidence needs the tip persisted to a separate trust
        domain or signed (Phase 1+). It still lets any reader that
        obtained the tip out of band detect truncation/forgery via
        audit.chain.verify_journal(expected_*)."""
        head_path = self._path.with_suffix(self._path.suffix + ".head")
        tip_hash, count = self.tip
        payload = json.dumps(
            {"hash": tip_hash, "count": count},
            sort_keys=True,
            separators=(",", ":"),
        )
        with open(head_path, "w", encoding="ascii") as fh:
            fh.write(payload)
            fh.flush()
            if self._fsync:
                os.fsync(fh.fileno())

    def close(self) -> None:
        """Flush and fsync the completed journal once, then write the head
        anchor. So even with per-record fsync off (the default), a cleanly
        closed journal is power-loss durable as a whole; pass fsync=True
        for per-record durability in live trading."""
        if self._fh.closed:
            return
        self._fh.flush()
        try:
            os.fsync(self._fh.fileno())
        except OSError:
            pass
        self._fh.close()
        self._write_head_anchor()

    def __enter__(self) -> JournalWriter:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


class JournalReader:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def iter_records(self) -> Iterator[dict[str, Any]]:
        """Parsed journal records in order. An *unparseable* final line is
        a torn tail (crash mid-write) and is tolerated as end-of-journal.
        A corrupt/non-record line that is not last, or a parseable
        non-record *final* line, raises JournalIntegrityError -- matching
        audit.chain so both readers agree on the same bytes."""
        with open(self._path, "rb") as fh:
            pending: bytes | None = None
            pending_no = 0
            lineno = 0
            for raw in fh:
                lineno += 1
                if pending is not None:
                    kind, rec = _classify_line(pending)
                    if kind != "ok":
                        raise JournalIntegrityError(
                            f"line {pending_no}: corrupt or non-record line "
                            "followed by more data"
                        )
                    yield rec  # type: ignore[misc]
                pending, pending_no = raw, lineno
            if pending is not None:
                kind, rec = _classify_line(pending)
                if kind == "ok":
                    yield rec  # type: ignore[misc]
                elif kind == _LINE_NONRECORD:
                    raise JournalIntegrityError(
                        f"line {pending_no}: final line is valid JSON but not "
                        "a record dict; not a torn tail"
                    )
                # garbage final line == torn tail: stop cleanly.

    def iter_events(self, verify: bool = True) -> Iterator[Event]:
        """Replay Events; when verify, recompute the hash chain and raise
        JournalIntegrityError (with line number) on the first violation."""
        prev_hash = GENESIS_HASH
        for lineno, rec in enumerate(self.iter_records(), start=1):
            if verify:
                recorded_prev = rec.get("prev_hash")
                recorded_hash = rec.get("hash")
                event_dict = rec.get("event")
                if not (
                    isinstance(recorded_prev, str)
                    and isinstance(recorded_hash, str)
                    and isinstance(event_dict, dict)
                ):
                    raise JournalIntegrityError(
                        f"line {lineno}: record is missing prev_hash/hash/event"
                    )
                if recorded_prev != prev_hash:
                    raise JournalIntegrityError(
                        f"line {lineno}: prev_hash {recorded_prev!r} does not "
                        f"match prior record hash {prev_hash!r}"
                    )
                recomputed = chain_hash(prev_hash, canonical_json(event_dict))
                if recomputed != recorded_hash:
                    raise JournalIntegrityError(
                        f"line {lineno}: hash mismatch (recorded "
                        f"{recorded_hash!r}, recomputed {recomputed!r})"
                    )
                prev_hash = recorded_hash
            yield Event.model_validate(rec["event"])

    def payloads(self, stream: str) -> Iterator[tuple[Event, BaseModel]]:
        for event in self.iter_events():
            if event.stream == stream:
                yield event, event.decode()
