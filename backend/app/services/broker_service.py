"""Broker account management — CRUD + connection lifecycle.

Credentials are encrypted at rest via Fernet. The encryption key comes from
the BROKER_ENC_KEY env var; if absent, a process-local key is derived so the
dev experience is friction-free. **Set BROKER_ENC_KEY in production** — a
new derived key on restart would render previously-stored credentials
unrecoverable.
"""
from __future__ import annotations

import base64
import hashlib
import os
from datetime import datetime, timedelta
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.database import BrokerAccount, BrokerStatus
from .broker_adapters import BrokerCreds, get_adapter


def _invalidate_market_cache():
    """Tell the market-data router to drop its cached broker list.

    Imported lazily to avoid a circular import: market_providers imports from
    this module to decrypt creds.
    """
    try:
        from . import market_providers
        market_providers.invalidate_broker_cache()
    except Exception:
        pass


def _derive_dev_key() -> bytes:
    raw = os.getenv("BROKER_ENC_KEY")
    if raw:
        # Accept either a Fernet key (urlsafe base64) or any passphrase
        try:
            Fernet(raw.encode())
            return raw.encode()
        except (ValueError, InvalidToken):
            digest = hashlib.sha256(raw.encode()).digest()
            return base64.urlsafe_b64encode(digest)
    # Local dev fallback — stable across restarts of the same process tree
    digest = hashlib.sha256(b"trading-bot-local-dev-key-do-not-use-in-prod").digest()
    return base64.urlsafe_b64encode(digest)


_FERNET = Fernet(_derive_dev_key())


def _enc(plain: Optional[str]) -> Optional[str]:
    if plain is None or plain == "":
        return None
    return _FERNET.encrypt(plain.encode()).decode()


def _dec(token: Optional[str]) -> Optional[str]:
    if not token:
        return None
    try:
        return _FERNET.decrypt(token.encode()).decode()
    except InvalidToken:
        return None


def _mask(plain: Optional[str]) -> str:
    if not plain:
        return ""
    if len(plain) <= 6:
        return "•" * len(plain)
    return f"{plain[:3]}{'•' * (len(plain) - 6)}{plain[-3:]}"


def _compute_token_expiry(broker_name: str, now: Optional[datetime] = None) -> Optional[datetime]:
    """Best-effort expiry for the broker's access_token.

    Every Indian SEBI-regulated broker (Dhan, Zerodha, Upstox, Angel One)
    expires personal access tokens at 06:00 IST daily — a regulatory
    requirement, not a quirk we can engineer around. We compute the next
    deadline so the UI can show a countdown and prompt for re-auth.
    """
    if broker_name not in {"dhan", "zerodha", "upstox", "angelone", "icici_breeze"}:
        return None
    now = now or datetime.utcnow()
    # IST = UTC + 5:30. 06:00 IST == 00:30 UTC.
    cutoff_utc_hour = 0
    cutoff_utc_minute = 30
    today_cutoff = now.replace(hour=cutoff_utc_hour, minute=cutoff_utc_minute, second=0, microsecond=0)
    if now < today_cutoff:
        return today_cutoff
    return today_cutoff + timedelta(days=1)


def _to_dict(acc: BrokerAccount, api_key_plain: Optional[str] = None) -> dict:
    expires_at = acc.token_expires_at
    seconds_remaining = None
    is_expired = False
    if expires_at:
        delta = (expires_at - datetime.utcnow()).total_seconds()
        seconds_remaining = max(0, int(delta))
        is_expired = delta <= 0

    # Reflect token expiry in the displayed status even if the row still
    # says CONNECTED — the bot shouldn't act on a stale broker session.
    raw_status = acc.status.value if acc.status else "disconnected"
    display_status = "expired" if is_expired and raw_status == "connected" else raw_status

    return {
        "id": acc.id,
        "broker_name": acc.broker_name,
        "label": acc.label,
        "account_id": acc.account_id,
        "status": display_status,
        "is_paper": acc.is_paper,
        "balance": acc.balance,
        "equity": acc.equity,
        "margin_available": acc.margin_available,
        "currency": acc.currency,
        "last_synced_at": acc.last_synced_at.isoformat() if acc.last_synced_at else None,
        "last_error": acc.last_error,
        "api_key_masked": _mask(api_key_plain or _dec(acc.api_key_enc)),
        "token_issued_at": acc.token_issued_at.isoformat() if acc.token_issued_at else None,
        "token_expires_at": expires_at.isoformat() if expires_at else None,
        "token_seconds_remaining": seconds_remaining,
        "token_expired": is_expired,
        "created_at": acc.created_at.isoformat() if acc.created_at else None,
    }


class BrokerService:
    async def list_accounts(self, db: AsyncSession, user_id: int = 1) -> list:
        res = await db.execute(
            select(BrokerAccount).where(BrokerAccount.user_id == user_id).order_by(BrokerAccount.created_at.desc())
        )
        return [_to_dict(a) for a in res.scalars().all()]

    async def available_capital(self, db: AsyncSession, user_id: int = 1) -> Optional[float]:
        """Total deployable capital across CONNECTED brokers (equity, else balance).

        Sizes positions against the user's REAL account instead of a mock figure.
        Returns None when no broker is connected (caller keeps its default)."""
        res = await db.execute(
            select(BrokerAccount).where(
                BrokerAccount.user_id == user_id,
                BrokerAccount.status == BrokerStatus.CONNECTED,
            )
        )
        total = 0.0
        for a in res.scalars().all():
            total += float(a.equity or a.balance or 0)
        return total if total > 0 else None

    async def connect(
        self,
        db: AsyncSession,
        *,
        broker_name: str,
        api_key: str,
        api_secret: str,
        access_token: Optional[str] = None,
        account_id: Optional[str] = None,
        label: Optional[str] = None,
        is_paper: bool = True,
        user_id: int = 1,
    ) -> dict:
        adapter = get_adapter(broker_name)
        creds = BrokerCreds(api_key=api_key.strip(), api_secret=api_secret.strip(),
                            access_token=(access_token or "").strip() or None,
                            account_id=(account_id or "").strip() or None,
                            is_paper=bool(is_paper))
        result = await adapter.test_connection(creds)
        if not result.ok:
            return {"ok": False, "error": result.error or "Connection failed"}

        now = datetime.utcnow()
        acc = BrokerAccount(
            user_id=user_id,
            broker_name=broker_name,
            label=label or adapter.spec.name,
            account_id=result.account_id,
            api_key_enc=_enc(creds.api_key) or "",
            api_secret_enc=_enc(creds.api_secret) or "",
            access_token_enc=_enc(creds.access_token),
            status=BrokerStatus.CONNECTED,
            is_paper=is_paper,
            balance=result.balance,
            equity=result.equity,
            margin_available=result.margin_available,
            currency=result.currency,
            last_synced_at=now,
            token_issued_at=now,
            token_expires_at=_compute_token_expiry(broker_name, now),
        )
        db.add(acc)
        await db.commit()
        await db.refresh(acc)
        _invalidate_market_cache()
        return {"ok": True, "account": _to_dict(acc, api_key_plain=creds.api_key)}

    async def refresh(self, db: AsyncSession, account_id: int, user_id: int = 1) -> Optional[dict]:
        acc = await self._get(db, account_id, user_id)
        if not acc:
            return None
        creds = BrokerCreds(
            api_key=_dec(acc.api_key_enc) or "",
            api_secret=_dec(acc.api_secret_enc) or "",
            access_token=_dec(acc.access_token_enc),
            account_id=acc.account_id,
            is_paper=bool(acc.is_paper),
        )
        adapter = get_adapter(acc.broker_name)
        result = await adapter.fetch_balance(creds)
        if result.ok:
            acc.status = BrokerStatus.CONNECTED
            acc.balance = result.balance
            acc.equity = result.equity
            acc.margin_available = result.margin_available
            acc.currency = result.currency
            acc.last_synced_at = datetime.utcnow()
            acc.last_error = None
        else:
            acc.status = BrokerStatus.ERROR
            acc.last_error = result.error
        await db.commit()
        await db.refresh(acc)
        _invalidate_market_cache()
        return _to_dict(acc)

    async def refresh_token(self, db: AsyncSession, account_id: int, new_access_token: str,
                            user_id: int = 1) -> Optional[dict]:
        """Replace just the access_token for an existing broker connection.

        Validates the new token by hitting the broker's account endpoint
        before persisting. On failure the old token is kept untouched.
        """
        acc = await self._get(db, account_id, user_id)
        if not acc:
            return None
        new_access_token = (new_access_token or "").strip()
        if not new_access_token:
            return {"ok": False, "error": "access_token is required"}

        adapter = get_adapter(acc.broker_name)
        creds = BrokerCreds(
            api_key=_dec(acc.api_key_enc) or "",
            api_secret=_dec(acc.api_secret_enc) or "",
            access_token=new_access_token,
            account_id=acc.account_id,
            is_paper=bool(acc.is_paper),
        )
        result = await adapter.test_connection(creds)
        if not result.ok:
            return {"ok": False, "error": result.error or "Token rejected"}

        now = datetime.utcnow()
        acc.access_token_enc = _enc(new_access_token) or ""
        acc.status = BrokerStatus.CONNECTED
        acc.balance = result.balance
        acc.equity = result.equity
        acc.margin_available = result.margin_available
        acc.currency = result.currency
        acc.last_synced_at = now
        acc.token_issued_at = now
        acc.token_expires_at = _compute_token_expiry(acc.broker_name, now)
        acc.last_error = None
        await db.commit()
        await db.refresh(acc)
        _invalidate_market_cache()
        return {"ok": True, "account": _to_dict(acc)}

    async def disconnect(self, db: AsyncSession, account_id: int, user_id: int = 1) -> bool:
        acc = await self._get(db, account_id, user_id)
        if not acc:
            return False
        await db.delete(acc)
        await db.commit()
        _invalidate_market_cache()
        return True

    async def _get(self, db: AsyncSession, account_id: int, user_id: int) -> Optional[BrokerAccount]:
        res = await db.execute(
            select(BrokerAccount).where(BrokerAccount.id == account_id, BrokerAccount.user_id == user_id)
        )
        return res.scalar_one_or_none()


broker_service = BrokerService()
