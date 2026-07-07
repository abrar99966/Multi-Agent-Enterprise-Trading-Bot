from pydantic import BaseModel
from datetime import datetime
from typing import List, Optional, Dict, Any
from ..models.database import TradeStatus, TradeSide, MarketType

class TradeRecommendationBase(BaseModel):
    symbol: str
    market: MarketType
    side: TradeSide
    entry_price: float
    target_price: float
    stop_loss: float
    quantity: int
    confidence_score: float
    risk_reward_ratio: float
    reasoning: str
    agent_outputs: Dict[str, Any]

class TradeRecommendationCreate(TradeRecommendationBase):
    pass

class TradeRecommendationRead(TradeRecommendationBase):
    id: int
    status: TradeStatus
    created_at: datetime
    expires_at: datetime

    class Config:
        from_attributes = True

class TradeApproval(BaseModel):
    approved: bool
    notes: Optional[str] = None
    adjusted_quantity: Optional[int] = None
