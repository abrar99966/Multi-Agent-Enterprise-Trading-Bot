"""Confidence calibration — map raw model confidence to REALIZED hit rate.

"Confidence 0.6" is only meaningful if calls made at 0.6 actually win ~60% of the
time. We learn that mapping from graded recommendations (closed-loop horizon
outcomes first, else the 1h direction grade), bucketed by stated confidence.

Each bucket is shrunk toward identity (its own midpoint) with a pseudo-count, so
the calibration is SAFE when data is thin (≈ no change) and sharpens as outcomes
accumulate. Cached briefly; rebuilt as grading adds samples.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.database import TradeRecommendation

log = logging.getLogger(__name__)

_SHRINK = 12.0          # pseudo-count pulling each bucket toward identity
_TTL = 300.0            # seconds
_cache = {"ts": 0.0, "map": None}


async def build_calibration(db: AsyncSession) -> dict:
    """Reliability table: per confidence-decile, observed vs shrunk realized hit rate."""
    # Calibrate ONLY on closed-loop horizon outcomes — matching how the recs are
    # actually made. Mixing in legacy 1h intraday grades distorts horizon calls.
    res = await db.execute(
        select(TradeRecommendation).where(TradeRecommendation.horizon_correct.isnot(None))
    )
    buckets = {i: [0, 0] for i in range(10)}   # decile -> [n, correct]
    total_n = total_c = 0
    for r in res.scalars().all():
        conf = r.confidence_score
        if conf is None:
            continue
        correct = r.horizon_correct
        if correct is None:
            continue
        b = min(9, max(0, int(conf * 10)))
        buckets[b][0] += 1
        buckets[b][1] += 1 if correct else 0
        total_n += 1
        total_c += 1 if correct else 0

    table = []
    for i in range(10):
        n, c = buckets[i]
        mid = (i + 0.5) / 10.0
        realized = (c + _SHRINK * mid) / (n + _SHRINK)   # shrink toward identity
        table.append({
            "bucket": f"{i * 10}-{(i + 1) * 10}%", "lo": i / 10.0, "hi": (i + 1) / 10.0, "mid": mid,
            "n": n, "observed": round(c / n, 3) if n else None, "realized": round(realized, 3),
        })
    return {
        "table": table,
        "samples": total_n,
        "base_rate": round(total_c / total_n, 3) if total_n else None,
        "shrink": _SHRINK,
    }


def calibrate(raw: Optional[float], cal: Optional[dict]) -> Optional[float]:
    """Map a raw confidence to its bucket's realized hit rate (shrunk). Identity if no data."""
    if raw is None or not cal:
        return raw
    b = min(9, max(0, int(float(raw) * 10)))
    realized = cal["table"][b]["realized"]
    return max(0.05, min(0.95, realized))


async def get_calibration(db: AsyncSession) -> dict:
    """Cached calibration map (rebuilt every _TTL)."""
    now = time.monotonic()
    if _cache["map"] is not None and now - _cache["ts"] < _TTL:
        return _cache["map"]
    cal = await build_calibration(db)
    _cache.update(ts=now, map=cal)
    return cal


def invalidate() -> None:
    _cache["map"] = None
