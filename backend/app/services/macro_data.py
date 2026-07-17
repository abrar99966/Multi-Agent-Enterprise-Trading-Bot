"""Macro data adapter -- slow-path enrichment (US Treasury + FRED).

Two free public sources feed the macro regime analyst (slowpath/macro_regime.py):

* **US Treasury daily par-yield curve** -- NO API key. The Treasury publishes an
  XML feed of every business day's yield curve. We read it to get the 2Y and 10Y
  par yields and the 10Y-2Y spread; a negative spread (an inverted curve) is a
  classic recession/stress precursor.

* **FRED** (Federal Reserve Economic Data) -- free API key (``ETB_FRED_API_KEY``).
  Used for series like VIXCLS (equity-implied vol) and DFF (fed funds). Disabled
  (returns ``[]``) when no key is set, so the app runs unchanged without one.

Design contract (matches services/openbb_adapter.py):
  * OFF the deterministic fast path -- external network, real wall clock. Never
    called inside the replay/bus dispatch loop.
  * Every method degrades gracefully: on any failure it returns an empty result
    and never raises. A macro outage must not affect trading.

The XML/JSON parsing is split into pure functions (``parse_treasury_xml`` /
``parse_fred_json``) so they can be unit-tested offline with fixtures, with no
network access.
"""
from __future__ import annotations

import logging
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import get_settings

log = logging.getLogger(__name__)

# No-key Treasury daily par-yield-curve XML feed (data.gov / home.treasury.gov).
_TREASURY_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
    "pages/xml"
)
_FRED_URL = "https://api.stlouisfed.org/fred/series/observations"


@dataclass(frozen=True)
class YieldCurvePoint:
    """One business day's par yields (percent) and the 10Y-2Y spread."""

    date: str
    y2: Optional[float]
    y10: Optional[float]

    @property
    def spread_10y_2y(self) -> Optional[float]:
        if self.y2 is None or self.y10 is None:
            return None
        return self.y10 - self.y2

    @property
    def inverted(self) -> bool:
        s = self.spread_10y_2y
        return s is not None and s < 0.0


def _strip_ns(tag: str) -> str:
    """`{namespace}LocalName` -> `LocalName` (the Treasury feed is namespaced)."""
    return tag.rsplit("}", 1)[-1]


def _to_float(text: Optional[str]) -> Optional[float]:
    if text is None:
        return None
    try:
        return float(text.strip())
    except (ValueError, AttributeError):
        return None


def parse_treasury_xml(xml_text: str) -> List[YieldCurvePoint]:
    """Parse the Treasury yield-curve XML into a date-sorted list of points.

    Pure function -- no network. The feed is an Atom document whose entries carry
    ``<d:NEW_DATE>``, ``<d:BC_2YEAR>``, ``<d:BC_10YEAR>`` (namespaced). We match
    on the stripped local names so a namespace-URL change doesn't break parsing.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        log.debug("Treasury XML parse failed: %s", exc)
        return []

    points: List[YieldCurvePoint] = []
    for props in root.iter():
        if _strip_ns(props.tag) != "properties":
            continue
        fields: Dict[str, str] = {}
        for child in props:
            fields[_strip_ns(child.tag)] = (child.text or "").strip()
        date = fields.get("NEW_DATE", "")
        if not date:
            continue
        points.append(
            YieldCurvePoint(
                date=date,
                y2=_to_float(fields.get("BC_2YEAR")),
                y10=_to_float(fields.get("BC_10YEAR")),
            )
        )
    points.sort(key=lambda p: p.date)
    return points


def parse_fred_json(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract ``[{date, value}]`` from a FRED observations JSON response.

    Pure function. FRED marks missing values with ``"."``; those are dropped.
    """
    out: List[Dict[str, Any]] = []
    for obs in payload.get("observations", []):
        raw = obs.get("value")
        val = _to_float(raw)
        if val is None:  # FRED uses "." for missing observations
            continue
        out.append({"date": obs.get("date", ""), "value": val})
    return out


class _TTLCache:
    def __init__(self, default_ttl: float = 1800.0) -> None:
        self._store: Dict[str, tuple] = {}
        self._default_ttl = default_ttl

    def get(self, key: str) -> Optional[Any]:
        entry = self._store.get(key)
        if entry is None or time.monotonic() > entry[1]:
            return None
        return entry[0]

    def set(self, key: str, data: Any, ttl: Optional[float] = None) -> None:
        self._store[key] = (data, time.monotonic() + (ttl or self._default_ttl))

    def clear(self) -> None:
        self._store.clear()


class MacroDataAdapter:
    """US Treasury + FRED macro reader. Every method returns empty on failure."""

    def __init__(self, cache_ttl: float = 1800.0, timeout_s: float = 15.0) -> None:
        self._cache = _TTLCache(default_ttl=cache_ttl)
        self._timeout = timeout_s

    @property
    def fred_enabled(self) -> bool:
        return bool(get_settings().fred_api_key.strip())

    async def yield_curve(self) -> List[YieldCurvePoint]:
        """Latest Treasury par-yield curve points (no key required)."""
        cached = self._cache.get("treasury:curve")
        if cached is not None:
            return cached
        # The feed is indexed by year (`field_tdr_date_value`); the bare query
        # returns "No results found". Wall clock is fine here -- this adapter is
        # off the deterministic replay path. Fall back to the previous year when
        # the current-year feed is empty (e.g. the first days of January).
        year = datetime.now(timezone.utc).year
        points: List[YieldCurvePoint] = []
        try:
            async with httpx.AsyncClient(timeout=self._timeout,
                                         follow_redirects=True) as client:
                for yr in (year, year - 1):
                    resp = await client.get(
                        _TREASURY_URL,
                        params={
                            "data": "daily_treasury_yield_curve",
                            "field_tdr_date_value": str(yr),
                        },
                    )
                    resp.raise_for_status()
                    points = parse_treasury_xml(resp.text)
                    if points:
                        break
        except Exception as exc:  # network / parse -- degrade to empty
            log.debug("Treasury yield_curve fetch failed: %s", exc)
            return []
        self._cache.set("treasury:curve", points)
        return points

    async def latest_yield_curve(self) -> Optional[YieldCurvePoint]:
        """Most recent yield-curve point with both a 2Y and 10Y reading."""
        points = await self.yield_curve()
        for p in reversed(points):
            if p.y2 is not None and p.y10 is not None:
                return p
        return None

    async def fred_series(self, series_id: str, limit: int = 30) -> List[Dict[str, Any]]:
        """Recent observations for a FRED series. Empty when no key is set."""
        if not self.fred_enabled:
            return []
        ck = f"fred:{series_id}:{limit}"
        cached = self._cache.get(ck)
        if cached is not None:
            return cached
        try:
            async with httpx.AsyncClient(timeout=self._timeout,
                                         follow_redirects=True) as client:
                resp = await client.get(
                    _FRED_URL,
                    params={
                        "series_id": series_id,
                        "api_key": get_settings().fred_api_key.strip(),
                        "file_type": "json",
                        "sort_order": "desc",
                        "limit": limit,
                    },
                )
                resp.raise_for_status()
                obs = parse_fred_json(resp.json())
        except Exception as exc:
            log.debug("FRED fred_series(%s) failed: %s", series_id, exc)
            return []
        self._cache.set(ck, obs)
        return obs

    async def latest_value(self, series_id: str) -> Optional[float]:
        """Most recent numeric value of a FRED series, or None."""
        obs = await self.fred_series(series_id, limit=1)
        return obs[0]["value"] if obs else None

    def clear_cache(self) -> None:
        self._cache.clear()


# Module-level singleton (mirrors services/openbb_adapter.py::openbb_data).
macro_data = MacroDataAdapter()
