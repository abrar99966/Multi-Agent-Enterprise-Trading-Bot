"""Phase 0 exit criteria, end to end against the real MemoryBus.

A full paper session must (1) actually trade with every order gated by
risk, (2) journal tamper-evidently, (3) replay byte-identically from its
own journal, and (4) keep positions consistent with fills.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.audit.chain import verify_journal
from app.core.events import Fill, Order, RiskVerdict, Side, Streams
from app.engine.runner import PaperSession

SYMBOLS = ["RELIANCE", "TCS"]


def _run_session(tmp_path: Path) -> PaperSession:
    session = PaperSession(
        SYMBOLS, n_bars=500, seed=42, journal_path=tmp_path / "session.jsonl"
    )
    session.run()
    return session


def _payloads(session: PaperSession, stream: str) -> list[dict[str, Any]]:
    return [
        event.decode().model_dump(mode="json")
        for event in session.bus.events
        if event.stream == stream
    ]


def test_session_produces_trades(tmp_path: Path) -> None:
    session = _run_session(tmp_path)
    summary = session.summary
    assert summary is not None
    assert summary["intents"] > 0
    assert summary["fills"] > 0

    order_intent_ids = {
        Order.model_validate(event.payload).intent_id
        for event in session.bus.events
        if event.stream == Streams.EXEC_ORDERS
    }
    approved_intent_ids = {
        verdict.intent_id
        for verdict in (
            RiskVerdict.model_validate(event.payload)
            for event in session.bus.events
            if event.stream == Streams.RISK_VERDICTS
        )
        if verdict.approved
    }
    # No order bypassed risk, and every approval became exactly one order.
    assert order_intent_ids == approved_intent_ids
    assert summary["orders"] == len(order_intent_ids)
    assert summary["fills"] <= summary["orders"]


def test_journal_chain_verifies(tmp_path: Path) -> None:
    session = _run_session(tmp_path)
    report = verify_journal(tmp_path / "session.jsonl")
    assert report.ok is True
    assert report.records == len(session.bus.events)


def test_replay_determinism(tmp_path: Path) -> None:
    session = _run_session(tmp_path)
    replayed = PaperSession.replay_from_journal(tmp_path / "session.jsonl")

    original_intents = _payloads(session, Streams.SIGNAL_INTENTS)
    original_fills = _payloads(session, Streams.EXEC_FILLS)
    assert original_intents  # the property must not pass vacuously
    assert original_fills
    assert _payloads(replayed, Streams.SIGNAL_INTENTS) == original_intents
    assert _payloads(replayed, Streams.EXEC_FILLS) == original_fills


def test_positions_consistent(tmp_path: Path) -> None:
    session = _run_session(tmp_path)
    summary = session.summary
    assert summary is not None

    net: dict[str, float] = {symbol: 0.0 for symbol in SYMBOLS}
    for event in session.bus.events:
        if event.stream != Streams.EXEC_FILLS:
            continue
        fill = Fill.model_validate(event.payload)
        net[fill.symbol] += fill.qty if fill.side is Side.BUY else -fill.qty
    assert summary["final_positions"] == net
