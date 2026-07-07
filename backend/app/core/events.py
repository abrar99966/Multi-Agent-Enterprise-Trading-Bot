"""Canonical event schema.

Every fact in the platform -- market data, signals, risk verdicts,
orders, fills, control changes -- is an immutable Event appended to a
named stream. The journal (bus/journal.py) chains events with SHA-256
(core/hashing.py) so history is tamper-evident, and replaying journaled
market data through the same code must reproduce identical downstream
events.

Determinism contract for every event handler:
- no wall-clock reads: inject a Clock (core/clock.py); replay uses
  SimClock driven by event timestamps,
- no unseeded randomness, no uuid4,
- IDs derive from event content (e.g. strategy_id + symbol + bar ts).

Timestamps are integer nanoseconds UTC.
- ts_event:    when the fact was true in the world (bar close, fill time)
- ts_recorded: when this process appended it (SimClock time in replay)
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional, Type

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 1

NS_PER_SEC = 1_000_000_000


class Streams:
    """Well-known stream names. Hierarchical; subscribe supports 'md.*'."""

    MD_BARS = "md.bars"
    MD_TICKS = "md.ticks"
    SIGNAL_INTENTS = "signal.intents"
    RISK_VERDICTS = "risk.verdicts"
    EXEC_ORDERS = "exec.orders"
    EXEC_ORDER_UPDATES = "exec.order_updates"
    EXEC_FILLS = "exec.fills"
    OMS_POSITIONS = "oms.positions"
    CTL_PARAMS = "ctl.params"
    CTL_PARAM_PROPOSALS = "ctl.param_proposals"
    CTL_KILL = "ctl.kill"
    CTL_APPROVAL_REQUESTS = "ctl.approval_requests"
    CTL_APPROVAL_DECISIONS = "ctl.approval_decisions"


_PAYLOAD_TYPES: Dict[str, Type[BaseModel]] = {}


def register_payload(cls: Type[BaseModel]) -> Type[BaseModel]:
    """Register a payload model so Event.decode() can rehydrate it."""
    _PAYLOAD_TYPES[cls.__name__] = cls
    return cls


def payload_type(name: str) -> Type[BaseModel]:
    try:
        return _PAYLOAD_TYPES[name]
    except KeyError:
        raise KeyError(
            f"unknown payload type {name!r}; missing @register_payload?"
        ) from None


class Event(BaseModel):
    """Immutable envelope. seq is per-stream and assigned by the bus."""

    model_config = ConfigDict(frozen=True)

    stream: str
    seq: int
    ts_event: int
    ts_recorded: int
    type: str
    schema_version: int = SCHEMA_VERSION
    payload: Dict[str, Any]

    def decode(self) -> BaseModel:
        return payload_type(self.type).model_validate(self.payload)


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class OrderStatus(str, Enum):
    NEW = "NEW"
    ACKED = "ACKED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


@register_payload
class Bar(BaseModel):
    """OHLCV bar. Published at close: ts_event must be Bar.ts_close."""

    symbol: str
    ts_open: int
    interval_s: int = Field(gt=0)
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def ts_close(self) -> int:
        return self.ts_open + self.interval_s * NS_PER_SEC


@register_payload
class Tick(BaseModel):
    symbol: str
    ltp: float
    bid: Optional[float] = None
    ask: Optional[float] = None
    bid_qty: Optional[float] = None
    ask_qty: Optional[float] = None
    volume: Optional[float] = None


@register_payload
class OrderIntent(BaseModel):
    """What a strategy wants. Intents never reach a broker directly --
    only the risk gateway turns approved intents into Orders."""

    intent_id: str
    strategy_id: str
    model_id: str = "rules-v0"
    symbol: str
    side: Side
    qty: float = Field(gt=0)
    order_type: OrderType = OrderType.MARKET
    limit_price: Optional[float] = None
    ts_signal: int
    reason: str = ""
    attributions: Dict[str, float] = Field(default_factory=dict)


@register_payload
class RiskCheck(BaseModel):
    name: str
    passed: bool
    detail: str = ""


@register_payload
class RiskVerdict(BaseModel):
    """Full check list is always present, pass or fail (audit trail)."""

    intent_id: str
    approved: bool
    tier: int = 3
    checks: List[RiskCheck] = Field(default_factory=list)
    reject_reason: Optional[str] = None


@register_payload
class Order(BaseModel):
    """An approved order released by the risk gateway. Nothing else may
    publish to exec.orders."""

    order_id: str
    intent_id: str
    strategy_id: str
    symbol: str
    side: Side
    qty: float = Field(gt=0)
    order_type: OrderType
    limit_price: Optional[float] = None


@register_payload
class OrderUpdate(BaseModel):
    order_id: str
    status: OrderStatus
    detail: str = ""


@register_payload
class Fill(BaseModel):
    fill_id: str
    order_id: str
    intent_id: str
    strategy_id: str
    symbol: str
    side: Side
    qty: float = Field(gt=0)
    price: float
    fees: float = 0.0
    ts_fill: int


@register_payload
class PositionSnapshot(BaseModel):
    symbol: str
    qty: float  # signed: + long, - short
    avg_price: float
    realized_pnl: float
    ts: int


@register_payload
class ParameterChangeProposal(BaseModel):
    """The ONLY way the slow path (LLM analysts, regime classifier) may
    influence trading: a bounded request to change one controllable
    parameter. The ParameterController enforces bounds, direction asymmetry
    (tightening auto-applies, loosening is human-gated), rate limits, quorum,
    and TTL. Analysts can never emit an order or set a value directly."""

    proposal_id: str
    parameter: str
    proposed_value: float
    source: str
    ttl_s: Optional[int] = None
    rationale: str = ""
    evidence: List[str] = Field(default_factory=list)


@register_payload
class ParameterChange(BaseModel):
    """An APPLIED change to a controllable parameter's effective value,
    emitted by the ParameterController. The risk gateway consumes risk.*
    changes as effective-limit overrides."""

    parameter: str
    old_value: float
    new_value: float
    source: str
    ttl_s: Optional[int] = None
    rationale: str = ""


@register_payload
class ApprovalRequest(BaseModel):
    """Emitted when a risk-approved intent's autonomy tier exceeds the
    gateway's auto-release ceiling, so it must be approved before the order
    is released. The order is NOT created until an approving decision
    arrives (or the request expires)."""

    intent_id: str
    strategy_id: str
    symbol: str
    side: Side
    qty: float
    tier: int = Field(ge=1, le=3)
    reasons: List[str] = Field(default_factory=list)
    ts: int


@register_payload
class ApprovalDecision(BaseModel):
    intent_id: str
    approved: bool
    approver: str = ""
    ts: int = 0


@register_payload
class KillSwitch(BaseModel):
    """K1=halt scope strategy, K2=block all new orders, K3=de-risk,
    K4=platform dark. engaged=False lifts the switch at that scope."""

    level: int = Field(ge=1, le=4)
    engaged: bool
    scope: str = "*"  # '*' platform-wide, or a strategy_id for K1
    reason: str = ""
