"""External screener adapters — Chartink, TradingView, Screener.in.

Pulls scan results from public Indian screeners and normalises them to a common
hit shape so the UI and our edge-annotator can treat every source the same.

⚠ ToS / stability caveat (read before relying on this):
  None of these sources publish an official public API. We hit the same endpoints
  their own web UIs use. That means:
    • It can break without notice if they change their site.
    • Heavy/automated use may violate their Terms of Service and get your IP
      throttled or blocked. This is intended for *personal research* use only —
      not for redistribution or a hosted product.
  We send a real browser User-Agent, throttle, and never hammer in a loop.

Source mechanics (verified live):
  • TradingView — POST scanner.tradingview.com/india/scan with a JSON filter.
    No auth. Returns {data: [{s: 'NSE:ITC', d: [<columns...>]}]}.
  • Chartink — GET /screener/ to grab the CSRF token + cookies, then POST
    /screener/process with a `scan_clause`. Returns {data: [{nsecode, name,
    close, per_chg, volume, ...}]}.
  • Screener.in — custom queries are login-walled, but public pre-built screens
    at /screens/<id>/ render an HTML table we can scrape for symbols. Pass a
    numeric screen id or a full screen URL as the `scan`.

Every adapter returns:
  {"source", "scan", "label", "count", "hits": [ExternalHit, ...]}
ExternalHit = {"symbol" (bare NSE code), "name", "price", "change_pct",
               "volume", "extra": {source-specific fields}}
"""
from __future__ import annotations

import logging
import re
from typing import Dict, List

import httpx

log = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
_TIMEOUT = 20.0


def _bare_symbol(s: str) -> str:
    """Strip exchange prefixes/suffixes → bare NSE ticker (NSE:ITC → ITC, RELIANCE.NS → RELIANCE)."""
    s = (s or "").strip().upper()
    if ":" in s:
        s = s.split(":", 1)[1]
    for suf in (".NS", ".BO", ".NSE", ".BSE"):
        if s.endswith(suf):
            s = s[: -len(suf)]
    return s


# ======================================================================================
# TradingView
# ======================================================================================

_TV_URL = "https://scanner.tradingview.com/india/scan"
_TV_COLUMNS = ["name", "close", "change", "volume", "RSI", "market_cap_basic"]

# preset -> (label, filters, (sort_by, sort_order))
_TV_PRESETS: Dict[str, dict] = {
    "rsi_oversold": {
        "label": "RSI < 35 (oversold)",
        "filter": [{"left": "RSI", "operation": "less", "right": 35},
                   {"left": "volume", "operation": "greater", "right": 100000}],
        "sort": ("volume", "desc"),
    },
    "rsi_overbought": {
        "label": "RSI > 70 (overbought)",
        "filter": [{"left": "RSI", "operation": "greater", "right": 70},
                   {"left": "volume", "operation": "greater", "right": 100000}],
        "sort": ("volume", "desc"),
    },
    "top_gainers": {
        "label": "Top gainers (> +3%)",
        "filter": [{"left": "change", "operation": "greater", "right": 3},
                   {"left": "volume", "operation": "greater", "right": 100000}],
        "sort": ("change", "desc"),
    },
    "top_losers": {
        "label": "Top losers (< -3%)",
        "filter": [{"left": "change", "operation": "less", "right": -3},
                   {"left": "volume", "operation": "greater", "right": 100000}],
        "sort": ("change", "asc"),
    },
    "volume_shockers": {
        "label": "Volume shockers (> 1M)",
        "filter": [{"left": "volume", "operation": "greater", "right": 1000000}],
        "sort": ("volume", "desc"),
    },
}


def _tv_presets() -> List[dict]:
    return [{"key": k, "label": v["label"]} for k, v in _TV_PRESETS.items()]


async def _tv_scan(scan: str, limit: int = 100) -> dict:
    cfg = _TV_PRESETS.get(scan)
    if cfg is None:
        raise ValueError(f"Unknown TradingView scan '{scan}'. Valid: {list(_TV_PRESETS.keys())}")
    sort_by, sort_order = cfg["sort"]
    body = {
        "filter": cfg["filter"],
        "options": {"lang": "en"},
        "markets": ["india"],
        "symbols": {"query": {"types": []}, "tickers": []},
        "columns": _TV_COLUMNS,
        "sort": {"sortBy": sort_by, "sortOrder": sort_order},
        "range": [0, max(1, min(limit, 500))],
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT, headers={"User-Agent": _UA}) as c:
        r = await c.post(_TV_URL, json=body)
        r.raise_for_status()
        data = r.json()
    hits: List[dict] = []
    for row in data.get("data", []):
        d = row.get("d") or []
        col = dict(zip(_TV_COLUMNS, d))
        hits.append({
            "symbol": _bare_symbol(row.get("s", "")),
            "name": col.get("name"),
            "price": col.get("close"),
            "change_pct": round(col["change"], 2) if col.get("change") is not None else None,
            "volume": col.get("volume"),
            "extra": {
                "rsi": round(col["RSI"], 1) if col.get("RSI") is not None else None,
                "market_cap": col.get("market_cap_basic"),
                "exchange": (row.get("s", "").split(":", 1)[0] if ":" in row.get("s", "") else None),
            },
        })
    return {"source": "tradingview", "scan": scan, "label": cfg["label"],
            "count": data.get("totalCount", len(hits)), "hits": hits}


# ======================================================================================
# Chartink
# ======================================================================================

_CK_BASE = "https://chartink.com"
_CK_PRESETS: Dict[str, dict] = {
    "rsi_below_30": {
        "label": "RSI(14) < 30",
        "clause": "( {cash} ( latest rsi( 14 ) < 30 ) )",
    },
    "rsi_above_70": {
        "label": "RSI(14) > 70",
        "clause": "( {cash} ( latest rsi( 14 ) > 70 ) )",
    },
    "macd_bullish_crossover": {
        "label": "MACD bullish crossover",
        "clause": ("( {cash} ( latest macd line( 26 , 12 , 9 ) > latest macd signal( 26 , 12 , 9 ) and "
                   "1 day ago macd line( 26 , 12 , 9 ) <= 1 day ago macd signal( 26 , 12 , 9 ) ) )"),
    },
    "volume_breakout": {
        "label": "Volume breakout (2× SMA20, up day)",
        "clause": ("( {cash} ( latest volume > latest sma( latest volume , 20 ) * 2 and "
                   "latest close > latest open ) )"),
    },
    "golden_cross": {
        "label": "Golden cross 50/200 (today)",
        "clause": ("( {cash} ( latest sma( latest close , 50 ) > latest sma( latest close , 200 ) and "
                   "1 day ago sma( latest close , 50 ) <= 1 day ago sma( latest close , 200 ) ) )"),
    },
}


def _ck_presets() -> List[dict]:
    return [{"key": k, "label": v["label"]} for k, v in _CK_PRESETS.items()]


async def _ck_scan(scan: str, limit: int = 100) -> dict:
    cfg = _CK_PRESETS.get(scan)
    if cfg is None:
        raise ValueError(f"Unknown Chartink scan '{scan}'. Valid: {list(_CK_PRESETS.keys())}")
    async with httpx.AsyncClient(timeout=_TIMEOUT, headers={"User-Agent": _UA}, follow_redirects=True) as c:
        page = await c.get(f"{_CK_BASE}/screener/")
        m = re.search(r'name="csrf-token"\s+content="([^"]+)"', page.text)
        token = m.group(1) if m else None
        if not token:
            raise RuntimeError("Chartink CSRF token not found — site layout may have changed.")
        r = await c.post(
            f"{_CK_BASE}/screener/process",
            data={"scan_clause": cfg["clause"]},
            headers={"X-CSRF-TOKEN": token, "X-Requested-With": "XMLHttpRequest"},
        )
        r.raise_for_status()
        data = r.json()
    rows = data.get("data") or []
    hits: List[dict] = []
    for row in rows[:limit]:
        hits.append({
            "symbol": _bare_symbol(row.get("nsecode", "")),
            "name": row.get("name"),
            "price": row.get("close"),
            "change_pct": row.get("per_chg"),
            "volume": row.get("volume"),
            "extra": {"bsecode": row.get("bsecode")},
        })
    return {"source": "chartink", "scan": scan, "label": cfg["label"],
            "count": len(rows), "hits": hits}


# ======================================================================================
# Screener.in (public pre-built screens — fundamentals)
# ======================================================================================

_SI_BASE = "https://www.screener.in"
# Public pre-built screens (id/slug → label). Custom queries need login; these
# don't. The slug IS required — Screener.in 404s on a bare id. Paste any public
# screen URL via `scan` to use your own.
_SI_PRESETS: Dict[str, dict] = {
    "357649/low-pe": {"label": "Low P/E (large caps)"},
}
# Company links carry either an NSE ticker (RELIANCE) or a numeric BSE code; we
# keep the alphabetic NSE-style ones so they map to our store/annotation.
_SI_ROW_RE = re.compile(r'/company/([0-9A-Za-z&._-]+)/[^>]*>\s*([^<]+?)\s*</a>')


def _si_presets() -> List[dict]:
    return [{"key": k, "label": v["label"]} for k, v in _SI_PRESETS.items()]


def _si_screen_id(scan: str) -> str:
    """Accept an 'id/slug' path or a full screen URL → return the 'id/slug' path."""
    scan = (scan or "").strip()
    m = re.search(r"/screens/([^?#]+?)/?(?:[?#]|$)", scan)
    if m:
        return m.group(1).strip("/")
    return scan.strip("/")


async def _si_scan(scan: str, limit: int = 100) -> dict:
    path = _si_screen_id(scan)
    if not path:
        raise ValueError("Screener.in needs an 'id/slug' path or a /screens/<id>/<slug>/ URL as `scan`.")
    url = f"{_SI_BASE}/screens/{path}/"
    async with httpx.AsyncClient(timeout=_TIMEOUT, headers={"User-Agent": _UA}, follow_redirects=True) as c:
        r = await c.get(url)
        r.raise_for_status()
        html = r.text
    seen = set()
    hits: List[dict] = []
    for sym, name in _SI_ROW_RE.findall(html):
        sym = _bare_symbol(sym)
        if not sym or sym.isdigit() or sym in seen:   # drop BSE numeric codes — no NSE map
            continue
        seen.add(sym)
        hits.append({"symbol": sym, "name": name.strip(), "price": None,
                     "change_pct": None, "volume": None, "extra": {}})
        if len(hits) >= limit:
            break
    label = _SI_PRESETS.get(path, {}).get("label", f"Screen {path}")
    return {"source": "screener_in", "scan": path, "label": label,
            "count": len(hits), "hits": hits}


# ======================================================================================
# Public dispatch
# ======================================================================================

SOURCES = {
    "tradingview": {"label": "TradingView", "presets": _tv_presets, "scan": _tv_scan,
                    "note": "Live technical scanner. No login. Pick a preset."},
    "chartink": {"label": "Chartink", "presets": _ck_presets, "scan": _ck_scan,
                 "note": "Indian technical screener. No login. Pick a preset."},
    "screener_in": {"label": "Screener.in", "presets": _si_presets, "scan": _si_scan,
                    "note": "Fundamentals via PUBLIC screens. Pass a screen id or /screens/<id>/ URL (custom queries need a login)."},
}


def list_sources() -> List[dict]:
    return [
        {"key": k, "label": v["label"], "note": v["note"], "presets": v["presets"]()}
        for k, v in SOURCES.items()
    ]


async def run_external_scan(source: str, scan: str, limit: int = 100) -> dict:
    """Run a scan on one external source and return normalised hits."""
    src = SOURCES.get(source)
    if src is None:
        raise ValueError(f"Unknown source '{source}'. Valid: {list(SOURCES.keys())}")
    return await src["scan"](scan, limit=limit)
