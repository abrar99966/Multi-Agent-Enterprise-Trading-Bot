"""Real news headlines for sentiment — Google News RSS (free, no key).

The NewsAgent scores these headlines with a financial lexicon. Previously this
returned MOCK data when no NEWS_API_KEY was set, which made sentiment a constant
neutral. Now it pulls real, recent headlines from Google News RSS (no key
required), with NewsAPI used instead when a key is configured. On any failure it
returns [] so sentiment is honestly neutral — never fabricated.
"""
import html
import os
import re
import time
from typing import Dict, List

import httpx

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"


class NewsIngestionService:
    _TTL = 600.0  # cache headlines 10 min — they change slowly + avoids hammering

    def __init__(self):
        self.api_key = os.getenv("NEWS_API_KEY")
        self.base_url = "https://newsapi.org/v2/everything"
        self._cache: dict = {}  # query(upper) -> (monotonic_ts, items)

    async def fetch_news(self, query: str) -> List[Dict]:
        """Recent headlines for a symbol/topic. Cached; [] on failure (honest neutral)."""
        q = (query or "").strip()
        if not q:
            return []
        key = q.upper()
        now = time.monotonic()
        cached = self._cache.get(key)
        if cached and now - cached[0] < self._TTL:
            return cached[1]
        try:
            items = await (self._fetch_newsapi(q) if self.api_key else self._fetch_google_rss(q))
        except Exception:
            items = []
        # Only cache NON-EMPTY results — an empty fetch is usually a transient rate
        # limit, not "no news"; caching it would wrongly pin sentiment neutral for
        # the whole TTL. Leaving it uncached lets the next call retry.
        if items:
            self._cache[key] = (now, items)
        return items

    async def _fetch_google_rss(self, query: str) -> List[Dict]:
        # "share price" yields far better Indian-ticker coverage than "stock"
        # (e.g. SBIN/ABB: 0 → 100 results).
        params = {"q": f"{query} share price", "hl": "en-IN", "gl": "IN", "ceid": "IN:en"}
        async with httpx.AsyncClient(timeout=8, headers={"User-Agent": _UA}, follow_redirects=True) as c:
            r = await c.get("https://news.google.com/rss/search", params=params)
            r.raise_for_status()
            xml = r.text
        items: List[Dict] = []
        for m in re.finditer(r"<item>(.*?)</item>", xml, re.S):
            block = m.group(1)
            tm = re.search(r"<title>(.*?)</title>", block, re.S)
            if not tm:
                continue
            title = html.unescape(re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", tm.group(1), flags=re.S)).strip()
            if not title:
                continue
            sm = re.search(r"<source[^>]*>(.*?)</source>", block, re.S)
            pm = re.search(r"<pubDate>(.*?)</pubDate>", block, re.S)
            items.append({
                "title": title,
                "content": title,
                "source": html.unescape(sm.group(1)).strip() if sm else "Google News",
                "published_at": pm.group(1).strip() if pm else None,
            })
            if len(items) >= 8:
                break
        return items

    async def _fetch_newsapi(self, query: str) -> List[Dict]:
        params = {"q": query, "apiKey": self.api_key, "sortBy": "publishedAt", "pageSize": 8}
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get(self.base_url, params=params)
            if r.status_code != 200:
                return []
            return [
                {"title": a.get("title"), "content": a.get("description"),
                 "source": (a.get("source") or {}).get("name"), "published_at": a.get("publishedAt")}
                for a in r.json().get("articles", []) if a.get("title")
            ]


news_service = NewsIngestionService()
