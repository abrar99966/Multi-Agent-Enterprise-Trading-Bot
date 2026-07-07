"""Autonomy-tier policy (docs/TARGET_ARCHITECTURE.md section 8.4).

Classifies a risk-APPROVED intent into an autonomy tier:

  Tier 1  fully autonomous   -- release immediately
  Tier 2  conditional        -- notify; release on approval (or timeout)
  Tier 3  human required      -- hold until explicit approval

The gateway computes the tier for every approved intent and releases the
order only if ``tier <= auto_release_max_tier``; otherwise it emits an
ApprovalRequest and waits for an ApprovalDecision (risk/gateway.py).

Phase 2 stance -- "start everything Tier 2/3, earn Tier 1": a strategy reaches
Tier 1 only once it is in ``trusted`` AND the order is small (vs NAV) AND every
limit has comfortable headroom. New/untrusted strategies, large orders, or
tight headroom escalate. Regime is not yet wired (Phase 3); when absent it is
treated as normal. The policy is a pure function -- deterministic, no clock,
no I/O -- so tiers replay identically.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple

from app.core.events import OrderIntent
from app.risk.limits import RiskLimits


@dataclass(frozen=True)
class TierPolicy:
    #: strategy_ids cleared for autonomous (Tier 1) trading. Empty by default:
    #: nothing is autonomous until explicitly earned.
    trusted: frozenset = field(default_factory=frozenset)
    tier1_notional_frac: float = 0.0025  # <= 0.25% NAV for Tier 1
    tier3_notional_frac: float = 0.01    # > 1% NAV forces Tier 3
    tier1_min_headroom: float = 0.30     # >= 30% headroom on every limit
    tier2_min_headroom: float = 0.10     # < 10% headroom forces Tier 3

    def classify(
        self,
        intent: OrderIntent,
        ref_price: float,
        limits: RiskLimits,
        projected_position: float,
        projected_gross: float,
    ) -> Tuple[int, List[str]]:
        """Return (tier, reasons). ``projected_*`` already include this order
        and the working reservation, as the gateway computes them."""
        reasons: List[str] = []
        notional = intent.qty * ref_price
        notional_frac = notional / limits.nav if limits.nav > 0 else 1.0
        headroom = min(
            _headroom(abs(projected_position), limits.max_position_qty),
            _headroom(abs(projected_gross), limits.max_gross_exposure),
            _headroom(notional, limits.max_order_notional),
        )

        tier = 1
        if intent.strategy_id not in self.trusted:
            tier = max(tier, 3)
            reasons.append("strategy_not_trusted")
        if notional_frac > self.tier3_notional_frac:
            tier = max(tier, 3)
            reasons.append(f"order_notional_frac={notional_frac:.4f}>tier3")
        elif notional_frac > self.tier1_notional_frac:
            tier = max(tier, 2)
            reasons.append(f"order_notional_frac={notional_frac:.4f}>tier1")
        if headroom < self.tier2_min_headroom:
            tier = max(tier, 3)
            reasons.append(f"limit_headroom={headroom:.2f}<tier2")
        elif headroom < self.tier1_min_headroom:
            tier = max(tier, 2)
            reasons.append(f"limit_headroom={headroom:.2f}<tier1")
        if tier == 1:
            reasons.append("autonomous")
        return tier, reasons


def _headroom(used: float, limit: float) -> float:
    """Fraction of the limit still free, in [0, 1]. 0 when at/over the limit."""
    if limit <= 0:
        return 0.0
    return max(0.0, 1.0 - used / limit)
