"""Market-abuse surveillance detectors.

Phase 4 of the Institutional Target-State Architecture.

SEBI (Securities and Exchange Board of India) requires algorithmic
trading systems to implement surveillance for market abuse patterns.
These detectors run as streaming jobs on the event log.

Detector types:
  1. Spoofing: large orders placed and cancelled before execution
     to create false supply/demand signals.
  2. Wash Trading: simultaneous or near-simultaneous buy and sell in
     the same symbol by the same strategy, creating artificial volume.
  3. OTR (Order-to-Trade Ratio): excessive order submissions relative
     to actual fills â€” indicates potential manipulative quoting.
  4. Momentum Ignition: pattern of aggressive orders designed to trigger
     other algorithms' stop-losses or momentum signals.
  5. Layering: placing multiple orders at different price levels with
     intent to cancel â€” a form of spoofing across levels.

Each detector consumes events from the bus and emits SurveillanceAlert
events. Critical alerts trigger risk gateway intervention.
"""
from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.bus.base import EventBus
from app.core.events import (
    NS_PER_SEC,
    Event,
    Fill,
    Order,
    OrderStatus,
    OrderUpdate,
    Side,
    Streams,
)

log = logging.getLogger(__name__)

_NS_PER_MS = 1_000_000


class AlertSeverity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class AlertType(str, Enum):
    SPOOFING = "SPOOFING"
    WASH_TRADING = "WASH_TRADING"
    OTR_BREACH = "OTR_BREACH"
    MOMENTUM_IGNITION = "MOMENTUM_IGNITION"
    LAYERING = "LAYERING"
    RAPID_CANCELLATION = "RAPID_CANCELLATION"


@dataclass
class SurveillanceAlert:
    """A detected market-abuse pattern."""
    alert_id: str
    alert_type: AlertType
    severity: AlertSeverity
    symbol: str
    strategy_id: str
    detail: str
    timestamp: float         # wall-clock when detected
    event_time_ns: int = 0   # event-time context

    # Evidence
    order_ids: List[str] = field(default_factory=list)
    metrics: Dict[str, float] = field(default_factory=dict)

    # Action taken
    action: str = ""         # "none", "logged", "alert", "kill_switch"
    acknowledged: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "alert_id": self.alert_id,
            "alert_type": self.alert_type.value,
            "severity": self.severity.value,
            "symbol": self.symbol,
            "strategy_id": self.strategy_id,
            "detail": self.detail,
            "timestamp": self.timestamp,
            "order_ids": self.order_ids,
            "metrics": self.metrics,
            "action": self.action,
            "acknowledged": self.acknowledged,
        }


# -- Configuration ---------------------------------------------------------

@dataclass(frozen=True)
class SurveillanceConfig:
    """Tunable thresholds for surveillance detectors."""

    # Spoofing: order placed and cancelled within this window
    spoof_cancel_window_ms: int = 5000       # 5 seconds
    spoof_min_qty: float = 100               # Minimum qty to flag

    # Wash trading: buy+sell within this window for same strategy+symbol
    wash_window_ms: int = 60_000             # 1 minute
    wash_min_trades: int = 2                 # At least 2 fills (1 buy + 1 sell)

    # OTR: max orders / fills ratio in a rolling window
    otr_window_seconds: int = 300            # 5 minutes
    otr_max_ratio: float = 10.0             # 10 orders per fill maximum
    otr_min_orders: int = 20                 # Don't flag with < 20 orders

    # Rapid cancellation: too many cancels in a window
    rapid_cancel_window_ms: int = 10_000     # 10 seconds
    rapid_cancel_max: int = 10               # 10 cancels in window

    # Momentum ignition: sequential same-direction orders
    momentum_window_ms: int = 30_000         # 30 seconds
    momentum_min_orders: int = 5             # 5+ same-direction orders

    # Alert escalation
    critical_repeat_count: int = 3           # 3 alerts of same type â†’ CRITICAL


# -- Detector State --------------------------------------------------------

@dataclass
class _OrderRecord:
    """Internal tracking record for an order."""
    order_id: str
    symbol: str
    strategy_id: str
    side: Side
    qty: float
    created_ns: int
    status: OrderStatus = OrderStatus.NEW
    cancelled_ns: int = 0
    filled_ns: int = 0


@dataclass
class _FillRecord:
    """Internal tracking record for a fill."""
    fill_id: str
    order_id: str
    symbol: str
    strategy_id: str
    side: Side
    qty: float
    price: float
    ts_fill_ns: int


class SurveillanceEngine:
    """Streaming market-abuse surveillance.

    Subscribes to order, fill, and cancel events on the event bus.
    Produces SurveillanceAlert when patterns are detected.
    """

    def __init__(
        self,
        bus: EventBus,
        config: Optional[SurveillanceConfig] = None,
        on_alert: Optional[Callable[[SurveillanceAlert], None]] = None,
    ) -> None:
        self._bus = bus
        self._config = config or SurveillanceConfig()
        self._on_alert = on_alert

        # State
        self._orders: Dict[str, _OrderRecord] = {}  # order_id â†’ record
        self._fills: List[_FillRecord] = []
        self._cancels: List[Tuple[str, int]] = []    # (order_id, cancel_ns)

        # Per-strategy/symbol windows for OTR
        self._order_window: Dict[str, deque] = {}    # strategy â†’ deque of ts
        self._fill_window: Dict[str, deque] = {}     # strategy â†’ deque of ts

        # Alert history
        self._alerts: List[SurveillanceAlert] = []
        self._alert_counter = 0

        # Alert dedup: (type, strategy, symbol) â†’ count in last N minutes
        self._alert_counts: Dict[Tuple[str, str, str], int] = {}

        # Subscribe
        bus.subscribe(Streams.EXEC_ORDERS, self._on_order)
        bus.subscribe(Streams.EXEC_ORDER_UPDATES, self._on_order_update)
        bus.subscribe(Streams.EXEC_FILLS, self._on_fill)

    # -- Event handlers -----------------------------------------------------

    def _on_order(self, event: Event) -> None:
        order = Order.model_validate(event.payload)
        record = _OrderRecord(
            order_id=order.order_id,
            symbol=order.symbol,
            strategy_id=order.strategy_id,
            side=order.side,
            qty=order.qty,
            created_ns=event.ts_event,
        )
        self._orders[order.order_id] = record

        # Track for OTR
        key = order.strategy_id
        self._order_window.setdefault(key, deque()).append(event.ts_event)

        # Check OTR on placement too: a strategy spamming orders that never
        # fill (the worst abuse pattern) would otherwise never be checked,
        # because fills are the only other trigger.
        self._check_otr(order.strategy_id, event.ts_event)

        # Check momentum ignition
        self._check_momentum_ignition(order.symbol, order.strategy_id, order.side, event.ts_event)

    def _on_order_update(self, event: Event) -> None:
        update = OrderUpdate.model_validate(event.payload)
        record = self._orders.get(update.order_id)
        if record is None:
            return

        record.status = update.status

        if update.status == OrderStatus.CANCELLED:
            record.cancelled_ns = event.ts_event
            self._cancels.append((update.order_id, event.ts_event))

            # Check spoofing
            self._check_spoofing(record, event.ts_event)

            # Check rapid cancellation
            self._check_rapid_cancellation(record.strategy_id, event.ts_event)

    def _on_fill(self, event: Event) -> None:
        fill = Fill.model_validate(event.payload)
        fill_record = _FillRecord(
            fill_id=fill.fill_id,
            order_id=fill.order_id,
            symbol=fill.symbol,
            strategy_id=fill.strategy_id,
            side=fill.side,
            qty=fill.qty,
            price=fill.price,
            ts_fill_ns=fill.ts_fill,
        )
        self._fills.append(fill_record)

        # Track for OTR
        key = fill.strategy_id
        self._fill_window.setdefault(key, deque()).append(event.ts_event)

        # Update order record
        record = self._orders.get(fill.order_id)
        if record is not None:
            record.filled_ns = fill.ts_fill

        # Check wash trading
        self._check_wash_trading(fill.symbol, fill.strategy_id, fill.side, event.ts_event)

        # Check OTR
        self._check_otr(fill.strategy_id, event.ts_event)

    # -- Detector implementations -------------------------------------------

    def _check_spoofing(self, record: _OrderRecord, cancel_ns: int) -> None:
        """Detect spoofing: order placed and cancelled quickly."""
        if record.qty < self._config.spoof_min_qty:
            return

        lifetime_ms = (cancel_ns - record.created_ns) / _NS_PER_MS
        if lifetime_ms > self._config.spoof_cancel_window_ms:
            return

        # Check if it was filled (even partially) â€” not spoofing if filled
        if record.filled_ns > 0:
            return

        severity = AlertSeverity.MEDIUM
        if lifetime_ms < 1000:  # Cancelled in < 1 second
            severity = AlertSeverity.HIGH
        if record.qty > self._config.spoof_min_qty * 10:
            severity = AlertSeverity.HIGH

        self._emit_alert(
            AlertType.SPOOFING,
            record.symbol,
            record.strategy_id,
            f"Order {record.order_id} ({record.side.value} {record.qty} {record.symbol}) "
            f"cancelled after {lifetime_ms:.0f}ms without fill â€” potential spoofing",
            cancel_ns,
            order_ids=[record.order_id],
            metrics={"lifetime_ms": lifetime_ms, "qty": record.qty},
            severity=severity,
        )

    def _check_wash_trading(
        self, symbol: str, strategy_id: str, side: Side, now_ns: int
    ) -> None:
        """Detect wash trading: same strategy buys AND sells within window."""
        window_ns = self._config.wash_window_ms * _NS_PER_MS
        cutoff = now_ns - window_ns

        recent_fills = [
            f for f in self._fills
            if f.symbol == symbol
            and f.strategy_id == strategy_id
            and f.ts_fill_ns >= cutoff
        ]

        buys = [f for f in recent_fills if f.side == Side.BUY]
        sells = [f for f in recent_fills if f.side == Side.SELL]

        if len(buys) >= 1 and len(sells) >= 1:
            total_trades = len(buys) + len(sells)
            if total_trades >= self._config.wash_min_trades:
                buy_qty = sum(f.qty for f in buys)
                sell_qty = sum(f.qty for f in sells)
                overlap = min(buy_qty, sell_qty)

                severity = AlertSeverity.HIGH
                if overlap > 1000:
                    severity = AlertSeverity.CRITICAL

                order_ids = [f.order_id for f in recent_fills]
                self._emit_alert(
                    AlertType.WASH_TRADING,
                    symbol,
                    strategy_id,
                    f"Strategy {strategy_id} bought {buy_qty} and sold {sell_qty} "
                    f"{symbol} within {self._config.wash_window_ms}ms â€” "
                    f"overlapping qty={overlap:.0f}",
                    now_ns,
                    order_ids=order_ids,
                    metrics={
                        "buy_qty": buy_qty,
                        "sell_qty": sell_qty,
                        "overlap_qty": overlap,
                        "n_trades": total_trades,
                    },
                    severity=severity,
                )

    def _check_otr(self, strategy_id: str, now_ns: int) -> None:
        """Check Order-to-Trade Ratio for a strategy."""
        window_ns = self._config.otr_window_seconds * NS_PER_SEC
        cutoff = now_ns - window_ns

        # Prune windows
        orders = self._order_window.get(strategy_id, deque())
        while orders and orders[0] < cutoff:
            orders.popleft()

        fills = self._fill_window.get(strategy_id, deque())
        while fills and fills[0] < cutoff:
            fills.popleft()

        n_orders = len(orders)
        n_fills = len(fills)

        if n_orders < self._config.otr_min_orders:
            return

        otr = n_orders / max(n_fills, 1)
        if otr > self._config.otr_max_ratio:
            severity = AlertSeverity.MEDIUM
            if otr > self._config.otr_max_ratio * 2:
                severity = AlertSeverity.HIGH

            self._emit_alert(
                AlertType.OTR_BREACH,
                "*",  # All symbols for this strategy
                strategy_id,
                f"Strategy {strategy_id} OTR={otr:.1f} ({n_orders} orders / "
                f"{n_fills} fills in {self._config.otr_window_seconds}s) "
                f"exceeds max={self._config.otr_max_ratio}",
                now_ns,
                metrics={
                    "otr": otr,
                    "n_orders": n_orders,
                    "n_fills": n_fills,
                    "window_seconds": self._config.otr_window_seconds,
                },
                severity=severity,
            )

    def _check_rapid_cancellation(self, strategy_id: str, now_ns: int) -> None:
        """Detect rapid cancellation bursts."""
        window_ns = self._config.rapid_cancel_window_ms * _NS_PER_MS
        cutoff = now_ns - window_ns

        recent = [
            (oid, ts) for oid, ts in self._cancels
            if ts >= cutoff
            and self._orders.get(oid, _OrderRecord("", "", strategy_id, Side.BUY, 0, 0)).strategy_id == strategy_id
        ]

        if len(recent) >= self._config.rapid_cancel_max:
            self._emit_alert(
                AlertType.RAPID_CANCELLATION,
                "*",
                strategy_id,
                f"Strategy {strategy_id} cancelled {len(recent)} orders in "
                f"{self._config.rapid_cancel_window_ms}ms",
                now_ns,
                order_ids=[oid for oid, _ in recent],
                metrics={"cancel_count": len(recent)},
                severity=AlertSeverity.MEDIUM,
            )

    def _check_momentum_ignition(
        self, symbol: str, strategy_id: str, side: Side, now_ns: int
    ) -> None:
        """Detect momentum ignition: rapid same-direction order bursts."""
        window_ns = self._config.momentum_window_ms * _NS_PER_MS
        cutoff = now_ns - window_ns

        recent = [
            r for r in self._orders.values()
            if r.symbol == symbol
            and r.strategy_id == strategy_id
            and r.side == side
            and r.created_ns >= cutoff
        ]

        if len(recent) >= self._config.momentum_min_orders:
            total_qty = sum(r.qty for r in recent)
            self._emit_alert(
                AlertType.MOMENTUM_IGNITION,
                symbol,
                strategy_id,
                f"Strategy {strategy_id} sent {len(recent)} {side.value} orders "
                f"for {symbol} (total qty={total_qty:.0f}) in "
                f"{self._config.momentum_window_ms}ms â€” potential momentum ignition",
                now_ns,
                order_ids=[r.order_id for r in recent],
                metrics={
                    "n_orders": len(recent),
                    "total_qty": total_qty,
                    "side": side.value,
                },
                severity=AlertSeverity.HIGH,
            )

    # -- Alert management ---------------------------------------------------

    def _emit_alert(
        self,
        alert_type: AlertType,
        symbol: str,
        strategy_id: str,
        detail: str,
        event_time_ns: int,
        order_ids: Optional[List[str]] = None,
        metrics: Optional[Dict[str, float]] = None,
        severity: AlertSeverity = AlertSeverity.MEDIUM,
    ) -> None:
        """Create and store a surveillance alert."""
        self._alert_counter += 1
        alert_id = f"surv-{self._alert_counter:06d}"

        # Escalate based on repeat count
        dedup_key = (alert_type.value, strategy_id, symbol)
        self._alert_counts[dedup_key] = self._alert_counts.get(dedup_key, 0) + 1
        if self._alert_counts[dedup_key] >= self._config.critical_repeat_count:
            severity = AlertSeverity.CRITICAL

        alert = SurveillanceAlert(
            alert_id=alert_id,
            alert_type=alert_type,
            severity=severity,
            symbol=symbol,
            strategy_id=strategy_id,
            detail=detail,
            timestamp=time.time(),
            event_time_ns=event_time_ns,
            order_ids=order_ids or [],
            metrics=metrics or {},
            action="logged",
        )

        self._alerts.append(alert)

        # Log
        log.warning(
            "SURVEILLANCE [%s] %s: %s",
            severity.value, alert_type.value, detail,
        )

        # Callback
        if self._on_alert:
            self._on_alert(alert)

    # -- Public API ---------------------------------------------------------

    def alerts(
        self,
        min_severity: AlertSeverity = AlertSeverity.LOW,
        limit: int = 100,
    ) -> List[SurveillanceAlert]:
        """Return recent alerts at or above the given severity."""
        severity_order = {
            AlertSeverity.LOW: 0,
            AlertSeverity.MEDIUM: 1,
            AlertSeverity.HIGH: 2,
            AlertSeverity.CRITICAL: 3,
        }
        min_ord = severity_order[min_severity]
        filtered = [
            a for a in self._alerts
            if severity_order.get(a.severity, 0) >= min_ord
        ]
        return filtered[-limit:]

    def alert_summary(self) -> Dict[str, Any]:
        """Summary of surveillance status."""
        by_type: Dict[str, int] = {}
        by_severity: Dict[str, int] = {}
        for a in self._alerts:
            by_type[a.alert_type.value] = by_type.get(a.alert_type.value, 0) + 1
            by_severity[a.severity.value] = by_severity.get(a.severity.value, 0) + 1

        return {
            "total_alerts": len(self._alerts),
            "by_type": by_type,
            "by_severity": by_severity,
            "unacknowledged": sum(1 for a in self._alerts if not a.acknowledged),
            "orders_tracked": len(self._orders),
            "fills_tracked": len(self._fills),
        }

    def acknowledge(self, alert_id: str) -> bool:
        """Mark an alert as acknowledged."""
        for a in self._alerts:
            if a.alert_id == alert_id:
                a.acknowledged = True
                return True
        return False
