"""Dhan symbol resolver.

Dhan's API addresses instruments by numeric `security_id`, not by ticker. The
broker publishes a master CSV listing every tradable instrument. We download
it lazily on first lookup, cache to disk, and refresh weekly. Lookups after
the first one are O(1) dict hits.

Public surface:
    resolve(symbol) -> SymbolRef | None
    resolve_async(symbol) -> SymbolRef | None
"""
from __future__ import annotations

import asyncio
import csv
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

import httpx

log = logging.getLogger(__name__)

# Dhan's compact scrip master — ~50MB, refreshed daily by Dhan
MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
CACHE_DIR = Path(__file__).resolve().parents[2] / ".cache"
CACHE_FILE = CACHE_DIR / "dhan_scrip_master.csv"
REFRESH_AFTER_SECONDS = 7 * 24 * 3600  # weekly

# Common index aliases — Dhan stores them under SEM_TRADING_SYMBOL with spaces.
INDEX_ALIASES = {
    "NIFTY": "NIFTY 50",
    "NIFTY50": "NIFTY 50",
    "BANKNIFTY": "NIFTY BANK",
    "FINNIFTY": "NIFTY FIN SERVICE",
    "SENSEX": "SENSEX",
    "BANKEX": "BANKEX",
}


@dataclass(frozen=True)
class SymbolRef:
    symbol: str             # caller's input symbol (uppercased)
    security_id: str        # Dhan numeric id (as string)
    exchange_segment: str   # NSE_EQ, BSE_EQ, IDX_I, NSE_FNO, ...
    instrument_type: str    # EQUITY, INDEX, FUTIDX, OPTIDX, ...
    trading_symbol: str     # SEM_TRADING_SYMBOL
    name: str               # SEM_CUSTOM_SYMBOL


# Process-wide cache
_INDEX: Dict[str, SymbolRef] = {}
_INDEX_LOCK = threading.RLock()
_LOAD_LOCK = asyncio.Lock()


def _exchange_segment_from_row(row: dict) -> Optional[str]:
    """Map Dhan's row fields to the segment code expected by the trading API."""
    exch = (row.get("SEM_EXM_EXCH_ID") or "").strip().upper()
    seg_raw = (row.get("SEM_SEGMENT") or "").strip().upper()
    instr = (row.get("SEM_INSTRUMENT_NAME") or "").strip().upper()

    if instr == "INDEX":
        return "IDX_I"
    if exch == "NSE":
        if seg_raw == "E":
            return "NSE_EQ"
        if seg_raw == "D":
            return "NSE_FNO"
        if seg_raw == "C":
            return "NSE_CURRENCY"
    if exch == "BSE":
        if seg_raw == "E":
            return "BSE_EQ"
        if seg_raw == "D":
            return "BSE_FNO"
    if exch == "MCX":
        return "MCX_COMM"
    return None


def _load_csv_into_index(path: Path) -> int:
    """Parse the CSV and rebuild the in-memory index."""
    new_index: Dict[str, SymbolRef] = {}
    with path.open("r", encoding="utf-8", errors="ignore", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            instr = (row.get("SEM_INSTRUMENT_NAME") or "").strip().upper()
            # Limit to equity + index for now; F&O resolution would need expiry-aware lookup.
            if instr not in ("EQUITY", "INDEX"):
                continue
            seg = _exchange_segment_from_row(row)
            if not seg:
                continue
            trading = (row.get("SEM_TRADING_SYMBOL") or "").strip()
            custom = (row.get("SEM_CUSTOM_SYMBOL") or "").strip() or trading
            sid = (row.get("SEM_SMST_SECURITY_ID") or "").strip()
            if not sid or not trading:
                continue

            ref = SymbolRef(
                symbol=trading.upper(),
                security_id=sid,
                exchange_segment=seg,
                instrument_type=instr,
                trading_symbol=trading,
                name=custom,
            )

            # Index under several keys so callers can pass "RELIANCE", "RELIANCE-EQ",
            # "RELIANCE.NS", or the literal trading symbol.
            keys = {trading.upper(), custom.upper()}
            if trading.endswith("-EQ"):
                keys.add(trading[:-3].upper())
            keys.add(f"{trading.upper()}.NS" if seg == "NSE_EQ" else trading.upper())
            for k in keys:
                # Prefer NSE_EQ over BSE_EQ when both exist
                existing = new_index.get(k)
                if existing and existing.exchange_segment == "NSE_EQ" and seg == "BSE_EQ":
                    continue
                new_index[k] = ref
    with _INDEX_LOCK:
        _INDEX.clear()
        _INDEX.update(new_index)
    log.info("Dhan symbol index loaded: %d keys covering equity+index", len(new_index))
    return len(new_index)


def _file_is_fresh(path: Path) -> bool:
    if not path.exists() or path.stat().st_size < 1_000_000:
        return False
    age = time.time() - path.stat().st_mtime
    return age < REFRESH_AFTER_SECONDS


async def _download_master(timeout: float = 30.0) -> bool:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_FILE.with_suffix(".csv.tmp")
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            async with client.stream("GET", MASTER_URL) as r:
                r.raise_for_status()
                with tmp.open("wb") as f:
                    async for chunk in r.aiter_bytes(64 * 1024):
                        f.write(chunk)
        tmp.replace(CACHE_FILE)
        return True
    except Exception as exc:
        log.warning("Failed to download Dhan scrip master: %s", exc)
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        return False


async def ensure_loaded(force_refresh: bool = False) -> int:
    """Make sure the in-memory index is populated. Returns key count."""
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

        # CSV parsing is CPU-bound; run in a thread to keep the loop free.
        count = await asyncio.to_thread(_load_csv_into_index, CACHE_FILE)
        return count


def _lookup_key(symbol: str) -> str:
    s = symbol.upper().strip()
    if s in INDEX_ALIASES:
        return INDEX_ALIASES[s]
    if s.endswith(".NS"):
        return s[:-3]
    return s


def resolve(symbol: str) -> Optional[SymbolRef]:
    """Synchronous lookup (will return None if the index hasn't been loaded yet)."""
    key = _lookup_key(symbol)
    with _INDEX_LOCK:
        return _INDEX.get(key)


async def resolve_async(symbol: str) -> Optional[SymbolRef]:
    """Lookup, loading the index on first call."""
    ref = resolve(symbol)
    if ref is not None:
        return ref
    await ensure_loaded()
    return resolve(symbol)
