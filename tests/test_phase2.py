"""Phase 2 end-to-end: tiers + TCA wired into the session, determinism intact,
TCA persisted to a store."""
from __future__ import annotations

from pathlib import Path

from app.engine.inference import InferenceService
from app.engine.runner import PaperSession
from app.strategy.model_strategy import ModelStrategy
from app.tca.store import SqliteTcaStore


def _factory(inference: InferenceService):
    return lambda bus, clock: ModelStrategy(bus, clock, inference)


def test_session_routes_through_tiers_and_records_tca(
    inference_service: InferenceService, tmp_path: Path
) -> None:
    session = PaperSession(
        ["RELIANCE", "TCS"], n_bars=500, seed=7, journal_path=tmp_path / "p2.jsonl",
        strategy_factory=_factory(inference_service),
    )
    summary = session.run()
    # Untrusted strategy + auto_release_max_tier=1 => everything is Tier 3,
    # held, then cleared by the harness AutoApprover (max_tier=3).
    assert summary["approved"] > 0
    assert summary["tier_counts"][3] == summary["approved"]
    assert summary["approval_requests"] == summary["approved"]
    assert summary["orders"] == summary["approved"]  # all released after approval
    # TCA computed on every fill.
    tca = summary["tca"]
    assert tca["n_fills"] == summary["fills"]
    assert "total_is_bps" in tca

    # Persist + reload the per-fill TCA.
    with SqliteTcaStore(tmp_path / "tca.db") as store:
        assert store.insert(session.tca.results()) == summary["fills"]
        assert len(store.all()) == summary["fills"]


def test_phase2_replay_is_bit_identical(
    inference_service: InferenceService, tmp_path: Path
) -> None:
    journal = tmp_path / "p2.jsonl"
    original = PaperSession(
        ["RELIANCE", "TCS"], n_bars=500, seed=7, journal_path=journal,
        strategy_factory=_factory(inference_service),
    )
    original.run()
    replay = PaperSession.replay_from_journal(
        journal, strategy_factory=_factory(inference_service)
    )
    # Same intents, fills, AND the derived TCA must reproduce exactly.
    for stream in ("signal.intents", "exec.fills", "exec.orders",
                   "ctl.approval_requests", "ctl.approval_decisions"):
        o = [e.payload for e in original.bus.events if e.stream == stream]
        r = [e.payload for e in replay.bus.events if e.stream == stream]
        assert o == r, f"stream {stream} diverged on replay"
    assert original.summary["tca"] == replay.summary["tca"]
