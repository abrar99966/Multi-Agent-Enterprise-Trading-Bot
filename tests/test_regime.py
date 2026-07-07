"""Statistical regime classifier: classification + tightening proposals."""
from __future__ import annotations

from app.core.clock import SimClock
from app.core.events import Bar, ParameterChangeProposal, Streams
from app.slowpath.regime import RegimeClassifier, classify_regime
from tests.helpers import SyncTestBus

_T = 1_750_000_000_000_000_000
_IVL = 60_000_000_000


def test_classify_calm_is_not_stress() -> None:
    # Uniform small returns -> short/long vol ratio ~1 -> not stress/crisis.
    rets = [0.001 * (1 if i % 2 else -1) for i in range(80)]
    assert classify_regime(rets) in ("chop", "trend")


def test_classify_volatility_spike_is_stress_or_crisis() -> None:
    calm = [0.0005 * (1 if i % 2 else -1) for i in range(70)]
    spike = [0.05 * (1 if i % 2 else -1) for i in range(10)]  # 100x larger
    regime = classify_regime(calm + spike)
    assert regime in ("stress", "crisis")


def test_warmup_returns_none() -> None:
    assert classify_regime([0.01] * 10) is None


def _bars(symbol, closes, start=_T):
    return [
        Bar(symbol=symbol, ts_open=start + i * _IVL, interval_s=60,
            open=c, high=c, low=c, close=c, volume=1000.0)
        for i, c in enumerate(closes)
    ]


def test_classifier_emits_tightening_proposal_on_spike() -> None:
    clock = SimClock(_T)
    bus = SyncTestBus(clock)
    RegimeClassifier(bus, clock, baseline_gross=2_000_000.0, baseline_position_qty=20_000.0)
    # Calm random-walk closes, then a sharp jump sequence to spike short vol.
    closes = [100.0]
    for i in range(70):
        closes.append(closes[-1] * (1 + (0.0005 if i % 2 else -0.0005)))
    for i in range(10):
        closes.append(closes[-1] * (1 + (0.05 if i % 2 else -0.05)))
    for bar in _bars("RELIANCE", closes):
        clock.advance_to(bar.ts_open)
        bus.publish(Streams.MD_BARS, bar, ts_event=bar.ts_open)
    proposals = [
        ParameterChangeProposal.model_validate(e.payload)
        for e in bus.stream(Streams.CTL_PARAM_PROPOSALS)
    ]
    assert proposals, "expected tightening proposals after the volatility spike"
    # All proposals are tightenings (below baseline) -- fail-safe direction.
    for p in proposals:
        if p.parameter == "risk.max_gross_exposure":
            assert p.proposed_value < 2_000_000.0
        if p.parameter == "risk.max_position_qty":
            assert p.proposed_value < 20_000.0


def test_classifier_calm_market_emits_nothing() -> None:
    clock = SimClock(_T)
    bus = SyncTestBus(clock)
    RegimeClassifier(bus, clock)
    closes = [100.0]
    for i in range(120):
        closes.append(closes[-1] * (1 + (0.0005 if i % 2 else -0.0005)))
    for bar in _bars("TCS", closes):
        clock.advance_to(bar.ts_open)
        bus.publish(Streams.MD_BARS, bar, ts_event=bar.ts_open)
    # No regime more severe than trend -> no tightening proposals.
    assert bus.stream(Streams.CTL_PARAM_PROPOSALS) == []
