"""Implementation Shortfall decomposition + markouts (Perold; design §7.3).

Pure math, no I/O. Cost convention: POSITIVE = worse for us (we paid up on a
buy / sold low on a sell). For side sign s (+1 buy, -1 sell), decision price
p_d (mid at signal time), arrival price p_a (mid when the order reached the
venue), fill price p_f, quantity q, fees f:

    delay_cost     = s * (p_a - p_d) * q     -- latency: signal -> arrival
    execution_cost = s * (p_f - p_a) * q     -- spread + impact at execution
    total_IS       = delay + execution + fees
                   = s * (p_f - p_d) * q + f

Opportunity cost (unfilled quantity) is zero here because the Phase 0/1 paper
broker fills full size; it is kept in the formula for when partial fills land.
Everything is also reported in bps of decision notional (p_d * q).

Markout at horizon h: did the price keep moving our way after the fill?
    markout_bps = s * (mid_{t_fill+h} - p_f) / p_f * 1e4
Positive = favorable (we got a good price; the market moved in our direction).
Computed against mid (bar close), not fills, so bid-ask bounce does not bias it.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.core.events import Side


def side_sign(side: Side) -> int:
    return 1 if side is Side.BUY else -1


@dataclass(frozen=True)
class ShortfallBreakdown:
    side: Side
    qty: float
    decision_price: float
    arrival_price: float
    fill_price: float
    fees: float
    notional: float
    delay_cost: float
    execution_cost: float
    total_is: float
    delay_bps: float
    execution_bps: float
    fees_bps: float
    total_is_bps: float


def implementation_shortfall(
    side: Side,
    qty: float,
    decision_price: float,
    arrival_price: float,
    fill_price: float,
    fees: float = 0.0,
) -> ShortfallBreakdown:
    s = side_sign(side)
    notional = decision_price * qty
    delay = s * (arrival_price - decision_price) * qty
    execution = s * (fill_price - arrival_price) * qty
    total = delay + execution + fees

    def bps(cost: float) -> float:
        return cost / notional * 1e4 if notional else 0.0

    return ShortfallBreakdown(
        side=side,
        qty=qty,
        decision_price=decision_price,
        arrival_price=arrival_price,
        fill_price=fill_price,
        fees=fees,
        notional=notional,
        delay_cost=delay,
        execution_cost=execution,
        total_is=total,
        delay_bps=bps(delay),
        execution_bps=bps(execution),
        fees_bps=bps(fees),
        total_is_bps=bps(total),
    )


def markout_bps(side: Side, fill_price: float, future_mid: float) -> float:
    if fill_price == 0:
        return 0.0
    return side_sign(side) * (future_mid - fill_price) / fill_price * 1e4
