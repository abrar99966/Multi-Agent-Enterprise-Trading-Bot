"""Market-data provider router.

Routing rules (in order):
  1. Indian symbol + connected, live, market-data-capable broker (Dhan) → broker provider
  2. (Future) US symbol + connected Alpaca → broker provider
  3. Yahoo Finance fallback (15-min delayed for NSE, real-time-ish for US)

The active broker is queried per request from the broker_accounts table; a
30-second in-memory TTL keeps the DB out of the hot path during heavy polling.
"""
from __future__ import annotations

import logging
import time
from typing import List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.database import BrokerAccount, BrokerStatus
from .broker_adapters import BrokerCreds, get_adapter
from .broker_service import _dec
from . import dhan_symbols

log = logging.getLogger(__name__)

# Symbol → region inference. Anything resolvable via Dhan's index is "IN".
INDIAN_INDEX_KEYWORDS = {"NIFTY", "BANKNIFTY", "SENSEX", "BANKEX", "FINNIFTY", "NIFTY50"}


async def _looks_indian_async(symbol: str) -> bool:
    """Async version — ensures the Dhan symbol index is loaded before deciding.

    Without this, the first router call sees an empty index, returns False for
    Indian equities like RELIANCE, and routes them to Yahoo. With this, the
    index is loaded lazily on the first lookup and the answer is correct from
    then on.
    """
    s = symbol.upper().strip()
    if s in INDIAN_INDEX_KEYWORDS:
        return True
    if s.endswith(".NS") or s.endswith(".BO") or s.endswith("-EQ"):
        return True
    # Cheap US heuristic: short ALL-CAP tickers commonly used on NASDAQ/NYSE
    # don't trigger an expensive CSV load.
    US_HINT = {"AAPL","MSFT","NVDA","TSLA","GOOG","GOOGL","AMZN","META","NFLX","AMD"}
    if s in US_HINT:
        return False
    ref = await dhan_symbols.resolve_async(s)
    return ref is not None


# ---- per-account creds cache (avoids re-decrypting per request) -----------------------

_BROKER_TTL = 30  # seconds
_broker_cache: Tuple[float, List[dict]] = (0.0, [])


async def _load_live_brokers(db: AsyncSession) -> List[dict]:
    """Return decrypted creds for every connected broker whose adapter is live
    AND can actually serve market data (probed once per cache cycle)."""
    global _broker_cache
    now = time.time()
    if now - _broker_cache[0] < _BROKER_TTL:
        return _broker_cache[1]

    res = await db.execute(
        select(BrokerAccount).where(BrokerAccount.status == BrokerStatus.CONNECTED)
    )
    out: List[dict] = []
    for acc in res.scalars().all():
        try:
            adapter = get_adapter(acc.broker_name)
        except ValueError:
            continue
        if not getattr(adapter.spec, "streams_market_data", False):
            continue
        creds = BrokerCreds(
            api_key=_dec(acc.api_key_enc) or "",
            api_secret=_dec(acc.api_secret_enc) or "",
            access_token=_dec(acc.access_token_enc),
            account_id=acc.account_id,
            is_paper=bool(acc.is_paper),
        )

        # Probe — does this account actually have the Data API plan?
        data_api_ok = True
        if hasattr(adapter, "probe_data_api"):
            try:
                data_api_ok = await adapter.probe_data_api(creds)
            except Exception:
                data_api_ok = False

        out.append({
            "broker_name": acc.broker_name,
            "region": adapter.spec.region,
            "adapter": adapter,
            "creds": creds,
            "account_id": acc.id,
            "data_api_enabled": data_api_ok,
        })
    _broker_cache = (now, out)
    return out


def invalidate_broker_cache():
    """Called from broker_service when accounts change."""
    global _broker_cache
    _broker_cache = (0.0, [])


# ---- public router --------------------------------------------------------------------

async def pick_provider_for(symbol: str, db: AsyncSession) -> Optional[dict]:
    """Return the active broker entry that should serve this symbol, or None for fallback.

    Brokers without an active Data API plan are skipped (they'd return silent
    failures and waste a network round-trip per quote).
    """
    brokers = await _load_live_brokers(db)
    if not brokers:
        return None
    is_in = await _looks_indian_async(symbol)
    for b in brokers:
        if not b.get("data_api_enabled", True):
            continue
        if is_in and b["region"] == "IN":
            return b
        if not is_in and b["region"] in ("US", "GLOBAL"):
            return b
    return None


async def list_active_providers(db: AsyncSession) -> List[dict]:
    """Used by the /providers endpoint to show the user where data is coming from."""
    brokers = await _load_live_brokers(db)
    return [
        {
            "broker_name": b["broker_name"],
            "region": b["region"],
            "spec_name": b["adapter"].spec.name,
            "data_api_enabled": b.get("data_api_enabled", True),
            "covers": ("Indian equities + indices" if b["region"] == "IN"
                       else "US / global equities" if b["region"] in ("US", "GLOBAL") else "—"),
        }
        for b in brokers
    ]
