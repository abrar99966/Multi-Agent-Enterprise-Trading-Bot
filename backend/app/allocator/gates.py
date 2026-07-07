"""Â§7.4 Championâ€“Challenger promotion gate.

A challenger may only replace the current champion if it passes ALL of:

1. **Minimum sample size** â€” enough observations to be statistically
   meaningful (default: 100 evaluation periods).
2. **Sharpe improvement** â€” Sharpe ratio exceeds champion's by a
   configurable delta (default: 0.3 annualised Sharpe).
3. **Drawdown ceiling** â€” max drawdown â‰¤ threshold (default: 15%).
4. **Win rate floor** â€” posterior mean > 0.55 (must win more than lose).
5. **Stability** â€” standard deviation of returns must be within 2Ã— of
   champion's (no lottery-ticket strategies that spike then crash).

All gates must pass. A single failure blocks promotion, with a reason
string indicating which gate failed. The gate is a pure function: no
side effects, no database writes, no approval workflows â€” those are the
caller's responsibility.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from app.allocator.bandit import ArmState


@dataclass
class GateConfig:
    """Configurable thresholds for the promotion gate."""

    min_observations: int = 100
    sharpe_improvement: float = 0.3      # challenger Sharpe must exceed champion by this
    max_drawdown_pct: float = 15.0       # max drawdown ceiling (%)
    min_posterior_mean: float = 0.55      # must be a net winner
    max_volatility_ratio: float = 2.0    # challenger vol / champion vol â‰¤ this
    min_sharpe_absolute: float = 0.5     # absolute minimum Sharpe to promote


@dataclass
class PromotionDecision:
    """Result of a gate check."""

    promote: bool
    reason: str
    sharpe_delta: float = 0.0
    gates_passed: int = 0
    gates_total: int = 6


_DEFAULT_CONFIG = GateConfig()


def default_gate_check(
    challenger: "ArmState",
    champion: Optional["ArmState"],
    config: GateConfig | None = None,
) -> PromotionDecision:
    """Evaluate whether ``challenger`` should replace ``champion``.

    If there is no current champion, the gate relaxes: the challenger only
    needs to meet absolute thresholds (sample size, drawdown, win rate).
    """
    cfg = config or _DEFAULT_CONFIG
    gates_passed = 0
    total_gates = 6 if champion else 4  # some gates require a champion

    # Gate 1: Minimum observations
    if challenger.n_observations < cfg.min_observations:
        return PromotionDecision(
            promote=False,
            reason=f"Insufficient observations: {challenger.n_observations} < {cfg.min_observations}",
            gates_passed=gates_passed,
            gates_total=total_gates,
        )
    gates_passed += 1

    # Gate 2: Win rate / posterior mean
    if challenger.posterior_mean < cfg.min_posterior_mean:
        return PromotionDecision(
            promote=False,
            reason=f"Posterior mean too low: {challenger.posterior_mean:.3f} < {cfg.min_posterior_mean}",
            gates_passed=gates_passed,
            gates_total=total_gates,
        )
    gates_passed += 1

    # Gate 3: Drawdown ceiling
    if challenger.max_drawdown * 100 > cfg.max_drawdown_pct:
        return PromotionDecision(
            promote=False,
            reason=f"Max drawdown too high: {challenger.max_drawdown*100:.1f}% > {cfg.max_drawdown_pct}%",
            gates_passed=gates_passed,
            gates_total=total_gates,
        )
    gates_passed += 1

    # Gate 4: Absolute Sharpe floor
    if challenger.sharpe < cfg.min_sharpe_absolute:
        return PromotionDecision(
            promote=False,
            reason=f"Absolute Sharpe too low: {challenger.sharpe:.2f} < {cfg.min_sharpe_absolute}",
            gates_passed=gates_passed,
            gates_total=total_gates,
        )
    gates_passed += 1

    # If no champion exists, pass (we just need absolute quality)
    if champion is None:
        return PromotionDecision(
            promote=True,
            reason="No current champion; absolute quality gates passed",
            sharpe_delta=challenger.sharpe,
            gates_passed=gates_passed,
            gates_total=total_gates,
        )

    # Gate 5: Sharpe improvement over champion
    sharpe_delta = challenger.sharpe - champion.sharpe
    if sharpe_delta < cfg.sharpe_improvement:
        return PromotionDecision(
            promote=False,
            reason=(
                f"Sharpe improvement insufficient: "
                f"{sharpe_delta:.3f} < {cfg.sharpe_improvement}"
            ),
            sharpe_delta=sharpe_delta,
            gates_passed=gates_passed,
            gates_total=total_gates,
        )
    gates_passed += 1

    # Gate 6: Volatility stability
    if champion.return_std > 1e-12:
        vol_ratio = challenger.return_std / champion.return_std
        if vol_ratio > cfg.max_volatility_ratio:
            return PromotionDecision(
                promote=False,
                reason=(
                    f"Return volatility too high vs champion: "
                    f"{vol_ratio:.2f}Ã— > {cfg.max_volatility_ratio}Ã—"
                ),
                sharpe_delta=sharpe_delta,
                gates_passed=gates_passed,
                gates_total=total_gates,
            )
    gates_passed += 1

    return PromotionDecision(
        promote=True,
        reason=(
            f"All gates passed. Sharpe: {challenger.sharpe:.2f} vs "
            f"{champion.sharpe:.2f} (Î”={sharpe_delta:.3f})"
        ),
        sharpe_delta=sharpe_delta,
        gates_passed=gates_passed,
        gates_total=total_gates,
    )
