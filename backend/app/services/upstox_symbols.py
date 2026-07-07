"""Upstox symbol resolver.

Upstox addresses every instrument by an `instrument_key` like
`NSE_EQ|INE002A01018` (segment + ISIN) or `NSE_INDEX|Nifty 50`. We download
their compact CSV instrument master on first use, cache to disk, refresh
weekly. Look-ups are O(1) dict hits.
"""
from __future__ import annotations

import asyncio
import csv
import gzip
import io
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

import httpx

log = logging.getLogger(__name__)

MASTER_URL = "https://assets.upstox.com/market-quote/instruments/exchange/complete.csv.gz"
CACHE_DIR = Path(__file__).resolve().parents[2] / ".cache"
CACHE_FILE = CACHE_DIR / "upstox_instruments.csv"
REFRESH_AFTER_SECONDS = 7 * 24 * 3600

# Index naming differs between brokers
INDEX_ALIASES = {
    "NIFTY": "Nifty 50",
    "NIFTY50": "Nifty 50",
    "BANKNIFTY": "Nifty Bank",
    "FINNIFTY": "Nifty Fin Service",
    "SENSEX": "SENSEX",
    "BANKEX": "BANKEX",
}


@dataclass(frozen=True)
class UpstoxRef:
    symbol: str               # caller input
    instrument_key: str       # e.g. "NSE_EQ|INE002A01018"
    exchange: str             # NSE / BSE
    segment: str              # NSE_EQ / NSE_INDEX / BSE_EQ ...
    instrument_type: str      # EQ / INDEX / FUT / OPT
    name: str
    trading_symbol: str
    isin: Optional[str] = None


_INDEX: Dict[str, UpstoxRef] = {}
_INDEX_LOCK = threading.RLock()
_LOAD_LOCK = asyncio.Lock()


def _file_is_fresh(p: Path) -> bool:
    return p.exists() and p.stat().st_size > 500_000 and (time.time() - p.stat().st_mtime) < REFRESH_AFTER_SECONDS


async def _download_master(timeout: float = 60.0) -> bool:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_FILE.with_suffix(".csv.tmp")
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            r = await client.get(MASTER_URL)
            r.raise_for_status()
            # Gzipped CSV — decompress to plain CSV on disk
            with gzip.GzipFile(fileobj=io.BytesIO(r.content)) as gz:
                tmp.write_bytes(gz.read())
        tmp.replace(CACHE_FILE)
        return True
    except Exception as exc:
        log.warning("Failed to download Upstox instruments: %s", exc)
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        return False


def _load_csv_into_index(path: Path) -> int:
    """Upstox CSV columns (verified Nov 2024):
      instrument_key, exchange_token, tradingsymbol, name, last_price,
      expiry, strike, tick_size, lot_size, instrument_type, option_type, exchange

    The `exchange` column holds the full segment string ("NSE_EQ", "BSE_EQ",
    "NSE_INDEX", ...). instrument_type is "EQUITY" or "INDEX" (not "EQ").
    There is no separate `segment` or `isin` column — ISIN is embedded in
    the instrument_key for equities (e.g. NSE_EQ|INE002A01018).
    """
    new_index: Dict[str, UpstoxRef] = {}
    with path.open("r", encoding="utf-8", errors="ignore", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            instrument_key = (row.get("instrument_key") or "").strip()
            if not instrument_key:
                continue
            instr_type = (row.get("instrument_type") or "").strip().upper()
            # Limit to EQUITY + INDEX for now (F&O needs expiry-aware lookup)
            if instr_type not in ("EQUITY", "INDEX"):
                continue
            trading = (row.get("tradingsymbol") or "").strip()
            name = (row.get("name") or trading).strip()
            segment = (row.get("exchange") or "").strip().upper()  # NSE_EQ, BSE_EQ, NSE_INDEX, ...
            if not trading or not segment:
                continue

            # Parent exchange (NSE / BSE / MCX) derived from the segment prefix
            exchange = segment.split("_", 1)[0]

            # ISIN extracted from instrument_key when available (NSE_EQ|INE002A01018)
            isin = None
            if "|" in instrument_key:
                tail = instrument_key.split("|", 1)[1]
                if tail.startswith("INE") and len(tail) == 12:
                    isin = tail

            ref = UpstoxRef(
                symbol=trading.upper(),
                instrument_key=instrument_key,
                exchange=exchange,
                segment=segment,
                instrument_type=instr_type,
                name=name,
                trading_symbol=trading,
                isin=isin,
            )

            # Index under several aliases so callers can pass "RELIANCE",
            # "RELIANCE.NS", "INE002A01018", or even the company name.
            keys = {trading.upper(), name.upper()}
            if exchange == "NSE":
                keys.add(f"{trading.upper()}.NS")
            elif exchange == "BSE":
                keys.add(f"{trading.upper()}.BO")
            if isin:
                keys.add(isin.upper())

            for k in keys:
                existing = new_index.get(k)
                # Prefer NSE_EQ over BSE_EQ; prefer first INDEX hit on NSE_INDEX
                if existing:
                    existing_score = (existing.exchange == "NSE") * 2 + (existing.instrument_type == instr_type)
                    new_score = (exchange == "NSE") * 2 + 1
                    if existing_score >= new_score:
                        continue
                new_index[k] = ref

    with _INDEX_LOCK:
        _INDEX.clear()
        _INDEX.update(new_index)
    log.info("Upstox instrument index loaded: %d keys", len(new_index))
    return len(new_index)


async def ensure_loaded(force_refresh: bool = False) -> int:
    with _INDEX_LOCK:
        already = len(_INDEX)
    if already and not force_refresh:
        return already

    async with _LOAD_LOCK:
        with _INDEX_LOCK:
            already = len(_INDEX)
        if already and not force_refresh:
            return already

        if force_refresh or not _file_is_fresh(CACHE_FILE):
            await _download_master()

        if not CACHE_FILE.exists():
            return 0

        count = await asyncio.to_thread(_load_csv_into_index, CACHE_FILE)
        return count


def _lookup_key(symbol: str) -> str:
    s = symbol.upper().strip()
    if s in INDEX_ALIASES:
        return INDEX_ALIASES[s].upper()
    if s.endswith(".NS") or s.endswith(".BO"):
        return s  # try the suffixed form directly first
    return s


def resolve(symbol: str) -> Optional[UpstoxRef]:
    key = _lookup_key(symbol)
    with _INDEX_LOCK:
        ref = _INDEX.get(key)
        if ref is None and (key.endswith(".NS") or key.endswith(".BO")):
            ref = _INDEX.get(key[:-3])
        return ref


async def resolve_async(symbol: str) -> Optional[UpstoxRef]:
    ref = resolve(symbol)
    if ref is not None:
        return ref
    await ensure_loaded()
    return resolve(symbol)


def _is_equity_share(ref: "UpstoxRef") -> bool:
    """True only for ordinary equity SHARES — not bonds, ETFs, or govt securities.

    The Upstox master tags ETFs, NCDs/bonds, SGBs and SDLs all as instrument_type
    "EQUITY" in the cash segment, so type alone is far too broad (~9,300 on NSE).
    The reliable discriminator is the ISIN security-type code: Indian equity
    shares are `INE…01…` (the "01" series at ISIN positions 7–8). Bonds are
    `…07/08…`, ETFs/MFs start `INF`, and government securities start `IN1`/`IN2`
    — none of which get an ISIN assigned by the loader (it keeps only `INE…`),
    so the `isin[7:9] == "01"` test cleanly isolates real shares.
    """
    isin = ref.isin
    return bool(isin) and len(isin) == 12 and isin[7:9] == "01"


def list_equities(exchange: str = "NSE", limit: Optional[int] = None,
                  shares_only: bool = True) -> list[str]:
    """Every equity-share trading symbol on `exchange` from the loaded master.

    Deduplicated by instrument_key (the index holds several aliases per stock).
    With `shares_only` (default) the list is filtered to ordinary shares via the
    ISIN rule — excluding bonds/ETFs/govt securities. Returns [] if the master
    hasn't loaded yet — call `ensure_loaded()` first (or use `list_equities_async`).
    The master is a PUBLIC CSV, so this works even without a broker connection.
    """
    ex = (exchange or "NSE").upper()
    seen: set = set()
    out: list[str] = []
    with _INDEX_LOCK:
        for ref in _INDEX.values():
            if ref.instrument_type != "EQUITY":
                continue
            if ex and ref.exchange != ex:
                continue
            if shares_only and not _is_equity_share(ref):
                continue
            if ref.instrument_key in seen:
                continue
            seen.add(ref.instrument_key)
            out.append(ref.trading_symbol.upper())
    out = sorted(set(out))
    return out[:limit] if limit else out


async def list_equities_async(exchange: str = "NSE", limit: Optional[int] = None) -> list[str]:
    """Ensure the master is loaded, then list every equity on `exchange`."""
    await ensure_loaded()
    return list_equities(exchange, limit)
