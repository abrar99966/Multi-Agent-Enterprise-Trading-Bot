from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.session import get_db
from ...schemas.broker import BrokerConnectRequest
from ...services.broker_adapters import list_specs
from ...services.broker_service import broker_service


class RefreshTokenRequest(BaseModel):
    access_token: str = Field(..., min_length=10, max_length=4096)

router = APIRouter()


@router.get("/supported")
async def supported_brokers():
    """Catalog of brokers the bot knows how to talk to."""
    return {"brokers": list_specs()}


class UpstoxProbeRequest(BaseModel):
    api_key: str = Field(..., min_length=4)
    access_token: str = Field(..., min_length=10)


@router.post("/upstox/probe")
async def upstox_probe(payload: UpstoxProbeRequest):
    """Diagnostic: probe an Upstox token against BOTH endpoints (prod + sandbox).

    Tells you exactly which URL accepts the token and what error each one
    returns. Useful when /connect fails — narrows down whether the token is
    valid at all, valid for sandbox only, valid for prod only, or dead.
    """
    from ...services.broker_adapters import BrokerCreds, _UpstoxAdapter, SPECS
    adapter = _UpstoxAdapter(SPECS["upstox"])
    creds = BrokerCreds(api_key=payload.api_key, access_token=payload.access_token)

    results = {}
    for sandbox in (False, True):
        label = "sandbox" if sandbox else "production"
        url = "https://api-sandbox.upstox.com" if sandbox else "https://api.upstox.com"
        try:
            profile = await adapter._try_profile(creds, sandbox=sandbox)
            data = getattr(profile, "data", None)
            if hasattr(data, "to_dict"):
                data = data.to_dict()
            user_id = data.get("user_id") if isinstance(data, dict) else None
            results[label] = {"ok": True, "url": url, "user_id": user_id}
        except Exception as exc:
            results[label] = {"ok": False, "url": url, "error": adapter._extract_upstox_error(exc)}

    # Pick a recommendation
    if results["production"]["ok"]:
        recommended = {"is_paper": False, "url": results["production"]["url"]}
    elif results["sandbox"]["ok"]:
        recommended = {"is_paper": True, "url": results["sandbox"]["url"]}
    else:
        recommended = None

    return {
        "results": results,
        "recommendation": recommended,
        "next_step": (
            f"Set is_paper={recommended['is_paper']} when connecting." if recommended
            else "Token is dead on both endpoints — regenerate from your Upstox developer console."
        ),
    }


@router.get("/accounts")
async def list_accounts(db: AsyncSession = Depends(get_db)):
    return {"accounts": await broker_service.list_accounts(db)}


@router.post("/connect")
async def connect_account(payload: BrokerConnectRequest, db: AsyncSession = Depends(get_db)):
    try:
        result = await broker_service.connect(
            db,
            broker_name=payload.broker_name,
            api_key=payload.api_key,
            api_secret=payload.api_secret,
            access_token=payload.access_token,
            account_id=payload.account_id,
            label=payload.label,
            is_paper=payload.is_paper,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["error"])
    return result["account"]


@router.post("/accounts/{account_id}/refresh")
async def refresh_account(account_id: int, db: AsyncSession = Depends(get_db)):
    acc = await broker_service.refresh(db, account_id)
    if acc is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return acc


@router.post("/accounts/{account_id}/refresh-token")
async def refresh_access_token(account_id: int, payload: RefreshTokenRequest,
                               db: AsyncSession = Depends(get_db)):
    """Rotate just the access_token for an existing account — no need to disconnect."""
    result = await broker_service.refresh_token(db, account_id, payload.access_token)
    if result is None:
        raise HTTPException(status_code=404, detail="Account not found")
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["error"])
    return result["account"]


@router.delete("/accounts/{account_id}")
async def disconnect_account(account_id: int, db: AsyncSession = Depends(get_db)):
    ok = await broker_service.disconnect(db, account_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Account not found")
    return {"status": "disconnected", "account_id": account_id}
