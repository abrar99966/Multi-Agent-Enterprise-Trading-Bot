"""Cross-broker position reconciliation engine.

Phase 4 of the Institutional Target-State Architecture.

Reconciliation is the process of comparing our internal position view
(from the event log / OMS) against each broker's reported positions.
Mismatches are the single scariest silent failure in a trading system —
a phantom position or a missed fill can compound into uncontrolled exposure.

Architecture:
  - Runs on a configurable interval (default: 30s during market hours).
  - Queries each connected broker for their position view.
  - Compares against our internal position tracker (OMS).
  - Classifies mismatches by severity.
  - Emits alerts and, at critical severity, triggers kill switches.

Mismatch types:
  PHANTOM_INTERNAL: we think we have a position the broker doesn't know about.
  PHANTOM_BROKER: broker reports a position we don't have internally.
  QTY_MISMATCH: both agree position exists but qty differs.
  PRICE_MISMATCH: avg cost differs significantly (possible missed fill).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger(__name__)


class MismatchSeverity(str, Enum):
    INFO = "INFO"          # Minor rounding / timing differences
    WARNING = "WARNING"    # Significant but not dangerous
    CRITICAL = "CRITICAL"  # Dangerous — possible missed fills or phantom positions
    EMERGENCY = "EMERGENCY"  # Immediate kill switch required


class MismatchType(str, Enum):
    PHANTOM_INTERNAL = "PHANTOM_INTERNAL"
    PHANTOM_BROKER = "PHANTOM_BROKER"
    QTY_MISMATCH = "QTY_MISMATCH"
    PRICE_MISMATCH = "PRICE_MISMATCH"
    SIDE_MISMATCH = "SIDE_MISMATCH"


@dataclass
class PositionView:
    """A single position as seen by one source (internal OMS or broker)."""
    symbol: str
    qty: float              # Signed: + long, - short
    avg_cost: float = 0.0
    currency: str = "INR"
    source: str = ""        # "internal" or broker slug
    timestamp: float = 0.0  # When this view was captured


@dataclass
class ReconciliationMismatch:
    """A single position mismatch between internal and broker views."""
    symbol: str
    mismatch_type: MismatchType
    severity: MismatchSeverity

    internal_qty: float = 0.0
    broker_qty: float = 0.0
    qty_diff: float = 0.0

    internal_avg_cost: float = 0.0
    broker_avg_cost: float = 0.0

    broker_slug: str = ""
    detail: str = ""
    timestamp: float = 0.0

    @property
    def notional_exposure(self) -> float:
        """Approximate unreconciled notional exposure."""
        price = max(self.internal_avg_cost, self.broker_avg_cost, 1.0)
        return abs(self.qty_diff) * price


@dataclass
class ReconciliationReport:
    """Result of a single reconciliation cycle."""
    report_id: str
    timestamp: float
    duration_ms: float

    brokers_checked: List[str]
    symbols_checked: int = 0
    mismatches: List[ReconciliationMismatch] = field(default_factory=list)

    # Summary
    n_info: int = 0
    n_warning: int = 0
    n_critical: int = 0
    n_emergency: int = 0

    @property
    def total_mismatches(self) -> int:
        return len(self.mismatches)

    @property
    def is_clean(self) -> bool:
        return self.n_critical == 0 and self.n_emergency == 0

    @property
    def worst_severity(self) -> MismatchSeverity:
        if self.n_emergency > 0:
            return MismatchSeverity.EMERGENCY
        if self.n_critical > 0:
            return MismatchSeverity.CRITICAL
        if self.n_warning > 0:
            return MismatchSeverity.WARNING
        return MismatchSeverity.INFO

    def summary(self) -> Dict[str, Any]:
        return {
            "report_id": self.report_id,
            "timestamp": self.timestamp,
            "duration_ms": round(self.duration_ms, 1),
            "brokers_checked": self.brokers_checked,
            "symbols_checked": self.symbols_checked,
            "total_mismatches": self.total_mismatches,
            "info": self.n_info,
            "warnings": self.n_warning,
            "critical": self.n_critical,
            "emergency": self.n_emergency,
            "is_clean": self.is_clean,
            "worst_severity": self.worst_severity.value,
        }


@dataclass(frozen=True)
class ReconciliationConfig:
    """Configurable thresholds for mismatch classification."""
    # Quantity tolerance (shares) — differences below this are INFO
    qty_tolerance: float = 0.01

    # Qty difference that becomes CRITICAL (shares or % of position)
    critical_qty_threshold: float = 10.0
    critical_qty_pct: float = 5.0      # 5% of position

    # Price difference that becomes WARNING (bps)
    price_warning_bps: float = 50.0    # 0.5%
    price_critical_bps: float = 200.0  # 2%

    # Phantom position thresholds
    phantom_emergency_notional: float = 100_000.0  # ₹1L or $100K

    # Interval
    interval_seconds: float = 30.0


class ReconciliationEngine:
    """Cross-broker position reconciliation.

    Usage:
        engine = ReconciliationEngine()

        # Register position sources
        engine.set_internal_positions([
            PositionView("RELIANCE", 100, 2500.0, source="internal"),
        ])

        engine.set_broker_positions("dhan", [
            PositionView("RELIANCE", 100, 2500.0, source="dhan"),
        ])

        # Run reconciliation
        report = engine.reconcile()
        if not report.is_clean:
            handle_mismatches(report)
    """

    def __init__(
        self,
        config: Optional[ReconciliationConfig] = None,
        on_mismatch: Optional[Callable[[ReconciliationMismatch], None]] = None,
        on_emergency: Optional[Callable[[ReconciliationReport], None]] = None,
    ):
        self._config = config or ReconciliationConfig()
        self._on_mismatch = on_mismatch
        self._on_emergency = on_emergency

        # Position views
        self._internal: Dict[str, PositionView] = {}
        self._broker_views: Dict[str, Dict[str, PositionView]] = {}  # broker → {symbol → pos}

        # History
        self._reports: List[ReconciliationReport] = []
        self._report_counter = 0

    # -- Position updates ---------------------------------------------------

    def set_internal_positions(self, positions: List[PositionView]) -> None:
        """Update the internal (OMS) position view."""
        self._internal = {p.symbol: p for p in positions}

    def set_broker_positions(
        self, broker_slug: str, positions: List[PositionView]
    ) -> None:
        """Update a specific broker's position view."""
        self._broker_views[broker_slug] = {p.symbol: p for p in positions}

    def clear_broker(self, broker_slug: str) -> None:
        """Remove a broker's position view (e.g., when disconnected)."""
        self._broker_views.pop(broker_slug, None)

    # -- Reconciliation -----------------------------------------------------

    def reconcile(self) -> ReconciliationReport:
        """Run a full reconciliation cycle across all registered brokers.

        Returns a ReconciliationReport with all mismatches found.
        """
        start = time.time()
        self._report_counter += 1
        report_id = f"recon-{self._report_counter:06d}"

        mismatches: List[ReconciliationMismatch] = []
        brokers_checked = list(self._broker_views.keys())
        all_symbols: set[str] = set(self._internal.keys())

        for broker_slug, broker_positions in self._broker_views.items():
            all_symbols.update(broker_positions.keys())
            broker_mismatches = self._reconcile_broker(
                broker_slug, broker_positions
            )
            mismatches.extend(broker_mismatches)

        # Count severities
        n_info = sum(1 for m in mismatches if m.severity == MismatchSeverity.INFO)
        n_warning = sum(1 for m in mismatches if m.severity == MismatchSeverity.WARNING)
        n_critical = sum(1 for m in mismatches if m.severity == MismatchSeverity.CRITICAL)
        n_emergency = sum(1 for m in mismatches if m.severity == MismatchSeverity.EMERGENCY)

        duration_ms = (time.time() - start) * 1000

        report = ReconciliationReport(
            report_id=report_id,
            timestamp=time.time(),
            duration_ms=duration_ms,
            brokers_checked=brokers_checked,
            symbols_checked=len(all_symbols),
            mismatches=mismatches,
            n_info=n_info,
            n_warning=n_warning,
            n_critical=n_critical,
            n_emergency=n_emergency,
        )

        self._reports.append(report)

        # Callbacks
        for m in mismatches:
            if self._on_mismatch:
                self._on_mismatch(m)

        if n_emergency > 0 and self._on_emergency:
            self._on_emergency(report)

        if not report.is_clean:
            log.warning(
                "Reconciliation %s: %d mismatches (%d critical, %d emergency) "
                "across %d symbols, %d brokers",
                report_id, len(mismatches), n_critical, n_emergency,
                len(all_symbols), len(brokers_checked),
            )
        else:
            log.info(
                "Reconciliation %s: clean — %d symbols, %d brokers",
                report_id, len(all_symbols), len(brokers_checked),
            )

        return report

    def _reconcile_broker(
        self, broker_slug: str, broker_positions: Dict[str, PositionView]
    ) -> List[ReconciliationMismatch]:
        """Compare internal positions against one broker."""
        mismatches: List[ReconciliationMismatch] = []
        now = time.time()
        checked: set[str] = set()

        # Check all internal positions against this broker
        for symbol, internal in self._internal.items():
            checked.add(symbol)
            broker = broker_positions.get(symbol)

            if broker is None:
                # We have a position the broker doesn't
                if abs(internal.qty) > self._config.qty_tolerance:
                    severity = self._classify_phantom(
                        internal.qty, internal.avg_cost, is_internal=True
                    )
                    mismatches.append(ReconciliationMismatch(
                        symbol=symbol,
                        mismatch_type=MismatchType.PHANTOM_INTERNAL,
                        severity=severity,
                        internal_qty=internal.qty,
                        broker_qty=0.0,
                        qty_diff=internal.qty,
                        internal_avg_cost=internal.avg_cost,
                        broker_slug=broker_slug,
                        detail=f"Internal shows {internal.qty} shares, broker {broker_slug} shows none",
                        timestamp=now,
                    ))
                continue

            # Both have the position — compare quantities
            qty_diff = internal.qty - broker.qty
            if abs(qty_diff) > self._config.qty_tolerance:
                # Check if signs differ (worst case)
                if (internal.qty > 0 and broker.qty < 0) or (internal.qty < 0 and broker.qty > 0):
                    mismatches.append(ReconciliationMismatch(
                        symbol=symbol,
                        mismatch_type=MismatchType.SIDE_MISMATCH,
                        severity=MismatchSeverity.EMERGENCY,
                        internal_qty=internal.qty,
                        broker_qty=broker.qty,
                        qty_diff=qty_diff,
                        internal_avg_cost=internal.avg_cost,
                        broker_avg_cost=broker.avg_cost,
                        broker_slug=broker_slug,
                        detail=f"SIDE MISMATCH: internal={internal.qty}, broker={broker.qty}",
                        timestamp=now,
                    ))
                else:
                    severity = self._classify_qty_mismatch(
                        qty_diff, internal.qty, internal.avg_cost
                    )
                    mismatches.append(ReconciliationMismatch(
                        symbol=symbol,
                        mismatch_type=MismatchType.QTY_MISMATCH,
                        severity=severity,
                        internal_qty=internal.qty,
                        broker_qty=broker.qty,
                        qty_diff=qty_diff,
                        internal_avg_cost=internal.avg_cost,
                        broker_avg_cost=broker.avg_cost,
                        broker_slug=broker_slug,
                        detail=f"Qty mismatch: internal={internal.qty}, broker={broker.qty}, diff={qty_diff}",
                        timestamp=now,
                    ))

            # Compare avg cost (if quantities match)
            elif internal.avg_cost > 0 and broker.avg_cost > 0:
                price_diff_bps = abs(
                    internal.avg_cost - broker.avg_cost
                ) / internal.avg_cost * 10_000
                if price_diff_bps > self._config.price_warning_bps:
                    severity = (
                        MismatchSeverity.CRITICAL
                        if price_diff_bps > self._config.price_critical_bps
                        else MismatchSeverity.WARNING
                    )
                    mismatches.append(ReconciliationMismatch(
                        symbol=symbol,
                        mismatch_type=MismatchType.PRICE_MISMATCH,
                        severity=severity,
                        internal_qty=internal.qty,
                        broker_qty=broker.qty,
                        qty_diff=0.0,
                        internal_avg_cost=internal.avg_cost,
                        broker_avg_cost=broker.avg_cost,
                        broker_slug=broker_slug,
                        detail=f"Avg cost mismatch: internal={internal.avg_cost:.2f}, broker={broker.avg_cost:.2f} ({price_diff_bps:.0f} bps)",
                        timestamp=now,
                    ))

        # Check for broker positions we don't have internally
        for symbol, broker in broker_positions.items():
            if symbol in checked:
                continue
            if abs(broker.qty) > self._config.qty_tolerance:
                severity = self._classify_phantom(
                    broker.qty, broker.avg_cost, is_internal=False
                )
                mismatches.append(ReconciliationMismatch(
                    symbol=symbol,
                    mismatch_type=MismatchType.PHANTOM_BROKER,
                    severity=severity,
                    internal_qty=0.0,
                    broker_qty=broker.qty,
                    qty_diff=-broker.qty,
                    broker_avg_cost=broker.avg_cost,
                    broker_slug=broker_slug,
                    detail=f"Broker {broker_slug} shows {broker.qty} shares, internal shows none",
                    timestamp=now,
                ))

        return mismatches

    def _classify_phantom(
        self, qty: float, avg_cost: float, is_internal: bool
    ) -> MismatchSeverity:
        """Classify severity of a phantom position."""
        notional = abs(qty * avg_cost)
        if notional >= self._config.phantom_emergency_notional:
            return MismatchSeverity.EMERGENCY
        if notional > 10_000:  # > ₹10K or $10K
            return MismatchSeverity.CRITICAL
        return MismatchSeverity.WARNING

    def _classify_qty_mismatch(
        self, qty_diff: float, position_qty: float, avg_cost: float
    ) -> MismatchSeverity:
        """Classify severity of a quantity mismatch."""
        abs_diff = abs(qty_diff)
        abs_pos = abs(position_qty) if position_qty != 0 else 1.0
        pct_diff = abs_diff / abs_pos * 100

        if abs_diff >= self._config.critical_qty_threshold:
            return MismatchSeverity.CRITICAL
        if pct_diff >= self._config.critical_qty_pct:
            return MismatchSeverity.CRITICAL
        if abs_diff > 1:
            return MismatchSeverity.WARNING
        return MismatchSeverity.INFO

    # -- History & reporting ------------------------------------------------

    def latest_report(self) -> Optional[ReconciliationReport]:
        return self._reports[-1] if self._reports else None

    def report_history(self, n: int = 10) -> List[Dict[str, Any]]:
        """Return the last N reconciliation report summaries."""
        return [r.summary() for r in self._reports[-n:]]

    def mismatch_history(
        self, min_severity: MismatchSeverity = MismatchSeverity.WARNING
    ) -> List[ReconciliationMismatch]:
        """Return all mismatches at or above the given severity."""
        severity_order = {
            MismatchSeverity.INFO: 0,
            MismatchSeverity.WARNING: 1,
            MismatchSeverity.CRITICAL: 2,
            MismatchSeverity.EMERGENCY: 3,
        }
        min_ord = severity_order[min_severity]
        result = []
        for report in self._reports:
            for m in report.mismatches:
                if severity_order.get(m.severity, 0) >= min_ord:
                    result.append(m)
        return result
