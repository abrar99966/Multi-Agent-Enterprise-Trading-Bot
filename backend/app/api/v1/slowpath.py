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
