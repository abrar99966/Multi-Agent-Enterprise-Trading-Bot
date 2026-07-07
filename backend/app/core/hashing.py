"""Canonical serialization and the audit hash chain.

Journal file format (one JSON object per line, UTF-8, '\\n' endings):

    {"prev_hash": "<64 hex>", "hash": "<64 hex>", "event": {<Event.model_dump(mode="json")>}}

where  hash = chain_hash(prev_hash, canonical_json(event_dict)).

The first record's prev_hash is GENESIS_HASH. A verifier recomputes
every hash from the embedded event dict and checks linkage.

What this guarantees, and what it does NOT
------------------------------------------
The chain is an *unkeyed* SHA-256 chain. It reliably detects any
in-place edit, reorder, deletion, or insertion in the *interior* of the
journal: each such change breaks the recomputed hash or the prev_hash
linkage of every following record.

It does NOT, on its own, detect two attacks, because there is no secret
and no externally retained anchor:

  * Tail truncation -- dropping the last N committed records leaves a
    shorter chain that still verifies clean.
  * Wholesale forgery -- anyone who can rewrite the file can fabricate a
    fully self-consistent chain of attacker-chosen events.

Detecting those requires an anchor the attacker cannot also rewrite:
verify against an independently retained tip ``(hash, count)`` -- see
``JournalWriter.tip`` and ``audit.chain.verify_journal(expected_*)`` --
and, for true tamper-evidence, an HMAC/signature with a writer-held key
or a checkpoint pushed to an append-only external sink (Phase 1+,
docs/TARGET_ARCHITECTURE.md section 9). The co-located ``.head`` sidecar
written on close gives truncation/forge detection only to readers that
obtained the expected tip out of band.

canonical_json uses ``allow_nan=False``: a non-finite float (NaN /
Infinity, which a bad feed can produce in an unconstrained price field)
raises at hash time rather than being folded into the chain as a token
that is not valid JSON for a strict third-party verifier.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

GENESIS_HASH = "0" * 64


def canonical_json(obj: Any) -> bytes:
    """Stable byte serialization: sorted keys, no whitespace, ASCII only,
    finite numbers only. The chain hashes these bytes, so this function
    must never change behavior for already-journaled data."""
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def chain_hash(prev_hash: str, record: bytes) -> str:
    return hashlib.sha256(prev_hash.encode("ascii") + record).hexdigest()
