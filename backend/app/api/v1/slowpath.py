"""Slow-path REST API — triggers analysis and manages agent lifecycle.

Endpoints:
    POST /api/v1/slowpath/analyze     — run persona analysts on a symbol
    GET  /api/v1/slowpath/agents      — list agents with metrics
    GET  /api/v1/slowpath/dashboard   — governance dashboard summary
    POST /api/v1/slowpath/agents/{id}/pause   — pause an agent
    POST /api/v1/slowpath/agents/{id}/resume  — resume an agent
    POST /api/v1/slowpath/agents/{id}/reset   — reset agent metrics
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.slowpath.orchestrator import slowpath
from app.slowpath.macro_regime import classify_macro_regime, _MACRO_SIZING
from app.services.macro_data import macro_data
from app.services.finnhub_provider import finnhub
from app.services import openfigi_symbols

router = APIRouter()


class AnalyzeRequest(BaseModel):
    symbol: str
    headline: Optional[str] = None
    personas: Optional[List[str]] = None
    include_openbb: bool = True


class AgentActionRequest(BaseModel):
    reason: Optional[str] = "manual"


@router.post("/analyze")
async def analyze_symbol(req: AnalyzeRequest):
    """Run slow-path persona analysts on a symbol.

    Fetches enriched context from OpenBB (if available), runs selected
    persona analysts through the governor, and returns structured
    assessments with any ParameterChangeProposals.
    """
    if not req.symbol or not req.symbol.strip():
        raise HTTPException(status_code=400, detail="Symbol is required")

    result = await slowpath.analyze(
        symbol=req.symbol.strip().upper(),
        headline=req.headline,
        personas=req.personas,
        include_openbb=req.include_openbb,
    )
    return result


@router.get("/agents")
async def list_agents(status: Optional[str] = None):
    """List all registered slow-path agents with their current metrics."""
    try:
        agents = slowpath.list_agents(status=status)
        return {"agents": agents, "count": len(agents)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/dashboard")
async def governance_dashboard():
    """Full governance dashboard: agent counts, metrics, provider info."""
    return slowpath.agent_dashboard()


@router.post("/agents/{agent_id}/pause")
async def pause_agent(agent_id: str, req: Optional[AgentActionRequest] = None):
    """Pause a slow-path agent (stops it from being invoked)."""
    reason = req.reason if req else "manual"
    if slowpath.pause_agent(agent_id, reason=reason):
        return {"status": "paused", "agent_id": agent_id, "reason": reason}
    raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found or already terminated")


@router.post("/agents/{agent_id}/resume")
async def resume_agent(agent_id: str):
    """Resume a paused slow-path agent."""
    if slowpath.resume_agent(agent_id):
        return {"status": "resumed", "agent_id": agent_id}
    raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found or terminated")


@router.post("/agents/{agent_id}/reset")
async def reset_agent_metrics(agent_id: str):
    """Reset metrics counters for a slow-path agent."""
    if slowpath.reset_agent_metrics(agent_id):
        return {"status": "reset", "agent_id": agent_id}
    raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")


# ── Public-API enrichment (slow path only, off the deterministic fast path) ──

@router.get("/macro")
async def macro_snapshot():
    """Live macro-regime snapshot: US Treasury yield curve (no key required)
    plus FRED VIX (when ETB_FRED_API_KEY is set). Reports the regime the
    MacroRegimeAnalyst would classify and the TIGHTENING it would propose. Never
    loosens, never emits an order — read-only advisory."""
    point = await macro_data.latest_yield_curve()
    spread = point.spread_10y_2y if point else None
    vix = await macro_data.latest_value("VIXCLS")  # None when no FRED key
    regime = classify_macro_regime(spread, vix)
    factor = _MACRO_SIZING.get(regime) if regime else None
    return {
        "yield_curve": (
            {"date": point.date, "y2": point.y2, "y10": point.y10,
             "spread_10y_2y": spread, "inverted": point.inverted}
            if point else None
        ),
        "vix": vix,
        "fred_enabled": macro_data.fred_enabled,
        "macro_regime": regime,
        "would_tighten_gross_to_pct": round(factor * 100, 1) if factor else None,
        "note": "read-only; tighten-only advisory. Treasury needs no key.",
    }


@router.get("/symbology/{ticker}")
async def symbology(ticker: str, exch: str = "IN"):
    """Resolve a ticker to its broker-neutral OpenFIGI id (works keyless).
    ``exch`` narrows the exchange: IN (NSE/BSE), US (NYSE/NASDAQ)."""
    ref = await openfigi_symbols.map_symbol(ticker, exch_code=exch)
    if ref is None:
        raise HTTPException(status_code=404,
                            detail=f"No FIGI mapping for '{ticker}' on '{exch}'")
    return {
        "ticker": ticker.upper(), "exch": exch.upper(),
        "figi": ref.figi, "name": ref.name, "figi_ticker": ref.ticker,
        "exch_code": ref.exch_code, "security_type": ref.security_type,
        "market_sector": ref.market_sector,
    }


@router.get("/macro/service/status")
async def macro_service_status():
    """Status of the always-on macro regime service: effective vs baseline
    risk limits, current regime, polls, and proposals published."""
    from app.engine.macro_regime_service import macro_regime_service
    return macro_regime_service.status


@router.post("/macro/service/start")
async def macro_service_start():
    """Start the background macro regime loop (opt-in; makes network calls).
    Auto-publishes tightening proposals to its parameter controller."""
    from app.engine.macro_regime_service import macro_regime_service
    macro_regime_service.start()
    return {"message": "Macro regime service started", "running": True,
            "poll_interval_s": macro_regime_service._poll_interval}


@router.post("/macro/service/stop")
async def macro_service_stop():
    """Stop the background macro regime loop."""
    from app.engine.macro_regime_service import macro_regime_service
    macro_regime_service.stop()
    return {"message": "Macro regime service stopped", "running": False}


@router.post("/macro/service/poll")
async def macro_service_poll():
    """Run ONE macro poll now (propose-on-stress, tick TTL, apply) and return
    the resulting status. Lets you drive/observe the pipeline without waiting
    for the interval."""
    from app.engine.macro_regime_service import macro_regime_service
    return await macro_regime_service.poll_once()


@router.get("/enrichment/status")
async def enrichment_status():
    """Which public-API enrichment sources are live (keys configured)."""
    return {
        "treasury_yield_curve": True,          # always on, no key
        "fred": macro_data.fred_enabled,
        "finnhub": finnhub.enabled,
        "openfigi": "keyless-ok (key raises rate limit)",
    }
