"""Symbol universes for AI training.

Defines pre-built symbol lists the user can train on without typing each one.
Mix of broad-market indexes (for regime detection) and large-cap equities
(for tradable signals).
"""
from typing import Dict, List


# NSE/BSE major broad-market + sector indexes (16 total)
# All resolve via market_data.INDEX_ALIAS → Yahoo Finance tickers.
MAJOR_INDEXES_NSE: List[str] = [
    "NIFTY",            # Nifty 50
    "BANKNIFTY",        # Bank Nifty
    "SENSEX",           # BSE Sensex
    "FINNIFTY",         # Nifty Financial Services
    "NIFTYNEXT50",      # Nifty Next 50
    "NIFTYMIDCAP100",   # Nifty Midcap 100
    "NIFTYSMALLCAP100", # Nifty Smallcap 100
    "NIFTYIT",          # Sector: IT
    "NIFTYAUTO",        # Sector: Auto
    "NIFTYPHARMA",      # Sector: Pharma
    "NIFTYFMCG",        # Sector: FMCG
    "NIFTYMETAL",       # Sector: Metal
    "NIFTYREALTY",      # Sector: Realty
    "NIFTYENERGY",      # Sector: Energy
    "NIFTYPSUBANK",     # Sector: PSU Banks
    "BANKEX",           # BSE Bankex
]

# NIFTY 50 constituents (as of 2024-2025 — refreshed periodically by NSE).
# These are the 50 most-traded blue-chips on NSE.
NIFTY_50: List[str] = [
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK",
    "HINDUNILVR", "SBIN", "BHARTIARTL", "ITC", "KOTAKBANK",
    "LT", "BAJFINANCE", "AXISBANK", "ASIANPAINT", "MARUTI",
    "SUNPHARMA", "TITAN", "HCLTECH", "ULTRACEMCO", "NESTLEIND",
    "BAJAJFINSV", "WIPRO", "ONGC", "NTPC", "POWERGRID",
    "TATAMOTORS", "ADANIENT", "ADANIPORTS", "TATASTEEL", "JSWSTEEL",
    "M&M", "TECHM", "COALINDIA", "GRASIM", "INDUSINDBK",
    "DIVISLAB", "DRREDDY", "CIPLA", "BRITANNIA", "HEROMOTOCO",
    "EICHERMOT", "HINDALCO", "BAJAJ-AUTO", "BPCL", "IOC",
    "APOLLOHOSP", "HDFCLIFE", "SBILIFE", "LTIM", "TATACONSUM",
]


# Predefined universes the UI exposes as dropdown options
UNIVERSES: Dict[str, Dict] = {
    "watchlist": {
        "label": "Dashboard watchlist (6 symbols)",
        "symbols": ["RELIANCE", "INFY", "TCS", "HDFCBANK", "ICICIBANK", "SBIN"],
        "description": "Quick test — same 6 stocks as the dashboard default. ~5s training.",
    },
    "indexes": {
        "label": "All major NSE/BSE indexes (16 symbols)",
        "symbols": MAJOR_INDEXES_NSE,
        "description": "All Nifty + sector indexes. Good for regime-aware signals. ~30s training.",
    },
    "nifty50": {
        "label": "NIFTY 50 constituents (50 stocks)",
        "symbols": NIFTY_50,
        "description": "All 50 NIFTY blue-chips. Most useful for stock-trading signals. ~90s training.",
    },
    "indexes_plus_nifty50": {
        "label": "Indexes + NIFTY 50 (66 symbols, recommended)",
        "symbols": MAJOR_INDEXES_NSE + NIFTY_50,
        "description": "Indexes for regime + NIFTY 50 for individual stocks. Most comprehensive. ~2 min training.",
    },
}


# Dynamic universes resolved at run time from the broker instrument master,
# so we cover the WHOLE market without hardcoding thousands of tickers.
DYNAMIC_UNIVERSES: Dict[str, Dict] = {
    "all_nse": {
        "label": "All NSE equities (whole market)",
        "source": "master",
        "exchange": "NSE",
        "approx_count": 2900,
        "with_indexes": True,
        "description": (
            "Every NSE-listed equity share (~2,900) from the Upstox instrument master, plus "
            "the major indexes. Bonds, ETFs and govt securities are filtered out. Backtests "
            "the entire market — this is slow (sequential history fetch); use the 'Max symbols' "
            "cap for a quicker run. Works without a broker login (public master CSV)."
        ),
    },
    "stored": {
        "label": "All stored symbols (data store)",
        "source": "store",
        "approx_count": None,
        "description": (
            "Every symbol already downloaded into the durable bar store. Train the tournament "
            "across your whole stored market — runs fast and fully offline (no re-fetching)."
        ),
    },
}

# Map a generic interval to the key the bar store uses (matches what fetch_bars persists).
_STORE_IV = {
    "1minute": "1minute", "1m": "1minute",
    "30minute": "30minute", "30m": "30minute",
    "day": "day", "1d": "day",
    "week": "week", "1w": "week",
}


def resolve_universe(preset: str, custom_symbols: List[str] | None = None) -> List[str]:
    """Pick the symbol list for a STATIC preset key, with 'custom' bypassing.

    Dynamic presets (e.g. 'all_nse') are NOT resolved here — use
    `resolve_universe_async`, which can hit the broker instrument master.
    """
    if preset == "custom" and custom_symbols:
        return [s.strip().upper() for s in custom_symbols if s.strip()]
    return list(UNIVERSES.get(preset, UNIVERSES["watchlist"])["symbols"])


async def resolve_universe_async(
    preset: str, custom_symbols: List[str] | None = None, max_symbols: int | None = None,
    interval: str = "day",
) -> List[str]:
    """Resolve any preset — static, custom, or dynamic (whole-market / stored) — to a symbol list."""
    if preset == "custom" and custom_symbols:
        symbols = [s.strip().upper() for s in custom_symbols if s.strip()]
    elif preset in DYNAMIC_UNIVERSES:
        cfg = DYNAMIC_UNIVERSES[preset]
        if cfg.get("source") == "store":
            # Everything already downloaded into the durable bar store.
            from . import bar_store
            symbols = bar_store.stored_symbols(_STORE_IV.get(interval, "day"))
        else:
            from ..services import upstox_symbols
            equities = await upstox_symbols.list_equities_async(cfg["exchange"])
            # Lead with the indexes (regime context), then the full equity list.
            symbols = (MAJOR_INDEXES_NSE + equities) if cfg.get("with_indexes") else list(equities)
    else:
        symbols = resolve_universe(preset, custom_symbols)
    if max_symbols and max_symbols > 0:
        symbols = symbols[:max_symbols]
    return symbols


def _dynamic_count(cfg: Dict) -> int | None:
    """Live count for a dynamic universe (stored = actual rows; master = approx)."""
    if cfg.get("source") == "store":
        try:
            from . import bar_store
            return bar_store.coverage_summary().get("symbols")
        except Exception:
            return None
    return cfg.get("approx_count")


def list_universes() -> List[Dict]:
    """Surfaced via API for the UI dropdown (static presets + dynamic whole-market ones)."""
    static = [
        {"key": k, "label": v["label"], "count": len(v["symbols"]),
         "description": v["description"], "dynamic": False}
        for k, v in UNIVERSES.items()
    ]
    dynamic = [
        {"key": k, "label": v["label"], "count": _dynamic_count(v),
         "description": v["description"], "dynamic": True}
        for k, v in DYNAMIC_UNIVERSES.items()
    ]
    return static + dynamic


def is_known_preset(preset: str) -> bool:
    return preset in UNIVERSES or preset in DYNAMIC_UNIVERSES or preset == "custom"
