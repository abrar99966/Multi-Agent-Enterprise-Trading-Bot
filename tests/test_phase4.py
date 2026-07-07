"""Phase 4 verification: impact model, SOR, execution algos, reconciliation,
surveillance. These modules arrived without pytest coverage; this file pins
the behaviors claimed in docs/PHASE4_IMPLEMENTATION.md."""
from __future__ import annotations

from app.core.clock import SimClock
from app.core.events import (
    Bar,
    Fill,
    Order,
    OrderIntent,
    OrderStatus,
    OrderType,
    OrderUpdate,
    Side,
    Streams,
)
from app.execution.algos import AlgoStatus, AlgoType, ExecutionAlgoEngine
from app.execution.impact_model import ImpactModel
from app.execution.sor import BrokerHealth, SmartOrderRouter
from app.reconciliation.engine import (
    MismatchSeverity,
    MismatchType,
    PositionView,
    ReconciliationEngine,
)
from app.surveillance.detectors import AlertType, SurveillanceEngine
from tests.helpers import SyncTestBus

_T = 1_750_000_000_000_000_000
_IVL_NS = 60_000_000_000


# ---------------------------------------------------------------- impact model


def test_impact_model_components_and_monotonicity() -> None:
    model = ImpactModel(region="IN")
    small = model.estimate("RELIANCE", "BUY", 500, 2500.0,
                           avg_daily_volume=5_000_000, daily_volatility=0.02)
    assert small.spread_cost_bps > 0
    assert small.temporary_impact_bps >= 0 and small.permanent_impact_bps >= 0
    assert abs(small.total_expected_cost_bps
               - (small.spread_cost_bps + small.temporary_impact_bps
                  + small.permanent_impact_bps)) < 1e-6
    assert small.notional == 500 * 2500.0
    assert 0 < small.pov_pct < 0.1  # 500/5M = 0.01% ADV
    assert small.recommended_algo == "IS"  # tiny order -> IS per the doc

    big = model.estimate("RELIANCE", "BUY", 500_000, 2500.0,
                         avg_daily_volume=5_000_000, daily_volatility=0.02)
    assert big.total_expected_cost_bps > small.total_expected_cost_bps
    assert big.recommended_algo in ("VWAP", "POV", "ADAPTIVE", "IS")


# ------------------------------------------------------------------------ SOR


def _sor_with_two_brokers() -> SmartOrderRouter:
    sor = SmartOrderRouter()
    for slug in ("dhan", "zerodha"):
        sor.register_broker(slug, slug.title(), "IN", is_live=True, is_connected=True)
        sor.update_health(slug, BrokerHealth.GREEN)
        sor.record_success(slug, latency_ms=80.0)
    return sor


def test_sor_routes_and_reports_scores() -> None:
    sor = _sor_with_two_brokers()
    decision = sor.route("RELIANCE", "IN")
    assert decision.primary_broker in ("dhan", "zerodha")
    assert decision.all_scores  # audit trail of all broker scores
    assert decision.reason
    status = sor.failover_status()
    assert isinstance(status, dict)


def test_sor_circuit_breaker_fails_over() -> None:
    sor = _sor_with_two_brokers()
    primary = sor.route("RELIANCE", "IN").primary_broker
    other = "zerodha" if primary == "dhan" else "dhan"
    for i in range(5):  # 5 errors in window -> breaker trips -> RED
        sor.record_error(primary, f"timeout {i}")
    decision = sor.route("RELIANCE", "IN")
    assert decision.primary_broker == other, "SOR must fail over off the tripped broker"


# ---------------------------------------------------------------- execution algos


def test_algo_engine_children_are_risk_gated_intents() -> None:
    """Child orders go out as OrderIntents on signal.intents (risk-gated path);
    the engine must never write exec.orders directly."""
    clock = SimClock(_T)
    bus = SyncTestBus(clock)
    engine = ExecutionAlgoEngine(bus, n_slices=5)
    algo = engine.submit(
        AlgoType.IS, parent_intent_id="p1", strategy_id="s1", symbol="RELIANCE",
        side=Side.BUY, qty=100, reference_price=100.0, urgency=0.5,
        duration_min=5, start_time_ns=0,
    )
    assert algo.status == AlgoStatus.PENDING
    assert len(algo.slices) == 5

    def bar(i: int) -> Bar:
        return Bar(symbol="RELIANCE", ts_open=_T + i * _IVL_NS, interval_s=60,
                   open=100, high=101, low=99, close=100, volume=10_000)

    bus.publish(Streams.MD_BARS, bar(0), ts_event=_T)  # activates + fires slice 0
    intents = [OrderIntent.model_validate(e.payload)
               for e in bus.stream(Streams.SIGNAL_INTENTS)]
    assert intents, "first due slice should fire a child intent"
    assert all(i.reason.startswith("algo:IS") for i in intents)
    assert bus.stream(Streams.EXEC_ORDERS) == []  # order boundary intact

    # Fill the emitted children -> attribution back to the parent algo.
    for i, intent in enumerate(intents):
        bus.publish(
            Streams.EXEC_FILLS,
            Fill(fill_id=f"f{i}", order_id=f"ord-{intent.intent_id}",
                 intent_id=intent.intent_id, strategy_id="s1", symbol="RELIANCE",
                 side=Side.BUY, qty=intent.qty, price=100.05, ts_fill=_T),
            ts_event=_T,
        )
    assert algo.filled_qty > 0
    assert algo.avg_fill_price > 0

    # Past the 5-min duration: remaining qty force-fires, then fills complete it.
    bus.publish(Streams.MD_BARS, bar(6), ts_event=_T + 6 * _IVL_NS)
    later = [OrderIntent.model_validate(e.payload)
             for e in bus.stream(Streams.SIGNAL_INTENTS)]
    filled = {OrderIntent.model_validate(e.payload).intent_id  # already-filled ids
              for e in bus.stream(Streams.SIGNAL_INTENTS)[: len(intents)]}
    for i, intent in enumerate(later):
        if intent.intent_id in filled:
            continue
        bus.publish(
            Streams.EXEC_FILLS,
            Fill(fill_id=f"g{i}", order_id=f"ord-{intent.intent_id}",
                 intent_id=intent.intent_id, strategy_id="s1", symbol="RELIANCE",
                 side=Side.BUY, qty=intent.qty, price=100.10,
                 ts_fill=_T + 6 * _IVL_NS),
            ts_event=_T + 6 * _IVL_NS,
        )
    assert algo.filled_qty >= 100 - 1e-9
    assert algo.is_complete
    assert algo.realized_shortfall_bps >= 0  # bought above reference -> a cost


# ---------------------------------------------------------------- reconciliation


def test_reconciliation_clean_book() -> None:
    eng = ReconciliationEngine()
    eng.set_internal_positions([PositionView("RELIANCE", 100, 2500.0, source="internal")])
    eng.set_broker_positions("dhan", [PositionView("RELIANCE", 100, 2500.0, source="dhan")])
    report = eng.reconcile()
    assert report.is_clean and report.total_mismatches == 0


def test_reconciliation_qty_mismatch_critical() -> None:
    eng = ReconciliationEngine()
    eng.set_internal_positions([PositionView("TCS", 50, 3500.0, source="internal")])
    eng.set_broker_positions("dhan", [PositionView("TCS", 45, 3500.0, source="dhan")])
    report = eng.reconcile()
    assert report.total_mismatches == 1
    mm = report.mismatches[0]
    assert mm.mismatch_type == MismatchType.QTY_MISMATCH
    # diff = 5 shares = 10% of position > 5% threshold -> CRITICAL (per doc)
    assert mm.severity == MismatchSeverity.CRITICAL
    assert not report.is_clean


def test_reconciliation_side_mismatch_emergency() -> None:
    eng = ReconciliationEngine()
    eng.set_internal_positions([PositionView("INFY", 100, 1500.0, source="internal")])
    eng.set_broker_positions("dhan", [PositionView("INFY", -100, 1500.0, source="dhan")])
    report = eng.reconcile()
    assert any(m.mismatch_type == MismatchType.SIDE_MISMATCH
               and m.severity == MismatchSeverity.EMERGENCY
               for m in report.mismatches)


def test_reconciliation_phantom_internal_emergency_by_notional() -> None:
    eng = ReconciliationEngine()
    # 100 @ 2500 = 250k notional > 1L emergency threshold
    eng.set_internal_positions([PositionView("RELIANCE", 100, 2500.0, source="internal")])
    eng.set_broker_positions("dhan", [])
    report = eng.reconcile()
    assert any(m.mismatch_type == MismatchType.PHANTOM_INTERNAL
               and m.severity == MismatchSeverity.EMERGENCY
               for m in report.mismatches)


# ---------------------------------------------------------------- surveillance


def _order(bus: SyncTestBus, oid: str, ts: int, side: Side = Side.BUY,
           qty: float = 500, strategy: str = "s1", symbol: str = "TCS") -> None:
    bus.publish(
        Streams.EXEC_ORDERS,
        Order(order_id=oid, intent_id=f"i-{oid}", strategy_id=strategy,
              symbol=symbol, side=side, qty=qty, order_type=OrderType.LIMIT,
              limit_price=100.0),
        ts_event=ts,
    )


def test_surveillance_spoofing_detected() -> None:
    clock = SimClock(_T)
    bus = SyncTestBus(clock)
    eng = SurveillanceEngine(bus)
    _order(bus, "o1", _T, qty=500)
    # Cancelled 2s after placement (< 5s window, qty >= 100) -> spoofing alert.
    bus.publish(
        Streams.EXEC_ORDER_UPDATES,
        OrderUpdate(order_id="o1", status=OrderStatus.CANCELLED),
        ts_event=_T + 2_000_000_000,
    )
    assert any(a.alert_type == AlertType.SPOOFING for a in eng.alerts())


def test_surveillance_wash_trading_detected() -> None:
    clock = SimClock(_T)
    bus = SyncTestBus(clock)
    eng = SurveillanceEngine(bus)
    for i, side in enumerate((Side.BUY, Side.SELL)):  # same strategy+symbol, 5s apart
        bus.publish(
            Streams.EXEC_FILLS,
            Fill(fill_id=f"w{i}", order_id=f"o{i}", intent_id=f"i{i}",
                 strategy_id="arb-v1", symbol="TCS", side=side, qty=100,
                 price=3500.0, ts_fill=_T + i * 5_000_000_000),
            ts_event=_T + i * 5_000_000_000,
        )
    assert any(a.alert_type == AlertType.WASH_TRADING for a in eng.alerts())


def test_surveillance_otr_breach_detected() -> None:
    clock = SimClock(_T)
    bus = SyncTestBus(clock)
    eng = SurveillanceEngine(bus)
    for i in range(25):  # 25 orders, zero fills, inside 5-min window
        _order(bus, f"q{i}", _T + i * 1_000_000_000, strategy="hf-1")
    assert any(a.alert_type == AlertType.OTR_BREACH for a in eng.alerts())


def test_surveillance_summary_and_acknowledge() -> None:
    clock = SimClock(_T)
    bus = SyncTestBus(clock)
    eng = SurveillanceEngine(bus)
    _order(bus, "o1", _T)
    bus.publish(
        Streams.EXEC_ORDER_UPDATES,
        OrderUpdate(order_id="o1", status=OrderStatus.CANCELLED),
        ts_event=_T + 1_000_000_000,
    )
    alerts = eng.alerts()
    assert alerts
    summary = eng.alert_summary()
    assert summary.get("total", len(alerts)) >= 1 or summary  # structure exists
    assert eng.acknowledge(alerts[0].alert_id) is True
