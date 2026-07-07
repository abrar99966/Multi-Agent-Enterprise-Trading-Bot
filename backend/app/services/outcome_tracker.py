"""Grade past recommendations against actual market moves.

For every recommendation older than its grading window (1h, 24h), fetch the
current price and compute whether the signal was correct (direction matched
the actual move). This is the foundation of honest performance reporting —
without it there's no way to know if the bot is actually working.

Grading rules:
  • BUY signal correct if price moved UP by any meaningful amount (>0.1%)
  • SELL signal correct if price moved DOWN by >0.1%
  • Sub-threshold moves count as "neutral" (signal_correct = False, but not
    counted as definitely wrong — UI distinguishes)

Lazy execution: grading runs whenever /performance/* endpoints are hit
(rate-limited to once per 60s) — no separate cron needed.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.database import TradeRecommendation
from ..services.market_data import market_data_service

log = logging.getLogger(__name__)

# Grading windows
WINDOW_1H = timedelta(hours=1)
WINDOW_24H = timedelta(hours=24)
NEUTRAL_THRESHOLD_PCT = 0.1  # moves smaller than this don't count as "correct" either way

# Run no more than once per minute to avoid hammering Yahoo
_lock = asyncio.Lock()
_last_run: Optional[datetime] = None
_MIN_INTERVAL = timedelta(seconds=60)


def _apply_grade(rec: TradeRecommendation, current: float, now: Optional[datetime] = None) -> bool:
    """Grade one rec against an already-fetched current price. Pure (no I/O) so the
    grader can batch all quote fetches up front. Returns True if anything changed."""
    now = now or datetime.utcnow()
    age = now - rec.created_at
    if age < WINDOW_1H or current <= 0 or not rec.entry_price:
        return False

    move_pct = (current - rec.entry_price) / rec.entry_price * 100
    side = (rec.side.value if hasattr(rec.side, "value") else str(rec.side)).lower()

    def is_correct(direction_pct: float) -> bool:
        """For BUY, correct iff move was up beyond threshold. Mirror for SELL."""
        if abs(direction_pct) < NEUTRAL_THRESHOLD_PCT:
            return False
        return (direction_pct > 0) if side == "buy" else (direction_pct < 0)

    updated = False
    if rec.signal_correct_1h is None and age >= WINDOW_1H:
        rec.price_after_1h = round(current, 2)
        rec.actual_move_pct_1h = round(move_pct, 3)
        rec.signal_correct_1h = is_correct(move_pct)
        updated = True

    if rec.signal_correct_24h is None and age >= WINDOW_24H:
        rec.price_after_24h = round(current, 2)
        rec.actual_move_pct_24h = round(move_pct, 3)
        rec.signal_correct_24h = is_correct(move_pct)
        updated = True

    if updated:
        rec.graded_at = now
    return updated


async def grade_pending_outcomes(db: AsyncSession, *, force: bool = False) -> int:
    """Grade all eligible recommendations. Rate-limited to once per minute
    unless `force=True`. Returns the number of recs newly graded."""
    global _last_run
    now = datetime.utcnow()
    if not force and _last_run and (now - _last_run) < _MIN_INTERVAL:
        return 0

    if _lock.locked():
        return 0
    async with _lock:
        # Pull anything that's at least 1h old AND not fully graded for both windows
        cutoff = now - WINDOW_1H
        res = await db.execute(
            select(TradeRecommendation).where(
                and_(
                    TradeRecommendation.created_at <= cutoff,
                    or_(
                        TradeRecommendation.signal_correct_1h.is_(None),
                        and_(
                            TradeRecommendation.created_at <= now - WINDOW_24H,
                            TradeRecommendation.signal_correct_24h.is_(None),
                        ),
                    ),
                )
            ).order_by(TradeRecommendation.created_at.desc()).limit(50)
        )
        rows = res.scalars().all()
        if not rows:
            _last_run = now
            return 0

        # Batch ALL quote fetches into one routed call (brokers batched, Yahoo in
        # parallel) instead of one sequential request per rec — turns ~30s into ~1s
        # and stops hammering Yahoo (which was throttling charts too).
        symbols = list({r.symbol for r in rows})
        price_map: dict = {}
        try:
            quotes = await market_data_service.get_quotes_batch_routed(symbols, db)
            for q in quotes or []:
                sym = (q.get("symbol") or "").upper()
                px = float(q.get("current_price") or 0)
                if sym and px > 0:
                    price_map[sym] = px
        except Exception as exc:
            log.warning("Batch quote fetch failed during grading: %s", exc)

        updated = 0
        for r in rows:
            px = price_map.get((r.symbol or "").upper())
            if not px:
                continue
            try:
                if _apply_grade(r, px, now):
                    updated += 1
            except Exception as exc:
                log.warning("Grade failed for rec %d: %s", r.id, exc)
        if updated:
            await db.commit()
            log.info("Graded %d recommendations against actual market moves", updated)
        _last_run = now
        return updated


def schedule_grading() -> None:
    """Fire-and-forget grading on its own DB session so HTTP responses never block.

    The /performance/stats poll triggers this but returns immediately from current
    DB state; newly graded rows appear on the next poll. Rate-limiting inside
    grade_pending_outcomes (60s) keeps repeated triggers cheap.
    """
    try:
        asyncio.get_running_loop().create_task(_run_grading_bg())
    except RuntimeError:
        pass  # no running loop (shouldn't happen under the server)


async def grade_horizon_outcomes(db: AsyncSession, *, limit: int = 100) -> int:
    """Grade investment-horizon recs once their window has passed (closed loop).

    A 1M/3M/6M/1Y call is judged at its horizon end: did price move in the
    predicted direction? This is the REAL per-horizon hit rate (vs the backtest's
    expected rate), accumulating as recs mature. Batches quote fetches."""
    now = datetime.utcnow()
    res = await db.execute(
        select(TradeRecommendation).where(
            TradeRecommendation.horizon.isnot(None),
            TradeRecommendation.horizon_correct.is_(None),
            TradeRecommendation.horizon_due_at.isnot(None),
            TradeRecommendation.horizon_due_at <= now,
        ).order_by(TradeRecommendation.horizon_due_at.asc()).limit(limit)
    )
    rows = res.scalars().all()
    if not rows:
        return 0

    symbols = list({r.symbol for r in rows})
    price_map: dict = {}
    try:
        quotes = await market_data_service.get_quotes_batch_routed(symbols, db)
        for q in quotes or []:
            sym = (q.get("symbol") or "").upper()
            px = float(q.get("current_price") or 0)
            if sym and px > 0:
                price_map[sym] = px
    except Exception as exc:
        log.warning("Batch quote failed during horizon grading: %s", exc)

    updated = 0
    for r in rows:
        px = price_map.get((r.symbol or "").upper())
        if not px or not r.entry_price:
            continue
        move = (px - r.entry_price) / r.entry_price * 100
        side = (r.side.value if hasattr(r.side, "value") else str(r.side)).lower()
        if abs(move) < NEUTRAL_THRESHOLD_PCT:
            correct = False
        else:
            correct = (move > 0) if side == "buy" else (move < 0)
        r.horizon_move_pct = round(move, 3)
        r.horizon_correct = bool(correct)
        r.graded_horizon_at = now
        updated += 1
        # Feed the self-learning policy with this matured outcome (+1 win / −1 loss),
        # keyed on the SAME state the rec was generated under.
        try:
            from ai.rl.q_learning_agent import rl_learning_agent
            rationale = (r.agent_outputs or {}).get("rationale") or {}
            state = rationale.get("rl_state") or rl_learning_agent.get_state(r.agent_outputs or {})
            rl_learning_agent.update(state, 1.0 if correct else -1.0)
        except Exception:
            pass
    if updated:
        await db.commit()
        log.info("Graded %d horizon outcomes", updated)
    return updated


async def _run_grading_bg() -> None:
    from ..db.session import async_session
    try:
        async with async_session() as db:
            n1 = await grade_pending_outcomes(db)
            n2 = await grade_horizon_outcomes(db)
        if (n1 or 0) + (n2 or 0) > 0:
            try:
                from .calibration import invalidate
                invalidate()   # new graded outcomes → rebuild the calibration map
            except Exception:
                pass
    except Exception:
        log.exception("Background grading failed")


# ---- Aggregate stats for /performance endpoint ----------------------------------------

async def compute_stats(db: AsyncSession, *, days: int = 7) -> dict:
    """Honest performance numbers — hit rate, expectancy, per-symbol breakdown."""
    now = datetime.utcnow()
    since = now - timedelta(days=days)
    res = await db.execute(
        select(TradeRecommendation).where(
            and_(
                TradeRecommendation.created_at >= since,
                TradeRecommendation.signal_correct_1h.is_not(None),
            )
        )
    )
    rows = res.scalars().all()

    if not rows:
        return {
            "window_days": days,
            "graded_count": 0,
            "hit_rate_1h": None,
            "hit_rate_24h": None,
            "avg_correct_move_pct_1h": None,
            "avg_wrong_move_pct_1h": None,
            "expectancy_1h": None,
            "per_symbol": {},
            "recent": [],
            "message": "Not enough graded signals yet — need 1+ hour of post-recommendation history.",
        }

    correct_1h = [r for r in rows if r.signal_correct_1h]
    wrong_1h = [r for r in rows if r.signal_correct_1h is False and abs(r.actual_move_pct_1h or 0) >= NEUTRAL_THRESHOLD_PCT]
    correct_24h_rows = [r for r in rows if r.signal_correct_24h is not None]
    correct_24h = [r for r in correct_24h_rows if r.signal_correct_24h]

    avg_win = (sum(abs(r.actual_move_pct_1h) for r in correct_1h) / len(correct_1h)) if correct_1h else 0.0
    avg_loss = (sum(abs(r.actual_move_pct_1h) for r in wrong_1h) / len(wrong_1h)) if wrong_1h else 0.0
    hit_rate_1h = len(correct_1h) / len(rows) if rows else 0.0
    expectancy = (hit_rate_1h * avg_win) - ((1 - hit_rate_1h) * avg_loss)

    # Per-symbol breakdown
    per_symbol: dict = {}
    for r in rows:
        s = r.symbol.upper()
        bucket = per_symbol.setdefault(s, {"total": 0, "correct_1h": 0, "moves": []})
        bucket["total"] += 1
        if r.signal_correct_1h:
            bucket["correct_1h"] += 1
        if r.actual_move_pct_1h is not None:
            bucket["moves"].append(r.actual_move_pct_1h)
    per_symbol_out = {
        s: {
            "total": v["total"],
            "hit_rate_1h": round(v["correct_1h"] / v["total"], 3) if v["total"] else 0,
            "avg_move_pct": round(sum(v["moves"]) / len(v["moves"]), 3) if v["moves"] else 0,
        }
        for s, v in per_symbol.items()
    }

    # Per-horizon hit rate (closed-loop) — REAL accuracy of matured horizon calls,
    # all-time (horizons span weeks/months, so not limited to the `days` window).
    hres = await db.execute(
        select(TradeRecommendation).where(TradeRecommendation.horizon_correct.isnot(None))
    )
    hbuckets: dict = {}
    for r in hres.scalars().all():
        b = hbuckets.setdefault(r.horizon or "?", {"graded": 0, "correct": 0, "moves": []})
        b["graded"] += 1
        if r.horizon_correct:
            b["correct"] += 1
        if r.horizon_move_pct is not None:
            b["moves"].append(r.horizon_move_pct)
    by_horizon = {
        h: {"graded": v["graded"],
            "hit_rate": round(v["correct"] / v["graded"], 3) if v["graded"] else None,
            "avg_move_pct": round(sum(v["moves"]) / len(v["moves"]), 3) if v["moves"] else None}
        for h, v in hbuckets.items()
    }

    # Last 20 graded — for the timeline view
    recent = sorted(rows, key=lambda r: r.created_at, reverse=True)[:20]
    recent_out = [
        {
            "id": r.id, "symbol": r.symbol,
            "side": r.side.value if hasattr(r.side, "value") else str(r.side),
            "entry_price": r.entry_price,
            "price_after_1h": r.price_after_1h,
            "actual_move_pct_1h": r.actual_move_pct_1h,
            "correct_1h": r.signal_correct_1h,
            "correct_24h": r.signal_correct_24h,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "confidence": r.confidence_score,
        }
        for r in recent
    ]

    return {
        "window_days": days,
        "graded_count": len(rows),
        "hit_rate_1h": round(hit_rate_1h, 3),
        "hit_rate_24h": round(len(correct_24h) / len(correct_24h_rows), 3) if correct_24h_rows else None,
        "avg_correct_move_pct_1h": round(avg_win, 3),
        "avg_wrong_move_pct_1h": round(avg_loss, 3),
        "expectancy_1h": round(expectancy, 3),
        "by_horizon": by_horizon,
        "per_symbol": per_symbol_out,
        "recent": recent_out,
        "neutral_threshold_pct": NEUTRAL_THRESHOLD_PCT,
        "message": None,
    }
