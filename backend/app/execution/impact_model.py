"""Pre-trade market-impact model.

Phase 4 of the Institutional Target-State Architecture.

Estimates the expected market impact (slippage) of an order before
execution, allowing the execution engine to choose the optimal algo
and parameters. Based on a simplified Almgren-Chriss framework adapted
for retail-tier data availability.

Impact components:
  1. Temporary impact: I_temp = η · σ · (Q / V_daily)^0.5
  2. Permanent impact: I_perm = γ · σ · (Q / V_daily)
  3. Spread cost: S / 2  (half the bid-ask spread)

Where:
  Q = order quantity
  V_daily = average daily volume
  σ = daily volatility
  η, γ = calibrated impact coefficients (market-specific)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass(frozen=True)
class ImpactEstimate:
    """Pre-trade impact estimate for a single order."""
    symbol: str
    side: str
    qty: float
    reference_price: float

    # Costs in basis points
    spread_cost_bps: float = 0.0
    temporary_impact_bps: float = 0.0
    permanent_impact_bps: float = 0.0
    total_expected_cost_bps: float = 0.0

    # Costs in absolute currency
    total_expected_cost: float = 0.0
    notional: float = 0.0

    # Participation rate
    pov_pct: float = 0.0  # % of daily volume

    # Recommended execution parameters
    recommended_algo: str = "IS"
    recommended_urgency: float = 0.5  # 0=passive, 1=aggressive
    recommended_duration_min: int = 30


@dataclass
class MarketMicrostructure:
    """Observable microstructure state for a symbol."""
    symbol: str
    avg_daily_volume: float = 0.0        # shares/day
    daily_volatility: float = 0.0        # annualized σ (decimal)
    bid_ask_spread_bps: float = 5.0      # typical spread in bps
    avg_trade_size: float = 100.0        # shares per trade
    intraday_volume_curve: Dict[int, float] = field(default_factory=dict)


# -- Calibrated impact coefficients by market --------------------------

@dataclass(frozen=True)
class ImpactCoefficients:
    """Market-specific impact model calibration."""
    eta: float = 0.142         # temporary impact coefficient
    gamma: float = 0.314       # permanent impact coefficient
    spread_mult: float = 1.0   # spread multiplier
    min_spread_bps: float = 2.0


# Default coefficients by market region
IMPACT_COEFFICIENTS: Dict[str, ImpactCoefficients] = {
    "IN": ImpactCoefficients(
        eta=0.18,      # Indian markets: wider impact due to lower depth
        gamma=0.35,
        spread_mult=1.2,
        min_spread_bps=3.0,
    ),
    "US": ImpactCoefficients(
        eta=0.12,      # US markets: tighter due to deeper books
        gamma=0.28,
        spread_mult=1.0,
        min_spread_bps=1.0,
    ),
    "GLOBAL": ImpactCoefficients(
        eta=0.15,
        gamma=0.30,
        spread_mult=1.1,
        min_spread_bps=2.0,
    ),
}


class ImpactModel:
    """Pre-trade impact estimator using a simplified Almgren-Chriss model.

    Thread-safe: all methods are pure functions of their inputs.
    """

    def __init__(self, region: str = "IN"):
        self._coeffs = IMPACT_COEFFICIENTS.get(region, IMPACT_COEFFICIENTS["GLOBAL"])
        self._micro: Dict[str, MarketMicrostructure] = {}

    def update_microstructure(self, micro: MarketMicrostructure) -> None:
        """Update the microstructure state for a symbol."""
        self._micro[micro.symbol] = micro

    def estimate(
        self,
        symbol: str,
        side: str,
        qty: float,
        reference_price: float,
        avg_daily_volume: Optional[float] = None,
        daily_volatility: Optional[float] = None,
        spread_bps: Optional[float] = None,
    ) -> ImpactEstimate:
        """Estimate pre-trade market impact.

        Args:
            symbol: instrument symbol
            side: "BUY" or "SELL"
            qty: order quantity in shares
            reference_price: current mid/last price
            avg_daily_volume: override for ADV (shares/day)
            daily_volatility: override for daily σ (decimal, e.g. 0.02 = 2%)
            spread_bps: override for typical bid-ask spread in bps

        Returns:
            ImpactEstimate with costs and algo recommendation.
        """
        micro = self._micro.get(symbol)

        # Use overrides or fall back to stored microstructure
        adv = avg_daily_volume or (micro.avg_daily_volume if micro else 0.0)
        vol = daily_volatility or (micro.daily_volatility if micro else 0.02)
        sprd = spread_bps or (micro.bid_ask_spread_bps if micro else self._coeffs.min_spread_bps)

        # Ensure minimum sane values
        adv = max(adv, 1000.0)  # At least 1000 shares/day
        vol = max(vol, 0.001)   # At least 0.1% daily vol
        sprd = max(sprd, self._coeffs.min_spread_bps)

        notional = qty * reference_price
        participation = qty / adv  # fraction of daily volume

        # -- Spread cost (half-spread) --
        spread_cost_bps = sprd * self._coeffs.spread_mult / 2.0

        # -- Temporary impact (square-root model) --
        # I_temp = η · σ_daily · sqrt(Q / ADV)
        temp_impact = self._coeffs.eta * vol * math.sqrt(participation)
        temp_impact_bps = temp_impact * 10_000

        # -- Permanent impact (linear model) --
        # I_perm = γ · σ_daily · (Q / ADV)
        perm_impact = self._coeffs.gamma * vol * participation
        perm_impact_bps = perm_impact * 10_000

        # Total expected cost
        total_bps = spread_cost_bps + temp_impact_bps + perm_impact_bps
        total_cost = notional * total_bps / 10_000

        # Participation rate as % of daily volume
        pov_pct = participation * 100

        # -- Algo recommendation --
        algo, urgency, duration = self._recommend_algo(
            participation, vol, total_bps, pov_pct
        )

        return ImpactEstimate(
            symbol=symbol,
            side=side.upper(),
            qty=qty,
            reference_price=reference_price,
            spread_cost_bps=round(spread_cost_bps, 2),
            temporary_impact_bps=round(temp_impact_bps, 2),
            permanent_impact_bps=round(perm_impact_bps, 2),
            total_expected_cost_bps=round(total_bps, 2),
            total_expected_cost=round(total_cost, 2),
            notional=round(notional, 2),
            pov_pct=round(pov_pct, 4),
            recommended_algo=algo,
            recommended_urgency=round(urgency, 2),
            recommended_duration_min=duration,
        )

    def _recommend_algo(
        self,
        participation: float,
        volatility: float,
        total_cost_bps: float,
        pov_pct: float,
    ) -> tuple[str, float, int]:
        """Choose the optimal execution algo based on order characteristics.

        Returns: (algo_name, urgency 0-1, duration_minutes)
        """
        # Very small orders: just use IS with high urgency
        if participation < 0.001:  # < 0.1% of ADV
            return "IS", 0.8, 5

        # Large orders: use VWAP or POV to minimize impact
        if participation > 0.05:  # > 5% of ADV
            if volatility > 0.03:  # High vol → more passive
                return "POV", 0.3, 120
            return "VWAP", 0.4, 90

        # Medium orders: IS with adaptive urgency
        if volatility > 0.04:
            # High vol → aggressive to capture before adverse move
            return "IS", 0.7, 15
        elif volatility > 0.02:
            # Normal vol → balanced
            return "ADAPTIVE", 0.5, 30
        else:
            # Low vol → passive to minimize impact
            return "IS", 0.3, 45

    def optimal_slice_schedule(
        self,
        estimate: ImpactEstimate,
        n_slices: int = 10,
    ) -> list[dict]:
        """Generate an optimal order-slicing schedule.

        Uses a front-loaded profile for IS (Almgren-Chriss optimal)
        and a VWAP-shaped profile for VWAP algo.
        """
        if n_slices < 1:
            n_slices = 1

        total_qty = estimate.qty
        duration = estimate.recommended_duration_min
        algo = estimate.recommended_algo

        slices = []
        if algo == "VWAP":
            # U-shaped volume profile (typical intraday)
            weights = self._vwap_weights(n_slices)
        elif algo == "POV":
            # Equal slices (POV tracks volume linearly)
            weights = [1.0 / n_slices] * n_slices
        else:
            # IS: front-loaded (Almgren-Chriss optimal trajectory)
            urgency = estimate.recommended_urgency
            weights = self._is_weights(n_slices, urgency)

        for i, w in enumerate(weights):
            slice_qty = max(1, round(total_qty * w))
            slices.append({
                "slice": i + 1,
                "time_offset_min": round(duration * i / n_slices, 1),
                "qty": slice_qty,
                "pct_of_total": round(w * 100, 1),
                "cumulative_pct": round(sum(weights[: i + 1]) * 100, 1),
            })

        return slices

    @staticmethod
    def _is_weights(n: int, urgency: float) -> list[float]:
        """Almgren-Chriss-inspired front-loaded weights.

        Higher urgency → more front-loaded.
        """
        decay = 1.0 + urgency * 2.0  # 1.0 (flat) to 3.0 (steep)
        raw = [math.exp(-decay * i / n) for i in range(n)]
        total = sum(raw)
        return [w / total for w in raw]

    @staticmethod
    def _vwap_weights(n: int) -> list[float]:
        """U-shaped intraday volume profile."""
        raw = []
        for i in range(n):
            t = i / max(n - 1, 1)  # 0 to 1
            # U-shape: high at open/close, low at midday
            w = 1.5 - 2.0 * t * (1 - t) * 4  # Parabolic dip
            w = max(w, 0.3)  # Floor
            raw.append(w)
        total = sum(raw)
        return [w / total for w in raw]
