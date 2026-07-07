"""Streaming Transaction Cost Analysis.

Consumes the event stream (intents, fills, bars) and produces one TcaResult
per fill: the Implementation Shortfall decomposition plus markouts at several
horizons. TCA is derived analytics -- recomputable from the journal -- so it
lives in its own store (tca/store.py), not on the event log.

Price proxies in the Phase 0/1 paper world (OHLC bars, no book):
- decision price p_d = the signal bar's close (the bar's close that produced
  the intent; captured as last_close when the intent is seen),
- arrival price p_a = the open of the fill bar (fill.ts_fill == bar.ts_open),
- fill price p_f  = the actual fill price (open +/- slippage),
- markout mid    = the close of the bar at t_fill + h*interval.

Deterministic: pure functions of the event stream, no clock or randomness, so
running TCA over a journal replay yields identical results.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from app.bus.base import EventBus
from app.core.events import Bar, Event, Fill, OrderIntent, Side, Streams
from app.tca.shortfall import ShortfallBreakdown, implementation_shortfall, markout_bps

DEFAULT_MARKOUT_HORIZONS = (1, 5, 30)  # in bars


@dataclass
class TcaResult:
    fill_id: str
    order_id: str
    intent_id: str
    strategy_id: str
    symbol: str
    side: Side
    qty: float
    ts_decision: int
    ts_fill: int
    breakdown: ShortfallBreakdown
    markouts_bps: Dict[int, float] = field(default_factory=dict)  # horizon -> bps


@dataclass
class _PendingMarkout:
    fill_id: str
    symbol: str
    side: Side
    fill_price: float
    horizon: int
    target_ts: int


class TcaEngine:
    def __init__(
        self,
        bus: EventBus,
        markout_horizons: tuple[int, ...] = DEFAULT_MARKOUT_HORIZONS,
    ) -> None:
        self._horizons = tuple(markout_horizons)
        self._last_close: Dict[str, float] = {}
        self._fill_bar: Dict[str, Bar] = {}  # latest bar per symbol
        self._decision_price: Dict[str, float] = {}  # intent_id -> decision mid
        self._results: List[TcaResult] = []
        self._by_fill: Dict[str, TcaResult] = {}
        self._pending: List[_PendingMarkout] = []
        bus.subscribe(Streams.MD_BARS, self._on_bar)
        bus.subscribe(Streams.SIGNAL_INTENTS, self._on_intent)
        bus.subscribe(Streams.EXEC_FILLS, self._on_fill)

    # -- inputs --------------------------------------------------------

    def _on_intent(self, event: Event) -> None:
        intent = OrderIntent.model_validate(event.payload)
        # The signal bar's close is the decision mid (intent follows its bar in
        # the dispatch cascade, so last_close is that bar's close).
        price = self._last_close.get(intent.symbol)
        if price is not None:
            self._decision_price[intent.intent_id] = price

    def _on_bar(self, event: Event) -> None:
        bar = Bar.model_validate(event.payload)
        self._last_close[bar.symbol] = bar.close
        self._fill_bar[bar.symbol] = bar
        self._resolve_markouts(bar)

    def _on_fill(self, event: Event) -> None:
        fill = Fill.model_validate(event.payload)
        decision = self._decision_price.get(fill.intent_id)
        bar = self._fill_bar.get(fill.symbol)
        # Arrival mid = open of the fill bar (ts_fill == its ts_open); fall back
        # to the fill price if the bar is somehow unavailable.
        if bar is not None and bar.ts_open == fill.ts_fill:
            arrival = bar.open
        else:
            arrival = fill.price
        if decision is None:
            decision = arrival  # external/unseen intent: no delay component
        breakdown = implementation_shortfall(
            side=fill.side,
            qty=fill.qty,
            decision_price=decision,
            arrival_price=arrival,
            fill_price=fill.price,
            fees=fill.fees,
        )
        result = TcaResult(
            fill_id=fill.fill_id,
            order_id=fill.order_id,
            intent_id=fill.intent_id,
            strategy_id=fill.strategy_id,
            symbol=fill.symbol,
            side=fill.side,
            qty=fill.qty,
            ts_decision=event.ts_event,  # best-effort; ts of fill event
            ts_fill=fill.ts_fill,
            breakdown=breakdown,
        )
        self._results.append(result)
        self._by_fill[fill.fill_id] = result
        interval_ns = (bar.interval_s * 1_000_000_000) if bar is not None else 0
        if interval_ns:
            for h in self._horizons:
                self._pending.append(
                    _PendingMarkout(
                        fill_id=fill.fill_id,
                        symbol=fill.symbol,
                        side=fill.side,
                        fill_price=fill.price,
                        horizon=h,
                        target_ts=fill.ts_fill + h * interval_ns,
                    )
                )

    def _resolve_markouts(self, bar: Bar) -> None:
        if not self._pending:
            return
        still: List[_PendingMarkout] = []
        for pm in self._pending:
            if pm.symbol == bar.symbol and bar.ts_open >= pm.target_ts:
                mo = markout_bps(pm.side, pm.fill_price, bar.close)
                self._by_fill[pm.fill_id].markouts_bps[pm.horizon] = mo
            else:
                still.append(pm)
        self._pending = still

    # -- outputs -------------------------------------------------------

    def results(self) -> List[TcaResult]:
        """Per-fill TCA in fill order. Markouts whose horizon bar never
        arrived (end of session) are simply absent from a result's map."""
        return list(self._results)

    def summary(self) -> Dict[str, float]:
        """Aggregate cost (bps), notional-weighted, plus mean markouts."""
        if not self._results:
            return {"n_fills": 0}
        tot_notional = sum(r.breakdown.notional for r in self._results) or 1.0

        def wavg(attr: str) -> float:
            return sum(
                getattr(r.breakdown, attr) * r.breakdown.notional for r in self._results
            ) / tot_notional

        out: Dict[str, float] = {
            "n_fills": float(len(self._results)),
            "delay_bps": wavg("delay_bps"),
            "execution_bps": wavg("execution_bps"),
            "fees_bps": wavg("fees_bps"),
            "total_is_bps": wavg("total_is_bps"),
            "total_is_cost": sum(r.breakdown.total_is for r in self._results),
        }
        for h in self._horizons:
            vals = [r.markouts_bps[h] for r in self._results if h in r.markouts_bps]
            if vals:
                out[f"markout_{h}_bps"] = sum(vals) / len(vals)
                out[f"markout_{h}_n"] = float(len(vals))
        return out
