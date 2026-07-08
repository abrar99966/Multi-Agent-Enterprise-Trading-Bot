"""Paper trading API endpoints.

Exposes the continuous paper trading service as REST endpoints:
  GET  /api/v1/paper-trading/status    — current status & rankings
  POST /api/v1/paper-trading/start     — start the background loop
  POST /api/v1/paper-trading/stop      — stop the background loop
"""
from __future__ import annotations

from fastapi import APIRouter
from typing import Any, Dict

router = APIRouter()


def _service():
    from app.engine.paper_trading_service import paper_trading_service
    return paper_trading_service


@router.get("/paper-trading/status")
async def paper_trading_status() -> Dict[str, Any]:
    """Get current paper trading status, strategy rankings, and recent results."""
    return _service().status


@router.post("/paper-trading/start")
async def paper_trading_start() -> Dict[str, Any]:
    """Start the continuous paper trading background loop."""
    svc = _service()
    svc.start()
    return {"message": "Paper trading started", "running": True}


@router.post("/paper-trading/stop")
async def paper_trading_stop() -> Dict[str, Any]:
    """Stop the continuous paper trading background loop."""
    svc = _service()
    svc.stop()
    return {"message": "Paper trading stopped", "running": False}
