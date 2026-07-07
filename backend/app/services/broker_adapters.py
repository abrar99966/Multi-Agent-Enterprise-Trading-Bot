"""Pluggable broker adapters.

Each adapter exposes the same async interface so the rest of the app does not
care which broker is on the other side:

    async def test_connection(creds) -> AdapterResult
    async def fetch_balance(creds)  -> AdapterResult

The shipped implementations are SANDBOX: they validate credential shape and
return deterministic synthetic balances. To wire a real broker, replace the
body of the corresponding `*Adapter.test_connection` / `fetch_balance` with
the broker SDK calls (kiteconnect, smartapi-python, alpaca-py, ib_insync,
python-binance, etc.). The router and UI do not need to change.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger(__name__)


@dataclass
class BrokerCreds:
    api_key: str
    api_secret: str = ""
    access_token: Optional[str] = None
    account_id: Optional[str] = None
    is_paper: bool = False    # For Upstox: routes to api-sandbox.upstox.com when True.
                              # For others: signals "simulate, don't place real orders".


@dataclass
class Quote:
    symbol: str
    name: str
    exchange: str
    currency: str
    current_price: float
    prev_close: float
    change: float
    change_pct: float
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    volume: Optional[int] = None
    timestamp: Optional[int] = None
    source: str = "unknown"

    def to_dict(self) -> dict:
        return {**self.__dict__}


@dataclass
class IntradayBar:
    t: int
    o: Optional[float]
    h: Optional[float]
    l: Optional[float]
    c: float
    v: Optional[float] = None


@dataclass
class OrderRequest:
    """Canonical order shape — adapters translate to their broker's vocabulary."""
    symbol: str
    side: str               # "BUY" | "SELL"
    quantity: int
    order_type: str = "MARKET"   # MARKET | LIMIT | SL | SL_M
    product: str = "MIS"         # MIS (intraday) | CNC (delivery) | NRML (carry-forward)
    price: Optional[float] = None
    trigger_price: Optional[float] = None
    validity: str = "DAY"
    tag: Optional[str] = None    # short note, surfaces in broker order history


@dataclass
class OrderResult:
    ok: bool
    order_id: Optional[str] = None
    broker: Optional[str] = None
    error: Optional[str] = None
    paper: bool = False         # True when we simulated (no real order placed)
    placed_price: Optional[float] = None
    placed_quantity: Optional[int] = None
    placed_side: Optional[str] = None
    extras: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AdapterResult:
    ok: bool
    account_id: Optional[str] = None
    balance: float = 0.0
    equity: float = 0.0
    margin_available: float = 0.0
    currency: str = "INR"
    error: Optional[str] = None
    extras: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BrokerField:
    """A single credential field rendered in the UI connect modal."""
    key: str            # form key submitted to /connect, stored in the corresponding *_enc column
    label: str          # UI label
    placeholder: str = ""
    secret: bool = False
    required: bool = True
    hint: str = ""


@dataclass
class BrokerSpec:
    """Static metadata describing a supported broker — surfaced to the UI."""
    slug: str
    name: str
    region: str          # "IN", "US", "GLOBAL"
    asset_classes: list  # ["equity", "f_o", "crypto", ...]
    auth_kind: str       # "api_key_secret" | "api_key_secret_token" | "key_only"
    docs_url: str
    notes: str
    paper_supported: bool = True
    requires_access_token: bool = False
    live: bool = False                # True when a real SDK adapter is wired
    streams_market_data: bool = False # True when this broker can serve quotes/intraday
    fields: list = field(default_factory=list)  # explicit field config for the UI

    @property
    def field_schema(self) -> list:
        """Default field schema derived from auth_kind if .fields is not overridden."""
        if self.fields:
            return self.fields
        out = [
            BrokerField(key="api_key", label="API Key"),
            BrokerField(key="api_secret", label="API Secret", secret=True),
        ]
        if self.requires_access_token:
            out.append(BrokerField(key="access_token", label="Access Token", secret=True,
                                   hint="Daily-expiring OAuth token from the broker's login redirect."))
        out.append(BrokerField(key="account_id", label="Client / Account ID", required=False))
        return out


SPECS: Dict[str, BrokerSpec] = {
    "dhan": BrokerSpec(
        slug="dhan", name="Dhan", region="IN",
        asset_classes=["equity", "f_o", "commodity", "currency"],
        auth_kind="client_id_token",
        docs_url="https://dhanhq.co/docs/v2/",
        notes="Generate access_token at web.dhan.co → My Profile → DhanHQ Trading APIs. Token is long-lived. Free with Dhan account.",
        live=True,
        streams_market_data=True,
        fields=[
            BrokerField(key="api_key", label="Client ID",
                        placeholder="1000000000",
                        hint="Your Dhan Client ID — top-right of web.dhan.co."),
            BrokerField(key="access_token", label="Access Token", secret=True,
                        hint="Long-lived JWT from My Profile → DhanHQ Trading APIs → Generate."),
        ],
    ),
    "zerodha": BrokerSpec(
        slug="zerodha", name="Zerodha Kite", region="IN",
        asset_classes=["equity", "f_o", "commodity", "currency"],
        auth_kind="api_key_secret_token",
        docs_url="https://kite.trade/docs/connect/v3/",
        notes="Kite Connect ₹500 one-time + ₹2000/month covers BOTH trading and real-time data. Token expires daily at 06:00 IST — regenerate via the OAuth flow at developers.kite.trade.",
        requires_access_token=True,
        live=True,
        streams_market_data=True,
        fields=[
            BrokerField(key="api_key", label="API Key",
                        placeholder="abc123…", hint="From developers.kite.trade → My apps."),
            BrokerField(key="api_secret", label="API Secret", secret=True,
                        hint="From the same app page. Used to exchange request_token for access_token."),
            BrokerField(key="access_token", label="Access Token", secret=True,
                        hint="Generate daily via the Kite Connect OAuth login flow. Expires 06:00 IST."),
        ],
    ),
    "upstox": BrokerSpec(
        slug="upstox", name="Upstox Pro", region="IN",
        asset_classes=["equity", "f_o", "commodity"],
        auth_kind="api_key_token",
        docs_url="https://upstox.com/developer/api-documentation",
        notes=(
            "Real-time market data is FREE with any Upstox account — no separate Data API subscription. "
            "IMPORTANT: If your token is from the Sandbox tab of the developer console, you MUST check "
            "'Paper Mode' below — that routes the API call to api-sandbox.upstox.com. Sandbox tokens "
            "sent to the production endpoint fail with UDAPI100050 (invalid token). "
            "Tokens expire daily at 06:00 IST (SEBI rule)."
        ),
        live=True,
        streams_market_data=True,
        fields=[
            BrokerField(key="api_key", label="API Key",
                        placeholder="Your Upstox API key",
                        hint="Optional — used as a label. Auth uses the access_token below."),
            BrokerField(key="access_token", label="Access Token", secret=True,
                        hint="Generate from upstox.com → Apps → your app → 'Generate' (Algo Trading tab for production, Sandbox tab for sandbox)."),
        ],
    ),
    "angelone": BrokerSpec(
        slug="angelone", name="Angel One SmartAPI", region="IN",
        asset_classes=["equity", "f_o", "commodity"],
        auth_kind="api_key_secret",
        docs_url="https://smartapi.angelbroking.com/docs",
        notes="Uses SmartAPI key + secret + TOTP login. Sandbox simulates the login handshake.",
    ),
    "icici_breeze": BrokerSpec(
        slug="icici_breeze", name="ICICI Direct Breeze", region="IN",
        asset_classes=["equity", "f_o"],
        auth_kind="api_key_secret_token",
        docs_url="https://api.icicidirect.com/breezeapi/documents/index.html",
        notes="Customer-side session token must be issued from Breeze portal.",
        requires_access_token=True,
    ),
    "ibkr": BrokerSpec(
        slug="ibkr", name="Interactive Brokers", region="GLOBAL",
        asset_classes=["equity", "f_o", "forex", "commodity"],
        auth_kind="gateway_connection",
        docs_url="https://www.interactivebrokers.com/en/trading/ib-api.php",
        notes=(
            "Requires IB Gateway or TWS running locally. Paper account uses port 4002, "
            "live uses port 4001. Install ib_insync: pip install ib_insync. "
            "No API key needed — authentication is via the Gateway login."
        ),
        live=True,
        streams_market_data=True,
        fields=[
            BrokerField(key="account_id", label="Account ID",
                        placeholder="DU1234567",
                        hint="Your IB account ID (DU-prefix for paper, U-prefix for live)."),
            BrokerField(key="api_key", label="Gateway Host:Port",
                        placeholder="127.0.0.1:4002", required=False,
                        hint="IB Gateway address. Default: 127.0.0.1:4002 (paper) or 4001 (live)."),
        ],
    ),
    "alpaca": BrokerSpec(
        slug="alpaca", name="Alpaca", region="US",
        asset_classes=["equity", "crypto"],
        auth_kind="api_key_secret",
        docs_url="https://alpaca.markets/docs/api-references/trading-api/",
        notes="Best-in-class paper trading. Toggle paper/live on the Alpaca dashboard.",
    ),
    "binance": BrokerSpec(
        slug="binance", name="Binance", region="GLOBAL",
        asset_classes=["crypto"],
        auth_kind="api_key_secret",
        docs_url="https://binance-docs.github.io/apidocs/spot/en/",
        notes="Use spot testnet keys (testnet.binance.vision) for sandbox mode.",
    ),
}


# ---- Sandbox implementation helpers --------------------------------------------------

_KEY_RX = re.compile(r"^[A-Za-z0-9_\-\.]{6,128}$")


def _validate_creds(creds: BrokerCreds, spec: BrokerSpec) -> Optional[str]:
    if not creds.api_key or not _KEY_RX.match(creds.api_key):
        return "api_key looks malformed (expected 6-128 alphanumeric / _ - . chars)"
    if not creds.api_secret or len(creds.api_secret) < 6:
        return "api_secret too short"
    if spec.requires_access_token and not creds.access_token:
        return f"{spec.name} requires an access_token (daily-expiring OAuth token)"
    return None


def _synthetic_balance(creds: BrokerCreds, spec: BrokerSpec) -> AdapterResult:
    """Deterministic synthetic balance so the UI feels alive without real APIs."""
    h = hashlib.sha256(f"{spec.slug}:{creds.api_key}".encode()).digest()
    bucket = int.from_bytes(h[:4], "big")
    base = 50_000 + (bucket % 9_500_000)        # 50k – 9.55M
    pnl_delta = ((bucket >> 4) % 200_000) - 100_000
    currency = "INR" if spec.region == "IN" else ("USD" if spec.region == "US" else "USD")
    return AdapterResult(
        ok=True,
        account_id=creds.account_id or f"SBX-{h.hex()[:8].upper()}",
        balance=round(base, 2),
        equity=round(base + pnl_delta, 2),
        margin_available=round(base * 0.78, 2),
        currency=currency,
        extras={"mode": "sandbox", "spec": spec.slug},
    )


class _SandboxAdapter:
    def __init__(self, spec: BrokerSpec):
        self.spec = spec

    async def test_connection(self, creds: BrokerCreds) -> AdapterResult:
        await asyncio.sleep(0.25)  # mimic network latency
        err = _validate_creds(creds, self.spec)
        if err:
            return AdapterResult(ok=False, error=err)
        return _synthetic_balance(creds, self.spec)

    async def fetch_balance(self, creds: BrokerCreds) -> AdapterResult:
        await asyncio.sleep(0.15)
        err = _validate_creds(creds, self.spec)
        if err:
            return AdapterResult(ok=False, error=err)
        return _synthetic_balance(creds, self.spec)


# ---- Real Dhan adapter ----------------------------------------------------------------

class _DhanAdapter:
    """Real Dhan adapter — backed by the `dhanhq` SDK.

    The SDK is sync. We wrap every network call in `asyncio.to_thread` so the
    FastAPI event loop stays free under polling load.
    """

    def __init__(self, spec: BrokerSpec):
        self.spec = spec
        self._client_cache: Dict[str, Any] = {}  # client_id+token -> dhanhq client

    def _client(self, creds: BrokerCreds):
        from dhanhq import DhanContext, dhanhq  # local import — keeps import-time cheap
        key = f"{creds.api_key}:{creds.access_token}"
        client = self._client_cache.get(key)
        if client is None:
            ctx = DhanContext(creds.api_key.strip(), (creds.access_token or "").strip())
            client = dhanhq(ctx)
            self._client_cache[key] = client
        return client

    def _validate(self, creds: BrokerCreds) -> Optional[str]:
        if not creds.api_key or not creds.api_key.strip().isdigit():
            return "client_id must be your numeric Dhan Client ID (e.g. 1000000000)"
        if not creds.access_token or len(creds.access_token.strip()) < 20:
            return "access_token looks malformed — paste the full JWT from My Profile → DhanHQ Trading APIs"
        return None

    # Dhan's silent signature for "you haven't subscribed to this API plan"
    # is status='failure' + all remarks fields null + data="".
    _SILENT_AUTH = {"error_code": None, "error_type": None, "error_message": None}

    @classmethod
    def _is_silent_unauthorized(cls, response) -> bool:
        if not isinstance(response, dict):
            return False
        if response.get("status") not in ("failure", "error"):
            return False
        remarks = response.get("remarks") or {}
        return remarks == cls._SILENT_AUTH and response.get("data") in ("", None, {})

    @staticmethod
    def _unwrap(response: dict) -> dict:
        """Dhan SDK returns {'status': 'success', 'data': {...}, 'remarks': {...}}."""
        if not isinstance(response, dict):
            return {}
        if response.get("status") in ("failure", "error"):
            return {}
        return response.get("data") or response or {}

    async def probe_data_api(self, creds: BrokerCreds) -> bool:
        """One cheap call to check if the Data API plan is enabled on this account.

        RELIANCE on NSE_EQ (security_id 2885) is hardcoded — we want to probe
        without paying the symbol-master CSV load cost.
        """
        try:
            client = self._client(creds)
            raw = await asyncio.to_thread(client.ticker_data, {"NSE_EQ": [2885]})
        except Exception:
            return False
        if self._is_silent_unauthorized(raw):
            return False
        return isinstance(raw, dict) and raw.get("status") == "success"

    async def test_connection(self, creds: BrokerCreds) -> AdapterResult:
        err = self._validate(creds)
        if err:
            return AdapterResult(ok=False, error=err)
        try:
            client = self._client(creds)
            raw = await asyncio.to_thread(client.get_fund_limits)
        except Exception as exc:
            return AdapterResult(ok=False, error=f"Dhan API error: {exc}")
        if not isinstance(raw, dict) or raw.get("status") in ("failure", "error"):
            remarks = (raw or {}).get("remarks") if isinstance(raw, dict) else None
            return AdapterResult(ok=False, error=f"Dhan rejected credentials: {remarks or raw}")
        data = self._unwrap(raw)
        return AdapterResult(
            ok=True,
            account_id=str(creds.api_key),
            balance=float(data.get("availabelBalance") or data.get("availableBalance") or 0),
            equity=float(data.get("collateralAmount") or 0) + float(data.get("availabelBalance") or data.get("availableBalance") or 0),
            margin_available=float(data.get("availabelBalance") or data.get("availableBalance") or 0),
            currency="INR",
            extras={"mode": "live", "spec": self.spec.slug, "raw": data},
        )

    async def fetch_balance(self, creds: BrokerCreds) -> AdapterResult:
        return await self.test_connection(creds)

    # ---- Market data ------------------------------------------------------------------

    async def get_quote(self, creds: BrokerCreds, symbol: str) -> Optional[Quote]:
        """Live LTP/OHLC for a single equity or index symbol."""
        from . import dhan_symbols
        ref = await dhan_symbols.resolve_async(symbol)
        if ref is None:
            return None
        try:
            client = self._client(creds)
            payload = await asyncio.to_thread(
                client.quote_data, {ref.exchange_segment: [int(ref.security_id)]}
            )
        except Exception as exc:
            log.warning("Dhan quote_data failed for %s: %s", symbol, exc)
            return None
        data = self._unwrap(payload)
        seg_block = (data.get("data") or {}).get(ref.exchange_segment) if isinstance(data.get("data"), dict) else None
        if seg_block is None:
            seg_block = data.get(ref.exchange_segment)
        if not seg_block:
            return None
        row = seg_block.get(ref.security_id) or seg_block.get(str(ref.security_id))
        if not row:
            return None
        ltp = float(row.get("last_price") or row.get("LTP") or 0)
        ohlc = row.get("ohlc") or {}
        prev_close = float(ohlc.get("close") or row.get("close_price") or ltp)
        change = ltp - prev_close
        change_pct = (change / prev_close * 100) if prev_close else 0.0
        return Quote(
            symbol=symbol.upper(),
            name=ref.name,
            exchange=ref.exchange_segment.split("_")[0],
            currency="INR",
            current_price=round(ltp, 2),
            prev_close=round(prev_close, 2),
            change=round(change, 2),
            change_pct=round(change_pct, 2),
            open=float(ohlc.get("open")) if ohlc.get("open") is not None else None,
            high=float(ohlc.get("high")) if ohlc.get("high") is not None else None,
            low=float(ohlc.get("low")) if ohlc.get("low") is not None else None,
            volume=int(row.get("volume")) if row.get("volume") is not None else None,
            timestamp=int(datetime.now(timezone.utc).timestamp()),
            source="dhan",
        )

    async def get_quotes_batch(self, creds: BrokerCreds, symbols: List[str]) -> Dict[str, Quote]:
        """One Dhan API call per exchange_segment — much faster than N parallel calls.

        Dhan's `quote_data` accepts {exchange_segment: [security_id, ...]} and
        returns a nested dict. The watchlist has 7-10 NSE symbols; pre-batching
        cuts the network round-trips from 7-10 → 1 (or 2 if a US/index slips in).
        """
        from . import dhan_symbols
        # Resolve all symbols (may trigger one CSV load on first call)
        refs = await asyncio.gather(*(dhan_symbols.resolve_async(s) for s in symbols))
        by_segment: Dict[str, List[int]] = {}
        sym_to_ref = {}
        for s, ref in zip(symbols, refs):
            if ref is None:
                continue
            sym_to_ref[s.upper()] = ref
            by_segment.setdefault(ref.exchange_segment, []).append(int(ref.security_id))

        if not by_segment:
            return {}

        try:
            client = self._client(creds)
            payload = await asyncio.to_thread(client.quote_data, by_segment)
        except Exception as exc:
            log.warning("Dhan batch quote_data failed: %s", exc)
            return {}

        data = self._unwrap(payload)
        seg_root = data.get("data") if isinstance(data.get("data"), dict) else data
        out: Dict[str, Quote] = {}
        now_ts = int(datetime.now(timezone.utc).timestamp())
        for sym, ref in sym_to_ref.items():
            seg_block = (seg_root or {}).get(ref.exchange_segment) or {}
            row = seg_block.get(ref.security_id) or seg_block.get(str(ref.security_id))
            if not row:
                continue
            ltp = float(row.get("last_price") or row.get("LTP") or 0)
            ohlc = row.get("ohlc") or {}
            prev_close = float(ohlc.get("close") or row.get("close_price") or ltp)
            change = ltp - prev_close
            change_pct = (change / prev_close * 100) if prev_close else 0.0
            out[sym] = Quote(
                symbol=sym,
                name=ref.name,
                exchange=ref.exchange_segment.split("_")[0],
                currency="INR",
                current_price=round(ltp, 2),
                prev_close=round(prev_close, 2),
                change=round(change, 2),
                change_pct=round(change_pct, 2),
                open=float(ohlc.get("open")) if ohlc.get("open") is not None else None,
                high=float(ohlc.get("high")) if ohlc.get("high") is not None else None,
                low=float(ohlc.get("low")) if ohlc.get("low") is not None else None,
                volume=int(row.get("volume")) if row.get("volume") is not None else None,
                timestamp=now_ts,
                source="dhan",
            )
        return out

    async def place_order(self, creds: BrokerCreds, order: OrderRequest) -> OrderResult:
        """Place a real order via Dhan Trading API.

        Trading API is included free with every Dhan account (no Data API plan needed).
        Returns an OrderResult with the broker's order_id on success.
        """
        from . import dhan_symbols
        ref = await dhan_symbols.resolve_async(order.symbol)
        if ref is None:
            return OrderResult(ok=False, broker="dhan",
                               error=f"Symbol {order.symbol!r} not found in Dhan instrument master")

        # Map our canonical vocabulary → Dhan's expected strings
        side = order.side.upper()
        if side not in ("BUY", "SELL"):
            return OrderResult(ok=False, broker="dhan", error=f"Invalid side: {order.side}")

        ot = order.order_type.upper()
        order_type_map = {"MARKET": "MARKET", "LIMIT": "LIMIT", "SL": "STOP_LOSS", "SL_M": "STOP_LOSS_MARKET"}
        if ot not in order_type_map:
            return OrderResult(ok=False, broker="dhan", error=f"Unsupported order_type: {ot}")

        product_map = {"MIS": "INTRADAY", "CNC": "CNC", "NRML": "MARGIN"}
        product = product_map.get(order.product.upper(), "INTRADAY")

        try:
            client = self._client(creds)
            raw = await asyncio.to_thread(
                client.place_order,
                security_id=str(ref.security_id),
                exchange_segment=ref.exchange_segment,
                transaction_type=side,
                quantity=int(order.quantity),
                order_type=order_type_map[ot],
                product_type=product,
                price=float(order.price or 0),
                trigger_price=float(order.trigger_price or 0),
                validity=order.validity or "DAY",
            )
        except Exception as exc:
            return OrderResult(ok=False, broker="dhan", error=f"Dhan order failed: {exc}")

        if not isinstance(raw, dict) or raw.get("status") not in ("success",):
            remarks = (raw or {}).get("remarks") if isinstance(raw, dict) else {}
            msg = remarks.get("error_message") if isinstance(remarks, dict) else str(remarks)
            return OrderResult(ok=False, broker="dhan", error=msg or "Dhan rejected the order")

        data = self._unwrap(raw)
        return OrderResult(
            ok=True, broker="dhan",
            order_id=str(data.get("orderId") or data.get("order_id") or ""),
            placed_price=order.price, placed_quantity=order.quantity, placed_side=side,
            extras={"raw": data, "order_status": data.get("orderStatus")},
        )

    async def get_intraday(self, creds: BrokerCreds, symbol: str, interval_min: int = 5) -> Optional[List[IntradayBar]]:
        """Today's intraday bars at the requested minute interval."""
        from . import dhan_symbols
        ref = await dhan_symbols.resolve_async(symbol)
        if ref is None:
            return None
        # Dhan accepts intervals: 1, 5, 15, 25, 60
        valid = {1, 5, 15, 25, 60}
        iv = interval_min if interval_min in valid else min(valid, key=lambda v: abs(v - interval_min))
        today = datetime.now().strftime("%Y-%m-%d")
        try:
            client = self._client(creds)
            payload = await asyncio.to_thread(
                client.intraday_minute_data,
                str(ref.security_id), ref.exchange_segment, ref.instrument_type, today, today, iv,
            )
        except Exception as exc:
            log.warning("Dhan intraday failed for %s: %s", symbol, exc)
            return None
        data = self._unwrap(payload)
        # Response shape: parallel arrays {open, high, low, close, volume, timestamp/start_Time}
        ts = data.get("timestamp") or data.get("start_Time") or []
        opens = data.get("open") or []
        highs = data.get("high") or []
        lows = data.get("low") or []
        closes = data.get("close") or []
        vols = data.get("volume") or []
        if not ts or not closes:
            return []
        bars: List[IntradayBar] = []
        for i in range(min(len(ts), len(closes))):
            try:
                c = float(closes[i])
            except (TypeError, ValueError):
                continue
            bars.append(IntradayBar(
                t=int(ts[i]),
                o=float(opens[i]) if i < len(opens) and opens[i] is not None else None,
                h=float(highs[i]) if i < len(highs) and highs[i] is not None else None,
                l=float(lows[i]) if i < len(lows) and lows[i] is not None else None,
                c=c,
                v=float(vols[i]) if i < len(vols) and vols[i] is not None else None,
            ))
        return bars


# ---- Real Upstox adapter --------------------------------------------------------------

class _UpstoxAdapter:
    """Real Upstox adapter — backed by the official `upstox-python-sdk`.

    Upstox API v2 quirks worth knowing:
      • Access token is daily-expiring (06:00 IST cutoff like every Indian broker).
      • `get_full_market_quote` accepts a comma-separated string of instrument_keys
        and returns all quotes in ONE round-trip — we exploit this for the watchlist.
      • Free real-time data is included with any active Upstox account; no extra
        subscription is needed (unlike Dhan's split plan).
    """

    def __init__(self, spec: BrokerSpec):
        self.spec = spec
        self._client_cache: Dict[str, Any] = {}

    def _apis(self, creds: BrokerCreds, sandbox: Optional[bool] = None):
        """Returns (UserApi, MarketQuoteApi, HistoryApi) bound to this token.

        Sandbox routing — `creds.is_paper=True` points at api-sandbox.upstox.com
        (Upstox's free sandbox env, doesn't need active trading segments). Explicit
        `sandbox=` kwarg overrides the creds default when callers need to force it.
        """
        import upstox_client
        use_sandbox = sandbox if sandbox is not None else bool(creds.is_paper)
        token = (creds.access_token or "").strip()
        key = f"{'sbx' if use_sandbox else 'prd'}:{token[:32]}"
        cached = self._client_cache.get(key)
        if cached:
            return cached
        cfg = upstox_client.Configuration()
        cfg.access_token = token
        if use_sandbox:
            cfg.host = "https://api-sandbox.upstox.com"
        api_client = upstox_client.ApiClient(cfg)
        bundle = (
            upstox_client.UserApi(api_client),
            upstox_client.MarketQuoteApi(api_client),
            upstox_client.HistoryApi(api_client),
        )
        self._client_cache[key] = bundle
        return bundle

    def _data_apis(self, creds: BrokerCreds):
        """APIs for market data / history — ALWAYS bound to production.

        Upstox's sandbox host (api-sandbox.upstox.com) only simulates orders and
        funds; the Market Quote and Historical Candle endpoints don't exist there
        and return `Resource not Found (UDAPI100060)`. Real-time + historical data
        are served from production (api.upstox.com) for every account, paper or
        live, using the same token. So data calls force `sandbox=False` regardless
        of `creds.is_paper` (which still routes ORDER placement to the sandbox).
        """
        return self._apis(creds, sandbox=False)

    def _validate(self, creds: BrokerCreds) -> Optional[str]:
        if not creds.access_token or len(creds.access_token.strip()) < 20:
            return "access_token looks malformed — paste the full token from upstox.com → Apps → Get Token"
        return None

    @staticmethod
    def _extract_upstox_error(exc: Exception) -> str:
        """Pull the readable error out of Upstox's noisy ApiException message."""
        msg = str(exc)
        # Body always trails the headers; try to parse it for the human message
        import json as _json, re
        m = re.search(r"HTTP response body:\s*b?'(.*)'\s*$", msg, re.DOTALL)
        if m:
            try:
                body = _json.loads(m.group(1).encode().decode("unicode_escape"))
                errs = body.get("errors") or []
                if errs and isinstance(errs, list):
                    e0 = errs[0]
                    code = e0.get("errorCode") or e0.get("error_code") or ""
                    text = e0.get("message") or "Upstox rejected the request"
                    return f"{text}" + (f" ({code})" if code else "")
            except Exception:
                pass
        # Fallback: first line of the exception
        return msg.splitlines()[0][:200]

    async def _try_profile(self, creds: BrokerCreds, sandbox: bool):
        """Single-attempt profile fetch with given sandbox setting."""
        user_api, _, _ = self._apis(creds, sandbox=sandbox)
        return await asyncio.to_thread(user_api.get_profile, api_version="2.0")

    async def test_connection(self, creds: BrokerCreds) -> AdapterResult:
        err = self._validate(creds)
        if err:
            return AdapterResult(ok=False, error=err)

        # Step 1: profile call. We ALWAYS try both URLs unconditionally because
        # different error codes can mean "wrong URL for this token type":
        #   UDAPI100060 (Resource not found) — endpoint doesn't exist in this env
        #   UDAPI100050 (Invalid token)      — token wasn't issued for this env
        # The user shouldn't have to know which one their token is for; we just
        # find the URL that works and stick with it for subsequent calls.
        primary = bool(creds.is_paper)
        profile = None
        errors: dict = {}
        tried_sandbox = None

        for attempt_sandbox in (primary, not primary):
            url_label = "sandbox" if attempt_sandbox else "production"
            try:
                profile = await self._try_profile(creds, sandbox=attempt_sandbox)
                tried_sandbox = attempt_sandbox
                break
            except Exception as exc:
                errors[url_label] = self._extract_upstox_error(exc)

        if profile is None:
            # Both URLs rejected the token — give the user the full picture
            prod = errors.get("production", "—")
            sbx = errors.get("sandbox", "—")
            return AdapterResult(
                ok=False,
                error=(
                    f"Token rejected by BOTH Upstox endpoints. "
                    f"Production (api.upstox.com): {prod}. "
                    f"Sandbox (api-sandbox.upstox.com): {sbx}. "
                    f"Likely causes: token expired, token was revoked "
                    f"(Upstox auto-revokes publicly-leaked tokens), or token "
                    f"is from a different/deleted Upstox app."
                ),
            )

        prof_data = getattr(profile, "data", None) or {}
        prof_dict = prof_data if isinstance(prof_data, dict) else (prof_data.to_dict() if hasattr(prof_data, "to_dict") else {})
        client_id = (prof_dict.get("user_id") if isinstance(prof_dict, dict) else None) or creds.api_key

        # If we found the token works on the OTHER URL, persist that preference
        # by mutating creds.is_paper so subsequent calls (funds, quotes, intraday)
        # go to the right place.
        if tried_sandbox is not None and tried_sandbox != primary:
            log.info("Upstox auto-detected URL for token %s — auto-flipping is_paper to %s",
                     (creds.access_token or '')[:12], tried_sandbox)
            creds.is_paper = tried_sandbox

        prof_data = getattr(profile, "data", None) or {}
        prof_dict = prof_data if isinstance(prof_data, dict) else (prof_data.to_dict() if hasattr(prof_data, "to_dict") else {})
        client_id = (prof_dict.get("user_id") if isinstance(prof_dict, dict) else None) or creds.api_key

        # Step 2: funds call — BEST-EFFORT. Upstox sandbox doesn't always expose
        # this endpoint (returns UDAPI100060 "Resource not found"); production
        # users with active SEC segment get real numbers. Either way, profile
        # success means the connection is valid; we just default to 0 balance
        # when funds can't be retrieved.
        available = 0.0
        used = 0.0
        funds_warning: Optional[str] = None
        try:
            user_api2, _, _ = self._apis(creds)
            margin = await asyncio.to_thread(
                user_api2.get_user_fund_margin, api_version="2.0", segment="SEC"
            )
            margin_data = getattr(margin, "data", None) or {}
            if hasattr(margin_data, "to_dict"):
                margin_data = margin_data.to_dict()
            sec = (margin_data or {}).get("equity", {}) if isinstance(margin_data, dict) else {}
            available = float(sec.get("available_margin", 0) or 0)
            used = float(sec.get("used_margin", 0) or 0)
        except Exception as exc:
            funds_warning = self._extract_upstox_error(exc)
            log.info("Upstox funds endpoint unavailable (%s) — connection still valid via profile", funds_warning)

        return AdapterResult(
            ok=True,
            account_id=str(client_id),
            balance=available,
            equity=available + used,
            margin_available=available,
            currency="INR",
            extras={
                "mode": "sandbox" if creds.is_paper else "live",
                "spec": self.spec.slug,
                "profile": prof_dict if isinstance(prof_dict, dict) else {},
                "funds_unavailable": funds_warning,
            },
        )

    async def fetch_balance(self, creds: BrokerCreds) -> AdapterResult:
        return await self.test_connection(creds)

    # ---- Market data: free real-time, included with Upstox account --------------------

    async def probe_data_api(self, creds: BrokerCreds) -> bool:
        """Confirm market data works. Reliance ISIN INE002A01018 on NSE_EQ."""
        try:
            _, market_api, _ = self._data_apis(creds)
            resp = await asyncio.to_thread(market_api.ltp, symbol="NSE_EQ|INE002A01018", api_version="2.0")
            return getattr(resp, "status", None) == "success" and bool(getattr(resp, "data", None))
        except Exception:
            return False

    @staticmethod
    def _quote_from_payload(symbol: str, ref, raw: dict, source_tag: str = "upstox") -> Optional[Quote]:
        """Build our common Quote from Upstox's nested response dict."""
        if not isinstance(raw, dict):
            return None
        ltp = float(raw.get("last_price") or raw.get("ltp") or 0)
        ohlc = raw.get("ohlc") or {}
        prev_close = float(ohlc.get("close") or raw.get("close_price") or ltp)
        change = ltp - prev_close
        change_pct = (change / prev_close * 100) if prev_close else 0.0
        return Quote(
            symbol=symbol.upper(),
            name=ref.name if ref else symbol.upper(),
            exchange=ref.exchange if ref else "",
            currency="INR",
            current_price=round(ltp, 2),
            prev_close=round(prev_close, 2),
            change=round(change, 2),
            change_pct=round(change_pct, 2),
            open=float(ohlc.get("open")) if ohlc.get("open") is not None else None,
            high=float(ohlc.get("high")) if ohlc.get("high") is not None else None,
            low=float(ohlc.get("low")) if ohlc.get("low") is not None else None,
            volume=int(raw.get("volume")) if raw.get("volume") is not None else None,
            timestamp=int(datetime.now(timezone.utc).timestamp()),
            source=source_tag,
        )

    async def get_quote(self, creds: BrokerCreds, symbol: str) -> Optional[Quote]:
        from . import upstox_symbols
        ref = await upstox_symbols.resolve_async(symbol)
        if ref is None:
            return None
        try:
            _, market_api, _ = self._data_apis(creds)
            resp = await asyncio.to_thread(
                market_api.get_full_market_quote, symbol=ref.instrument_key, api_version="2.0"
            )
        except Exception as exc:
            log.warning("Upstox quote failed for %s: %s", symbol, exc)
            return None
        data = getattr(resp, "data", None) or {}
        if hasattr(data, "to_dict"):
            data = data.to_dict()
        # Response data is keyed by instrument_key (sometimes with trading-symbol form)
        row = None
        if isinstance(data, dict):
            for k, v in data.items():
                if k == ref.instrument_key or k.endswith(":" + ref.trading_symbol) or k == ref.trading_symbol:
                    row = v
                    break
            if row is None and data:
                row = next(iter(data.values()), None)
        if hasattr(row, "to_dict"):
            row = row.to_dict()
        return self._quote_from_payload(symbol, ref, row)

    async def get_quotes_batch(self, creds: BrokerCreds, symbols: List[str]) -> Dict[str, Quote]:
        """Single API call for all symbols — Upstox supports comma-separated keys."""
        from . import upstox_symbols
        refs = await asyncio.gather(*(upstox_symbols.resolve_async(s) for s in symbols))
        sym_to_ref = {s.upper(): r for s, r in zip(symbols, refs) if r is not None}
        if not sym_to_ref:
            return {}

        # Upstox docs: up to 1000 instrument_keys per call, comma-separated.
        symbol_param = ",".join(r.instrument_key for r in sym_to_ref.values())
        try:
            _, market_api, _ = self._data_apis(creds)
            resp = await asyncio.to_thread(
                market_api.get_full_market_quote, symbol=symbol_param, api_version="2.0"
            )
        except Exception as exc:
            log.warning("Upstox batch quote failed: %s", exc)
            return {}

        data = getattr(resp, "data", None) or {}
        if hasattr(data, "to_dict"):
            data = data.to_dict()
        if not isinstance(data, dict):
            return {}

        # Index Upstox's response by instrument_key suffix so we can pair to our symbols
        by_key: Dict[str, Any] = {}
        for k, v in data.items():
            row = v.to_dict() if hasattr(v, "to_dict") else v
            # Upstox keys can be "NSE_EQ:RELIANCE" or "NSE_EQ|INE002A01018"
            by_key[k] = row
            if isinstance(row, dict) and "instrument_token" in row:
                by_key[row.get("instrument_token") or ""] = row

        out: Dict[str, Quote] = {}
        for sym, ref in sym_to_ref.items():
            row = by_key.get(ref.instrument_key)
            if row is None:
                # Try alternate key forms
                trade_key = f"{ref.segment}:{ref.trading_symbol}"
                row = by_key.get(trade_key) or by_key.get(ref.trading_symbol)
            if not row:
                continue
            q = self._quote_from_payload(sym, ref, row)
            if q is not None:
                out[sym] = q
        return out

    async def get_intraday(self, creds: BrokerCreds, symbol: str, interval_min: int = 5) -> Optional[List[IntradayBar]]:
        """Intraday minute bars. Upstox intervals: 1minute, 30minute. For others
        we fall back to 1minute and the chart still renders fine.
        """
        from . import upstox_symbols
        ref = await upstox_symbols.resolve_async(symbol)
        if ref is None:
            return None
        # Upstox v2 currently supports '1minute' and '30minute' for intraday
        iv = "30minute" if interval_min >= 15 else "1minute"
        try:
            _, _, history_api = self._data_apis(creds)
            resp = await asyncio.to_thread(
                history_api.get_intra_day_candle_data,
                instrument_key=ref.instrument_key, interval=iv, api_version="2.0",
            )
        except Exception as exc:
            log.warning("Upstox intraday failed for %s: %s", symbol, exc)
            return None

        data = getattr(resp, "data", None) or {}
        if hasattr(data, "to_dict"):
            data = data.to_dict()
        candles = (data or {}).get("candles") or []
        # Upstox returns [timestamp_iso, open, high, low, close, volume, oi]
        bars: List[IntradayBar] = []
        for c in candles:
            if not c or len(c) < 5:
                continue
            try:
                ts_iso = c[0]
                ts = int(datetime.fromisoformat(ts_iso.replace("Z", "+00:00")).timestamp()) if isinstance(ts_iso, str) else int(ts_iso)
                bars.append(IntradayBar(
                    t=ts,
                    o=float(c[1]) if c[1] is not None else None,
                    h=float(c[2]) if c[2] is not None else None,
                    l=float(c[3]) if c[3] is not None else None,
                    c=float(c[4]),
                    v=float(c[5]) if len(c) > 5 and c[5] is not None else None,
                ))
            except Exception:
                continue
        # Upstox returns newest-first; the dashboard expects oldest-first
        bars.reverse()
        return bars


# ---- Real Zerodha adapter -------------------------------------------------------------

class _ZerodhaAdapter:
    """Real Zerodha Kite Connect adapter.

    Auth model is different from Dhan/Upstox: every morning the user must complete
    Zerodha's OAuth dance to obtain a fresh `access_token`. Their developer portal
    has a one-click flow that returns the token directly — see /brokers UI for the
    walkthrough. Tokens expire at 06:00 IST daily (SEBI rule).

    Kite Connect (₹500 one-time + ₹2000/month) covers BOTH trading and market data.
    """

    def __init__(self, spec: BrokerSpec):
        self.spec = spec
        self._client_cache: Dict[str, Any] = {}

    def _client(self, creds: BrokerCreds):
        from kiteconnect import KiteConnect
        key = f"{creds.api_key}:{creds.access_token}"
        cached = self._client_cache.get(key)
        if cached:
            return cached
        kite = KiteConnect(api_key=creds.api_key.strip())
        kite.set_access_token((creds.access_token or "").strip())
        self._client_cache[key] = kite
        return kite

    def _validate(self, creds: BrokerCreds) -> Optional[str]:
        if not creds.api_key or len(creds.api_key.strip()) < 4:
            return "api_key is required (from developer.kite.trade)"
        if not creds.access_token or len(creds.access_token.strip()) < 20:
            return "access_token is required (generated via Kite Connect OAuth flow)"
        return None

    async def test_connection(self, creds: BrokerCreds) -> AdapterResult:
        err = self._validate(creds)
        if err:
            return AdapterResult(ok=False, error=err)
        try:
            kite = self._client(creds)
            profile = await asyncio.to_thread(kite.profile)
            margins = await asyncio.to_thread(kite.margins)
        except Exception as exc:
            # KiteException carries a clear `message` attribute
            msg = getattr(exc, "message", None) or str(exc).splitlines()[0]
            return AdapterResult(ok=False, error=f"Zerodha rejected credentials: {msg[:200]}")

        equity = (margins or {}).get("equity", {}) if isinstance(margins, dict) else {}
        available = float((equity.get("available") or {}).get("live_balance") or 0)
        used = float((equity.get("utilised") or {}).get("debits") or 0)
        net = float(equity.get("net") or available)

        return AdapterResult(
            ok=True,
            account_id=str(profile.get("user_id") or creds.api_key),
            balance=net,
            equity=net + used,
            margin_available=available,
            currency="INR",
            extras={"mode": "live", "spec": self.spec.slug, "user_name": profile.get("user_name")},
        )

    async def fetch_balance(self, creds: BrokerCreds) -> AdapterResult:
        return await self.test_connection(creds)

    async def probe_data_api(self, creds: BrokerCreds) -> bool:
        """Kite Connect includes market data — confirm with a cheap LTP call."""
        try:
            kite = self._client(creds)
            resp = await asyncio.to_thread(kite.ltp, ["NSE:RELIANCE"])
            return isinstance(resp, dict) and "NSE:RELIANCE" in resp
        except Exception:
            return False

    async def place_order(self, creds: BrokerCreds, order: OrderRequest) -> OrderResult:
        """Place a real order via Kite Connect."""
        from kiteconnect import KiteConnect
        side = order.side.upper()
        if side not in ("BUY", "SELL"):
            return OrderResult(ok=False, broker="zerodha", error=f"Invalid side: {order.side}")

        # Zerodha needs (exchange, tradingsymbol) — for stocks it's NSE/RELIANCE,
        # for indices it's NSE/NIFTY 50. Indices can't be traded directly; only
        # their derivatives are tradable. For now we route equities only.
        ts = order.symbol.upper().strip()
        if ts.endswith(".NS"):
            ts = ts[:-3]
        exchange = KiteConnect.EXCHANGE_NSE

        ot_map = {
            "MARKET": KiteConnect.ORDER_TYPE_MARKET,
            "LIMIT": KiteConnect.ORDER_TYPE_LIMIT,
            "SL": KiteConnect.ORDER_TYPE_SL,
            "SL_M": KiteConnect.ORDER_TYPE_SLM,
        }
        product_map = {
            "MIS": KiteConnect.PRODUCT_MIS,
            "CNC": KiteConnect.PRODUCT_CNC,
            "NRML": KiteConnect.PRODUCT_NRML,
        }
        ot = ot_map.get(order.order_type.upper())
        if not ot:
            return OrderResult(ok=False, broker="zerodha", error=f"Unsupported order_type: {order.order_type}")
        product = product_map.get(order.product.upper(), KiteConnect.PRODUCT_MIS)

        kwargs = dict(
            variety=KiteConnect.VARIETY_REGULAR,
            exchange=exchange,
            tradingsymbol=ts,
            transaction_type=KiteConnect.TRANSACTION_TYPE_BUY if side == "BUY" else KiteConnect.TRANSACTION_TYPE_SELL,
            quantity=int(order.quantity),
            product=product,
            order_type=ot,
            validity=order.validity or KiteConnect.VALIDITY_DAY,
        )
        if order.price is not None and ot in (KiteConnect.ORDER_TYPE_LIMIT, KiteConnect.ORDER_TYPE_SL):
            kwargs["price"] = float(order.price)
        if order.trigger_price is not None and ot in (KiteConnect.ORDER_TYPE_SL, KiteConnect.ORDER_TYPE_SLM):
            kwargs["trigger_price"] = float(order.trigger_price)
        if order.tag:
            kwargs["tag"] = order.tag[:20]  # Kite enforces 20-char tag limit

        try:
            kite = self._client(creds)
            order_id = await asyncio.to_thread(kite.place_order, **kwargs)
        except Exception as exc:
            msg = getattr(exc, "message", None) or str(exc).splitlines()[0]
            return OrderResult(ok=False, broker="zerodha", error=f"Zerodha rejected order: {msg[:300]}")

        return OrderResult(
            ok=True, broker="zerodha", order_id=str(order_id),
            placed_price=order.price, placed_quantity=order.quantity, placed_side=side,
            extras={"variety": KiteConnect.VARIETY_REGULAR, "exchange": exchange, "tradingsymbol": ts},
        )


# Registry: real SDK-backed adapters.
# IBKR adapter uses lazy import to avoid hard dependency on ib_insync.
def _make_ibkr_adapter(spec: BrokerSpec):
    from .ibkr_adapter import IBKRAdapter
    return IBKRAdapter(spec)

_ADAPTERS: Dict[str, Callable[[BrokerSpec], Any]] = {
    "dhan": _DhanAdapter,
    "upstox": _UpstoxAdapter,
    "zerodha": _ZerodhaAdapter,
    "ibkr": _make_ibkr_adapter,
}


def get_adapter(slug: str):
    spec = SPECS.get(slug)
    if not spec:
        raise ValueError(f"Unsupported broker: {slug}")
    factory = _ADAPTERS.get(slug)
    if factory:
        return factory(spec)
    return _SandboxAdapter(spec)


def list_specs() -> list:
    return [
        {
            "slug": s.slug,
            "name": s.name,
            "region": s.region,
            "asset_classes": s.asset_classes,
            "auth_kind": s.auth_kind,
            "docs_url": s.docs_url,
            "notes": s.notes,
            "paper_supported": s.paper_supported,
            "requires_access_token": s.requires_access_token,
            "live": s.live,
            "streams_market_data": s.streams_market_data,
            "fields": [
                {
                    "key": f.key, "label": f.label, "placeholder": f.placeholder,
                    "secret": f.secret, "required": f.required, "hint": f.hint,
                }
                for f in s.field_schema
            ],
        }
        for s in SPECS.values()
    ]
