from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services.market_data import market_data_service
from app.services.market_providers import list_active_providers
from app.services.news_service import news_service

router = APIRouter()


@router.get("/providers")
async def providers(db: AsyncSession = Depends(get_db)):
    """Show which broker(s) are serving live market data right now."""
    active = await list_active_providers(db)
    serving = [p for p in active if p.get("data_api_enabled")]
    blocked = [p for p in active if not p.get("data_api_enabled")]
    return {
        "active": serving,
        "blocked": blocked,
        "fallback": "yahoo",
        "fallback_note": "Yahoo Finance â€” free, ~15-min delayed for NSE/BSE.",
        "blocked_note": (
            "Your broker is connected for trading but the Data API plan is not active â€” "
            "market quotes / OHLC / intraday bars are routing through Yahoo (delayed). "
            "Subscribe at web.dhan.co â†’ API Subscription, or switch to Upstox / Angel One (free data)."
            if blocked else None
        ),
    }


@router.get("/quotes/{symbol}")
async def get_quote(symbol: str, db: AsyncSession = Depends(get_db)):
    try:
        return await market_data_service.get_quote_routed(symbol, db)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/intraday/{symbol}")
async def get_intraday(symbol: str, db: AsyncSession = Depends(get_db),
                       range_: str = Query("1d", alias="range"), interval: str = Query("5m")):
    try:
        return await market_data_service.get_intraday_routed(symbol, db, range_=range_, interval=interval)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/watchlist")
async def watchlist(symbols: Optional[str] = Query(None), db: AsyncSession = Depends(get_db)):
    default = "NIFTY,BANKNIFTY,SENSEX,RELIANCE,INFY,TCS,HDFCBANK,AAPL,MSFT,NVDA"
    requested = [s.strip() for s in (symbols or default).split(",") if s.strip()]
    quotes = await market_data_service.get_quotes_batch_routed(requested, db)
    return {"quotes": quotes}


@router.get("/news/{symbol}")
async def get_news(symbol: str):
    news = await news_service.fetch_news(symbol)
    return {"symbol": symbol, "news": news}
