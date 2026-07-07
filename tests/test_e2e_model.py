"""End-to-end GBDT fast-path session + replay determinism.

The Phase 0 e2e proved the SMA strategy replays bit-identically. This proves
the SAME property for the model path: a session driven by ModelStrategy +
InferenceService, journaled, replays through fresh components reproducing the
exact intent and fill sequences -- so swapping the decision engine from rules
to a trained model did not break the determinism contract.
"""
from __future__ import annotations

from pathlib import Path

from app.core.events import RiskVerdict, Streams
from app.engine.inference import InferenceService
from app.engine.runner import PaperSession
from app.strategy.model_strategy import ModelStrategy


def _factory(inference: InferenceService):
    # Fresh strategy each call (own fabric/state), shared stateless inference.
    return lambda bus, clock: ModelStrategy(bus, clock, inference)


def _payloads(session: PaperSession, stream: str) -> list[dict]:
    return [e.payload for e in session.bus.events if e.stream == stream]


def test_model_session_trades_and_gates(
    inference_service: InferenceService, tmp_path: Path
) -> None:
    journal = tmp_path / "model.jsonl"
    session = PaperSession(
        ["RELIANCE", "TCS"], n_bars=500, seed=7, journal_path=journal,
        strategy_factory=_factory(inference_service),
    )
    summary = session.run()
    assert summary["intents"] > 0
    assert summary["fills"] > 0
    assert summary["rejected"] == 0  # all within default limits

    # Every released order maps to an APPROVED verdict -> nothing bypassed risk.
    approved_ids = {
        RiskVerdict.model_validate(e.payload).intent_id
        for e in session.bus.events
        if e.stream == Streams.RISK_VERDICTS
        and RiskVerdict.model_validate(e.payload).approved
    }
    order_intent_ids = {p["intent_id"] for p in _payloads(session, Streams.EXEC_ORDERS)}
    assert order_intent_ids <= approved_ids

    # Intents carry model_id + attributions (explainability from the journal).
    intents = _payloads(session, Streams.SIGNAL_INTENTS)
    assert intents and all(p["model_id"] == inference_service.model_id for p in intents)
    assert all("prob" in p["attributions"] for p in intents)


def test_model_session_replay_is_bit_identical(
    inference_service: InferenceService, tmp_path: Path
) -> None:
    journal = tmp_path / "model.jsonl"
    original = PaperSession(
        ["RELIANCE", "TCS"], n_bars=500, seed=7, journal_path=journal,
        strategy_factory=_factory(inference_service),
    )
    original.run()
    replay = PaperSession.replay_from_journal(
        journal, strategy_factory=_factory(inference_service)
    )
    for stream in (Streams.SIGNAL_INTENTS, Streams.EXEC_FILLS):
        assert _payloads(original, stream) == _payloads(replay, stream)
    assert original.summary["realized_pnl_total"] == replay.summary["realized_pnl_total"]
