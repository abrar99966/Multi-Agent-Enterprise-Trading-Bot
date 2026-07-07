"""Autonomy tiers: policy classification + gateway approval routing."""
from __future__ import annotations

from app.core.clock import SimClock
from app.core.events import (
    ApprovalDecision,
    ApprovalRequest,
    Bar,
    OrderIntent,
    RiskVerdict,
    Side,
    Streams,
)
from app.risk.approver import AutoApprover
from app.risk.gateway import RiskGateway
from app.risk.limits import RiskLimits
from app.risk.tiers import TierPolicy
from tests.helpers import SyncTestBus

_T = 1_750_000_000_000_000_000
_LIMITS = RiskLimits(nav=1_000_000.0)


def _intent(qty: float, strategy: str = "s1") -> OrderIntent:
    return OrderIntent(intent_id=f"i-{strategy}-{qty}", strategy_id=strategy,
                       symbol="X", side=Side.BUY, qty=qty, ts_signal=_T)


# ---------------------------------------------------------------- policy


def test_untrusted_strategy_is_tier3() -> None:
    tier, reasons = TierPolicy().classify(_intent(10), 100.0, _LIMITS, 10, 1000)
    assert tier == 3 and "strategy_not_trusted" in reasons


def test_trusted_small_liquid_is_tier1() -> None:
    pol = TierPolicy(trusted=frozenset({"s1"}))
    # notional 1000 = 0.1% NAV; ample headroom.
    tier, reasons = pol.classify(_intent(10), 100.0, _LIMITS, 10, 1000)
    assert tier == 1 and "autonomous" in reasons


def test_trusted_large_order_forced_tier3() -> None:
    pol = TierPolicy(trusted=frozenset({"s1"}))
    # notional 20000 = 2% NAV > tier3 fraction (1%).
    tier, _ = pol.classify(_intent(200), 100.0, _LIMITS, 200, 20000)
    assert tier == 3


def test_trusted_mid_order_is_tier2() -> None:
    pol = TierPolicy(trusted=frozenset({"s1"}))
    # notional 5000 = 0.5% NAV: between tier1 (0.25%) and tier3 (1%).
    tier, _ = pol.classify(_intent(50), 100.0, _LIMITS, 50, 5000)
    assert tier == 2


# ---------------------------------------------------------------- gateway routing


def _seed_price(bus: SyncTestBus) -> None:
    bus.publish(
        Streams.MD_BARS,
        Bar(symbol="X", ts_open=_T, interval_s=60, open=100, high=100, low=100,
            close=100, volume=10),
        ts_event=_T,
    )


def test_tier3_intent_is_held_then_released_on_approval() -> None:
    clock = SimClock(_T)
    bus = SyncTestBus(clock)
    gw = RiskGateway(bus, clock, _LIMITS, auto_release_max_tier=1)  # untrusted -> tier 3
    _seed_price(bus)
    bus.publish(Streams.SIGNAL_INTENTS, _intent(10), ts_event=_T)
    # Risk-approved, but held: an ApprovalRequest, not an order.
    v = next(RiskVerdict.model_validate(e.payload) for e in bus.stream(Streams.RISK_VERDICTS))
    assert v.approved is True and v.tier == 3
    reqs = bus.stream(Streams.CTL_APPROVAL_REQUESTS)
    assert len(reqs) == 1
    assert bus.stream(Streams.EXEC_ORDERS) == []
    assert gw.working("X") == 0.0  # nothing reserved while held
    # Approve -> order released and reserved now.
    req = ApprovalRequest.model_validate(reqs[0].payload)
    bus.publish(
        Streams.CTL_APPROVAL_DECISIONS,
        ApprovalDecision(intent_id=req.intent_id, approved=True, ts=_T),
        ts_event=_T,
    )
    assert len(bus.stream(Streams.EXEC_ORDERS)) == 1
    assert gw.working("X") == 10.0


def test_tier3_intent_rejected_yields_no_order() -> None:
    clock = SimClock(_T)
    bus = SyncTestBus(clock)
    RiskGateway(bus, clock, _LIMITS, auto_release_max_tier=1)
    _seed_price(bus)
    bus.publish(Streams.SIGNAL_INTENTS, _intent(10), ts_event=_T)
    req = ApprovalRequest.model_validate(bus.stream(Streams.CTL_APPROVAL_REQUESTS)[0].payload)
    bus.publish(
        Streams.CTL_APPROVAL_DECISIONS,
        ApprovalDecision(intent_id=req.intent_id, approved=False, ts=_T),
        ts_event=_T,
    )
    assert bus.stream(Streams.EXEC_ORDERS) == []


def test_auto_approver_releases_held_intent() -> None:
    clock = SimClock(_T)
    bus = SyncTestBus(clock)
    RiskGateway(bus, clock, _LIMITS, auto_release_max_tier=1)
    AutoApprover(bus, clock, max_tier=3)
    _seed_price(bus)
    bus.publish(Streams.SIGNAL_INTENTS, _intent(10), ts_event=_T)
    # Request -> auto decision -> order, all synchronously.
    assert len(bus.stream(Streams.CTL_APPROVAL_DECISIONS)) == 1
    assert len(bus.stream(Streams.EXEC_ORDERS)) == 1


def test_tier1_with_trusted_policy_auto_releases() -> None:
    clock = SimClock(_T)
    bus = SyncTestBus(clock)
    gw = RiskGateway(
        bus, clock, _LIMITS, policy=TierPolicy(trusted=frozenset({"s1"})),
        auto_release_max_tier=1,
    )
    _seed_price(bus)
    bus.publish(Streams.SIGNAL_INTENTS, _intent(10), ts_event=_T)
    # Trusted + small -> Tier 1 -> released immediately, no approval request.
    assert bus.stream(Streams.CTL_APPROVAL_REQUESTS) == []
    assert len(bus.stream(Streams.EXEC_ORDERS)) == 1
    assert gw.working("X") == 10.0
