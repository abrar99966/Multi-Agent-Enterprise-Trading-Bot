"""Execution-broker router — SOR-backed (Phase 4).

Different from `market_providers.pick_provider_for` (which picks the broker
that *serves* data). This module picks the broker that *places orders*.

Phase 4 upgrade: integrated with the Smart Order Router (SOR) for
health-based scoring, circuit-breaker failover, and order splitting.

Rules (in order):
  1. The broker must be CONNECTED and have a real (`live=True`) adapter with
     a `place_order` method.
  2. Indian symbols → first live Indian broker (Dhan / Zerodha / Upstox).
     US symbols → first live US/global broker (IBKR / Alpaca when wired).
  3. SOR scores eligible brokers on health, latency, cost, fill rate, and
     recency — picking the highest-scoring candidate.
  4. If primary is unavailable, automatic failover to the next best broker.
  5. If the user marked the chosen broker as `is_paper=True`, the resulting
     OrderResult is **simulated** (no real order sent). UI surfaces this.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..execution.sor import SmartOrderRouter, BrokerHealth
from ..models.database import BrokerAccount, BrokerStatus
from .broker_adapters import BrokerCreds, get_adapter
from .broker_service import _dec
from . import dhan_symbols, upstox_symbols

log = logging.getLogger(__name__)

# Module-level SOR instance — shared across requests.
_sor = SmartOrderRouter()


async def _is_indian_symbol(symbol: str) -> bool:
    s = symbol.upper().strip()
    if s in {"NIFTY", "BANKNIFTY", "SENSEX", "BANKEX", "FINNIFTY", "NIFTY50"}:
        return True
    if s.endswith(".NS") or s.endswith(".BO") or s.endswith("-EQ"):
        return True
    # Cheap US-ticker skip so we don't pay a CSV load
    if s in {"AAPL","MSFT","NVDA","TSLA","GOOG","GOOGL","AMZN","META","NFLX","AMD"}:
        return False
    if (await dhan_symbols.resolve_async(s)) is not None:
        return True
    if (await upstox_symbols.resolve_async(s)) is not None:
        return True
    return False


def get_sor() -> SmartOrderRouter:
    """Return the module-level SOR instance for external registration/status."""
    return _sor


async def _sync_sor_from_db(db: AsyncSession) -> None:
    """Refresh the SOR's broker registry from the database.

    Called before each routing decision to ensure the SOR reflects
    current connection state.
    """
    res = await db.execute(select(BrokerAccount))
    all_accounts = res.scalars().all()

    for acc in all_accounts:
        try:
            adapter = get_adapter(acc.broker_name)
        except ValueError:
            continue

        is_connected = acc.status == BrokerStatus.CONNECTED
        is_live = getattr(adapter.spec, "live", False)
        has_data = getattr(adapter.spec, "streams_market_data", False)

        _sor.register_broker(
            slug=acc.broker_name,
            name=adapter.spec.name,
            region=adapter.spec.region,
            is_live=is_live and is_connected,
            is_connected=is_connected,
            supports_market_data=has_data,
        )

        # Set health based on connection status
        if is_connected and is_live:
            _sor.update_health(acc.broker_name, BrokerHealth.GREEN)
        elif is_connected:
            _sor.update_health(acc.broker_name, BrokerHealth.YELLOW)
        else:
            _sor.update_health(acc.broker_name, BrokerHealth.RED)


async def pick_execution_broker(
    symbol: str,
    db: AsyncSession,
    qty: float = 0.0,
    prefer_broker: Optional[str] = None,
) -> Optional[dict]:
    """Return the broker that should place this order, or None if no live broker can.

    Phase 4: uses the Smart Order Router for health-based scoring and failover.
    """
    # Sync SOR state from the database
    await _sync_sor_from_db(db)

    is_in = await _is_indian_symbol(symbol)
    target_region = "IN" if is_in else "GLOBAL"

    # Ask the SOR for the best route
    decision = _sor.route(
        symbol=symbol,
        target_region=target_region,
        qty=qty,
        prefer_broker=prefer_broker,
    )

    if not decision.primary_broker:
        log.warning(
            "SOR: no broker available for %s (region=%s). Disqualified: %s",
            symbol, target_region, decision.disqualified,
        )
        return None

    # Resolve the primary broker account from DB
    primary_slug = decision.primary_broker
    res = await db.execute(
        select(BrokerAccount)
        .where(BrokerAccount.broker_name == primary_slug)
        .where(BrokerAccount.status == BrokerStatus.CONNECTED)
        .order_by(BrokerAccount.created_at.desc())
    )
    acc = res.scalars().first()
    if acc is None:
        # Fallback to backup broker
        if decision.backup_broker:
            res = await db.execute(
                select(BrokerAccount)
                .where(BrokerAccount.broker_name == decision.backup_broker)
                .where(BrokerAccount.status == BrokerStatus.CONNECTED)
                .order_by(BrokerAccount.created_at.desc())
            )
            acc = res.scalars().first()
            if acc:
                primary_slug = decision.backup_broker
                log.info(
                    "SOR: primary %s unavailable, failed over to %s",
                    decision.primary_broker, decision.backup_broker,
                )

    if acc is None:
        return None

    try:
        adapter = get_adapter(acc.broker_name)
    except ValueError:
        return None

    creds = BrokerCreds(
        api_key=_dec(acc.api_key_enc) or "",
        api_secret=_dec(acc.api_secret_enc) or "",
        access_token=_dec(acc.access_token_enc),
        account_id=acc.account_id,
        is_paper=bool(acc.is_paper),
    )
    return {
        "account_id": acc.id,
        "broker_name": acc.broker_name,
        "spec_name": adapter.spec.name,
        "adapter": adapter,
        "creds": creds,
        "is_paper": bool(acc.is_paper),
        "sor_decision": {
            "primary": decision.primary_broker,
            "backup": decision.backup_broker,
            "score": decision.score,
            "reason": decision.reason,
            "should_split": decision.should_split,
            "all_scores": decision.all_scores,
        },
    }


async def pick_execution_broker_with_failover(
    symbol: str,
    db: AsyncSession,
    qty: float = 0.0,
) -> List[dict]:
    """Return a prioritised list of brokers — primary + fallbacks.

    Used by the execution engine when it needs to retry on a different broker
    after a rejection or timeout.
    """
    await _sync_sor_from_db(db)

    is_in = await _is_indian_symbol(symbol)
    target_region = "IN" if is_in else "GLOBAL"

    decision = _sor.route(symbol=symbol, target_region=target_region, qty=qty)

    brokers_to_try = []
    for slug in [decision.primary_broker, decision.backup_broker]:
        if not slug:
            continue
        res = await db.execute(
            select(BrokerAccount)
            .where(BrokerAccount.broker_name == slug)
            .where(BrokerAccount.status == BrokerStatus.CONNECTED)
            .order_by(BrokerAccount.created_at.desc())
        )
        acc = res.scalars().first()
        if acc is None:
            continue
        try:
            adapter = get_adapter(acc.broker_name)
        except ValueError:
            continue

        creds = BrokerCreds(
            api_key=_dec(acc.api_key_enc) or "",
            api_secret=_dec(acc.api_secret_enc) or "",
            access_token=_dec(acc.access_token_enc),
            account_id=acc.account_id,
            is_paper=bool(acc.is_paper),
        )
        brokers_to_try.append({
            "account_id": acc.id,
            "broker_name": acc.broker_name,
            "spec_name": adapter.spec.name,
            "adapter": adapter,
            "creds": creds,
            "is_paper": bool(acc.is_paper),
            "sor_score": decision.all_scores.get(slug, 0),
        })

    return brokers_to_try


def record_execution_result(broker_slug: str, success: bool, latency_ms: float = 0.0, error: str = "") -> None:
    """Record an execution result to update the SOR's broker health metrics.

    Should be called after every order placement attempt.
    """
    if success:
        _sor.record_success(broker_slug, latency_ms)
    else:
        _sor.record_error(broker_slug, error)


def sor_status() -> Dict[str, Any]:
    """Return the SOR's current broker health and failover status."""
    return {
        "brokers": _sor.broker_status(),
        "failover": _sor.failover_status(),
    }
