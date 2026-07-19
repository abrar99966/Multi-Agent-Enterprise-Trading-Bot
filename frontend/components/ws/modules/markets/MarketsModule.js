/**
 * MarketsModule — composition root for the Markets view.
 *
 * Owns three things and delegates everything else:
 *   1. THE POLL. One /market-data/watchlist request per 20s cadence feeds both
 *      the watchlist grid and the movers lists. Hoisting it here (rather than
 *      letting each panel fetch) is what keeps the endpoint at one request per
 *      cycle instead of two, and makes it impossible for the two panels to
 *      disagree about the same tick.
 *   2. THE SYMBOL UNIVERSE. Add/remove, persisted locally when uncontrolled.
 *   3. TICK HISTORY. The watchlist endpoint returns no series, so sparklines are
 *      built from prices this session actually observed — real data, accumulated
 *      client-side, labelled "session ticks". Never back-filled or interpolated.
 *
 * Selection is controlled-with-fallback: pass `symbol` + `onSymbolChange` from
 * the workspace store and the module becomes store-driven; omit them and it
 * keeps its own state so the module runs standalone. It never reaches into a
 * global — see marketsApi.useControllable.
 *
 * LAYOUT NOTE: no ancestor here sets transform/filter/perspective. The ui layer's
 * Drawer and NotificationStack use position:fixed rather than a portal, and any
 * of those properties on an ancestor would create a containing block and clip
 * them. Do not add a `.gpu-layer`-style class to these wrappers.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useLivePoll } from '../../../../lib/useLivePoll';
import { Panel, PanelHeader, PanelBody, EmptyState, Badge, fmtTime } from '../../ui';
import {
  apiBase, jget, MARKETS_CADENCE, DEFAULT_SYMBOLS,
  reconcileWatchlist, freshestTs, useControllable,
} from './marketsApi';
import { WatchlistPanel } from './WatchlistPanel';
import { MarketMovers } from './MarketMovers';
import { OrderBook } from './OrderBook';

const STORE_KEY = 'hx.ws.markets.symbols';
/** ~16 min of 20s ticks — enough shape for a sparkline, bounded memory. */
const MAX_HISTORY = 48;
/** Quotes older than this read as stale rather than live (closed market, dead feed). */
const STALE_MS = 120000;

/** Load the persisted universe. Only used when the shell doesn't supply one. */
function loadSymbols() {
  if (typeof window === 'undefined') return DEFAULT_SYMBOLS;
  try {
    const raw = window.localStorage.getItem(STORE_KEY);
    const parsed = raw ? JSON.parse(raw) : null;
    return Array.isArray(parsed) && parsed.length ? parsed : DEFAULT_SYMBOLS;
  } catch {
    return DEFAULT_SYMBOLS;
  }
}

export function MarketsModule({
  // --- workspace store bindings (all optional) ---
  symbol,               // selected symbol
  onSymbolChange,       // publish selection so other modules react
  symbols,              // watchlist universe
  onSymbolsChange,
  // --- composition ---
  chartSlot,            // the chart module renders here
  className = '',
}) {
  // SSR-safe: both server and first client render start from the default
  // universe (identical markup), then the persisted list is adopted on mount.
  const [localSymbols, setLocalSymbols] = useState(DEFAULT_SYMBOLS);
  useEffect(() => {
    if (symbols === undefined) setLocalSymbols(loadSymbols());
  }, [symbols]);

  const universe = symbols !== undefined ? symbols : localSymbols;

  const setUniverse = useCallback(
    (next) => {
      if (symbols === undefined) {
        setLocalSymbols(next);
        try {
          window.localStorage.setItem(STORE_KEY, JSON.stringify(next));
        } catch {
          /* private mode / quota — the list still works for this session */
        }
      }
      if (onSymbolsChange) onSymbolsChange(next);
    },
    [symbols, onSymbolsChange],
  );

  const [selected, setSelected] = useControllable(symbol, onSymbolChange, null);

  /* ---- the one poll ---- */

  const url = universe.length
    ? `${apiBase()}/api/v1/market-data/watchlist?symbols=${encodeURIComponent(universe.join(','))}`
    : null;
  const fetcher = useMemo(() => (url ? jget(url) : () => Promise.resolve(null)), [url]);
  const { data, error, loading, refresh } = useLivePoll(fetcher, MARKETS_CADENCE.watchlist, [url]);

  const { quotes, missing } = useMemo(
    () => reconcileWatchlist(data, universe),
    [data, universe],
  );

  /* ---- session tick history ----
     Mutating a ref and bumping a counter, rather than rebuilding a state object:
     useLivePoll hands back the SAME data reference when the payload is
     unchanged, so this effect only runs on a real tick. */
  const historyRef = useRef(new Map());
  const [, bumpHistory] = useState(0);

  useEffect(() => {
    if (!quotes.length) return;
    const m = historyRef.current;
    let changed = false;
    quotes.forEach((q) => {
      if (q.price == null) return;
      const arr = m.get(q.symbol) || [];
      if (arr.length && arr[arr.length - 1] === q.price) return; // no tick
      arr.push(q.price);
      if (arr.length > MAX_HISTORY) arr.shift();
      m.set(q.symbol, arr);
      changed = true;
    });
    if (changed) bumpHistory((n) => n + 1);
  }, [quotes]);

  // Drop history for symbols no longer tracked, so a long session doesn't
  // accumulate series for removed tickers.
  useEffect(() => {
    const keep = new Set(universe);
    historyRef.current.forEach((_, k) => {
      if (!keep.has(k)) historyRef.current.delete(k);
    });
  }, [universe]);

  /* ---- default selection ----
     Select the first symbol once data lands so the order book isn't empty on
     arrival. Only when nothing is selected — never overrides the user. */
  useEffect(() => {
    if (!selected && quotes.length) setSelected(quotes[0].symbol);
  }, [selected, quotes, setSelected]);

  /* ---- universe mutations ---- */

  const addSymbol = useCallback(
    (sym) => setUniverse([...universe, sym]),
    [universe, setUniverse],
  );

  const removeSymbol = useCallback(
    (sym) => {
      const next = universe.filter((s) => s !== sym);
      setUniverse(next);
      // Selection must not dangle on a symbol we stopped tracking.
      if (selected === sym) setSelected(next[0] || null);
    },
    [universe, setUniverse, selected, setSelected],
  );

  /* ---- feed status ----
     Derived from the newest server-side quote timestamp, not from fetch time —
     a successful poll of a closed market is stale data, and saying "live" there
     would be a lie. */
  const newestTs = useMemo(() => freshestTs(quotes), [quotes]);
  const feedStatus = error && !quotes.length
    ? 'error'
    : newestTs != null && Date.now() - newestTs > STALE_MS
      ? 'stale'
      : quotes.length
        ? 'live'
        : 'offline';
  const feedDetail = newestTs != null ? fmtTime(newestTs) : undefined;

  return (
    <div className={`flex flex-col min-h-0 gap-2 ${className}`}>
      <div
        className="grid gap-2 min-h-0 flex-1"
        style={{ gridTemplateColumns: 'minmax(320px, 380px) minmax(0, 1fr) minmax(330px, 400px)' }}
      >
        <WatchlistPanel
          quotes={quotes}
          missing={missing}
          symbols={universe}
          history={historyRef.current}
          loading={loading}
          error={error}
          onRefresh={refresh}
          selected={selected}
          onSelect={setSelected}
          onAdd={addSymbol}
          onRemove={removeSymbol}
          feedStatus={feedStatus}
          feedDetail={feedDetail}
          className="min-h-0"
        />

        <div
          className="grid gap-2 min-h-0"
          style={{ gridTemplateRows: 'minmax(0, 1fr) minmax(150px, 200px)' }}
        >
          {chartSlot || (
            <Panel className="min-h-0">
              <PanelHeader
                title="Chart"
                subtitle={selected || undefined}
                icon="spark"
                actions={<Badge tone="neutral" size="xs">slot</Badge>}
              />
              <PanelBody>
                <EmptyState
                  icon="spark"
                  title="Chart slot"
                  hint={
                    selected
                      ? `Pass a chartSlot to render ${selected} here.`
                      : 'The chart module renders in this slot.'
                  }
                />
              </PanelBody>
            </Panel>
          )}

          <MarketMovers
            quotes={quotes}
            loading={loading && quotes.length === 0}
            error={error}
            selected={selected}
            onSelect={setSelected}
            className="min-h-0"
          />
        </div>

        <OrderBook symbol={selected} className="min-h-0" />
      </div>
    </div>
  );
}

export default MarketsModule;
