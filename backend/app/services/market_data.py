import asyncio
import logging

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

NSE_HINTS = {
    "RELIANCE", "INFY", "TCS", "HDFCBANK", "ICICIBANK", "SBIN", "ITC", "LT",
    "HINDUNILVR", "BAJFINANCE", "KOTAKBANK", "AXISBANK", "MARUTI", "WIPRO",
    "ADANIENT", "ADANIPORTS", "TITAN", "SUNPHARMA", "ASIANPAINT", "NESTLEIND",
    "TATAMOTORS", "TATASTEEL", "NTPC", "POWERGRID", "ONGC", "COALINDIA",
    "BHARTIARTL", "HCLTECH", "TECHM", "ULTRACEMCO", "JSWSTEEL",
    "GRASIM", "DRREDDY", "CIPLA", "DIVISLAB", "HEROMOTOCO", "EICHERMOT",
    "BAJAJFINSV", "BRITANNIA", "INDUSINDBK", "HDFCLIFE",
}

INDEX_ALIAS = {
    # Main NSE/BSE indexes
    "NIFTY": "^NSEI", "NIFTY50": "^NSEI", "NIFTY 50": "^NSEI",
    "BANKNIFTY": "^NSEBANK", "NIFTY BANK": "^NSEBANK",
    "SENSEX": "^BSESN",
    "BANKEX": "^BSEBANK",
    # Cap indexes
    "NIFTYNEXT50": "^NSMIDCP", "NIFTY NEXT 50": "^NSMIDCP",
    "NIFTYMIDCAP50": "NIFTY_MIDCAP_50.NS",
    "NIFTYMIDCAP100": "^CNXMIDCAP",
    "NIFTYSMALLCAP100": "^CNXSC",
    # Sector indexes
    "NIFTYIT": "^CNXIT", "NIFTY IT": "^CNXIT",
    "NIFTYAUTO": "^CNXAUTO",
    "NIFTYPHARMA": "^CNXPHARMA",
    "NIFTYFMCG": "^CNXFMCG",
    "NIFTYMETAL": "^CNXMETAL",
    "NIFTYREALTY": "^CNXREALTY",
    "NIFTYMEDIA": "^CNXMEDIA",
    "NIFTYENERGY": "^CNXENERGY",
    "NIFTYFINSERVICE": "NIFTY_FIN_SERVICE.NS", "FINNIFTY": "NIFTY_FIN_SERVICE.NS",
    "NIFTYPSUBANK": "^CNXPSUBANK",
    "NIFTYPRIVATEBANK": "NIFTY_PVT_BANK.NS",
    # US benchmarks
    "SPX": "^GSPC",
    "DOW": "^DJI",
    "NASDAQ": "^IXIC",
    "VIX": "^VIX",
    "RUSSELL2000": "^RUT",
}


def normalize_symbol(symbol):
    s = symbol.upper().strip()
    if s in INDEX_ALIAS:
        return INDEX_ALIAS[s]
    if "." in s or "^" in s:
        return s
    if s in NSE_HINTS:
        return s + ".NS"
    return s


class MarketDataService:
    BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    _QUOTE_TTL = 10.0  # seconds — short cache so polling tabs don't re-hit Yahoo every cycle

    def __init__(self):
        self._quote_cache: dict = {}   # symbol -> (monotonic_ts, quote_dict)
        self._resolved: dict = {}      # input symbol -> working Yahoo symbol (avoids repeat 404s)

    def _candidates(self, symbol):
        """Yahoo symbols to try, best-guess first. NSE equities need a `.NS` suffix,
        which `normalize_symbol` only adds for ~40 hinted tickers — so for the other
        ~2000 NSE names we also try `<SYM>.NS`. US tickers (AAPL) succeed bare and
        never reach the suffix. The working form is cached so it's tried first next time."""
        s = (symbol or "").upper().strip()
        out = []
        cached = self._resolved.get(s)
        if cached:
            out.append(cached)
        out.append(normalize_symbol(symbol))
        if "." not in s and "^" not in s and s not in INDEX_ALIAS:
            out.append(s + ".NS")
        seen, uniq = set(), []
        for x in out:
            if x and x not in seen:
                seen.add(x); uniq.append(x)
        return uniq

    async def _fetch(self, symbol, range_, interval):
        params = {"range": range_, "interval": interval, "includePrePost": "false"}
        headers = {"User-Agent": "Mozilla/5.0 TradingBot"}
        last_exc = None
        async with httpx.AsyncClient(timeout=10, headers=headers) as client:
            for ysym in self._candidates(symbol):
                try:
                    response = await client.get(self.BASE_URL.format(symbol=ysym), params=params)
                    response.raise_for_status()
                    self._resolved[(symbol or "").upper()] = ysym   # remember the form that worked
                    return response.json()
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code in (404, 422):
                        last_exc = exc
                        continue   # try the next candidate (e.g. add .NS)
                    raise
        raise last_exc

    async def get_quote(self, symbol):
        import time
        key = (symbol or "").upper()
        now = time.monotonic()
        cached = self._quote_cache.get(key)
        if cached and now - cached[0] < self._QUOTE_TTL:
            return dict(cached[1])
        payload = await self._fetch(symbol, "1d", "1m")
        chart = payload.get("chart", {}).get("result")
        if not chart:
            raise ValueError("No chart data for " + symbol)
        result = chart[0]
        meta = result.get("meta", {})
        indicators = result.get("indicators", {}).get("quote", [])
        timestamps = result.get("timestamp", [])
        if not indicators or not timestamps:
            raise ValueError("Incomplete quote data for " + symbol)
        q = indicators[0]
        closes = q.get("close", [])
        last_index = -1
        for i in range(len(closes) - 1, -1, -1):
            if closes[i] is not None:
                last_index = i
                break
        current = float(closes[last_index]) if closes and closes[last_index] is not None else float(meta.get("regularMarketPrice", 0))
        prev_close = float(meta.get("chartPreviousClose", current))
        change = current - prev_close
        change_pct = (change / prev_close * 100) if prev_close else 0.0
        def safe(key):
            arr = q.get(key, [])
            return float(arr[last_index]) if arr and arr[last_index] is not None else None
        vol_arr = q.get("volume", [])
        volume = int(vol_arr[last_index]) if vol_arr and vol_arr[last_index] is not None else None
        result = {
            "symbol": symbol.upper(),
            "yahoo_symbol": normalize_symbol(symbol),
            "name": meta.get("shortName") or meta.get("longName") or symbol.upper(),
            "exchange": meta.get("exchangeName", "-"),
            "currency": meta.get("currency", "USD"),
            "current_price": round(current, 2),
            "prev_close": round(prev_close, 2),
            "change": round(change, 2),
            "change_pct": round(change_pct, 2),
            "open": safe("open"),
            "high": safe("high"),
            "low": safe("low"),
            "volume": volume,
            "timestamp": int(timestamps[last_index]),
            "market_type": "equity",
            "day_high": meta.get("regularMarketDayHigh"),
            "day_low": meta.get("regularMarketDayLow"),
            "fifty_two_week_high": meta.get("fiftyTwoWeekHigh"),
            "fifty_two_week_low": meta.get("fiftyTwoWeekLow"),
        }
        self._quote_cache[key] = (now, dict(result))
        return result

    async def get_intraday(self, symbol, range_="1d", interval="5m"):
        payload = await self._fetch(symbol, range_, interval)
        chart = payload.get("chart", {}).get("result")
        if not chart:
            raise ValueError("No chart data for " + symbol)
        result = chart[0]
        ts = result.get("timestamp", []) or []
        q = (result.get("indicators", {}).get("quote") or [{}])[0]
        series = []
        for i, t in enumerate(ts):
            c = (q.get("close") or [None] * len(ts))[i]
            if c is None:
                continue
            series.append({
                "t": int(t),
                "o": (q.get("open") or [None] * len(ts))[i],
                "h": (q.get("high") or [None] * len(ts))[i],
                "l": (q.get("low") or [None] * len(ts))[i],
                "c": c,
                "v": (q.get("volume") or [None] * len(ts))[i],
            })
        meta = result.get("meta", {})
        return {
            "symbol": symbol.upper(),
            "yahoo_symbol": normalize_symbol(symbol),
            "currency": meta.get("currency", "USD"),
            "range": range_,
            "interval": interval,
            "series": series,
        }

    async def get_quotes_batch(self, symbols):
        results = await asyncio.gather(*(self._safe_quote(s) for s in symbols))
        return [r for r in results if r is not None]

    async def _safe_quote(self, symbol):
        try:
            q = await self.get_quote(symbol)
            q["source"] = "yahoo"
            return q
        except Exception:
            return None

    # ---- Broker-routed entry points (preferred path) ---------------------------------

    async def get_quote_routed(self, symbol: str, db: AsyncSession):
        """Live quote from connected broker if available, else Yahoo (delayed)."""
        from .market_providers import pick_provider_for
        provider = await pick_provider_for(symbol, db)
        if provider is not None:
            try:
                q = await provider["adapter"].get_quote(provider["creds"], symbol)
                if q is not None:
                    return q.to_dict()
            except Exception as exc:
                log.warning("Broker quote failed for %s via %s: %s — falling back to Yahoo",
                            symbol, provider["broker_name"], exc)
        # Yahoo fallback
        q = await self.get_quote(symbol)
        q["source"] = "yahoo"
        return q

    async def get_intraday_routed(self, symbol: str, db: AsyncSession, range_="1d", interval="5m"):
        """Intraday bars from connected broker (live) or Yahoo (delayed)."""
        from .market_providers import pick_provider_for
        # Brokers only serve *intraday* (minute/hour) bars for the current session;
        # they can't return months of daily/weekly candles. So for daily+ intervals
        # (chart zoom-out: 3mo/6mo/1y) skip the broker and let Yahoo honour `range`.
        is_intraday = (interval or "").strip().lower().endswith(("m", "h"))
        provider = await pick_provider_for(symbol, db) if is_intraday else None
        if provider is not None and hasattr(provider["adapter"], "get_intraday"):
            try:
                iv_min = _interval_to_minutes(interval)
                bars = await provider["adapter"].get_intraday(provider["creds"], symbol, interval_min=iv_min)
                if bars:
                    return {
                        "symbol": symbol.upper(),
                        "source": provider["broker_name"],
                        "currency": "INR" if provider["region"] == "IN" else "USD",
                        "range": range_,
                        "interval": interval,
                        "series": [b.__dict__ for b in bars],
                    }
            except Exception as exc:
                log.warning("Broker intraday failed for %s via %s: %s — falling back to Yahoo",
                            symbol, provider["broker_name"], exc)
        data = await self.get_intraday(symbol, range_=range_, interval=interval)
        data["source"] = "yahoo"
        return data

    async def get_quotes_batch_routed(self, symbols: list, db: AsyncSession):
        """Group symbols by routing target, batch each group into a single broker call.

        This turns a 10-symbol watchlist from 10 sequential broker requests into
        ONE call (Dhan supports batched quote_data) — the single biggest perf win
        when a broker is connected.
        """
        from .market_providers import pick_provider_for

        # Bucket symbols by adapter — symbols routed to the same broker share a batch
        buckets: dict = {}
        fallback: list = []
        provider_for_symbol: dict = {}
        for s in symbols:
            provider = await pick_provider_for(s, db)
            if provider is None or not hasattr(provider["adapter"], "get_quotes_batch"):
                fallback.append(s)
                continue
            key = id(provider["adapter"])
            buckets.setdefault(key, {"provider": provider, "symbols": []})["symbols"].append(s)
            provider_for_symbol[s] = provider

        results: list = []

        # 1) Batched broker calls — one round-trip per broker
        async def run_bucket(bucket):
            try:
                quotes = await bucket["provider"]["adapter"].get_quotes_batch(
                    bucket["provider"]["creds"], bucket["symbols"]
                )
            except Exception as exc:
                log.warning("Batched broker quotes failed (%s): %s",
                            bucket["provider"]["broker_name"], exc)
                fallback.extend(bucket["symbols"])
                return []
            # Track which symbols the broker actually returned — anything missing
            # (silently dropped by the upstream) falls through to Yahoo so the
            # watchlist row is never blank.
            returned_keys = {k.upper() for k in quotes.keys()}
            missing = [s for s in bucket["symbols"] if s.upper() not in returned_keys]
            if missing:
                log.info("Broker %s returned %d/%d symbols, falling back to Yahoo for: %s",
                         bucket["provider"]["broker_name"], len(quotes), len(bucket["symbols"]), missing)
                fallback.extend(missing)
            return [q.to_dict() for q in quotes.values()]

        bucket_results = await asyncio.gather(*(run_bucket(b) for b in buckets.values()))
        for batch in bucket_results:
            results.extend(batch)

        # 2) Fallback symbols → Yahoo in parallel
        async def yahoo_one(s):
            try:
                q = await self.get_quote(s)
                q["source"] = "yahoo"
                return q
            except Exception:
                return None
        yahoo_results = await asyncio.gather(*(yahoo_one(s) for s in fallback))
        results.extend(r for r in yahoo_results if r is not None)
        return results


def _interval_to_minutes(interval: str) -> int:
    """'1m' → 1, '5m' → 5, '15m' → 15, '1h' → 60."""
    s = (interval or "").strip().lower()
    if s.endswith("m") and s[:-1].isdigit():
        return int(s[:-1])
    if s.endswith("h") and s[:-1].isdigit():
        return int(s[:-1]) * 60
    return 5


market_data_service = MarketDataService()
