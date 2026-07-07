import asyncio
import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ..agents.orchestrator import decision_agent
from .market_data import market_data_service

log = logging.getLogger(__name__)


class AIService:
    async def get_trade_recommendation(self, symbol: str, db: Optional[AsyncSession] = None,
                                       horizon: Optional[str] = None):
        """Fetch a real quote + intraday history, then run the agent ensemble.

        When `horizon` is given (1M/3M/6M/1Y/SW) the horizon engine runs a backtest
        on that timeframe's stored bars (off the event loop) and the decision agent
        uses it for direction, levels, and a horizon-matched track record."""
        if db is not None:
            quote = await market_data_service.get_quote_routed(symbol, db)
            intraday = await market_data_service.get_intraday_routed(symbol, db)
        else:
            quote = await market_data_service.get_quote(symbol)
            try:
                intraday = await market_data_service.get_intraday(symbol)
            except Exception:
                intraday = {"series": []}

        hs = None
        if horizon:
            from ..learning.horizons import horizon_signal
            # CPU-bound backtest tournament — run in a thread so it never blocks the loop.
            hs = await asyncio.to_thread(horizon_signal, symbol, horizon)

        # Size against the user's real connected-broker capital when available.
        capital = None
        calibration = None
        if db is not None:
            try:
                from .broker_service import broker_service
                capital = await broker_service.available_capital(db)
            except Exception:
                capital = None
            try:
                from .calibration import get_calibration
                calibration = await get_calibration(db)
            except Exception:
                calibration = None

        return await decision_agent.generate_recommendation(
            symbol, quote, intraday, horizon=horizon, horizon_signal=hs,
            capital=capital, calibration=calibration,
        )


ai_service = AIService()
