"""Slow path: failure isolation (chaos), gateway tightening, determinism,
and the provider-agnostic LLM analyst via a deterministic StubProvider."""
from __future__ import annotations

from pathlib import Path

from app.core.clock import SimClock
from app.core.events import (
    Bar,
    Event,
    OrderIntent,
    ParameterChange,
    RiskVerdict,
    Side,
    Streams,
)
from app.engine.runner import PaperSession
from app.risk.gateway import RiskGateway
from app.risk.limits import RiskLimits
from app.slowpath.analyst import LLMAnalyst
from app.slowpath.base import SlowPathAgent
from app.slowpath.params import ControllableParam, ParameterController
from app.slowpath.providers import StubProvider
from tests.helpers import SyncTestBus

_T = 1_750_000_000_000_000_000


# -------------------------------------------------- failure isolation (chaos)


class _BrokenAnalyst(SlowPathAgent):
    """A slow-path agent whose logic always raises (simulates an LLM outage /
    model crash). Its failures must be swallowed, never break the bus."""

    def __init__(self, bus) -> None:
        super().__init__(bus)
        self.subscribe(Streams.MD_BARS, self._on_bar)

    def _on_bar(self, event: Event) -> None:
        raise RuntimeError("analyst exploded")


def test_broken_slow_path_agent_does_not_break_dispatch() -> None:
    from app.bus.memory import MemoryBus

    clock = SimClock(_T)
    bus = MemoryBus(clock)
    broken = _BrokenAnalyst(bus)
    seen = []
    bus.subscribe(Streams.MD_BARS, lambda e: seen.append(e.seq))
    for i in range(5):
        bus.publish(
            Streams.MD_BARS,
            Bar(symbol="X", ts_open=_T + i, interval_s=60, open=1, high=1, low=1,
                close=1, volume=1),
            ts_event=_T + i,
        )
    bus.run_until_idle()
    assert seen == [0, 1, 2, 3, 4]   # the healthy subscriber saw every bar
    assert broken.errors == 5         # the broken agent's failures were counted


def test_slow_path_outage_is_harmless_to_fast_path() -> None:
    """A broken analyst attached to the live session must not change any
    fast-path output vs a baseline run without it."""
    baseline = PaperSession(["RELIANCE", "TCS"], n_bars=300, seed=11)
    baseline.run()

    chaos = PaperSession(["RELIANCE", "TCS"], n_bars=300, seed=11)
    _BrokenAnalyst(chaos.bus)  # attach a crashing analyst before running
    chaos.run()

    for stream in (Streams.SIGNAL_INTENTS, Streams.EXEC_ORDERS, Streams.EXEC_FILLS):
        b = [e.payload for e in baseline.bus.events if e.stream == stream]
        c = [e.payload for e in chaos.bus.events if e.stream == stream]
        assert b == c, f"fast-path stream {stream} changed under slow-path failure"


# -------------------------------------------------- gateway consumes tightening


def test_gateway_tightening_constrains_new_orders() -> None:
    clock = SimClock(_T)
    bus = SyncTestBus(clock)
    limits = RiskLimits(max_position_qty=1000, nav=1_000_000.0)
    gw = RiskGateway(bus, clock, limits, auto_release_max_tier=3)
    bus.publish(
        Streams.MD_BARS,
        Bar(symbol="X", ts_open=_T, interval_s=60, open=100, high=100, low=100,
            close=100, volume=10),
        ts_event=_T,
    )

    def _intent(iid, qty):
        bus.publish(
            Streams.SIGNAL_INTENTS,
            OrderIntent(intent_id=iid, strategy_id="s1", symbol="X", side=Side.BUY,
                        qty=qty, ts_signal=_T),
            ts_event=_T,
        )

    def _verdict(iid):
        return next(RiskVerdict.model_validate(e.payload)
                    for e in reversed(bus.stream(Streams.RISK_VERDICTS))
                    if RiskVerdict.model_validate(e.payload).intent_id == iid)

    _intent("i1", 800)  # under the 1000 baseline -> approved (and reserves 800)
    assert _verdict("i1").approved is True
    # Slow path tightens max_position_qty to 500.
    bus.publish(
        Streams.CTL_PARAMS,
        ParameterChange(parameter="risk.max_position_qty", old_value=1000,
                        new_value=500, source="regime", ttl_s=3600),
        ts_event=_T,
    )
    assert gw._effective_limit("max_position_qty") == 500
    _intent("i2", 100)  # 800 working + 100 = 900 > tightened 500 -> rejected
    v2 = _verdict("i2")
    assert v2.approved is False and "position_limit" in (v2.reject_reason or "")


# -------------------------------------------------- replay determinism


def test_slow_path_session_replays_identically(tmp_path: Path) -> None:
    journal = tmp_path / "sp.jsonl"
    original = PaperSession(["RELIANCE", "TCS"], n_bars=400, seed=13,
                            journal_path=journal, enable_slow_path=True)
    original.run()
    replay = PaperSession.replay_from_journal(journal, enable_slow_path=True)
    for stream in (Streams.SIGNAL_INTENTS, Streams.EXEC_FILLS, Streams.CTL_PARAMS,
                   Streams.CTL_PARAM_PROPOSALS):
        o = [e.payload for e in original.bus.events if e.stream == stream]
        r = [e.payload for e in replay.bus.events if e.stream == stream]
        assert o == r, f"slow-path stream {stream} diverged on replay"
    assert original.summary["realized_pnl_total"] == replay.summary["realized_pnl_total"]


# ------------------------------------------- LLM analyst (provider-agnostic)


def test_llm_analyst_bearish_tightens_via_controller() -> None:
    clock = SimClock(_T)
    bus = SyncTestBus(clock)
    param = ControllableParam("risk.max_gross_exposure", 2_000_000.0, 0.0,
                              2_000_000.0, "down", max_step_frac=1.0)
    ctrl = ParameterController(bus, clock, [param])
    provider = StubProvider(response={"direction": "bearish", "severity": "high",
                                      "confidence": 0.8, "affected": ["BANKS"],
                                      "rationale": "rate shock"})
    analyst = LLMAnalyst(bus, clock, provider, baseline_gross=2_000_000.0)
    proposal = analyst.assess_and_propose("RBI surprise hike", ["HDFCBANK"])
    assert proposal is not None and proposal.proposed_value < 2_000_000.0
    # Bearish/high -> tightening -> auto-applied by the controller.
    assert ctrl.effective("risk.max_gross_exposure") < 2_000_000.0


def test_llm_analyst_malformed_assessment_is_noop() -> None:
    """A model that returns JSON without a valid direction (observed live with
    a local 8B model) must change NOTHING -- not be read as bullish/bearish."""
    clock = SimClock(_T)
    bus = SyncTestBus(clock)
    param = ControllableParam("risk.max_gross_exposure", 2_000_000.0, 0.0,
                              2_200_000.0, "down", max_step_frac=1.0)
    ctrl = ParameterController(bus, clock, [param])
    for garbage in ({}, {"impact": "bad"}, {"direction": "sideways"},
                    {"direction": None}):
        provider = StubProvider(response=garbage)
        analyst = LLMAnalyst(bus, clock, provider, baseline_gross=2_000_000.0)
        assert analyst.assess_and_propose("noise", ["X"]) is None
    assert bus.stream(Streams.CTL_PARAM_PROPOSALS) == []  # nothing published
    assert ctrl.effective("risk.max_gross_exposure") == 2_000_000.0
    assert ctrl.pending_loosening == {}


def test_llm_analyst_bullish_is_held() -> None:
    clock = SimClock(_T)
    bus = SyncTestBus(clock)
    param = ControllableParam("risk.max_gross_exposure", 2_000_000.0, 0.0,
                              2_200_000.0, "down")
    ctrl = ParameterController(bus, clock, [param])
    provider = StubProvider(response={"direction": "bullish", "severity": "low",
                                      "confidence": 0.6, "affected": ["NIFTY"],
                                      "rationale": "risk-on"})
    analyst = LLMAnalyst(bus, clock, provider, baseline_gross=2_000_000.0)
    analyst.assess_and_propose("Strong GDP print", ["NIFTY"])
    # Bullish -> loosening above baseline -> held for human approval, not applied.
    assert ctrl.effective("risk.max_gross_exposure") == 2_000_000.0
    assert len(ctrl.pending_loosening) == 1
