"""Signed average-cost position book fed by exec.fills.

One book per symbol. BUY fills increase the signed quantity, SELL fills
decrease it; short positions (qty < 0) are first-class. Realized PnL is
recognized whenever a fill reduces |position| toward zero, fees always
reduce realized PnL, and crossing through zero closes the old side
fully and opens the remainder at the fill price.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict

from app.bus.base import EventBus
from app.core.events import Event, Fill, PositionSnapshot, Side, Streams

_FLAT_EPS = 1e-9


class MarkToMarket(TypedDict):
    unrealized: dict[str, float]
    unrealized_pnl: float
    realized_pnl: float
    total_pnl: float


@dataclass
class _Book:
    qty: float = 0.0
    avg_price: float = 0.0
    realized_pnl: float = 0.0


class PositionTracker:
    """Maintains per-symbol signed positions and publishes a
    PositionSnapshot on oms.positions after every fill."""

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._books: dict[str, _Book] = {}
        bus.subscribe(Streams.EXEC_FILLS, self._on_fill)

    def _on_fill(self, event: Event) -> None:
        payload = event.decode()
        if not isinstance(payload, Fill):
            return
        book = self._books.setdefault(payload.symbol, _Book())
        signed = payload.qty if payload.side is Side.BUY else -payload.qty
        old_qty = book.qty
        if old_qty == 0.0 or (old_qty > 0) == (signed > 0):
            # Opening, or adding to the same side: re-average the cost.
            new_qty = old_qty + signed
            book.avg_price = (
                old_qty * book.avg_price + signed * payload.price
            ) / new_qty
            book.qty = new_qty
        else:
            # Reducing toward zero (and possibly crossing through it).
            closed_qty = min(abs(signed), abs(old_qty))
            direction = 1.0 if old_qty > 0 else -1.0
            book.realized_pnl += (
                (payload.price - book.avg_price) * closed_qty * direction
            )
            new_qty = old_qty + signed
            if abs(new_qty) < _FLAT_EPS:
                book.qty = 0.0
                book.avg_price = 0.0
            elif (new_qty > 0) == (old_qty > 0):
                book.qty = new_qty  # partial close: avg cost unchanged
            else:
                book.qty = new_qty  # crossed zero: remainder opens here
                book.avg_price = payload.price
        book.realized_pnl -= payload.fees
        self._bus.publish(
            Streams.OMS_POSITIONS,
            PositionSnapshot(
                symbol=payload.symbol,
                qty=book.qty,
                avg_price=book.avg_price,
                realized_pnl=book.realized_pnl,
                ts=payload.ts_fill,
            ),
            ts_event=payload.ts_fill,
        )

    def position(self, symbol: str) -> tuple[float, float]:
        """(signed qty, avg price); (0.0, 0.0) for untracked symbols."""
        book = self._books.get(symbol)
        if book is None:
            return (0.0, 0.0)
        return (book.qty, book.avg_price)

    def realized_pnl(self, symbol: str) -> float:
        book = self._books.get(symbol)
        return 0.0 if book is None else book.realized_pnl

    def total_realized_pnl(self) -> float:
        return sum((b.realized_pnl for b in self._books.values()), 0.0)

    def positions(self) -> dict[str, dict]:
        """Return all positions as {symbol: {qty, avg_price, realized_pnl}}.

        Used by the cross-broker reconciliation engine (Phase 4).
        """
        return {
            sym: {
                "qty": book.qty,
                "avg_price": book.avg_price,
                "realized_pnl": book.realized_pnl,
            }
            for sym, book in self._books.items()
            if abs(book.qty) > _FLAT_EPS
        }

    def mark_to_market(self, prices: dict[str, float]) -> MarkToMarket:
        """Unrealized PnL per open symbol plus total equity PnL
        (realized + unrealized). Raises KeyError if `prices` is missing
        a symbol with an open position -- silent omission would
        misstate equity."""
        unrealized: dict[str, float] = {}
        for symbol, book in self._books.items():
            if book.qty == 0.0:
                continue
            unrealized[symbol] = (prices[symbol] - book.avg_price) * book.qty
        unrealized_total = sum(unrealized.values(), 0.0)
        realized_total = self.total_realized_pnl()
        return MarkToMarket(
            unrealized=unrealized,
            unrealized_pnl=unrealized_total,
            realized_pnl=realized_total,
            total_pnl=realized_total + unrealized_total,
        )


# -- Module-level accessor for cross-component use ----------------------

_tracker_instance: PositionTracker | None = None


def set_position_tracker(tracker: PositionTracker) -> None:
    """Register the active position tracker (called by the engine runner)."""
    global _tracker_instance
    _tracker_instance = tracker


def get_position_tracker() -> PositionTracker | None:
    """Return the active position tracker, if any."""
    return _tracker_instance
