/**
 * usePortfolioData — the portfolio module's entire data layer.
 *
 * WHY a single hook: seven endpoints have to be cross-joined (positions need
 * live quotes, quotes need the position symbol list, strategy labels need the
 * journal AI view) and doing that inside the presentational components would
 * make each of them un-renderable without a backend. Panels below receive plain
 * arrays.
 *
 * ------------------------------------------------------------------------
 * HONEST NOTE ON SOURCES — read before changing a binding.
 *
 * There is no `/positions` endpoint on this backend. Open positions come from
 * one of two places, in priority order:
 *
 *   1. GET /api/v1/dash/journal/{name}/trading — the ONLY payload that carries
 *      real {symbol, qty, avg_price, last_price, realized_pnl, unrealized_pnl}
 *      plus an equity curve. It is journal-scoped, so it needs a journal name
 *      from GET /api/v1/dash/journals first.
 *   2. GET /api/v1/trades/history — fallback when no journal exists. It has NO
 *      pnl column (documented: get_trade_history omits it), so positions are
 *      reconstructed here by replaying fills with average-cost accounting.
 *
 * Live LTP is layered on top from the watchlist. Everything downstream is
 * marked with `source` so the UI can be honest about which path produced it.
 * ------------------------------------------------------------------------
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useLivePoll } from '../../../../lib/useLivePoll';

const API = (typeof window !== 'undefined' && window.__API__) || 'http://127.0.0.1:8000';

/** Tagged fetcher — `cacheKey` lets useLivePoll seed from its SWR cache. */
const jget = (url) => {
  const f = (signal) =>
    fetch(url, { signal }).then((r) => {
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    });
  f.cacheKey = url;
  return f;
};

/** Placeholder used when a journal-scoped call has no journal to scope to. */
const idleFetcher = () => Promise.resolve(null);

/* ---- documented polling cadences ---------------------------------------- */
const MS_WATCHLIST = 20000;
const MS_SLOW = 60000; // history / accounts / risk / journal projections
const MS_PERF = 120000;

/* ---- instrument classification ------------------------------------------ */

/**
 * Bucket a symbol into the allocation categories.
 *
 * WHY heuristic: neither the journal positions payload nor /trades/history
 * carries an instrument type. Only /trades/recommendations exposes `market`
 * (equity|f_o|commodity|crypto|forex), and it covers pending ideas rather than
 * held positions. So classification is done on the ticker itself and the UI
 * labels the donut as derived, not authoritative.
 */
export function classifyInstrument(symbol) {
  const s = String(symbol || '').toUpperCase();
  if (!s) return 'other';
  if (/\d(CE|PE)$/.test(s) || /(CE|PE)$/.test(s.replace(/\d+$/, '')) || /OPT$/.test(s)) return 'options';
  if (/FUT$/.test(s) || /\d{2}[A-Z]{3}FUT/.test(s)) return 'futures';
  if (/^(BTC|ETH|SOL|XRP|DOGE|USDT)/.test(s)) return 'other';
  return 'equity';
}

/**
 * Static sector map. Client-side by necessity — the backend exposes no sector
 * or GICS field on any endpoint in this API surface. Unknown tickers fall into
 * "Unclassified" rather than being silently dropped, so the heatmap total
 * always reconciles with gross exposure.
 */
const SECTORS = {
  RELIANCE: 'Energy',
  ONGC: 'Energy',
  BPCL: 'Energy',
  IOC: 'Energy',
  NTPC: 'Utilities',
  POWERGRID: 'Utilities',
  TATAPOWER: 'Utilities',
  INFY: 'Technology',
  TCS: 'Technology',
  WIPRO: 'Technology',
  HCLTECH: 'Technology',
  TECHM: 'Technology',
  LTIM: 'Technology',
  HDFCBANK: 'Financials',
  ICICIBANK: 'Financials',
  SBIN: 'Financials',
  AXISBANK: 'Financials',
  KOTAKBANK: 'Financials',
  BAJFINANCE: 'Financials',
  INDUSINDBK: 'Financials',
  BANKNIFTY: 'Financials',
  HINDUNILVR: 'Consumer Staples',
  ITC: 'Consumer Staples',
  NESTLEIND: 'Consumer Staples',
  BRITANNIA: 'Consumer Staples',
  DABUR: 'Consumer Staples',
  MARUTI: 'Consumer Disc.',
  TATAMOTORS: 'Consumer Disc.',
  M_M: 'Consumer Disc.',
  TITAN: 'Consumer Disc.',
  BAJAJ_AUTO: 'Consumer Disc.',
  SUNPHARMA: 'Healthcare',
  DRREDDY: 'Healthcare',
  CIPLA: 'Healthcare',
  DIVISLAB: 'Healthcare',
  APOLLOHOSP: 'Healthcare',
  TATASTEEL: 'Materials',
  JSWSTEEL: 'Materials',
  HINDALCO: 'Materials',
  ULTRACEMCO: 'Materials',
  GRASIM: 'Materials',
  LT: 'Industrials',
  ADANIPORTS: 'Industrials',
  SIEMENS: 'Industrials',
  BHARTIARTL: 'Communication',
  NIFTY: 'Index',
  SENSEX: 'Index',
  FINNIFTY: 'Index',
  AAPL: 'Technology',
  MSFT: 'Technology',
  NVDA: 'Technology',
  GOOGL: 'Communication',
  META: 'Communication',
  AMZN: 'Consumer Disc.',
  TSLA: 'Consumer Disc.',
  JPM: 'Financials',
  XOM: 'Energy',
};

export function sectorOf(symbol) {
  const s = String(symbol || '').toUpperCase();
  return SECTORS[s] || 'Unclassified';
}

/* ---- normalisers --------------------------------------------------------- */

/**
 * Quote field names differ per provider. The Finnhub branch of
 * /market-data/quotes uses `price`/`change_percent` while the broker and Yahoo
 * branches use `current_price`/`change_pct`. Normalising here means no panel
 * renders blank the day a Finnhub key is configured in prod.
 */
export function normalizeQuote(q) {
  if (!q) return null;
  const price = q.current_price != null ? q.current_price : q.price;
  const pct = q.change_pct != null ? q.change_pct : q.change_percent;
  return {
    symbol: String(q.symbol || '').toUpperCase(),
    price: Number.isFinite(Number(price)) ? Number(price) : null,
    changePct: Number.isFinite(Number(pct)) ? Number(pct) : null,
    change: Number.isFinite(Number(q.change)) ? Number(q.change) : null,
    prevClose: q.prev_close != null ? Number(q.prev_close) : null,
    currency: q.currency || null,
    source: q.source || null,
    // Backend sends UNIX SECONDS; the whole UI works in ms.
    ts: Number.isFinite(Number(q.timestamp)) ? Number(q.timestamp) * 1000 : null,
  };
}

/** Journal timestamps are integer NANOSECONDS since epoch. */
const nsToMs = (ns) => (Number.isFinite(Number(ns)) ? Number(ns) / 1e6 : null);

/**
 * Reconstruct open positions from the flat trade blotter.
 * Average-cost accounting: adds average into the position, reductions realise
 * P&L against the running average, and a flip resets the basis to the fill.
 */
export function positionsFromTrades(trades) {
  const book = new Map();
  // Oldest first — /trades/history returns newest first.
  const ordered = [...(trades || [])].sort((a, b) => {
    const ta = a.executed_at ? Date.parse(`${a.executed_at}Z`) : 0;
    const tb = b.executed_at ? Date.parse(`${b.executed_at}Z`) : 0;
    return ta - tb;
  });

  for (const t of ordered) {
    const sym = String(t.symbol || '').toUpperCase();
    if (!sym) continue;
    const status = String(t.status || '').toUpperCase();
    // REJECTED never traded; OPEN/PLACED have no fill yet.
    if (status === 'REJECTED' || status === 'OPEN' || status === 'PLACED') continue;

    const px = t.executed_price != null ? Number(t.executed_price) : Number(t.placed_price);
    const qty = Number(t.quantity);
    if (!Number.isFinite(px) || !Number.isFinite(qty) || qty === 0) continue;

    const signed = String(t.side || '').toUpperCase() === 'SELL' ? -qty : qty;
    const p = book.get(sym) || { symbol: sym, qty: 0, avg: 0, realized: 0, paper: !!t.is_paper, last: null };

    if (p.qty === 0 || Math.sign(p.qty) === Math.sign(signed)) {
      const next = p.qty + signed;
      p.avg = next !== 0 ? (p.avg * p.qty + px * signed) / next : 0;
      p.qty = next;
    } else {
      const closing = Math.min(Math.abs(signed), Math.abs(p.qty));
      // Long close: (px - avg) * qty. Short close: (avg - px) * qty.
      p.realized += (px - p.avg) * closing * Math.sign(p.qty);
      const next = p.qty + signed;
      if (next === 0) p.avg = 0;
      else if (Math.sign(next) !== Math.sign(p.qty)) p.avg = px; // flipped through zero
      p.qty = next;
    }
    p.paper = !!t.is_paper;
    p.last = px;
    book.set(sym, p);
  }

  return [...book.values()]
    .filter((p) => Math.abs(p.qty) > 1e-9)
    .map((p) => ({
      symbol: p.symbol,
      qty: p.qty,
      avgPrice: p.avg,
      lastPrice: p.last,
      realized: p.realized,
      unrealized: null,
      paper: p.paper,
      source: 'trades',
    }));
}

/* ---- the hook ------------------------------------------------------------ */

export function usePortfolioData() {
  /* 1. Journals — the index that unlocks every journal-scoped projection. */
  const journalsQ = useLivePoll(useMemo(() => jget(`${API}/api/v1/dash/journals`), []), MS_SLOW, ['ws:journals']);

  const journals = useMemo(() => {
    const list = journalsQ.data && Array.isArray(journalsQ.data.journals) ? journalsQ.data.journals : [];
    // Verified, non-empty journals first — a broken chain still gets listed so
    // the operator can see it, but it should not be the default selection.
    return [...list].sort((a, b) => {
      const rank = (j) => (j.chain_ok && j.records > 0 ? 0 : j.records > 0 ? 1 : 2);
      return rank(a) - rank(b) || String(a.name).localeCompare(String(b.name));
    });
  }, [journalsQ.data]);

  const [journalName, setJournalName] = useState(null);
  useEffect(() => {
    // Auto-select once, and re-select if the chosen journal disappears.
    if (journals.length && !journals.some((j) => j.name === journalName)) {
      setJournalName(journals[0].name);
    }
  }, [journals, journalName]);

  const jn = journalName;
  const jPath = jn ? encodeURIComponent(jn) : null;

  /* 2. Journal projections — positions, equity curve, strategy attribution. */
  const tradingQ = useLivePoll(
    useMemo(() => (jPath ? jget(`${API}/api/v1/dash/journal/${jPath}/trading`) : idleFetcher), [jPath]),
    MS_SLOW,
    [`ws:trading:${jn || 'none'}`],
  );

  const aiQ = useLivePoll(
    useMemo(() => (jPath ? jget(`${API}/api/v1/dash/journal/${jPath}/ai`) : idleFetcher), [jPath]),
    MS_SLOW,
    [`ws:ai:${jn || 'none'}`],
  );

  /* 3. Blotter / accounts / grading stats. */
  const historyQ = useLivePoll(useMemo(() => jget(`${API}/api/v1/trades/history`), []), MS_SLOW, ['ws:history']);
  const accountsQ = useLivePoll(useMemo(() => jget(`${API}/api/v1/brokers/accounts`), []), MS_SLOW, ['ws:accounts']);
  const statsQ = useLivePoll(
    useMemo(() => jget(`${API}/api/v1/performance/stats?days=30`), []),
    MS_PERF,
    ['ws:perfstats'],
  );

  /* 4. Base positions — journal first, blotter reconstruction as fallback. */
  const basePositions = useMemo(() => {
    const jp = tradingQ.data && Array.isArray(tradingQ.data.positions) ? tradingQ.data.positions : null;
    if (jp && jp.length) {
      return jp
        .filter((p) => Math.abs(Number(p.qty) || 0) > 1e-9)
        .map((p) => ({
          symbol: String(p.symbol || '').toUpperCase(),
          qty: Number(p.qty) || 0,
          avgPrice: Number(p.avg_price) || 0,
          lastPrice: p.last_price != null ? Number(p.last_price) : null,
          realized: Number(p.realized_pnl) || 0,
          // Documented: 0.0 (not null) when last_price is missing.
          unrealized: Number(p.unrealized_pnl) || 0,
          paper: false,
          source: 'journal',
        }));
    }
    const trades = historyQ.data && Array.isArray(historyQ.data.trades) ? historyQ.data.trades : [];
    return positionsFromTrades(trades);
  }, [tradingQ.data, historyQ.data]);

  /* 5. Live quotes for exactly the symbols we hold. */
  const symbolsKey = useMemo(
    () => [...new Set(basePositions.map((p) => p.symbol))].sort().join(','),
    [basePositions],
  );

  const watchlistQ = useLivePoll(
    useMemo(
      () =>
        symbolsKey
          ? jget(`${API}/api/v1/market-data/watchlist?symbols=${encodeURIComponent(symbolsKey)}`)
          : idleFetcher,
      [symbolsKey],
    ),
    MS_WATCHLIST,
    [`ws:wl:${symbolsKey || 'none'}`],
  );

  const quotes = useMemo(() => {
    const raw = watchlistQ.data && Array.isArray(watchlistQ.data.quotes) ? watchlistQ.data.quotes : [];
    const m = new Map();
    // Response order is NOT stable and may be shorter than requested (symbols
    // that fail on both provider paths are dropped silently) — so key by symbol.
    for (const q of raw) {
      const n = normalizeQuote(q);
      if (n && n.symbol) m.set(n.symbol, n);
    }
    return m;
  }, [watchlistQ.data]);

  /* 6. Strategy attribution from the journal AI view (last 100 intents). */
  const strategyBySymbol = useMemo(() => {
    const decisions = aiQ.data && Array.isArray(aiQ.data.decisions) ? aiQ.data.decisions : [];
    const m = new Map();
    // Ascending scan leaves the most recent strategy per symbol in the map.
    for (const d of decisions) {
      const s = String(d.symbol || '').toUpperCase();
      if (s && d.strategy_id) m.set(s, d.strategy_id);
    }
    return m;
  }, [aiQ.data]);

  /* 7. Enriched positions — the shape every panel consumes. */
  const positions = useMemo(
    () =>
      basePositions.map((p) => {
        const q = quotes.get(p.symbol);
        const ltp = q && q.price != null ? q.price : p.lastPrice;
        const priced = ltp != null ? ltp : p.avgPrice;
        // Recompute against the live tick when we have one; otherwise keep the
        // journal's own figure rather than inventing a number.
        const unrealized =
          ltp != null && Number.isFinite(p.avgPrice) ? (ltp - p.avgPrice) * p.qty : p.unrealized;
        const cost = Math.abs(p.avgPrice * p.qty);
        return {
          ...p,
          ltp,
          quoteSource: q ? q.source : null,
          changePct: q ? q.changePct : null,
          unrealized,
          unrealizedPct: cost > 0 && unrealized != null ? unrealized / cost : null,
          exposure: Math.abs(priced * p.qty),
          strategy: strategyBySymbol.get(p.symbol) || null,
          instrument: classifyInstrument(p.symbol),
          sector: sectorOf(p.symbol),
        };
      }),
    [basePositions, quotes, strategyBySymbol],
  );

  /* 8. Accounts → cash / net liquidation, and the reporting currency. */
  const accounts = useMemo(
    () => (accountsQ.data && Array.isArray(accountsQ.data.accounts) ? accountsQ.data.accounts : []),
    [accountsQ.data],
  );

  const currency = useMemo(() => {
    const a = accounts.find((x) => x.currency);
    if (a) return a.currency;
    const q = [...quotes.values()].find((x) => x.currency);
    return (q && q.currency) || 'INR';
  }, [accounts, quotes]);

  /* 9. Equity curve (cumulative REALISED P&L — see PerformanceChart header). */
  const equityCurve = useMemo(() => {
    const raw = tradingQ.data && Array.isArray(tradingQ.data.equity_curve) ? tradingQ.data.equity_curve : [];
    return raw
      .map((p) => ({ t: nsToMs(p.ts), v: Number(p.realized_pnl) }))
      .filter((p) => p.t != null && Number.isFinite(p.v))
      .sort((a, b) => a.t - b.t);
  }, [tradingQ.data]);

  /* 10. Underwater series — distance below the running peak, in currency. */
  const drawdownCurve = useMemo(() => {
    let peak = -Infinity;
    return equityCurve.map((p) => {
      if (p.v > peak) peak = p.v;
      return { t: p.t, v: p.v - peak, peak };
    });
  }, [equityCurve]);

  /* 11. Totals. */
  const totals = useMemo(() => {
    const grossExposure = positions.reduce((s, p) => s + (p.exposure || 0), 0);
    const unrealized = positions.reduce((s, p) => s + (p.unrealized || 0), 0);
    const cash = accounts.reduce((s, a) => s + (Number(a.balance) || 0), 0);
    const equityBal = accounts.reduce((s, a) => s + (Number(a.equity) || 0), 0);
    const td = tradingQ.data || {};
    const realized = Number.isFinite(Number(td.realized_pnl_total))
      ? Number(td.realized_pnl_total)
      : positions.reduce((s, p) => s + (p.realized || 0), 0);
    const maxDrawdown = drawdownCurve.reduce((m, p) => Math.min(m, p.v), 0);

    return {
      grossExposure,
      unrealized,
      realized,
      cash,
      // Net liquidation prefers broker equity; falls back to cash + exposure so
      // the tile is never blank when only paper journals exist.
      netLiq: equityBal > 0 ? equityBal : cash + grossExposure,
      hasAccounts: accounts.length > 0,
      openCount: positions.length,
      maxDrawdown,
      dataThrough: nsToMs(td.data_through),
      counts: td.counts || null,
    };
  }, [positions, accounts, tradingQ.data, drawdownCurve]);

  /* 12. Allocation buckets for the donut. */
  const allocation = useMemo(() => {
    const buckets = { cash: 0, equity: 0, options: 0, futures: 0, other: 0 };
    if (totals.cash > 0) buckets.cash = totals.cash;
    for (const p of positions) buckets[p.instrument] = (buckets[p.instrument] || 0) + (p.exposure || 0);
    const meta = {
      cash: { label: 'Cash', tone: 'info' },
      equity: { label: 'Equity', tone: 'accent' },
      options: { label: 'Options', tone: 'warn' },
      futures: { label: 'Futures', tone: 'pos' },
      other: { label: 'Other', tone: 'neutral' },
    };
    return Object.keys(meta)
      .map((k) => ({ key: k, label: meta[k].label, tone: meta[k].tone, value: buckets[k] || 0 }))
      .filter((s) => s.value > 0);
  }, [positions, totals.cash]);

  /* 13. Sector rollup — notional-weighted move, so a large holding moves the
         sector tile more than a token one. */
  const sectors = useMemo(() => {
    const m = new Map();
    for (const p of positions) {
      const s = p.sector;
      const cur = m.get(s) || { sector: s, exposure: 0, weighted: 0, weight: 0, count: 0, unrealized: 0 };
      cur.exposure += p.exposure || 0;
      cur.unrealized += p.unrealized || 0;
      cur.count += 1;
      if (p.changePct != null && p.exposure > 0) {
        cur.weighted += p.changePct * p.exposure;
        cur.weight += p.exposure;
      }
      m.set(s, cur);
    }
    return [...m.values()]
      .map((s) => ({ ...s, changePct: s.weight > 0 ? s.weighted / s.weight : null }))
      .sort((a, b) => b.exposure - a.exposure);
  }, [positions]);

  /* 14. Grading stats — two shapes; `message` non-null is the no-data branch. */
  const stats = useMemo(() => {
    const d = statsQ.data;
    if (!d) return null;
    return {
      ...d,
      hasData: d.message == null,
      byHorizon: d.by_horizon || null,
      perSymbol: d.per_symbol || {},
      recent: Array.isArray(d.recent) ? d.recent : [],
    };
  }, [statsQ.data]);

  const refreshAll = useCallback(() => {
    journalsQ.refresh();
    tradingQ.refresh();
    aiQ.refresh();
    historyQ.refresh();
    accountsQ.refresh();
    statsQ.refresh();
    watchlistQ.refresh();
  }, [journalsQ, tradingQ, aiQ, historyQ, accountsQ, statsQ, watchlistQ]);

  /* Positions are "loading" only on a genuine cold start — once the journal
     resolves we must not flash a skeleton over data we already hold. */
  const positionsLoading =
    (journalsQ.loading && !journals.length) ||
    (jn ? tradingQ.loading && !tradingQ.data : historyQ.loading && !historyQ.data);

  const positionsError = jn ? tradingQ.error || historyQ.error : historyQ.error;

  return {
    // selection of the journal backing every projection
    journals,
    journalName: jn,
    setJournalName,
    journalsQ,

    // derived domain data
    positions,
    positionsLoading,
    positionsError,
    positionsSource: basePositions.length ? basePositions[0].source : null,
    equityCurve,
    drawdownCurve,
    allocation,
    sectors,
    totals,
    accounts,
    currency,
    stats,
    quotes,

    // raw queries, for per-panel loading/error/retry wiring
    q: { journalsQ, tradingQ, aiQ, historyQ, accountsQ, statsQ, watchlistQ },
    refreshAll,
  };
}

export default usePortfolioData;
