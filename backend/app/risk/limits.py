"""Hard risk limits enforced by the risk gateway.

These intentionally live here rather than in core/config.py: every
change to a hot-path limit must go through the risk module's audit
path, not ambient environment configuration.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class RiskLimits(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_order_qty: float = Field(default=10_000.0, gt=0)
    max_order_notional: float = Field(default=500_000.0, gt=0)
    max_position_qty: float = Field(default=20_000.0, gt=0)
    max_gross_exposure: float = Field(default=2_000_000.0, gt=0)
    price_collar_pct: float = Field(default=3.0, gt=0)
    max_orders_per_min_per_strategy: int = Field(default=30, gt=0)
    max_signal_age_ms: int = Field(default=5_000, gt=0)
    # Capital reference for autonomy-tier sizing (risk/tiers.py). A proxy for
    # NAV until the OMS supplies live equity (Phase 2+).
    nav: float = Field(default=1_000_000.0, gt=0)

    @classmethod
    def conservative(cls) -> RiskLimits:
        """Tighter limits for tests and cautious first deployments."""
        return cls(
            max_order_qty=100.0,
            max_order_notional=10_000.0,
            max_position_qty=200.0,
            max_gross_exposure=50_000.0,
            price_collar_pct=1.0,
            max_orders_per_min_per_strategy=5,
            max_signal_age_ms=1_000,
        )
