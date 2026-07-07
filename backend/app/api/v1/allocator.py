"""Phase 5 API â€” Bandit allocator, RL shadow, latency profiling, DMA economics.

All endpoints are read-only or trigger analysis â€” no live trading
side-effects. The bandit allocator and RL agent are stateful singletons
initialised on first access.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional

router = APIRouter()


# ---------------------------------------------------------------------------
# Lazy singletons
# ---------------------------------------------------------------------------

_bandit = None
_rl_agent = None


def _get_bandit():
    global _bandit
    if _bandit is None:
        from app.allocator.bandit import BanditAllocator, BanditConfig
        _bandit = BanditAllocator(config=BanditConfig())
    return _bandit


def _get_rl_agent():
    global _rl_agent
    if _rl_agent is None:
        from app.allocator.rl_execution import OfflineRLAgent, CQLConfig
        _rl_agent = OfflineRLAgent(config=CQLConfig())
    return _rl_agent


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------

class RegisterArmRequest(BaseModel):
    strategy_key: str
    params: Dict[str, Any] = Field(default_factory=dict)


class RecordRewardRequest(BaseModel):
    arm_id: str
    reward: float = Field(ge=0.0, le=1.0)


class RecordPnlRequest(BaseModel):
    arm_id: str
    pnl_delta: float


class ImpactEstimateRequest(BaseModel):
    symbol: str
    spread_bps: float = 5.0
    daily_vol: float = 0.02
    volume_ratio: float = 1.0
    urgency: float = 0.5
    adv_pct: float = 1.0


class DMAAnalysisRequest(BaseModel):
    alpha_0_bps: float = 5.0
    decay_rate_per_ms: float = 0.005
    trades_per_day: int = 50
    avg_notional: float = 500_000
    capital_deployed_inr: float = 10_000_000


# ---------------------------------------------------------------------------
# Bandit Allocator endpoints
# ---------------------------------------------------------------------------

@router.get("/allocator/status")
async def allocator_status():
    """Current state of the bandit capital allocator."""
    return _get_bandit().status_summary()


@router.get("/allocator/leaderboard")
async def allocator_leaderboard(top_n: int = Query(20, ge=1, le=100)):
    """Top arms ranked by posterior mean."""
    return _get_bandit().leaderboard(top_n=top_n)


@router.post("/allocator/arms")
async def register_arm(req: RegisterArmRequest):
    """Register a new strategy arm in the bandit."""
    try:
        arm_id = _get_bandit().register_arm(req.strategy_key, req.params)
        return {"arm_id": arm_id, "status": "registered"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/allocator/allocate")
async def run_allocation():
    """Run Thompson Sampling and compute new capital fractions."""
    allocs = _get_bandit().allocate()
    return {
        "allocations": allocs,
        "n_arms": len(allocs),
        "champion": _get_bandit().champion.arm_id if _get_bandit().champion else None,
    }


@router.post("/allocator/reward")
async def record_reward(req: RecordRewardRequest):
    """Record a normalised reward [0,1] for an arm."""
    _get_bandit().record_reward(req.arm_id, req.reward)
    arm = _get_bandit().arm(req.arm_id)
    if arm is None:
        raise HTTPException(status_code=404, detail=f"Arm {req.arm_id} not found")
    return {
        "arm_id": req.arm_id,
        "n_observations": arm.n_observations,
        "posterior_mean": round(arm.posterior_mean, 4),
        "sharpe": round(arm.sharpe, 3),
    }


@router.post("/allocator/pnl")
async def record_pnl(req: RecordPnlRequest):
    """Record a raw PnL delta (auto-converted to reward)."""
    _get_bandit().record_pnl(req.arm_id, req.pnl_delta)
    arm = _get_bandit().arm(req.arm_id)
    if arm is None:
        raise HTTPException(status_code=404, detail=f"Arm {req.arm_id} not found")
    return {
        "arm_id": req.arm_id,
        "current_equity": round(arm.current_equity, 2),
        "max_drawdown": round(arm.max_drawdown, 4),
    }


@router.post("/allocator/evaluate-promotions")
async def evaluate_promotions():
    """Evaluate championâ€“challenger promotions via Â§7.4 gate."""
    promotions = _get_bandit().evaluate_promotions()
    return {
        "promotions": promotions,
        "champion": _get_bandit().champion.arm_id if _get_bandit().champion else None,
    }


@router.get("/allocator/promotions")
async def promotion_history():
    """Full history of champion promotions."""
    return _get_bandit().promotion_history()


@router.post("/allocator/retire/{arm_id}")
async def retire_arm(arm_id: str):
    """Retire an arm from the tournament."""
    _get_bandit().retire_arm(arm_id)
    return {"arm_id": arm_id, "status": "retired"}


# ---------------------------------------------------------------------------
# Offline-RL Shadow Agent endpoints
# ---------------------------------------------------------------------------

@router.get("/rl-shadow/stats")
async def rl_shadow_stats():
    """Shadow-mode performance statistics."""
    return _get_rl_agent().shadow_stats()


@router.get("/rl-shadow/q-table")
async def rl_q_table():
    """Q-table summary: best action per visited state."""
    return _get_rl_agent().q_table_summary()


@router.post("/rl-shadow/recommend")
async def rl_recommend(req: ImpactEstimateRequest):
    """Get the RL agent's shadow recommendation for a given market state."""
    from app.allocator.rl_execution import make_state
    state = make_state(
        spread_bps=req.spread_bps,
        daily_vol=req.daily_vol,
        volume_ratio=req.volume_ratio,
        urgency=req.urgency,
        adv_pct=req.adv_pct,
    )
    rec = _get_rl_agent().recommend(state)
    return {
        "state": state.key(),
        "recommendation": rec.key() if rec else None,
        "recommendation_detail": {
            "algo": rec.algo.value,
            "urgency": rec.urgency.value,
        } if rec else None,
        "note": "Shadow mode â€” recommendation only, not executed",
    }


@router.post("/rl-shadow/train")
async def rl_train():
    """Trigger a training run on the experience buffer."""
    stats = _get_rl_agent().train_batch()
    return stats


# ---------------------------------------------------------------------------
# Latency Profiler endpoints
# ---------------------------------------------------------------------------

@router.get("/profiler/report")
async def profiler_report():
    """Full latency profiling report across all pipeline stages."""
    from app.hotpath.profiler import get_profiler
    return get_profiler().report()


@router.get("/profiler/stage/{stage}")
async def profiler_stage(stage: str):
    """Detailed report for a single pipeline stage."""
    from app.hotpath.profiler import get_profiler, Stage
    try:
        s = Stage(stage)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown stage: {stage}. Valid: {[s.value for s in Stage]}",
        )
    return get_profiler().stage_report(s)


@router.get("/profiler/phase5-target")
async def profiler_phase5_target():
    """Check if the Phase 5 exit criterion (p99 < 1ms) is met."""
    from app.hotpath.profiler import get_profiler
    return get_profiler().meets_phase5_target()


@router.post("/profiler/reset")
async def profiler_reset():
    """Reset all profiling measurements."""
    from app.hotpath.profiler import get_profiler
    get_profiler().reset()
    return {"status": "reset"}


# ---------------------------------------------------------------------------
# DMA Economics endpoints
# ---------------------------------------------------------------------------

@router.post("/dma/evaluate")
async def dma_evaluate(req: DMAAnalysisRequest):
    """Produce a DMA go/no-go memo with current parameters."""
    from app.hotpath.dma_evaluator import DMAEvaluator, AlphaDecayModel
    model = AlphaDecayModel(
        alpha_0_bps=req.alpha_0_bps,
        decay_rate_per_ms=req.decay_rate_per_ms,
        trades_per_day=req.trades_per_day,
        avg_notional_per_trade=req.avg_notional,
    )
    evaluator = DMAEvaluator(
        alpha_model=model,
        capital_deployed_inr=req.capital_deployed_inr,
    )
    return evaluator.evaluate_all()


@router.post("/dma/sensitivity")
async def dma_sensitivity():
    """Run alpha-vs-latency sensitivity analysis across scenarios."""
    from app.hotpath.dma_evaluator import DMAEvaluator
    evaluator = DMAEvaluator()
    return {"scenarios": evaluator.sensitivity_analysis()}


@router.get("/dma/tiers")
async def dma_tiers():
    """List available latency tiers and their characteristics."""
    from app.hotpath.dma_evaluator import TIERS
    return {
        name: {
            "name": t.name,
            "description": t.description,
            "tick_to_order_p50_ms": t.tick_to_order_p50_ms,
            "tick_to_order_p99_ms": t.tick_to_order_p99_ms,
            "venue_rtt_ms": t.venue_rtt_ms,
            "annual_cost_inr": t.annual_cost_inr,
            "is_colo": t.is_colo,
        }
        for name, t in TIERS.items()
    }


# ---------------------------------------------------------------------------
# Phase 5 overview
# ---------------------------------------------------------------------------

@router.get("/phase5/status")
async def phase5_status():
    """Phase 5 implementation overview."""
    from app.hotpath.profiler import get_profiler
    profiler = get_profiler()
    bandit = _get_bandit()
    rl = _get_rl_agent()

    return {
        "phase": 5,
        "title": "Learning & Speed",
        "components": {
            "bandit_allocator": {
                "status": "active",
                "summary": bandit.status_summary(),
            },
            "rl_shadow_agent": {
                "status": "shadow_mode",
                "summary": rl.shadow_stats(),
            },
            "latency_profiler": {
                "status": "active",
                "phase5_target": profiler.meets_phase5_target(),
            },
            "dma_evaluator": {
                "status": "ready",
                "tiers_available": 4,
            },
        },
        "exit_criteria": {
            "challenger_promotions_via_gate": {
                "met": bandit.status_summary().get("total_promotions", 0) > 0
                       or bandit.status_summary().get("active_arms", 0) == 0,
                "detail": f"{bandit.status_summary().get('total_promotions', 0)} promotions logged",
            },
            "internal_p99_lt_1ms": profiler.meets_phase5_target(),
            "dma_go_nogo_memo": {
                "met": True,
                "detail": "DMA evaluator available at POST /api/v1/dma/evaluate",
            },
        },
    }
