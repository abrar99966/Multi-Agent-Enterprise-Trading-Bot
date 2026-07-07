"""The order boundary: only the risk gateway may emit orders.

Phase 0/1 has no network yet, so the structural analog of the "strategy hosts
cannot reach a broker" rule (Phase 1 exit criterion) is: across the whole
backend, the ONLY code that publishes to Streams.EXEC_ORDERS is the risk
gateway. A source scan enforces that, plus a behavioral check that a rejected
intent yields no order.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.core.clock import SimClock
from app.core.events import Bar, KillSwitch, OrderIntent, Side, Streams
from app.risk.gateway import RiskGateway
from app.risk.limits import RiskLimits
from tests.helpers import SyncTestBus

_BACKEND = Path(__file__).resolve().parents[1] / "backend" / "app"
# Match publishes whose destination is EXEC_ORDERS specifically (the constant
# or its literal value), not EXEC_ORDER_UPDATES.
_PUBLISH_EXEC_ORDERS = re.compile(
    r"\.publish\(\s*(?:Streams\.EXEC_ORDERS|[\"']exec\.orders[\"'])\b"
)
_T = 1_750_000_000_000_000_000


def test_only_gateway_publishes_orders() -> None:
    offenders = []
    for path in _BACKEND.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if _PUBLISH_EXEC_ORDERS.search(text):
            offenders.append(path.relative_to(_BACKEND).as_posix())
    assert offenders == ["risk/gateway.py"], (
        f"only risk/gateway.py may publish to exec.orders, found: {offenders}"
    )


def test_rejected_intent_yields_no_order() -> None:
    clock = SimClock(_T)
    bus = SyncTestBus(clock)
    RiskGateway(bus, clock, RiskLimits())
    bus.publish(
        Streams.MD_BARS,
        Bar(symbol="X", ts_open=_T, interval_s=60, open=100, high=100, low=100,
            close=100, volume=10),
        ts_event=_T,
    )
    # K2 platform kill blocks everything.
    bus.publish(
        Streams.CTL_KILL,
        KillSwitch(level=2, engaged=True, scope="*", reason="test"),
        ts_event=_T,
    )
    bus.publish(
        Streams.SIGNAL_INTENTS,
        OrderIntent(intent_id="i1", strategy_id="s1", symbol="X", side=Side.BUY,
                    qty=100, ts_signal=_T),
        ts_event=_T,
    )
    assert bus.stream(Streams.EXEC_ORDERS) == []
