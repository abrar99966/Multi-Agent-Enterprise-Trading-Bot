"""Honest performance + risk endpoints.

`/performance/stats` — actual hit rate vs. recommendation predictions
`/risk/limits` — get/set hard pre-trade gates + kill switch
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.session import get_db
from ...services import outcome_tracker, risk_limits

router = APIRouter()


@router.get("/performance/stats")
async def performance_stats(
    days: int = Query(7, ge=1, le=90, description="Window size for hit-rate computation"),
    grade_now: bool = Query(True, description="Run grader before computing stats"),
    db: AsyncSession = Depends(get_db),
):
    """Honest signal-accuracy numbers, returned immediately from current state.

    Grading is triggered in the BACKGROUND (not awaited) so this endpoint stays
    fast — it used to block ~30s grading dozens of recs with sequential quote
    fetches. Newly-graded signals appear on the next poll. Use POST
    /performance/grade-now for a synchronous, force-graded recompute.

    Hit rate = recommendations whose direction matched the 1h actual move.
    Sub-0.1% moves are NOT counted as either correct or wrong (neutral).
    """
    if grade_now:
        outcome_tracker.schedule_grading()   # fire-and-forget; don't block the response
    return await outcome_tracker.compute_stats(db, days=days)


@router.get("/performance/calibration")
async def calibration(db: AsyncSession = Depends(get_db)):
    """Reliability table — does a stated confidence match the realized hit rate?

    Built from graded recs (horizon outcomes first, else 1h direction). Each bucket
    is shrunk toward identity, so it reads as 'no adjustment yet' until enough calls
    have matured, then sharpens. The same mapping adjusts live confidence + sizing.
    """
    from ...services.calibration import get_calibration
    return await get_calibration(db)


@router.get("/performance/health")
async def system_health(db: AsyncSession = Depends(get_db), symbol: str = Query("RELIANCE")):
    """Live health of every agent + the learning loops — powers the monitor page.

    Probes each agent on one symbol (timing it), and reports data-source, training,
    grading, calibration and RL-policy state so the user can see what's working.
    """
    import time as _t
    from ...services.market_data import market_data_service
    from ...agents.base import technical_agent_singleton, NewsAgent, MacroAgent, RiskAgent
    from ...learning import bar_store

    async def _timed(coro):
        t0 = _t.perf_counter()
        try:
            out = await coro
            return out, round((_t.perf_counter() - t0) * 1000), None
        except Exception as exc:
            return None, round((_t.perf_counter() - t0) * 1000), str(exc)[:160]

    agents = []
    # Build probe context (quote + intraday)
    quote, q_ms, q_err = await _timed(market_data_service.get_quote_routed(symbol, db))
    intraday, _, _ = await _timed(market_data_service.get_intraday_routed(symbol, db))
    ctx = {**(quote or {}), "symbol": symbol, "intraday": intraday or {}, "currency": (quote or {}).get("currency")}

    async def probe(name, agent):
        out, ms, err = await _timed(agent.analyze(ctx))
        return {"name": name, "ok": err is None, "latency_ms": ms, "error": err, "out": out or {}}

    tech = await probe("TechnicalAnalysis", technical_agent_singleton)
    news = await probe("NewsIntelligence", NewsAgent())
    macro = await probe("MacroEconomics", MacroAgent())
    risk = await probe("RiskManager", RiskAgent())

    agents.append({"name": "Technical", "ok": tech["ok"], "latency_ms": tech["latency_ms"], "error": tech["error"],
                   "detail": f"signal={tech['out'].get('signal')} · strat={tech['out'].get('strategy')} · {tech['out'].get('params_source')}"})
    agents.append({"name": "News", "ok": news["ok"], "latency_ms": news["latency_ms"], "error": news["error"],
                   "detail": f"{news['out'].get('sentiment')} · impact {news['out'].get('impact_score')} · {news['out'].get('news_count')} headlines"})
    agents.append({"name": "Macro", "ok": macro["ok"], "latency_ms": macro["latency_ms"], "error": macro["error"],
                   "detail": f"{macro['out'].get('market_regime')} · {macro['out'].get('benchmark')}"})
    agents.append({"name": "Risk", "ok": risk["ok"], "latency_ms": risk["latency_ms"], "error": risk["error"],
                   "detail": f"ATR {risk['out'].get('atr')} · stop {risk['out'].get('stop_loss_suggested')}"})

    # Horizon engine probe (CPU-bound → thread)
    import asyncio
    from ...learning.horizons import horizon_signal
    hs, hs_ms, hs_err = await _timed(asyncio.to_thread(horizon_signal, symbol, "1M"))
    agents.append({"name": "HorizonEngine", "ok": bool(hs and hs.get("ok")), "latency_ms": hs_ms,
                   "error": hs_err or (None if (hs and hs.get("ok")) else (hs or {}).get("reason")),
                   "detail": (f"{hs.get('strategy')} {hs.get('signal')} · OOS win {hs['metrics'].get('win_rate')} · {hs.get('validation')}"
                              if hs and hs.get("ok") else "—")})

    # RL policy + calibration state
    rl_count = 0
    try:
        from ai.rl.q_learning_agent import rl_learning_agent
        rl_count = len(rl_learning_agent.q)
        agents.append({"name": "RL policy", "ok": True, "latency_ms": 0, "error": None,
                       "detail": f"{rl_count} learned states"})
    except Exception as exc:
        agents.append({"name": "RL policy", "ok": False, "latency_ms": 0, "error": str(exc)[:120], "detail": "—"})

    cal_samples = 0
    try:
        from ...services.calibration import get_calibration
        cal = await get_calibration(db)
        cal_samples = cal.get("samples", 0)
        agents.append({"name": "Calibration", "ok": True, "latency_ms": 0, "error": None,
                       "detail": f"{cal_samples} graded · base {cal.get('base_rate')}"})
    except Exception as exc:
        agents.append({"name": "Calibration", "ok": False, "latency_ms": 0, "error": str(exc)[:120], "detail": "—"})

    cov = bar_store.coverage_summary()
    from ...learning.tune import load_tuned_params
    tuned = load_tuned_params()

    return {
        "agents": agents,
        "data": {
            "quote_source": (quote or {}).get("source") or "yahoo",
            "quote_latency_ms": q_ms, "quote_error": q_err,
            "coverage_symbols": cov.get("symbols"), "total_bars": cov.get("total_bars"),
            "probe_symbol": symbol,
        },
        "learning": {
            "trained_symbols": tuned.get("n_symbols"),
            "trained_at": tuned.get("trained_at"),
            "interval": tuned.get("interval"), "lookback_days": tuned.get("lookback_days"),
            "strategy_wins": tuned.get("strategy_wins"),
            "calibration_samples": cal_samples,
            "rl_states": rl_count,
        },
        "ok": all(a["ok"] for a in agents),
    }


@router.get("/performance/rl-policy")
async def rl_policy():
    """What the self-learning policy has learned: per market state, the running
    win/loss value and the confidence multiplier it now applies. Empty until
    horizon recs mature and feed it."""
    try:
        from ai.rl.q_learning_agent import rl_learning_agent
        rows = rl_learning_agent.policy_snapshot()
        return {"states": rows, "count": len(rows)}
    except Exception as exc:
        return {"states": [], "count": 0, "error": str(exc)}


@router.post("/performance/grade-now")
async def grade_now(db: AsyncSession = Depends(get_db)):
    """Force-run the grader (bypasses 60s rate limit). Useful for the UI's
    'recompute' button so users don't wait for the next natural poll."""
    n = await outcome_tracker.grade_pending_outcomes(db, force=True)
    return {"newly_graded": n}


# ---- Risk-limits endpoints --------------------------------------------------------------

class UpdateLimitsPayload(BaseModel):
    per_trade_max_inr: Optional[float] = Field(None, ge=0)
    daily_max_loss_inr: Optional[float] = Field(None, ge=0)
    daily_max_trades: Optional[int] = Field(None, ge=0, le=1000)
    kill_switch: Optional[bool] = None


@router.get("/risk/limits")
async def get_risk_limits(db: AsyncSession = Depends(get_db)):
    """Current risk gates + today's usage (trade count, realized P&L)."""
    return await risk_limits.get_limits(db)


@router.post("/risk/limits")
async def update_risk_limits(payload: UpdateLimitsPayload, db: AsyncSession = Depends(get_db)):
    """Update any subset of the risk gates. Pass `kill_switch: true` to halt
    all live trading immediately (paper trading is unaffected)."""
    return await risk_limits.update_limits(
        db,
        per_trade_max_inr=payload.per_trade_max_inr,
        daily_max_loss_inr=payload.daily_max_loss_inr,
        daily_max_trades=payload.daily_max_trades,
        kill_switch=payload.kill_switch,
    )


@router.post("/risk/kill")
async def kill_switch_on(db: AsyncSession = Depends(get_db)):
    """One-click panic button — engages the kill switch."""
    return await risk_limits.update_limits(db, kill_switch=True)


@router.post("/risk/resume")
async def kill_switch_off(db: AsyncSession = Depends(get_db)):
    """Disengage kill switch — live trading allowed again (subject to other limits)."""
    return await risk_limits.update_limits(db, kill_switch=False)
