"""Execution algorithms â€” IS, VWAP, POV, Adaptive.

Phase 4 of the Institutional Target-State Architecture.

These algorithms break a parent order into child slices and manage their
execution over time. Each algo subscribes to the event bus for market data
and publishes child orders back through the risk gateway path.

All algos operate on event time (not wall clock) for replay determinism.

Algo definitions:
  IS (Implementation Shortfall): front-loaded, minimizes deviation from
    decision price. Urgency parameter trades off impact vs timing risk.
  VWAP: follows an intraday volume profile, targeting volume-weighted
    average price over the execution horizon.
  POV (Percent of Volume): participates at a fixed % of observed volume,
    automatically pacing with the market.
  ADAPTIVE: starts with IS schedule but adjusts urgency dynamically
    based on realized vs expected shortfall.
"""
from __future__ import annotations

import logging
import math
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

from app.bus.base import EventBus
from app.core.events import (
    NS_PER_SEC,
    Bar,
    Event,
    Fill,
    OrderIntent,
    OrderType,
    Side,
    Streams,
    Tick,
)
from app.execution.impact_model import ImpactEstimate, ImpactModel

log = logging.getLogger(__name__)


class AlgoType(str, Enum):
    IS = "IS"
    VWAP = "VWAP"
    POV = "POV"
    ADAPTIVE = "ADAPTIVE"


class AlgoStatus(str, Enum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


@dataclass
class AlgoSlice:
    """A single child order slice within an algo execution."""
    slice_id: str
    target_qty: float
    filled_qty: float = 0.0
    intent_id: Optional[str] = None
    status: str = "PENDING"        # PENDING â†’ SENT â†’ FILLED â†’ CANCELLED
    target_time_ns: int = 0        # Event time when this slice should fire
    actual_fill_price: float = 0.0
    sent_at_ns: int = 0


@dataclass
class AlgoOrder:
    """A parent algo order that manages child slices."""
    algo_id: str
    algo_type: AlgoType
    parent_intent_id: str
    strategy_id: str
    symbol: str
    side: Side
    total_qty: float
    reference_price: float         # Decision price (for IS calculation)

    # Configuration
    urgency: float = 0.5           # 0=passive, 1=aggressive
    duration_ns: int = 30 * 60 * NS_PER_SEC  # Default: 30 minutes
    max_pov_pct: float = 5.0      # Max participation rate for POV
    limit_price: Optional[float] = None

    # State
    status: AlgoStatus = AlgoStatus.PENDING
    filled_qty: float = 0.0
    avg_fill_price: float = 0.0
    total_fees: float = 0.0
    slices: List[AlgoSlice] = field(default_factory=list)
    start_time_ns: int = 0
    end_time_ns: int = 0

    # Tracking
    impact_estimate: Optional[ImpactEstimate] = None
    realized_shortfall_bps: float = 0.0
    n_child_orders: int = 0

    @property
    def remaining_qty(self) -> float:
        return max(0.0, self.total_qty - self.filled_qty)

    @property
    def fill_pct(self) -> float:
        return (self.filled_qty / self.total_qty * 100) if self.total_qty else 0.0

    @property
    def is_complete(self) -> bool:
        return self.filled_qty >= self.total_qty - 1e-9

    def realized_is_bps(self) -> float:
        """Compute realized implementation shortfall in bps."""
        if self.filled_qty <= 0 or self.reference_price <= 0:
            return 0.0
        if self.side == Side.BUY:
            shortfall = (self.avg_fill_price - self.reference_price) / self.reference_price
        else:
            shortfall = (self.reference_price - self.avg_fill_price) / self.reference_price
        return shortfall * 10_000


class ExecutionAlgoEngine:
    """Manages execution algorithms â€” creates, schedules, and monitors algo orders.

    Subscribes to market data and fills to drive algo state machines.
    Publishes child order intents through the standard signal.intents stream
    (so they pass through the risk gateway like any other order).
    """

    def __init__(
        self,
        bus: EventBus,
        impact_model: Optional[ImpactModel] = None,
        n_slices: int = 10,
    ) -> None:
        self._bus = bus
        self._impact = impact_model or ImpactModel()
        self._n_slices = n_slices

        # Active algo orders: algo_id â†’ AlgoOrder
        self._algos: Dict[str, AlgoOrder] = {}

        # Map child intent_id â†’ algo_id for fill attribution
        self._intent_to_algo: Dict[str, str] = {}

        # Latest market data
        self._last_price: Dict[str, float] = {}
        self._last_volume: Dict[str, float] = {}  # Cumulative volume today
        self._volume_at_start: Dict[str, float] = {}  # Volume when algo started

        # Subscribe to feeds
        bus.subscribe(Streams.MD_BARS, self._on_bar)
        bus.subscribe(Streams.MD_TICKS, self._on_tick)
        bus.subscribe(Streams.EXEC_FILLS, self._on_fill)

    # -- Public API ---------------------------------------------------------

    def submit(
        self,
        algo_type: AlgoType,
        parent_intent_id: str,
        strategy_id: str,
        symbol: str,
        side: Side,
        qty: float,
        reference_price: float,
        urgency: float = 0.5,
        duration_min: int = 30,
        max_pov_pct: float = 5.0,
        limit_price: Optional[float] = None,
        start_time_ns: int = 0,
    ) -> AlgoOrder:
        """Submit a new algo order for execution.

        Returns the AlgoOrder (PENDING state). It will begin executing
        on the next market data event after start_time_ns.
        """
        algo_id = f"algo-{uuid.uuid4().hex[:12]}"
        duration_ns = duration_min * 60 * NS_PER_SEC

        # Pre-trade impact estimate
        estimate = self._impact.estimate(
            symbol=symbol,
            side=side.value,
            qty=qty,
            reference_price=reference_price,
        )

        order = AlgoOrder(
            algo_id=algo_id,
            algo_type=algo_type,
            parent_intent_id=parent_intent_id,
            strategy_id=strategy_id,
            symbol=symbol,
            side=side,
            total_qty=qty,
            reference_price=reference_price,
            urgency=urgency,
            duration_ns=duration_ns,
            max_pov_pct=max_pov_pct,
            limit_price=limit_price,
            start_time_ns=start_time_ns,
            impact_estimate=estimate,
        )

        # Generate initial slice schedule
        self._schedule_slices(order)

        self._algos[algo_id] = order
        log.info(
            "Algo %s submitted: %s %s %.0f %s @ ref=%.2f urgency=%.2f duration=%dm",
            algo_id, algo_type.value, side.value, qty, symbol,
            reference_price, urgency, duration_min,
        )

        return order

    def cancel(self, algo_id: str) -> bool:
        """Cancel an active algo order. Remaining slices are abandoned."""
        algo = self._algos.get(algo_id)
        if algo is None:
            return False
        if algo.status in (AlgoStatus.COMPLETED, AlgoStatus.CANCELLED):
            return False

        algo.status = AlgoStatus.CANCELLED
        for s in algo.slices:
            if s.status == "PENDING":
                s.status = "CANCELLED"

        log.info(
            "Algo %s cancelled: filled %.0f/%.0f",
            algo_id, algo.filled_qty, algo.total_qty,
        )
        return True

    def get_algo(self, algo_id: str) -> Optional[AlgoOrder]:
        return self._algos.get(algo_id)

    def active_algos(self) -> List[AlgoOrder]:
        return [
            a for a in self._algos.values()
            if a.status in (AlgoStatus.PENDING, AlgoStatus.ACTIVE)
        ]

    def all_algos(self) -> List[AlgoOrder]:
        return list(self._algos.values())

    # -- Slice scheduling ---------------------------------------------------

    def _schedule_slices(self, algo: AlgoOrder) -> None:
        """Generate the initial slice schedule based on algo type."""
        n = self._n_slices
        total = algo.total_qty
        duration = algo.duration_ns

        if algo.algo_type == AlgoType.IS:
            weights = self._is_weights(n, algo.urgency)
        elif algo.algo_type == AlgoType.VWAP:
            weights = self._vwap_weights(n)
        elif algo.algo_type == AlgoType.POV:
            # POV doesn't pre-schedule â€” it fires reactively on volume
            weights = [1.0 / n] * n
        elif algo.algo_type == AlgoType.ADAPTIVE:
            # Start with IS profile, adjust dynamically
            weights = self._is_weights(n, algo.urgency)
        else:
            weights = [1.0 / n] * n

        for i, w in enumerate(weights):
            slice_qty = max(1, round(total * w))
            target_time = algo.start_time_ns + int(duration * i / n)
            algo.slices.append(AlgoSlice(
                slice_id=f"{algo.algo_id}-s{i:02d}",
                target_qty=slice_qty,
                target_time_ns=target_time,
            ))

    # -- Market data handlers -----------------------------------------------

    def _on_bar(self, event: Event) -> None:
        bar = Bar.model_validate(event.payload)
        self._last_price[bar.symbol] = bar.close
        self._last_volume[bar.symbol] = (
            self._last_volume.get(bar.symbol, 0.0) + bar.volume
        )
        self._check_and_fire(bar.symbol, bar.ts_close)

    def _on_tick(self, event: Event) -> None:
        tick = Tick.model_validate(event.payload)
        self._last_price[tick.symbol] = tick.ltp
        if tick.volume is not None:
            self._last_volume[tick.symbol] = tick.volume
        self._check_and_fire(tick.symbol, event.ts_event)

    def _on_fill(self, event: Event) -> None:
        """Attribute child fills to the parent algo order."""
        fill = Fill.model_validate(event.payload)
        algo_id = self._intent_to_algo.get(fill.intent_id)
        if algo_id is None:
            return  # Not an algo child order

        algo = self._algos.get(algo_id)
        if algo is None:
            return

        # Update algo fill state
        old_notional = algo.avg_fill_price * algo.filled_qty
        algo.filled_qty += fill.qty
        algo.total_fees += fill.fees
        if algo.filled_qty > 0:
            algo.avg_fill_price = (old_notional + fill.price * fill.qty) / algo.filled_qty

        # Update the slice
        for s in algo.slices:
            if s.intent_id == fill.intent_id:
                s.filled_qty += fill.qty
                s.actual_fill_price = fill.price
                if s.filled_qty >= s.target_qty - 1e-9:
                    s.status = "FILLED"
                break

        # Compute realized IS
        algo.realized_shortfall_bps = algo.realized_is_bps()

        # Check completion
        if algo.is_complete:
            algo.status = AlgoStatus.COMPLETED
            algo.end_time_ns = event.ts_event
            log.info(
                "Algo %s COMPLETED: filled %.0f @ avg=%.4f IS=%.1f bps",
                algo.algo_id, algo.filled_qty,
                algo.avg_fill_price, algo.realized_shortfall_bps,
            )

    # -- Firing logic -------------------------------------------------------

    def _check_and_fire(self, symbol: str, now_ns: int) -> None:
        """Check all active algos for this symbol and fire due slices."""
        for algo in list(self._algos.values()):
            if algo.symbol != symbol:
                continue
            if algo.status in (AlgoStatus.COMPLETED, AlgoStatus.CANCELLED, AlgoStatus.FAILED):
                continue

            # Activate on first data after start time
            if algo.status == AlgoStatus.PENDING:
                if algo.start_time_ns == 0 or now_ns >= algo.start_time_ns:
                    algo.status = AlgoStatus.ACTIVE
                    algo.start_time_ns = now_ns
                    self._volume_at_start[algo.algo_id] = self._last_volume.get(symbol, 0.0)
                    # Re-calibrate slice times relative to actual start
                    for i, s in enumerate(algo.slices):
                        s.target_time_ns = now_ns + int(
                            algo.duration_ns * i / len(algo.slices)
                        )

            if algo.status != AlgoStatus.ACTIVE:
                continue

            # POV: fire based on volume
            if algo.algo_type == AlgoType.POV:
                self._fire_pov(algo, now_ns)
            # ADAPTIVE: adjust urgency and fire
            elif algo.algo_type == AlgoType.ADAPTIVE:
                self._adapt_urgency(algo, now_ns)
                self._fire_time_based(algo, now_ns)
            else:
                # IS / VWAP: fire on schedule
                self._fire_time_based(algo, now_ns)

            # Check for timeout
            if now_ns > algo.start_time_ns + algo.duration_ns:
                if algo.remaining_qty > 0:
                    # Force-fire remaining as market order
                    self._fire_remaining(algo, now_ns)
                if algo.is_complete or algo.remaining_qty <= 0:
                    algo.status = AlgoStatus.COMPLETED
                    algo.end_time_ns = now_ns

    def _fire_time_based(self, algo: AlgoOrder, now_ns: int) -> None:
        """Fire slices whose target time has arrived."""
        for s in algo.slices:
            if s.status != "PENDING":
                continue
            if now_ns >= s.target_time_ns:
                self._emit_child_intent(algo, s, now_ns)

    def _fire_pov(self, algo: AlgoOrder, now_ns: int) -> None:
        """POV algo: fire based on observed volume participation.

        Participate at algo.max_pov_pct of observed volume.
        """
        current_vol = self._last_volume.get(algo.symbol, 0.0)
        start_vol = self._volume_at_start.get(algo.algo_id, 0.0)
        traded_vol = max(0.0, current_vol - start_vol)

        # Target: fill this fraction of traded volume
        target_fill = traded_vol * (algo.max_pov_pct / 100.0)
        shortfall = target_fill - algo.filled_qty

        if shortfall <= 0:
            return  # Already on pace

        # Fire the next pending slice
        for s in algo.slices:
            if s.status != "PENDING":
                continue
            # Adjust slice size to cover the shortfall
            s.target_qty = min(max(1, round(shortfall)), round(algo.remaining_qty))
            self._emit_child_intent(algo, s, now_ns)
            break

    def _adapt_urgency(self, algo: AlgoOrder, now_ns: int) -> None:
        """ADAPTIVE algo: adjust urgency based on realized vs expected IS.

        If we're doing better than expected, slow down (reduce urgency).
        If we're doing worse, speed up to capture remaining at better prices.
        """
        if algo.filled_qty <= 0 or algo.impact_estimate is None:
            return

        realized_is = algo.realized_is_bps()
        expected_is = algo.impact_estimate.total_expected_cost_bps

        if expected_is <= 0:
            return

        ratio = realized_is / expected_is
        elapsed_frac = max(0.0, min(1.0,
            (now_ns - algo.start_time_ns) / max(algo.duration_ns, 1)
        ))
        fill_frac = algo.fill_pct / 100.0

        # Adjust urgency
        if ratio > 1.5 and fill_frac < 0.5:
            # Costs are 1.5x expected and we're not half done â†’ slow down
            algo.urgency = max(0.1, algo.urgency * 0.8)
        elif ratio < 0.5 and elapsed_frac > 0.3:
            # Costs are well below expected â†’ can speed up safely
            algo.urgency = min(1.0, algo.urgency * 1.2)
        elif elapsed_frac > 0.8 and fill_frac < 0.6:
            # Running out of time â†’ force urgency up
            algo.urgency = min(1.0, algo.urgency * 1.5)

    def _fire_remaining(self, algo: AlgoOrder, now_ns: int) -> None:
        """Fire any remaining unfilled quantity as a single slice."""
        remaining = algo.remaining_qty
        if remaining <= 0:
            return

        for s in algo.slices:
            if s.status == "PENDING":
                s.target_qty = round(remaining)
                self._emit_child_intent(algo, s, now_ns)
                return

        # All slices used â€” create a final mop-up slice
        mop = AlgoSlice(
            slice_id=f"{algo.algo_id}-mop",
            target_qty=round(remaining),
            target_time_ns=now_ns,
        )
        algo.slices.append(mop)
        self._emit_child_intent(algo, mop, now_ns)

    def _emit_child_intent(self, algo: AlgoOrder, s: AlgoSlice, now_ns: int) -> None:
        """Publish a child OrderIntent for this slice."""
        intent_id = f"child-{s.slice_id}"
        s.intent_id = intent_id
        s.status = "SENT"
        s.sent_at_ns = now_ns
        algo.n_child_orders += 1
        self._intent_to_algo[intent_id] = algo.algo_id

        intent = OrderIntent(
            intent_id=intent_id,
            strategy_id=algo.strategy_id,
            model_id=f"algo-{algo.algo_type.value}",
            symbol=algo.symbol,
            side=algo.side,
            qty=s.target_qty,
            order_type=OrderType.LIMIT if algo.limit_price else OrderType.MARKET,
            limit_price=algo.limit_price,
            ts_signal=now_ns,
            reason=f"algo:{algo.algo_type.value} slice:{s.slice_id}",
        )

        self._bus.publish(Streams.SIGNAL_INTENTS, intent, ts_event=now_ns)
        log.debug(
            "Algo %s fired slice %s: qty=%.0f",
            algo.algo_id, s.slice_id, s.target_qty,
        )

    # -- Weight functions ---------------------------------------------------

    @staticmethod
    def _is_weights(n: int, urgency: float) -> list[float]:
        """Almgren-Chriss optimal execution trajectory (front-loaded)."""
        decay = 1.0 + urgency * 2.0
        raw = [math.exp(-decay * i / n) for i in range(n)]
        total = sum(raw)
        return [w / total for w in raw]

    @staticmethod
    def _vwap_weights(n: int) -> list[float]:
        """U-shaped intraday volume profile (high at open/close)."""
        raw = []
        for i in range(n):
            t = i / max(n - 1, 1)
            w = 1.5 - 2.0 * t * (1 - t) * 4
            w = max(w, 0.3)
            raw.append(w)
        total = sum(raw)
        return [w / total for w in raw]
