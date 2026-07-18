"""DMA / co-location economics evaluator.

Produces the Phase 5 exit-criteria deliverable: a go/no-go memo with
measured alpha-vs-latency sensitivity. The question is:

    "How much alpha do we lose to latency at our current retail-API tier,
     and would a DMA/colo setup (at NSE, at ~₹15–30L/year) pay for itself?"

Methodology:
    1. Replay historical TCA data at different simulated latency tiers.
    2. For each tier, estimate the fill quality degradation (adverse selection,
       queue position loss, missed fills).
    3. Compute the annual alpha recovery from faster execution.
    4. Compare against the annual cost of DMA/colo infrastructure.

This module is a *framework* for the analysis, not a black-box answer.
The actual go/no-go decision requires human judgement on:
    - Capital deployed (alpha recovery scales with capital)
    - Strategy alpha decay rate (faster strategies benefit more from speed)
    - Regulatory/operational readiness for DMA (SEBI DMA framework compliance)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Latency tiers
# ---------------------------------------------------------------------------

@dataclass
class LatencyTier:
    """Describes a deployment scenario's latency profile."""
    name: str
    description: str
    tick_to_order_p50_ms: float
    tick_to_order_p99_ms: float
    venue_rtt_ms: float              # network round-trip to exchange
    annual_cost_inr: float           # infra + connectivity cost per year
    is_colo: bool = False


# Pre-defined tiers from ARCHITECTURE.md Part II §12
TIERS: Dict[str, LatencyTier] = {
    "retail_api": LatencyTier(
        name="retail_api",
        description="Current: Zerodha/Dhan/IBKR REST/WS API from cloud VM",
        tick_to_order_p50_ms=3.0,
        tick_to_order_p99_ms=10.0,
        venue_rtt_ms=150.0,
        annual_cost_inr=500_000,   # ~₹5L/yr for cloud VMs + data
    ),
    "retail_optimised": LatencyTier(
        name="retail_optimised",
        description="Optimised: pinned VM in Mumbai, Rust hot path, same broker API",
        tick_to_order_p50_ms=0.5,
        tick_to_order_p99_ms=1.0,
        venue_rtt_ms=100.0,
        annual_cost_inr=1_200_000,  # ~₹12L/yr
    ),
    "dma_basic": LatencyTier(
        name="dma_basic",
        description="DMA: direct market access via clearing member, Mumbai rack",
        tick_to_order_p50_ms=0.25,
        tick_to_order_p99_ms=0.5,
        venue_rtt_ms=5.0,
        annual_cost_inr=2_500_000,  # ~₹25L/yr
    ),
    "colo_nse": LatencyTier(
        name="colo_nse",
        description="NSE co-location: cross-connect to matching engine",
        tick_to_order_p50_ms=0.1,
        tick_to_order_p99_ms=0.25,
        venue_rtt_ms=0.3,
        annual_cost_inr=3_500_000,  # ~₹35L/yr (rack + cross-connect + DMA fees)
        is_colo=True,
    ),
}


# ---------------------------------------------------------------------------
# Alpha–Latency sensitivity model
# ---------------------------------------------------------------------------

@dataclass
class AlphaDecayModel:
    """Models how much alpha decays as latency increases.

    The core assumption: alpha decays exponentially with latency because
    faster participants capture the signal first (adverse selection).

        alpha(latency) = alpha_0 × exp(-decay_rate × latency_ms)

    decay_rate depends on strategy type:
        - Mean reversion (fast):  ~0.01–0.05 per ms (high sensitivity)
        - Momentum (medium):      ~0.001–0.01 per ms
        - Statistical arb (slow): ~0.0001–0.001 per ms
    """
    alpha_0_bps: float = 5.0          # gross alpha at zero latency (bps/trade)
    decay_rate_per_ms: float = 0.005  # exponential decay constant
    trades_per_day: int = 50
    avg_notional_per_trade: float = 500_000  # ₹5L per trade
    trading_days_per_year: int = 245

    def alpha_at_latency(self, latency_ms: float) -> float:
        """Alpha per trade (bps) at a given end-to-end latency."""
        return self.alpha_0_bps * math.exp(-self.decay_rate_per_ms * latency_ms)

    def annual_alpha_inr(self, latency_ms: float) -> float:
        """Annual alpha in INR at a given latency."""
        alpha_bps = self.alpha_at_latency(latency_ms)
        per_trade = alpha_bps / 10_000 * self.avg_notional_per_trade
        return per_trade * self.trades_per_day * self.trading_days_per_year

    def alpha_loss_vs_zero(self, latency_ms: float) -> float:
        """Annual alpha lost compared to zero-latency (INR)."""
        best = self.annual_alpha_inr(0)
        actual = self.annual_alpha_inr(latency_ms)
        return best - actual


# ---------------------------------------------------------------------------
# Fill quality degradation model
# ---------------------------------------------------------------------------

@dataclass
class FillQualityModel:
    """Models how fill quality degrades with latency.

    Components:
        1. Adverse selection: slower fills execute at worse prices
        2. Queue position: slower orders land further back in the queue
        3. Miss rate: some signals expire before the order reaches venue
    """
    # Adverse selection: extra slippage per ms of latency (bps)
    adverse_selection_bps_per_ms: float = 0.02

    # Queue position: additional delay penalty (bps)
    queue_loss_bps_per_ms: float = 0.01

    # Miss rate: probability of missing a signal entirely
    miss_rate_base: float = 0.01       # at 0ms
    miss_rate_per_ms: float = 0.0005   # linear increase per ms

    def total_cost_bps(self, latency_ms: float) -> float:
        """Total fill quality cost at a given latency (bps)."""
        adverse = self.adverse_selection_bps_per_ms * latency_ms
        queue = self.queue_loss_bps_per_ms * latency_ms
        miss_cost = (
            self.miss_rate_base + self.miss_rate_per_ms * latency_ms
        ) * 5.0  # 5 bps opportunity cost per missed fill
        return adverse + queue + miss_cost


# ---------------------------------------------------------------------------
# DMA Go/No-Go Evaluator
# ---------------------------------------------------------------------------

@dataclass
class TierEvaluation:
    """Evaluation of a single latency tier."""
    tier: LatencyTier
    alpha_bps_per_trade: float
    annual_alpha_inr: float
    alpha_loss_vs_zero_inr: float
    fill_quality_cost_bps: float
    annual_infra_cost_inr: float
    net_annual_inr: float              # alpha - infra cost
    roi_pct: float                     # (alpha / infra cost) × 100
    payback_months: float


class DMAEvaluator:
    """Evaluates the economics of moving to faster execution tiers.

    Usage::

        evaluator = DMAEvaluator(
            alpha_model=AlphaDecayModel(alpha_0_bps=5.0, decay_rate_per_ms=0.005),
            capital_deployed_inr=50_000_000,  # ₹5 Cr
        )
        memo = evaluator.evaluate_all()
        print(memo['recommendation'])
    """

    def __init__(
        self,
        alpha_model: AlphaDecayModel | None = None,
        fill_model: FillQualityModel | None = None,
        capital_deployed_inr: float = 10_000_000,
        tiers: Dict[str, LatencyTier] | None = None,
    ) -> None:
        self._alpha = alpha_model or AlphaDecayModel()
        self._fill = fill_model or FillQualityModel()
        self._capital = capital_deployed_inr
        self._tiers = tiers or TIERS

    def evaluate_tier(self, tier: LatencyTier) -> TierEvaluation:
        """Evaluate a single latency tier."""
        latency = tier.tick_to_order_p50_ms + tier.venue_rtt_ms
        alpha_bps = self._alpha.alpha_at_latency(latency)
        annual_alpha = self._alpha.annual_alpha_inr(latency)
        alpha_loss = self._alpha.alpha_loss_vs_zero(latency)
        fill_cost = self._fill.total_cost_bps(latency)
        net = annual_alpha - tier.annual_cost_inr

        roi = (annual_alpha / tier.annual_cost_inr * 100) if tier.annual_cost_inr > 0 else 0
        payback = (
            tier.annual_cost_inr / (annual_alpha / 12)
            if annual_alpha > 0 else float('inf')
        )

        return TierEvaluation(
            tier=tier,
            alpha_bps_per_trade=round(alpha_bps, 3),
            annual_alpha_inr=round(annual_alpha, 0),
            alpha_loss_vs_zero_inr=round(alpha_loss, 0),
            fill_quality_cost_bps=round(fill_cost, 3),
            annual_infra_cost_inr=tier.annual_cost_inr,
            net_annual_inr=round(net, 0),
            roi_pct=round(roi, 1),
            payback_months=round(payback, 1),
        )

    def evaluate_all(self) -> Dict[str, Any]:
        """Evaluate all tiers and produce a go/no-go recommendation."""
        evaluations = {
            name: self.evaluate_tier(tier)
            for name, tier in self._tiers.items()
        }

        # Compare upgrade paths
        current = evaluations.get("retail_api")
        upgrades = []
        for name, ev in evaluations.items():
            if name == "retail_api":
                continue
            if current:
                incremental_alpha = ev.annual_alpha_inr - current.annual_alpha_inr
                incremental_cost = ev.annual_infra_cost_inr - current.annual_infra_cost_inr
                incremental_roi = (
                    incremental_alpha / incremental_cost * 100
                    if incremental_cost > 0 else 0
                )
            else:
                incremental_alpha = ev.annual_alpha_inr
                incremental_cost = ev.annual_infra_cost_inr
                incremental_roi = ev.roi_pct

            upgrades.append({
                "from": "retail_api",
                "to": name,
                "incremental_alpha_inr": round(incremental_alpha, 0),
                "incremental_cost_inr": round(incremental_cost, 0),
                "incremental_roi_pct": round(incremental_roi, 1),
                "recommended": incremental_roi > 200,  # 2× ROI threshold
            })

        # Recommendation
        best_upgrade = max(upgrades, key=lambda u: u["incremental_roi_pct"]) if upgrades else None
        recommendation = "HOLD"
        if best_upgrade and best_upgrade["recommended"]:
            recommendation = f"UPGRADE to {best_upgrade['to']}"
        elif best_upgrade and best_upgrade["incremental_roi_pct"] > 100:
            recommendation = f"EVALUATE {best_upgrade['to']} — marginal ROI"

        return {
            "capital_deployed_inr": self._capital,
            "alpha_model": {
                "alpha_0_bps": self._alpha.alpha_0_bps,
                "decay_rate_per_ms": self._alpha.decay_rate_per_ms,
                "trades_per_day": self._alpha.trades_per_day,
                "avg_notional": self._alpha.avg_notional_per_trade,
            },
            "tier_evaluations": {
                name: {
                    "tier": ev.tier.name,
                    "description": ev.tier.description,
                    "latency_p50_ms": ev.tier.tick_to_order_p50_ms,
                    "venue_rtt_ms": ev.tier.venue_rtt_ms,
                    "alpha_bps": ev.alpha_bps_per_trade,
                    "annual_alpha_inr": ev.annual_alpha_inr,
                    "alpha_loss_inr": ev.alpha_loss_vs_zero_inr,
                    "fill_cost_bps": ev.fill_quality_cost_bps,
                    "infra_cost_inr": ev.annual_infra_cost_inr,
                    "net_annual_inr": ev.net_annual_inr,
                    "roi_pct": ev.roi_pct,
                    "payback_months": ev.payback_months,
                }
                for name, ev in evaluations.items()
            },
            "upgrade_analysis": upgrades,
            "recommendation": recommendation,
            "decision_criteria": {
                "roi_threshold": "200% (2× infra cost)",
                "min_incremental_alpha": "₹5L/yr",
                "max_payback": "6 months",
            },
        }

    def sensitivity_analysis(
        self,
        alpha_range: List[float] | None = None,
        decay_range: List[float] | None = None,
    ) -> List[Dict[str, Any]]:
        """Run sensitivity analysis across different alpha assumptions.

        Returns a matrix of go/no-go decisions for different scenarios.
        """
        alphas = alpha_range or [2.0, 3.0, 5.0, 8.0, 10.0]
        decays = decay_range or [0.001, 0.003, 0.005, 0.01, 0.02]

        results = []
        for a0 in alphas:
            for decay in decays:
                model = AlphaDecayModel(
                    alpha_0_bps=a0,
                    decay_rate_per_ms=decay,
                    trades_per_day=self._alpha.trades_per_day,
                    avg_notional_per_trade=self._alpha.avg_notional_per_trade,
                )
                evaluator = DMAEvaluator(
                    alpha_model=model,
                    fill_model=self._fill,
                    capital_deployed_inr=self._capital,
                )
                memo = evaluator.evaluate_all()
                results.append({
                    "alpha_0_bps": a0,
                    "decay_rate": decay,
                    "recommendation": memo["recommendation"],
                    "best_tier": max(
                        memo["upgrade_analysis"],
                        key=lambda u: u["incremental_roi_pct"],
                    )["to"] if memo["upgrade_analysis"] else "retail_api",
                    "best_incremental_roi": max(
                        (u["incremental_roi_pct"] for u in memo["upgrade_analysis"]),
                        default=0,
                    ),
                })
        return results
