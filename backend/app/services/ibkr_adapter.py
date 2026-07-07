"""Interactive Brokers adapter — real SDK integration via ib_insync.

Phase 4 of the Institutional Target-State Architecture.

Connection model:
  IBKR requires a local IB Gateway or TWS (Trader Workstation) running.
  - Paper account: use the IB paper-trading gateway (port 4002).
  - Live account: use the live IB Gateway (port 4001).
  The adapter connects via the TCP API. No cloud/REST endpoint — this is
  the same protocol that institutional desks use.

Dependencies:
  pip install ib_insync  (or ib_async for Python 3.12+)

Credential mapping (BrokerCreds):
  api_key    → not used (IB uses host:port connection)
  api_secret → not used
  access_token → not used
  account_id → IB account ID (e.g. "DU1234567" for paper)
  is_paper   → True → port 4002, False → port 4001

Connection config is read from environment:
  ETB_IBKR_HOST = "127.0.0.1"   (default)
  ETB_IBKR_LIVE_PORT = 4001
  ETB_IBKR_PAPER_PORT = 4002
  ETB_IBKR_CLIENT_ID = 1        (unique per concurrent connection)
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .broker_adapters import (
    AdapterResult,
    BrokerCreds,
    BrokerField,
    BrokerSpec,
    IntradayBar,
    OrderRequest,
    OrderResult,
    Quote,
    SPECS,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Environment-based configuration
# ---------------------------------------------------------------------------
_IBKR_HOST = os.getenv("ETB_IBKR_HOST", "127.0.0.1")
_IBKR_LIVE_PORT = int(os.getenv("ETB_IBKR_LIVE_PORT", "4001"))
_IBKR_PAPER_PORT = int(os.getenv("ETB_IBKR_PAPER_PORT", "4002"))
_IBKR_CLIENT_ID = int(os.getenv("ETB_IBKR_CLIENT_ID", "1"))
_IBKR_TIMEOUT = int(os.getenv("ETB_IBKR_TIMEOUT", "15"))


def _try_import_ib():
    """Lazy-import ib_insync (or ib_async). Returns the module or None."""
    try:
        import ib_insync
        return ib_insync
    except ImportError:
        pass
    try:
        import ib_async as ib_insync
        return ib_insync
    except ImportError:
        return None


class IBKRAdapter:
    """Real Interactive Brokers adapter using ib_insync.

    Supports:
    - Connection testing (verifies Gateway/TWS is reachable)
    - Balance/margin queries
    - Market data quotes
    - Order placement (equities, futures, forex)
    - Position queries for reconciliation
    """

    def __init__(self, spec: BrokerSpec):
        self.spec = spec
        self._ib = None  # Lazy-initialized IB connection
        self._connected = False
        self._lock = asyncio.Lock()

    def _port_for(self, creds: BrokerCreds) -> int:
        return _IBKR_PAPER_PORT if creds.is_paper else _IBKR_LIVE_PORT

    async def _ensure_connected(self, creds: BrokerCreds):
        """Establish or reuse a connection to IB Gateway/TWS."""
        ib_mod = _try_import_ib()
        if ib_mod is None:
            raise ImportError(
                "ib_insync (or ib_async) is required for IBKR. "
                "Install with: pip install ib_insync"
            )

        async with self._lock:
            if self._ib is not None and self._ib.isConnected():
                return self._ib

            ib = ib_mod.IB()
            port = self._port_for(creds)
            account = creds.account_id or ""

            try:
                await asyncio.wait_for(
                    asyncio.to_thread(
                        ib.connect,
                        host=_IBKR_HOST,
                        port=port,
                        clientId=_IBKR_CLIENT_ID,
                        account=account,
                        readonly=False,
                        timeout=_IBKR_TIMEOUT,
                    ),
                    timeout=_IBKR_TIMEOUT + 5,
                )
            except Exception as exc:
                raise ConnectionError(
                    f"Cannot connect to IB Gateway at {_IBKR_HOST}:{port} — "
                    f"ensure TWS/IB Gateway is running. Error: {exc}"
                ) from exc

            self._ib = ib
            self._connected = True
            return ib

    async def _disconnect(self):
        if self._ib is not None:
            try:
                await asyncio.to_thread(self._ib.disconnect)
            except Exception:
                pass
            self._ib = None
            self._connected = False

    # -- Connection & balance -----------------------------------------------

    async def test_connection(self, creds: BrokerCreds) -> AdapterResult:
        """Verify IB Gateway is reachable and account is valid."""
        ib_mod = _try_import_ib()
        if ib_mod is None:
            return AdapterResult(
                ok=False,
                error="ib_insync not installed. Run: pip install ib_insync",
            )

        try:
            ib = await self._ensure_connected(creds)
        except (ConnectionError, ImportError, Exception) as exc:
            return AdapterResult(ok=False, error=str(exc)[:300])

        try:
            # Fetch account summary
            summary = await asyncio.to_thread(ib.accountSummary)
            if not summary:
                return AdapterResult(
                    ok=False,
                    error="Connected but no account summary returned. Check account ID.",
                )

            # Parse key values
            vals: Dict[str, float] = {}
            account_id = creds.account_id or ""
            for item in summary:
                if account_id and item.account != account_id:
                    continue
                if not account_id:
                    account_id = item.account  # Auto-detect
                if item.tag in (
                    "NetLiquidation", "TotalCashValue",
                    "AvailableFunds", "BuyingPower",
                    "GrossPositionValue",
                ):
                    try:
                        vals[item.tag] = float(item.value)
                    except (ValueError, TypeError):
                        pass

            net_liq = vals.get("NetLiquidation", 0.0)
            cash = vals.get("TotalCashValue", 0.0)
            available = vals.get("AvailableFunds", cash)
            buying_power = vals.get("BuyingPower", available)

            return AdapterResult(
                ok=True,
                account_id=account_id,
                balance=net_liq,
                equity=net_liq,
                margin_available=available,
                currency="USD",
                extras={
                    "mode": "paper" if creds.is_paper else "live",
                    "spec": self.spec.slug,
                    "buying_power": buying_power,
                    "cash": cash,
                    "gateway": f"{_IBKR_HOST}:{self._port_for(creds)}",
                },
            )

        except Exception as exc:
            return AdapterResult(
                ok=False,
                error=f"IBKR account query failed: {str(exc)[:300]}",
            )

    async def fetch_balance(self, creds: BrokerCreds) -> AdapterResult:
        return await self.test_connection(creds)

    async def probe_data_api(self, creds: BrokerCreds) -> bool:
        """IBKR always includes market data with the connection."""
        try:
            await self._ensure_connected(creds)
            return True
        except Exception:
            return False

    # -- Market data --------------------------------------------------------

    async def get_quote(self, creds: BrokerCreds, symbol: str) -> Optional[Quote]:
        """Fetch a snapshot quote from IBKR."""
        ib_mod = _try_import_ib()
        if ib_mod is None:
            return None

        try:
            ib = await self._ensure_connected(creds)
        except Exception:
            return None

        try:
            # Determine contract type
            contract = self._make_contract(ib_mod, symbol)
            qualified = await asyncio.to_thread(ib.qualifyContracts, contract)
            if not qualified:
                log.warning("IBKR: cannot qualify contract for %s", symbol)
                return None

            contract = qualified[0]

            # Request snapshot
            ticker = await asyncio.to_thread(
                ib.reqMktData, contract, "", True, False
            )
            # Wait briefly for data
            await asyncio.sleep(2)
            await asyncio.to_thread(ib.sleep, 0.1)

            ltp = ticker.last if ticker.last and ticker.last > 0 else (
                ticker.close if ticker.close and ticker.close > 0 else 0.0
            )
            prev_close = ticker.close if ticker.close and ticker.close > 0 else ltp
            change = ltp - prev_close
            change_pct = (change / prev_close * 100) if prev_close else 0.0

            return Quote(
                symbol=symbol.upper(),
                name=contract.localSymbol or symbol.upper(),
                exchange=contract.exchange or "SMART",
                currency=contract.currency or "USD",
                current_price=round(ltp, 4),
                prev_close=round(prev_close, 4),
                change=round(change, 4),
                change_pct=round(change_pct, 4),
                open=ticker.open if ticker.open and ticker.open > 0 else None,
                high=ticker.high if ticker.high and ticker.high > 0 else None,
                low=ticker.low if ticker.low and ticker.low > 0 else None,
                volume=int(ticker.volume) if ticker.volume and ticker.volume > 0 else None,
                timestamp=int(datetime.now(timezone.utc).timestamp()),
                source="ibkr",
            )

        except Exception as exc:
            log.warning("IBKR quote failed for %s: %s", symbol, exc)
            return None

    async def get_quotes_batch(
        self, creds: BrokerCreds, symbols: List[str]
    ) -> Dict[str, Quote]:
        """Batch quote fetch — IBKR doesn't have a native batch, so we parallelize."""
        results: Dict[str, Quote] = {}
        tasks = [self.get_quote(creds, s) for s in symbols]
        quotes = await asyncio.gather(*tasks, return_exceptions=True)
        for sym, q in zip(symbols, quotes):
            if isinstance(q, Quote):
                results[sym] = q
        return results

    async def get_intraday(
        self, creds: BrokerCreds, symbol: str, interval_min: int = 5
    ) -> Optional[List[IntradayBar]]:
        """Fetch intraday bars from IBKR historical data."""
        ib_mod = _try_import_ib()
        if ib_mod is None:
            return None

        try:
            ib = await self._ensure_connected(creds)
        except Exception:
            return None

        try:
            contract = self._make_contract(ib_mod, symbol)
            qualified = await asyncio.to_thread(ib.qualifyContracts, contract)
            if not qualified:
                return None
            contract = qualified[0]

            bar_size = f"{interval_min} mins" if interval_min < 60 else "1 hour"
            bars = await asyncio.to_thread(
                ib.reqHistoricalData,
                contract,
                endDateTime="",
                durationStr="1 D",
                barSizeSetting=bar_size,
                whatToShow="TRADES",
                useRTH=True,
                formatDate=2,
            )

            result: List[IntradayBar] = []
            for bar in bars:
                ts = int(bar.date.timestamp()) if hasattr(bar.date, "timestamp") else 0
                result.append(IntradayBar(
                    t=ts,
                    o=float(bar.open),
                    h=float(bar.high),
                    l=float(bar.low),
                    c=float(bar.close),
                    v=float(bar.volume) if bar.volume else None,
                ))
            return result

        except Exception as exc:
            log.warning("IBKR intraday failed for %s: %s", symbol, exc)
            return None

    # -- Order placement ----------------------------------------------------

    async def place_order(self, creds: BrokerCreds, order: OrderRequest) -> OrderResult:
        """Place a real order through IBKR."""
        ib_mod = _try_import_ib()
        if ib_mod is None:
            return OrderResult(
                ok=False, broker="ibkr",
                error="ib_insync not installed",
            )

        try:
            ib = await self._ensure_connected(creds)
        except Exception as exc:
            return OrderResult(ok=False, broker="ibkr", error=str(exc)[:300])

        try:
            contract = self._make_contract(ib_mod, order.symbol)
            qualified = await asyncio.to_thread(ib.qualifyContracts, contract)
            if not qualified:
                return OrderResult(
                    ok=False, broker="ibkr",
                    error=f"Cannot qualify contract for {order.symbol}",
                )
            contract = qualified[0]

            # Build the IB order
            side = order.side.upper()
            if side not in ("BUY", "SELL"):
                return OrderResult(
                    ok=False, broker="ibkr",
                    error=f"Invalid side: {order.side}",
                )

            ib_order = ib_mod.Order()
            ib_order.action = side
            ib_order.totalQuantity = int(order.quantity)
            ib_order.account = creds.account_id or ""

            ot = order.order_type.upper()
            if ot == "MARKET":
                ib_order.orderType = "MKT"
            elif ot == "LIMIT":
                ib_order.orderType = "LMT"
                ib_order.lmtPrice = float(order.price) if order.price else 0.0
            elif ot in ("SL", "SL_M"):
                ib_order.orderType = "STP"
                ib_order.auxPrice = float(order.trigger_price) if order.trigger_price else 0.0
            else:
                ib_order.orderType = "MKT"

            if order.validity and order.validity.upper() == "GTC":
                ib_order.tif = "GTC"
            else:
                ib_order.tif = "DAY"

            if order.tag:
                ib_order.orderRef = order.tag[:40]

            # Place the order
            trade = await asyncio.to_thread(ib.placeOrder, contract, ib_order)
            await asyncio.to_thread(ib.sleep, 1)  # Wait for ack

            order_id = str(trade.order.orderId) if trade.order else "unknown"

            return OrderResult(
                ok=True,
                broker="ibkr",
                order_id=order_id,
                placed_price=order.price,
                placed_quantity=order.quantity,
                placed_side=side,
                extras={
                    "exchange": contract.exchange,
                    "currency": contract.currency,
                    "order_type": ib_order.orderType,
                    "status": trade.orderStatus.status if trade.orderStatus else "Submitted",
                },
            )

        except Exception as exc:
            return OrderResult(
                ok=False, broker="ibkr",
                error=f"IBKR order failed: {str(exc)[:300]}",
            )

    # -- Position queries (for reconciliation) ------------------------------

    async def get_positions(self, creds: BrokerCreds) -> List[Dict[str, Any]]:
        """Fetch all open positions from IBKR — used by the reconciliation engine."""
        try:
            ib = await self._ensure_connected(creds)
        except Exception:
            return []

        try:
            positions = await asyncio.to_thread(ib.positions)
            result = []
            for pos in positions:
                if creds.account_id and pos.account != creds.account_id:
                    continue
                result.append({
                    "account": pos.account,
                    "symbol": pos.contract.localSymbol or pos.contract.symbol,
                    "exchange": pos.contract.exchange,
                    "currency": pos.contract.currency,
                    "qty": float(pos.position),
                    "avg_cost": float(pos.avgCost),
                    "market_value": float(pos.position) * float(pos.avgCost),
                })
            return result
        except Exception as exc:
            log.warning("IBKR positions query failed: %s", exc)
            return []

    async def get_open_orders(self, creds: BrokerCreds) -> List[Dict[str, Any]]:
        """Fetch all open/working orders from IBKR."""
        try:
            ib = await self._ensure_connected(creds)
        except Exception:
            return []

        try:
            trades = await asyncio.to_thread(ib.openTrades)
            result = []
            for trade in trades:
                result.append({
                    "order_id": str(trade.order.orderId),
                    "symbol": trade.contract.localSymbol or trade.contract.symbol,
                    "side": trade.order.action,
                    "qty": float(trade.order.totalQuantity),
                    "order_type": trade.order.orderType,
                    "status": trade.orderStatus.status if trade.orderStatus else "Unknown",
                    "filled": float(trade.orderStatus.filled) if trade.orderStatus else 0.0,
                    "remaining": float(trade.orderStatus.remaining) if trade.orderStatus else 0.0,
                })
            return result
        except Exception as exc:
            log.warning("IBKR open orders query failed: %s", exc)
            return []

    # -- Contract helpers ---------------------------------------------------

    @staticmethod
    def _make_contract(ib_mod, symbol: str):
        """Create an IBKR contract from a symbol string.

        Supports formats:
        - "AAPL"          → US stock (SMART routing)
        - "AAPL.US"       → explicit US stock
        - "RELIANCE.NS"   → NSE stock
        - "EUR.USD"       → Forex pair
        - "ES"            → E-mini S&P futures (current front month)
        """
        sym = symbol.upper().strip()

        # Forex pairs
        if "." in sym and len(sym.split(".")) == 2:
            parts = sym.split(".")
            if len(parts[0]) == 3 and len(parts[1]) == 3 and parts[1] not in ("NS", "BO", "US"):
                return ib_mod.Forex(parts[0] + parts[1])

        # Indian stocks
        if sym.endswith(".NS") or sym.endswith(".BO"):
            clean = sym.rsplit(".", 1)[0]
            return ib_mod.Stock(clean, "NSE", "INR")

        # US stock (default)
        if sym.endswith(".US"):
            clean = sym[:-3]
        else:
            clean = sym

        return ib_mod.Stock(clean, "SMART", "USD")
