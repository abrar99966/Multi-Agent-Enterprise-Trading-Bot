"""Hard pre-trade risk gates.

Called by trade_service.process_approval BEFORE any live order goes out.
These are not advisory — they block the order.

Limits enforced:
  1. kill_switch — master OFF for all live trading
  2. per_trade_max_inr — single position cannot exceed this rupee value
  3. daily_max_trades — max number of live orders per calendar day (IST)
  4. daily_max_loss_inr — if today's realized P&L drops below -limit, block
     all further live orders for the day

Day rollover happens at 06:00 IST (matches SEBI broker-token reset). The
single RiskLimits row stores cached counters that get auto-reset.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.database import RiskLimits, Trade

log = logging.getLogger(__name__)

# Sensible defaults if no row exists yet — conservative on purpose
DEFAULT_PER_TRADE_INR = 10_000.0
DEFAULT_DAILY_LOSS_INR = 2_000.0
DEFAULT_DAILY_TRADES = 10


@dataclass
class RiskCheckResult:
    allowed: bool
    reason: Optional[str] = None
    limits: Optional[dict] = None


def _ist_day_start(now: datetime) -> datetime:
    """06:00 IST today (or yesterday's 06:00 if it's before that)."""
    # IST = UTC + 5:30 → 06:00 IST = 00:30 UTC
    ist_cutoff_utc = now.replace(hour=0, minute=30, second=0, microsecond=0)
    if now < ist_cutoff_utc:
        ist_cutoff_utc -= timedelta(days=1)
    return ist_cutoff_utc


async def _get_or_create_limits(db: AsyncSession, user_id: int = 1) -> RiskLimits:
    res = await db.execute(select(RiskLimits).where(RiskLimits.user_id == user_id))
    row = res.scalar_one_or_none()
    if row is None:
        row = RiskLimits(
            user_id=user_id,
            per_trade_max_inr=DEFAULT_PER_TRADE_INR,
            daily_max_loss_inr=DEFAULT_DAILY_LOSS_INR,
            daily_max_trades=DEFAULT_DAILY_TRADES,
            kill_switch=False,
            updated_at=datetime.utcnow(),
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return row


async def _ensure_day_reset(db: AsyncSession, row: RiskLimits) -> RiskLimits:
    """Zero today_* counters if we've crossed the 06:00 IST boundary since last reset."""
    now = datetime.utcnow()
    day_start = _ist_day_start(now)
    if row.today_reset_at is None or row.today_reset_at < day_start:
        row.today_realized_pnl_inr = 0.0
        row.today_trade_count = 0
        row.today_reset_at = now
        await db.commit()
    return row


async def get_limits(db: AsyncSession, user_id: int = 1) -> dict:
    row = await _get_or_create_limits(db, user_id)
    row = await _ensure_day_reset(db, row)
    return {
        "per_trade_max_inr": row.per_trade_max_inr,
        "daily_max_loss_inr": row.daily_max_loss_inr,
        "daily_max_trades": row.daily_max_trades,
        "kill_switch": row.kill_switch,
        "today_realized_pnl_inr": row.today_realized_pnl_inr,
        "today_trade_count": row.today_trade_count,
        "today_remaining_trades": max(0, row.daily_max_trades - row.today_trade_count),
        "today_remaining_loss_buffer_inr": row.daily_max_loss_inr + min(0, row.today_realized_pnl_inr),
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "today_reset_at": row.today_reset_at.isoformat() if row.today_reset_at else None,
    }


async def update_limits(
    db: AsyncSession, user_id: int = 1, *,
    per_trade_max_inr: Optional[float] = None,
    daily_max_loss_inr: Optional[float] = None,
    daily_max_trades: Optional[int] = None,
    kill_switch: Optional[bool] = None,
) -> dict:
    row = await _get_or_create_limits(db, user_id)
    if per_trade_max_inr is not None:
        row.per_trade_max_inr = max(0.0, float(per_trade_max_inr))
    if daily_max_loss_inr is not None:
        row.daily_max_loss_inr = max(0.0, float(daily_max_loss_inr))
    if daily_max_trades is not None:
        row.daily_max_trades = max(0, int(daily_max_trades))
    if kill_switch is not None:
        row.kill_switch = bool(kill_switch)
    row.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(row)
    return await get_limits(db, user_id)


async def check_pre_trade(
    db: AsyncSession, *,
    order_value_inr: float,
    is_paper: bool,
    user_id: int = 1,
) -> RiskCheckResult:
    """Called before every live order. Paper orders bypass these checks."""
    if is_paper:
        return RiskCheckResult(allowed=True, reason=None)

    row = await _get_or_create_limits(db, user_id)
    row = await _ensure_day_reset(db, row)

    # 1. Kill switch
    if row.kill_switch:
        return RiskCheckResult(
            allowed=False,
            reason="Kill switch is ENGAGED. All live trading is disabled. Toggle it off on /performance to resume.",
            limits=await get_limits(db, user_id),
        )

    # 2. Per-trade size
    if order_value_inr > row.per_trade_max_inr:
        return RiskCheckResult(
            allowed=False,
            reason=f"Order value ₹{order_value_inr:,.0f} exceeds per-trade limit ₹{row.per_trade_max_inr:,.0f}. Reduce quantity or raise the limit.",
            limits=await get_limits(db, user_id),
        )

    # 3. Daily trade count
    if row.today_trade_count >= row.daily_max_trades:
        return RiskCheckResult(
            allowed=False,
            reason=f"Daily trade count cap reached ({row.daily_max_trades}). Resets at 06:00 IST tomorrow.",
            limits=await get_limits(db, user_id),
        )

    # 4. Daily loss limit (cumulative realized loss can't exceed configured cap)
    if row.today_realized_pnl_inr <= -row.daily_max_loss_inr:
        return RiskCheckResult(
            allowed=False,
            reason=f"Daily loss limit hit (realized P&L ₹{row.today_realized_pnl_inr:,.0f} ≤ −₹{row.daily_max_loss_inr:,.0f}). Live trading disabled for the day.",
            limits=await get_limits(db, user_id),
        )

    return RiskCheckResult(allowed=True, limits=await get_limits(db, user_id))


async def record_trade_placed(
    db: AsyncSession, *, order_value_inr: float, is_paper: bool, user_id: int = 1
) -> None:
    """Increment today_trade_count after a successful live order placement."""
    if is_paper:
        return
    row = await _get_or_create_limits(db, user_id)
    row = await _ensure_day_reset(db, row)
    row.today_trade_count += 1
    await db.commit()


async def record_pnl_change(
    db: AsyncSession, *, pnl_delta_inr: float, user_id: int = 1
) -> None:
    """Add a realized P&L change to today's running total.

    Called from outcome_tracker / trade close handler when a position closes.
    """
    row = await _get_or_create_limits(db, user_id)
    row = await _ensure_day_reset(db, row)
    row.today_realized_pnl_inr += float(pnl_delta_inr)
    await db.commit()
