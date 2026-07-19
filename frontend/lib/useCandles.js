import { useCallback, useEffect, useState } from 'react';

const API = (typeof window !== 'undefined' && window.__API__) || 'http://127.0.0.1:8000';

// Fetch of intraday/daily candles + the latest quote for a symbol.
// Aborts on unmount or when symbol/range/interval change, and times out so a
// busy backend (e.g. a bulk ingest hammering Yahoo) never hangs the UI — it
// fails fast with an error the caller can show a Retry for.
//
// `pollMs` opts into a refresh on the documented cadence; it defaults to 0
// (one-shot) so existing callers keep their single-fetch behaviour. A live price
// surface must pass it, or it renders a frozen snapshot as the last trade.
//
// Returns { loading, series, quote, source, error, reload }.
//   error: null | 'timeout' | 'failed' | 'empty'
export function useCandles(symbol, { range = '1d', interval = '5m', enabled = true, timeoutMs = 12000, pollMs = 0 } = {}) {
  const [state, setState] = useState({ loading: true, series: [], quote: null, source: null, error: null });
  const [nonce, setNonce] = useState(0);
  const reload = useCallback(() => setNonce((x) => x + 1), []);

  useEffect(() => {
    if (!symbol || !enabled) {
      setState({ loading: false, series: [], quote: null, source: null, error: null });
      return;
    }
    const ac = new AbortController();
    const timer = setTimeout(() => ac.abort('timeout'), timeoutMs);
    setState((s) => ({ ...s, loading: true, error: null }));

    Promise.all([
      fetch(`${API}/api/v1/market-data/intraday/${encodeURIComponent(symbol)}?range=${range}&interval=${interval}`, { signal: ac.signal })
        .then((r) => (r.ok ? r.json() : { series: [] })),
      fetch(`${API}/api/v1/market-data/quotes/${encodeURIComponent(symbol)}`, { signal: ac.signal })
        .then((r) => (r.ok ? r.json() : null)),
    ])
      .then(([i, q]) => {
        clearTimeout(timer);
        const series = i?.series || [];
        setState({ loading: false, series, quote: q, source: i?.source, error: series.length ? null : 'empty' });
      })
      .catch(() => {
        clearTimeout(timer);
        // Plain unmount/dep-change abort (reason undefined) — don't touch state.
        if (ac.signal.aborted && ac.signal.reason !== 'timeout') return;
        setState({ loading: false, series: [], quote: null, source: null, error: ac.signal.reason === 'timeout' ? 'timeout' : 'failed' });
      });

    // Re-run through the existing nonce path, so a refresh is identical to reload().
    const poll = pollMs > 0 ? setInterval(() => setNonce((x) => x + 1), pollMs) : null;

    return () => { clearTimeout(timer); if (poll) clearInterval(poll); ac.abort(); };
  }, [symbol, range, interval, enabled, timeoutMs, pollMs, nonce]);

  return { ...state, reload };
}
