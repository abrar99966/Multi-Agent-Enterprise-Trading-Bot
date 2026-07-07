"""Trade lifecycle — create recommendation → user approval → real broker order."""
import asyncio
import logging
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.database import (
    Trade, TradeRecommendation, TradeStatus,
)
from ..schemas.trade import TradeApproval, TradeRecommendationCreate
from .broker_adapters import OrderRequest
from .execution_router import pick_execution_broker
from .notification_service import notification_service
from .risk_limits import check_pre_trade, record_trade_placed

log = logging.getLogger(__name__)


# Per-symbol locks serialise recommendation creation so concurrent /recommendations
# polls can't race past the "is there a recent pending?" check and double-insert.
# Lazy-init dict — one Lock per symbol, kept for the process lifetime.
_symbol_locks: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def _build_order_request(rec: TradeRecommendation, override: Optional[dict] = None) -> OrderRequest:
    """Translate a recommendation row into a canonical OrderRequest.

    Default behaviour: LIMIT at entry_price, intraday product. The user can
    override via the approval payload (e.g. switch to MARKET or change qty).
    """
    override = override or {}
    return OrderRequest(
        symbol=rec.symbol,
        side=(rec.side.value if hasattr(rec.side, "value") else str(rec.side)).upper(),
        quantity=int(override.get("quantity") or rec.quantity),
        order_type=(override.get("order_type") or "LIMIT").upper(),
        product=(override.get("product") or "MIS").upper(),
        price=float(override.get("price") or rec.entry_price),
        validity="DAY",
        tag=f"AIBOT-{rec.id}"[:20],
    )


class TradeService:
    async def get_pending_recommendations(self, db: AsyncSession):
        result = await db.execute(
            select(TradeRecommendation).where(TradeRecommendation.status == TradeStatus.PENDING_APPROVAL)
        )
        return result.scalars().all()

    async def expire_stale(self, db: AsyncSession) -> int:
        """Mark recs whose expires_at has passed as CANCELLED so they stop showing."""
        now = datetime.utcnow()
        res = await db.execute(
            update(TradeRecommendation)
            .where(TradeRecommendation.status == TradeStatus.PENDING_APPROVAL,
                   TradeRecommendation.expires_at < now)
            .values(status=TradeStatus.CANCELLED)
        )
        await db.commit()
        return res.rowcount or 0

    async def list_active(self, db: AsyncSession, user_id: int = 1) -> List[dict]:
        """Currently-pending recommendations for the UI — newest first.

        Defensive dedup: even though `create_recommendation` cancels prior
        pending rows for the same symbol, race conditions in older code
        could have left orphan duplicates in the DB. Here we keep only the
        most recent pending row per symbol AND eagerly mark older
        duplicates as CANCELLED so the DB self-heals over time.
        """
        await self.expire_stale(db)
        result = await db.execute(
            select(TradeRecommendation)
            .where(TradeRecommendation.user_id == user_id,
                   TradeRecommendation.status == TradeStatus.PENDING_APPROVAL)
            .order_by(TradeRecommendation.created_at.desc())
            .limit(100)
        )
        rows = result.scalars().all()

        seen: set = set()
        kept: list = []
        stale_ids: list = []
        for r in rows:
            key = (r.symbol or "").upper()
            if key in seen:
                stale_ids.append(r.id)
            else:
                seen.add(key)
                kept.append(r)

        # Self-heal: cancel any duplicates we found so they don't show up next poll
        if stale_ids:
            await db.execute(
                update(TradeRecommendation)
                .where(TradeRecommendation.id.in_(stale_ids))
                .values(status=TradeStatus.CANCELLED)
            )
            await db.commit()
            log.info("Cancelled %d duplicate pending recommendations", len(stale_ids))

        return [self._rec_to_dict(r) for r in kept[:20]]

    async def find_recent_pending(self, db: AsyncSession, symbol: str, within_minutes: int = 30,
                                  user_id: int = 1, horizon: Optional[str] = None) -> Optional[TradeRecommendation]:
        """Avoid duplicates — return a recent pending rec for this symbol within the window.

        When `horizon` is given, only reuse a rec generated for the SAME horizon, so
        switching the horizon selector regenerates instead of showing a stale frame."""
        cutoff = datetime.utcnow() - timedelta(minutes=within_minutes)
        res = await db.execute(
            select(TradeRecommendation).where(
                TradeRecommendation.user_id == user_id,
                TradeRecommendation.symbol == symbol.upper(),
                TradeRecommendation.status == TradeStatus.PENDING_APPROVAL,
                TradeRecommendation.created_at >= cutoff,
            ).order_by(TradeRecommendation.created_at.desc()).limit(1)
        )
        rec = res.scalar_one_or_none()
        if rec is not None and horizon:
            stored = ((rec.agent_outputs or {}).get("rationale") or {}).get("horizon")
            if stored != horizon:
                return None   # different horizon requested — force regenerate
        return rec

    @staticmethod
    def _rec_to_dict(r: TradeRecommendation) -> dict:
        return {
            "id": r.id,
            "symbol": r.symbol,
            "market": r.market.value if hasattr(r.market, "value") else str(r.market),
            "side": r.side.value if hasattr(r.side, "value") else str(r.side),
            "entry_price": r.entry_price,
            "target_price": r.target_price,
            "stop_loss": r.stop_loss,
            "quantity": r.quantity,
            "confidence_score": r.confidence_score,
            "risk_reward_ratio": r.risk_reward_ratio,
            "reasoning": r.reasoning,
            "agent_outputs": r.agent_outputs,
            "status": r.status.value if hasattr(r.status, "value") else str(r.status),
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "expires_at": r.expires_at.isoformat() if r.expires_at else None,
        }

    async def create_recommendation(self, db: AsyncSession, rec_in: TradeRecommendationCreate,
                                    user_id: int = 1, expiry_days: Optional[int] = None):
        """Create a fresh PENDING recommendation. Cancels any prior PENDING recs
        for the same symbol+user so the UI never shows duplicates. `expiry_days`
        scales the validity window to the horizon (e.g. a 3-month call shouldn't
        expire in 4 hours); defaults to 4h for short-term recs."""
        symbol = (rec_in.symbol or "").upper()

        # Layer 1: per-symbol asyncio lock — serialises concurrent creations
        # for the same symbol within this process.
        async with _symbol_locks[symbol]:
            # Layer 2: cancel any prior PENDING recs for the same symbol so
            # only the newest one shows up in the UI's active list.
            await db.execute(
                update(TradeRecommendation)
                .where(
                    TradeRecommendation.user_id == user_id,
                    TradeRecommendation.symbol == symbol,
                    TradeRecommendation.status == TradeStatus.PENDING_APPROVAL,
                )
                .values(status=TradeStatus.CANCELLED)
            )

            now = datetime.utcnow()
            expires_at = now + (timedelta(days=expiry_days) if expiry_days else timedelta(hours=4))
            # Horizon (if any) is carried in agent_outputs.rationale — pull it out so
            # the closed-loop grader can score the call at the horizon's end.
            horizon = ((rec_in.agent_outputs or {}).get("rationale") or {}).get("horizon")
            db_rec = TradeRecommendation(
                **rec_in.model_dump(),
                user_id=user_id,
                status=TradeStatus.PENDING_APPROVAL,
                created_at=now,
                expires_at=expires_at,
                horizon=horizon,
                horizon_due_at=(expires_at if horizon else None),
            )
            db.add(db_rec)
            await db.commit()
            await db.refresh(db_rec)

        rec_dict = rec_in.model_dump()
        await notification_service.notify_new_recommendation(rec_dict)
        return db_rec

    async def preview_order(self, db: AsyncSession, recommendation_id: int) -> dict:
        """Show the user exactly what would be placed if they approve — before they click."""
        rec = await self._get(db, recommendation_id)
        if rec is None:
            return {"ok": False, "error": "Recommendation not found"}

        broker = await pick_execution_broker(rec.symbol, db)
        if broker is None:
            return {
                "ok": False,
                "error": "No connected broker can route this order. Connect a live broker (Dhan/Zerodha) on /brokers.",
                "recommendation_id": rec.id,
                "symbol": rec.symbol,
            }

        req = _build_order_request(rec)
        return {
            "ok": True,
            "recommendation_id": rec.id,
            "broker": broker["broker_name"],
            "broker_label": broker["spec_name"],
            "is_paper": broker["is_paper"],
            "order": {
                "symbol": req.symbol,
                "side": req.side,
                "quantity": req.quantity,
                "order_type": req.order_type,
                "product": req.product,
                "price": req.price,
                "validity": req.validity,
            },
            "estimated_cost": round((req.price or 0) * req.quantity, 2),
            "warning": "PAPER MODE — order will be simulated, no real money moves."
                       if broker["is_paper"] else
                       "LIVE — real order will be sent to the broker.",
        }

    async def process_approval(self, db: AsyncSession, recommendation_id: int, approval: TradeApproval):
        """Approve or reject. On approval, **actually places the order**."""
        rec = await self._get(db, recommendation_id)
        if rec is None:
            return {"ok": False, "error": "Recommendation not found"}

        if not approval.approved:
            rec.status = TradeStatus.REJECTED
            await db.commit()
            return {"ok": True, "status": "rejected", "recommendation_id": recommendation_id}

        # Pick the broker that will actually route the order
        broker = await pick_execution_broker(rec.symbol, db)
        if broker is None:
            rec.status = TradeStatus.REJECTED
            await db.commit()
            return {
                "ok": False,
                "error": "No live broker connected — cannot place real order. Connect Dhan or Zerodha on /brokers.",
                "recommendation_id": recommendation_id,
            }

        req = _build_order_request(rec, override={"quantity": approval.adjusted_quantity} if approval.adjusted_quantity else None)

        # ---- Pre-trade risk gate (LIVE orders only — paper orders bypass)
        order_value_inr = float(req.price or 0) * int(req.quantity or 0)
        gate = await check_pre_trade(db, order_value_inr=order_value_inr, is_paper=broker["is_paper"])
        if not gate.allowed:
            return {
                "ok": False,
                "error": f"Blocked by risk limits: {gate.reason}",
                "limits": gate.limits,
            }

        # Paper mode → simulate the order, no broker call
        if broker["is_paper"]:
            sim_order_id = f"SIM-{uuid.uuid4().hex[:12].upper()}"
            trade = Trade(
                recommendation_id=rec.id,
                broker_account_id=broker["account_id"],
                broker_name=broker["broker_name"],
                broker_order_id=sim_order_id,
                symbol=req.symbol, side=req.side, quantity=req.quantity,
                order_type=req.order_type, product=req.product,
                placed_price=req.price, executed_price=req.price,
                status="SIMULATED", is_paper=True,
            )
            db.add(trade)
            rec.status = TradeStatus.EXECUTED
            await db.commit()
            await db.refresh(trade)
            log.info("Simulated order %s for rec %d via %s", sim_order_id, rec.id, broker["broker_name"])
            return {
                "ok": True, "status": "simulated", "broker": broker["broker_name"],
                "order_id": sim_order_id, "trade_id": trade.id, "paper": True,
            }

        # LIVE — call the broker
        try:
            result = await broker["adapter"].place_order(broker["creds"], req)
        except Exception as exc:
            log.exception("Broker.place_order raised")
            rec.status = TradeStatus.REJECTED
            await db.commit()
            return {"ok": False, "error": f"Broker error: {exc}"}

        if not result.ok:
            rec.status = TradeStatus.REJECTED
            await db.commit()
            return {"ok": False, "error": result.error or "Broker rejected the order",
                    "broker": broker["broker_name"]}

        trade = Trade(
            recommendation_id=rec.id,
            broker_account_id=broker["account_id"],
            broker_name=broker["broker_name"],
            broker_order_id=result.order_id or "",
            symbol=req.symbol, side=req.side, quantity=req.quantity,
            order_type=req.order_type, product=req.product,
            placed_price=req.price, executed_price=None,
            status="PLACED", is_paper=False,
        )
        db.add(trade)
        rec.status = TradeStatus.EXECUTED
        await db.commit()
        await db.refresh(trade)
        # Increment today's live-trade counter for the daily cap
        await record_trade_placed(db, order_value_inr=order_value_inr, is_paper=False)
        log.info("LIVE order placed: %s via %s for rec %d", result.order_id, broker["broker_name"], rec.id)
        return {
            "ok": True, "status": "placed", "broker": broker["broker_name"],
            "order_id": result.order_id, "trade_id": trade.id, "paper": False,
        }

    async def get_trade_history(self, db: AsyncSession):
        result = await db.execute(select(Trade).order_by(Trade.executed_at.desc()).limit(100))
        trades = result.scalars().all()
        return [
            {
                "id": t.id,
                "symbol": t.symbol,
                "side": t.side,
                "quantity": t.quantity,
                "order_type": t.order_type,
                "product": t.product,
                "placed_price": t.placed_price,
                "executed_price": t.executed_price,
                "broker_name": t.broker_name,
                "broker_order_id": t.broker_order_id,
                "status": t.status,
                "is_paper": t.is_paper,
                "executed_at": t.executed_at.isoformat() if t.executed_at else None,
                "last_error": t.last_error,
            }
            for t in trades
        ]

    async def _get(self, db: AsyncSession, recommendation_id: int) -> Optional[TradeRecommendation]:
        res = await db.execute(
            select(TradeRecommendation).where(TradeRecommendation.id == recommendation_id)
        )
        return res.scalar_one_or_none()


trade_service = TradeService()
