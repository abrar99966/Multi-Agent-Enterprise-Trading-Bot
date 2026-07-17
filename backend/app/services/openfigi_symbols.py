"""OpenFIGI symbology resolver -- cross-broker instrument identity.

Different brokers key the same instrument differently (Dhan by numeric
security_id, Zerodha/Upstox by their own tokens, IBKR by conid). OpenFIGI maps a
ticker to Bloomberg's open FIGI identifier -- a broker-neutral anchor -- plus the
canonical name, exchange code, and security type. Resolving every symbol to its
FIGI first removes the cross-broker symbol-skew class of bugs.

Contract (matches services/dhan_symbols.py + openbb_adapter.py):
  * Product-surface / slow-path helper -- NOT on the deterministic fast path.
  * Works keyless (OpenFIGI allows ~25 mapping requests/min unauthenticated); a
    free key (``ETB_OPENFIGI_API_KEY``) raises the limit. No key is never an error.
  * Degrades gracefully: any failure returns None and never raises. Results are
    cached in-process so repeat lookups are O(1) and don't spend rate budget.

The response parsing is a pure function (``parse_mapping_response``) so it is
unit-testable offline with a fixture, no network.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Dict, List, Optional

import httpx

from app.core.config import get_settings

log = logging.getLogger(__name__)

_MAPPING_URL = "https://api.openfigi.com/v3/mapping"


@dataclass(frozen=True)
class FigiRef:
    figi: str            # the FIGI id -- broker-neutral instrument anchor
    name: str            # canonical instrument name
    ticker: str          # exchange ticker as OpenFIGI knows it
    exch_code: str       # micro/exchange code (e.g. "IN", "US")
    security_type: str   # e.g. "Common Stock", "Index"
    market_sector: str   # e.g. "Equity"


# Process-wide cache: "<TICKER>|<EXCH>" -> FigiRef (or None cached as a miss).
_CACHE: Dict[str, Optional[FigiRef]] = {}
_CACHE_LOCK = threading.RLock()


def parse_mapping_response(item: dict) -> Optional[FigiRef]:
    """Turn one OpenFIGI mapping result block into a FigiRef (first match).

    Pure function. OpenFIGI returns ``{"data": [...]}`` on success or
    ``{"error": "..."}`` / ``{"data": []}`` on a miss. We take the first datum.
    """
    if not isinstance(item, dict):
        return None
    data = item.get("data")
    if not data:  # error, empty, or malformed -> a miss
        return None
    d = data[0]
    figi = (d.get("figi") or "").strip()
    if not figi:
        return None
    return FigiRef(
        figi=figi,
        name=(d.get("name") or "").strip(),
        ticker=(d.get("ticker") or "").strip(),
        exch_code=(d.get("exchCode") or "").strip(),
        security_type=(d.get("securityType") or "").strip(),
        market_sector=(d.get("marketSecDes") or "").strip(),
    )


def _headers() -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    key = get_settings().openfigi_api_key.strip()
    if key:
        headers["X-OPENFIGI-APIKEY"] = key
    return headers


async def map_symbol(
    ticker: str, exch_code: str = "IN", timeout_s: float = 15.0
) -> Optional[FigiRef]:
    """Resolve a ticker to its FIGI. ``exch_code`` narrows the exchange
    ("IN" NSE/BSE, "US" NYSE/NASDAQ). Returns None on miss/failure/offline."""
    t = ticker.upper().strip()
    # Strip the region suffixes our other resolvers accept, so callers can pass
    # "RELIANCE", "RELIANCE.NS", "RELIANCE-EQ" and get the same FIGI.
    for suffix in (".NS", ".BO", "-EQ"):
        if t.endswith(suffix):
            t = t[: -len(suffix)]
    ck = f"{t}|{exch_code.upper()}"
    with _CACHE_LOCK:
        if ck in _CACHE:
            return _CACHE[ck]

    body = [{"idType": "TICKER", "idValue": t, "exchCode": exch_code.upper()}]
    ref: Optional[FigiRef] = None
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(_MAPPING_URL, json=body, headers=_headers())
            resp.raise_for_status()
            payload = resp.json()
            if isinstance(payload, list) and payload:
                ref = parse_mapping_response(payload[0])
    except Exception as exc:  # rate-limit, network, parse -> graceful miss
        log.debug("OpenFIGI map_symbol(%s) failed: %s", ticker, exc)
        return None  # don't cache transient failures as permanent misses

    with _CACHE_LOCK:
        _CACHE[ck] = ref
    return ref


def clear_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()
