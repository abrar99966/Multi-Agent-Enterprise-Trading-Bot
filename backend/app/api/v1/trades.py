import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.session import get_db
from ...schemas.trade import TradeApproval, TradeRecommendationCreate, TradeRecommendationRead
from ...services.ai_service import ai_service
from ...services.trade_service import trade_service

log = logging.getLogger(__name__)

router = APIRouter()


DEFAULT_UNIVERSE = ["RELIANCE", "INFY", "TCS", "HDFCBANK", "NIFTY", "BANKNIFTY"]


class ApprovePayload(BaseModel):
    confirmed: bool = False              # UI must send true after preview
    adjusted_quantity: int | None = None
    notes: str | None = None


@router.get("/horizons")
async def list_horizons():
    """Investment horizons the user can request recommendations for (1M/3M/6M/1Y/swing)."""
    from ...learning.horizons import list_horizons as _lh
    return {"horizons": _lh()}


@router.get("/recommendations")
async def get_recommendations(
    db: AsyncSession = Depends(get_db),
    symbols: Optional[str] = Query(None, description="Comma-separated symbols to recommend on"),
    refresh: bool = Query(False, description="Force regenerate even if a recent rec exists"),
    horizon: Optional[str] = Query(None, description="Investment horizon: 1M, 3M, 6M, 1Y, SW. Omit for short-term."),
):
    """Active recommendations for the UI.

    With `horizon`, each rec's direction/levels/track-record come from a backtest
    on that horizon's own timeframe (1M/3M/6M = daily, 1Y = weekly), and validity
    scales to the horizon. Reuse is horizon-aware so switching horizon regenerates.
    """
    from ...learning.horizons import is_known_horizon
    if horizon and not is_known_horizon(horizon):
        raise HTTPException(status_code=400, detail=f"Unknown horizon '{horizon}'. Valid: 1M, 3M, 6M, 1Y, SW.")
    requested = [s.strip().upper() for s in (symbols or ",".join(DEFAULT_UNIVERSE)).split(",") if s.strip()]

    async def ensure(symbol: str):
        if not refresh:
            existing = await trade_service.find_recent_pending(db, symbol, horizon=horizon)
            if existing is not None:
                return
        try:
            rec_data = await ai_service.get_trade_recommendation(symbol, db=db, horizon=horizon)
            expiry_days = rec_data.get("expiry_days")
            await trade_service.create_recommendation(
                db, TradeRecommendationCreate(**rec_data), expiry_days=expiry_days,
            )
        except Exception as exc:
            log.warning("Skipping %s recommendation: %s", symbol, exc)

    # Run sequentially — same DB session can't be used concurrently
    for s in requested:
        await ensure(s)

    return await trade_service.list_active(db)


@router.get("/generate/{symbol}")
async def generate_recommendation(symbol: str):
    """Generate a live AI trade signal for a given symbol."""
    try:
        return await ai_service.get_trade_recommendation(symbol)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/analyze/{symbol}", response_model=TradeRecommendationRead)
async def analyze_and_recommend(symbol: str, db: AsyncSession = Depends(get_db)):
    """Trigger AI analysis for a symbol and persist a recommendation."""
    rec_data = await ai_service.get_trade_recommendation(symbol)
    rec = await trade_service.create_recommendation(db, TradeRecommendationCreate(**rec_data))
    return rec


@router.get("/{recommendation_id}/preview")
async def preview_order(recommendation_id: int, db: AsyncSession = Depends(get_db)):
    """Show what would be placed if the user approves — broker, qty, price, paper/live."""
    result = await trade_service.preview_order(db, recommendation_id)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "Preview unavailable")
    return result


@router.post("/{recommendation_id}/approve")
async def approve_trade(recommendation_id: int, payload: ApprovePayload | None = None,
                        db: AsyncSession = Depends(get_db)):
    """Approve a recommendation — sends a REAL order to the selected broker.

    The UI fetches /preview first to show the user exactly what's about to happen,
    then POSTs here with confirmed=true.
    """
    approval = TradeApproval(
        approved=True,
        notes=(payload.notes if payload else None) or "Approved via UI",
        adjusted_quantity=payload.adjusted_quantity if payload else None,
    )
    result = await trade_service.process_approval(db, recommendation_id, approval)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "Failed to place order")
    return result


@router.post("/{recommendation_id}/reject")
async def reject_trade(recommendation_id: int, db: AsyncSession = Depends(get_db)):
    approval = TradeApproval(approved=False, notes="Rejected via UI")
    result = await trade_service.process_approval(db, recommendation_id, approval)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "Failed to reject")
    return result


@router.get("/history")
async def get_trade_history(db: AsyncSession = Depends(get_db)):
    """Real orders placed through the bot — broker, status, fills."""
    return {"trades": await trade_service.get_trade_history(db)}
