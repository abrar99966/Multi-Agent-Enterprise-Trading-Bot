/**
 * Data layer for the Markets + Order Book modules.
 *
 * WHY this lives inside the module directory instead of lib/ws/api.js: the shell
 * owns lib/ws/ and it does not exist yet. The jget contract below is byte-for-byte
 * the documented one, so when the shell lands this file can be deleted and the
 * imports repointed with no call-site changes. (Same approach as ops/opsApi.js —
 * duplicated rather than imported so the two modules stay independently deletable.)
 */
import { useCallback, useState } from 'react';

/** Resolved lazily — window.__API__ may be injected after this module evaluates. */
export function apiBase() {
  return (typeof window !== 'undefined' && window.__API__) || 'http://127.0.0.1:8000';
}

/**
 * Fetcher tagged with a cacheKey so useLivePoll's SWR cache keys off the URL.
 * Market-data failures are FastAPI `{detail}` prose (404 unknown symbol, 502
 * upstream), and that string is the only diagnostic the user gets — so it is
 * lifted into the Error message rather than discarded behind a status code.
 */
export const jget = (url) => {
  const f = (signal) =>
    fetch(url, { signal }).then(async (r) => {
      if (!r.ok) {
        let detail = '';
        try {
          const body = await r.json();
          if (typeof body?.detail === 'string') detail = body.detail;
        } catch {
          /* non-JSON error body — fall through to the status code */
        }
        throw new Error(detail || `HTTP ${r.status}`);
      }
      return r.json();
    });
  f.cacheKey = url;
  return f;
};

/* ---- polling cadences ----------------------------------------------------
   Fixed by the workspace contract. `providers` is deliberately far slower than
   everything else: that endpoint does a DB round-trip plus a live broker probe
   per 30s server cache cycle, so it is a mount-time fetch, not a poll. */
export const MARKETS_CADENCE = {
  watchlist: 20000,
  quote: 20000,
  intraday: 20000,
  providers: 300000,
};

/** Matches the backend's own default universe so a cold start looks identical. */
export const DEFAULT_SYMBOLS = [
  'NIFTY', 'BANKNIFTY', 'SENSEX', 'RELIANCE', 'INFY',
  'TCS', 'HDFCBANK', 'AAPL', 'MSFT', 'NVDA',
];

/* ---- symbols ------------------------------------------------------------- */

/** Uppercase + strip anything that can't be a ticker. Returns '' if unusable. */
export function normalizeSymbolInput(raw) {
  const s = String(raw ?? '').trim().toUpperCase().replace(/[^A-Z0-9.\-&]/g, '');
  return s.length >= 1 && s.length <= 20 ? s : '';
}

const INDIAN_HINTS = new Set(['NIFTY', 'BANKNIFTY', 'SENSEX', 'FINNIFTY', 'MIDCPNIFTY']);

/**
 * Currency fallback. The Finnhub branch of /quotes/{symbol} omits `currency`
 * entirely, so a missing value must be inferred rather than rendered blank —
 * an unlabelled price on a mixed IN/US watchlist is worse than a guess.
 */
function inferCurrency(q) {
  if (q?.currency) return q.currency;
  const ys = String(q?.yahoo_symbol ?? '');
  if (ys.endsWith('.NS') || ys.endsWith('.BO')) return 'INR';
  if (INDIAN_HINTS.has(String(q?.symbol ?? '').toUpperCase())) return 'INR';
  return 'USD';
}

/* ---- quote normalisation -------------------------------------------------
   /market-data/watchlist and /market-data/quotes/{symbol} can each answer in a
   different shape depending on which provider served the row:
     broker (dhan) -> 14 base keys
     yahoo         -> those 14 plus yahoo_symbol, market_type, day_high/low and
                      the fifty_two_week_ pair
     finnhub       -> DIFFERENT NAMES: `price` not `current_price`,
                      `change_percent` not `change_pct`, and no name/exchange/
                      currency/volume/timestamp at all.
   Binding components straight to `current_price` renders a blank row the moment
   Finnhub serves (which only happens in prod, where a key is configured), so
   every consumer in this module reads the normalised shape below instead. */

export function normalizeQuote(q) {
  if (!q || typeof q !== 'object') return null;
  const symbol = String(q.symbol ?? '').toUpperCase();
  if (!symbol) return null;

  const num = (v) => (Number.isFinite(Number(v)) ? Number(v) : null);
  const price = num(q.current_price ?? q.price);
  const prevClose = num(q.prev_close);
  const change = num(q.change);
  // `change_pct` is already a percent (1.23 === 1.23%), never a ratio.
  const changePct = num(q.change_pct ?? q.change_percent);

  return {
    symbol,
    name: q.name || symbol,
    exchange: q.exchange || null,
    currency: inferCurrency(q),
    price,
    prevClose,
    // Derive whichever of change/change% the provider omitted, so sorting and
    // colouring behave the same across all three shapes.
    change: change ?? (price != null && prevClose != null ? price - prevClose : null),
    changePct:
      changePct ??
      (price != null && prevClose ? ((price - prevClose) / prevClose) * 100 : null),
    open: num(q.open),
    high: num(q.high ?? q.day_high),
    low: num(q.low ?? q.day_low),
    volume: num(q.volume),
    // Wire value is UNIX SECONDS; every consumer here works in ms.
    tsMs: num(q.timestamp) != null ? num(q.timestamp) * 1000 : null,
    source: q.source || 'unknown',
    yahooSymbol: q.yahoo_symbol || null,
    // Yahoo-only extras — absent on broker and Finnhub rows.
    week52High: num(q.fifty_two_week_high),
    week52Low: num(q.fifty_two_week_low),
  };
}

/**
 * Watchlist rows arrive in provider-completion order (broker batch first, then
 * Yahoo fallbacks), which is neither stable nor the requested order — so the
 * client sorts. Symbols that failed on BOTH paths are dropped silently with no
 * error field, hence `missing`: without it a half-empty grid looks like a bug.
 */
export function reconcileWatchlist(payload, requested) {
  const rows = Array.isArray(payload?.quotes) ? payload.quotes : [];
  const quotes = rows.map(normalizeQuote).filter(Boolean);
  const byPos = new Map(requested.map((s, i) => [s, i]));
  quotes.sort((a, b) => {
    const ai = byPos.has(a.symbol) ? byPos.get(a.symbol) : Number.MAX_SAFE_INTEGER;
    const bi = byPos.has(b.symbol) ? byPos.get(b.symbol) : Number.MAX_SAFE_INTEGER;
    return ai - bi || a.symbol.localeCompare(b.symbol);
  });
  const got = new Set(quotes.map((q) => q.symbol));
  return { quotes, missing: requested.filter((s) => !got.has(s)) };
}

/** Newest quote timestamp across the book — drives the feed freshness chip. */
export function freshestTs(quotes) {
  let t = null;
  (quotes || []).forEach((q) => {
    if (q?.tsMs != null && (t === null || q.tsMs > t)) t = q.tsMs;
  });
  return t;
}

/* ---- synthetic depth -----------------------------------------------------
   This backend exposes NO level-2 / depth-of-market endpoint anywhere in its
   REST surface — quotes are last-trade only. Rather than ship an empty DOM, the
   ladder below is MODELLED from the last quote and is labelled as such in the
   UI at every level (panel badge, hatched size bars, footer disclaimer).

   Two deliberate properties keep it honest:
     1. It is DETERMINISTIC — seeded from symbol+price, so it does not shimmer
        between polls. Animated fake depth would read as a live feed; static
        depth that only moves when the price moves reads as what it is.
     2. Nothing derived from it is presented as a metric. The totals footer is
        explicitly scoped to "modelled", never to real market liquidity. */

/** mulberry32 — tiny deterministic PRNG. Same seed, same ladder, every render. */
function mulberry32(seed) {
  let t = seed >>> 0;
  return () => {
    t = (t + 0x6d2b79f5) >>> 0;
    let x = Math.imul(t ^ (t >>> 15), 1 | t);
    x ^= x + Math.imul(x ^ (x >>> 7), 61 | x);
    return ((x ^ (x >>> 14)) >>> 0) / 4294967296;
  };
}

/** FNV-1a — stable string hash for the PRNG seed. */
function hashStr(s) {
  let h = 2166136261;
  for (let i = 0; i < s.length; i += 1) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

/** NSE-style tick bands. Used for level spacing and price decimals. */
export function tickSizeFor(price) {
  const p = Math.abs(Number(price) || 0);
  if (p < 10) return 0.01;
  if (p < 100) return 0.05;
  if (p < 1000) return 0.1;
  if (p < 10000) return 0.5;
  return 1;
}

const roundToTick = (v, tick) => Number((Math.round(v / tick) * tick).toFixed(4));
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

/** Median — the large-order threshold is relative, so one block doesn't hide the rest. */
function median(xs) {
  if (!xs.length) return 0;
  const s = [...xs].sort((a, b) => a - b);
  const m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

/**
 * Build a modelled ladder around `price`. Returns null when there is no usable
 * price, so the caller renders an empty state rather than a ladder of zeros.
 */
export function buildSyntheticLadder({ symbol, price, volume, levels = 10 }) {
  const p = Number(price);
  if (!Number.isFinite(p) || p <= 0) return null;

  const tick = tickSizeFor(p);
  const rnd = mulberry32(hashStr(`${symbol}|${p.toFixed(4)}|${levels}`));

  // Touch sits 1-2 ticks either side of last — a plausible retail-visible spread.
  const halfTicks = 1 + Math.floor(rnd() * 2);
  const bestBid = roundToTick(p - halfTicks * tick, tick);
  const bestAsk = roundToTick(p + halfTicks * tick, tick);

  // Scale level sizes off daily volume when the provider gave us one, so a
  // large-cap ladder doesn't look like an illiquid one.
  const v = Number(volume);
  const base = Number.isFinite(v) && v > 0 ? clamp(v / 3000, 5, 25000) : 250;

  const mkSize = (i) => {
    const shape = 0.55 + i * 0.14; // books thicken away from the touch
    const jitter = 0.5 + rnd() * 1.3;
    const block = rnd() < 0.11 ? 3.4 : 1; // occasional institutional block
    return Math.max(1, Math.round(base * shape * jitter * block));
  };

  const bids = [];
  const asks = [];
  let cumB = 0;
  let cumA = 0;
  for (let i = 0; i < levels; i += 1) {
    const bs = mkSize(i);
    cumB += bs;
    bids.push({ price: roundToTick(bestBid - i * tick, tick), size: bs, cum: cumB });
    const as = mkSize(i);
    cumA += as;
    asks.push({ price: roundToTick(bestAsk + i * tick, tick), size: as, cum: cumA });
  }

  const mid = (bestBid + bestAsk) / 2;
  const spread = bestAsk - bestBid;
  const sizes = [...bids, ...asks].map((l) => l.size);

  return {
    synthetic: true,
    tick,
    mid,
    bestBid,
    bestAsk,
    spread,
    spreadBps: mid ? (spread / mid) * 10000 : null,
    bids,
    asks,
    bidTotal: cumB,
    askTotal: cumA,
    // Ratio of resting bid size to total — the classic book-pressure read.
    imbalance: cumB + cumA ? cumB / (cumB + cumA) : 0.5,
    largeThreshold: median(sizes) * 2.2,
  };
}

/* ---- intraday ------------------------------------------------------------ */

/**
 * Bars for the prints tape. Bars whose close is null are dropped server-side on
 * the Yahoo path, so the series has real time gaps — consumers must not assume
 * a fixed cadence. An empty series is a valid 200 (market closed), never an error.
 */
export function normalizeSeries(payload) {
  const raw = Array.isArray(payload?.series) ? payload.series : [];
  const bars = raw
    .map((b) => ({
      tMs: Number.isFinite(Number(b?.t)) ? Number(b.t) * 1000 : null, // wire is SECONDS
      o: Number.isFinite(Number(b?.o)) ? Number(b.o) : null,
      h: Number.isFinite(Number(b?.h)) ? Number(b.h) : null,
      l: Number.isFinite(Number(b?.l)) ? Number(b.l) : null,
      c: Number.isFinite(Number(b?.c)) ? Number(b.c) : null,
      v: Number.isFinite(Number(b?.v)) ? Number(b.v) : null,
    }))
    .filter((b) => b.tMs != null && b.c != null);
  return {
    bars,
    source: payload?.source || null,
    interval: payload?.interval || null,
    currency: payload?.currency || null,
  };
}

/* ---- store bridge --------------------------------------------------------
   The workspace store is not built yet. This module therefore takes its shared
   selection as an optional controlled prop and falls back to local state, so it
   works standalone today and becomes store-driven the moment the shell passes
   value + onChange. Nothing here reaches into a global. */
export function useControllable(value, onChange, initial) {
  const [inner, setInner] = useState(initial);
  const controlled = value !== undefined;
  const current = controlled ? value : inner;
  const set = useCallback(
    (next) => {
      if (!controlled) setInner(next);
      if (onChange) onChange(next);
    },
    [controlled, onChange],
  );
  return [current, set];
}
