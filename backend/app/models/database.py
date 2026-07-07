from datetime import datetime
from typing import List, Optional, Dict, Any
from sqlalchemy import Column, String, Float, DateTime, JSON, ForeignKey, Enum, Integer
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
import enum

class Base(DeclarativeBase):
    pass

class TradeStatus(enum.Enum):
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"
    CANCELLED = "cancelled"
    CLOSED = "closed"

class TradeSide(enum.Enum):
    BUY = "buy"
    SELL = "sell"

class MarketType(enum.Enum):
    EQUITY = "equity"
    F_O = "f_o"
    COMMODITY = "commodity"
    CRYPTO = "crypto"
    FOREX = "forex"

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

class BrokerStatus(enum.Enum):
    DISCONNECTED = "disconnected"
    CONNECTED = "connected"
    ERROR = "error"
    EXPIRED = "expired"

class BrokerAccount(Base):
    __tablename__ = "broker_accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    broker_name: Mapped[str] = mapped_column(String(50), index=True)  # e.g. "zerodha", "ibkr"
    label: Mapped[Optional[str]] = mapped_column(String(80))           # user-supplied nickname
    account_id: Mapped[Optional[str]] = mapped_column(String(80))      # broker-side client id
    # Credentials are stored encrypted (Fernet) — never returned to the client.
    api_key_enc: Mapped[str] = mapped_column(String(512))
    api_secret_enc: Mapped[str] = mapped_column(String(512))
    access_token_enc: Mapped[Optional[str]] = mapped_column(String(2048))
    status: Mapped[BrokerStatus] = mapped_column(Enum(BrokerStatus), default=BrokerStatus.DISCONNECTED)
    is_paper: Mapped[bool] = mapped_column(default=True)
    balance: Mapped[float] = mapped_column(Float, default=0.0)
    equity: Mapped[float] = mapped_column(Float, default=0.0)
    margin_available: Mapped[float] = mapped_column(Float, default=0.0)
    currency: Mapped[str] = mapped_column(String(10), default="INR")
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_error: Mapped[Optional[str]] = mapped_column(String(500))
    token_issued_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    token_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

class TradeRecommendation(Base):
    __tablename__ = "trade_recommendations"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    market: Mapped[MarketType] = mapped_column(Enum(MarketType))
    side: Mapped[TradeSide] = mapped_column(Enum(TradeSide))
    entry_price: Mapped[float] = mapped_column(Float)
    target_price: Mapped[float] = mapped_column(Float)
    stop_loss: Mapped[float] = mapped_column(Float)
    quantity: Mapped[int] = mapped_column(Integer)
    confidence_score: Mapped[float] = mapped_column(Float) # 0 to 1
    risk_reward_ratio: Mapped[float] = mapped_column(Float)
    
    # AI Reasoning
    reasoning: Mapped[str] = mapped_column(String(2000))
    agent_outputs: Mapped[Dict[str, Any]] = mapped_column(JSON) # Detailed output from each agent
    
    status: Mapped[TradeStatus] = mapped_column(Enum(TradeStatus), default=TradeStatus.PENDING_APPROVAL)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)

    # Outcome grading — populated by outcome_tracker after the signal's
    # prediction window passes. None until graded; True/False after.
    graded_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    price_after_1h: Mapped[Optional[float]] = mapped_column(Float)
    price_after_24h: Mapped[Optional[float]] = mapped_column(Float)
    signal_correct_1h: Mapped[Optional[bool]] = mapped_column()
    signal_correct_24h: Mapped[Optional[bool]] = mapped_column()
    actual_move_pct_1h: Mapped[Optional[float]] = mapped_column(Float)
    actual_move_pct_24h: Mapped[Optional[float]] = mapped_column(Float)

    # Horizon (investment-period) closed-loop grading — graded once the chosen
    # horizon's window passes, giving a REAL per-horizon hit rate over time.
    horizon: Mapped[Optional[str]] = mapped_column(String(8))
    horizon_due_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    horizon_correct: Mapped[Optional[bool]] = mapped_column()
    horizon_move_pct: Mapped[Optional[float]] = mapped_column(Float)
    graded_horizon_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

class Trade(Base):
    __tablename__ = "trades"
    id: Mapped[int] = mapped_column(primary_key=True)
    recommendation_id: Mapped[Optional[int]] = mapped_column(ForeignKey("trade_recommendations.id"), nullable=True)
    broker_account_id: Mapped[Optional[int]] = mapped_column(ForeignKey("broker_accounts.id"), nullable=True)
    broker_name: Mapped[Optional[str]] = mapped_column(String(50))
    broker_order_id: Mapped[str] = mapped_column(String(100), unique=True)
    symbol: Mapped[str] = mapped_column(String(20))
    side: Mapped[Optional[str]] = mapped_column(String(10))          # BUY / SELL
    quantity: Mapped[int] = mapped_column(Integer)
    order_type: Mapped[Optional[str]] = mapped_column(String(20))    # MARKET / LIMIT / SL / SL_M
    product: Mapped[Optional[str]] = mapped_column(String(20))       # MIS / CNC / NRML
    placed_price: Mapped[Optional[float]] = mapped_column(Float)
    executed_price: Mapped[Optional[float]] = mapped_column(Float)
    executed_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    pnl: Mapped[Optional[float]] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20))                  # PLACED / OPEN / COMPLETE / REJECTED / SIMULATED
    is_paper: Mapped[bool] = mapped_column(default=False)
    last_error: Mapped[Optional[str]] = mapped_column(String(500))

class MarketRegime(Base):
    __tablename__ = "market_regimes"
    id: Mapped[int] = mapped_column(primary_key=True)
    market: Mapped[str] = mapped_column(String(20)) # e.g., "NIFTY50", "SPY"
    regime_type: Mapped[str] = mapped_column(String(20)) # e.g., "BULLISH", "BEARISH", "SIDEWAYS"
    volatility_state: Mapped[str] = mapped_column(String(20))
    detected_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class RiskLimits(Base):
    """Hard guards on live order placement. Single-row table (user_id PK).

    These are NOT soft suggestions — they're checked in trade_service before
    every live order. Breaching daily_max_loss_inr disables further trading for
    the calendar day. Kill-switch disables ALL live trading until manually
    re-enabled.
    """
    __tablename__ = "risk_limits"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    # Per-trade limit: max ₹ committed in a single position
    per_trade_max_inr: Mapped[float] = mapped_column(Float, default=10_000.0)
    # Daily limits
    daily_max_loss_inr: Mapped[float] = mapped_column(Float, default=2_000.0)
    daily_max_trades: Mapped[int] = mapped_column(Integer, default=10)
    # Master kill switch — when True, NO live orders go out regardless of other settings
    kill_switch: Mapped[bool] = mapped_column(default=False)
    # Honest reporting metadata
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    # Day-state cache (reset at midnight IST by the risk service)
    today_realized_pnl_inr: Mapped[float] = mapped_column(Float, default=0.0)
    today_trade_count: Mapped[int] = mapped_column(Integer, default=0)
    today_reset_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
