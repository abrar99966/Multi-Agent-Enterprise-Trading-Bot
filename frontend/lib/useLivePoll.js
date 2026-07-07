import { useEffect, useRef, useState, useCallback } from 'react';

/**
 * Polling that:
 *   - aborts the in-flight request when the component unmounts or re-polls,
 *   - pauses while the tab is hidden,
 *   - re-fetches immediately when the tab becomes visible again,
 *   - never lets a stale response overwrite a fresher one,
 *   - keeps the SAME `data` reference when the JSON content didn't change
 *     (so React.memo children don't re-render on every successful poll).
 *
 *   const { data, error, loading, refresh } = useLivePoll(
 *     (signal) => fetch(url, { signal }).then(r => r.json()),
 *     30000,
 *     [url],
 *   );
 */

// Fast content hash — cheaper than JSON.stringify of huge payloads, good enough
// for the small dashboards payloads we use (quotes, recs, providers).
function hashPayload(v) {
  try {
    return JSON.stringify(v);
  } catch {
    return Math.random().toString();
  }
}

// Module-level stale-while-revalidate cache, survives route changes (component
// unmount/remount). Returning to the dashboard from another tab shows the last
// known data INSTANTLY, then revalidates in the background — no full blank reload.
const _swrCache = new Map();

export function useLivePoll(fetcher, intervalMs, deps = [], cacheKey) {
  // Stable key for this resource: explicit arg, or the URL jget() tagged on the
  // fetcher, or the deps signature. null ⇒ no cross-navigation cache.
  const keyRef = useRef(cacheKey ?? fetcher?.cacheKey ?? (deps.length ? JSON.stringify(deps) : null));
  const seeded = keyRef.current != null && _swrCache.has(keyRef.current);

  const [data, setData] = useState(() => (seeded ? _swrCache.get(keyRef.current) : null));
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(!seeded);
  const reqIdRef = useRef(0);
  const abortRef = useRef(null);
  const fetcherRef = useRef(fetcher);
  const hashRef = useRef(null);
  fetcherRef.current = fetcher;

  const run = useCallback(async () => {
    if (typeof document !== 'undefined' && document.hidden) return;
    if (abortRef.current) abortRef.current.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    const myId = ++reqIdRef.current;
    try {
      const result = await fetcherRef.current(ctrl.signal);
      if (myId !== reqIdRef.current) return; // a newer request finished first
      const h = hashPayload(result);
      if (h !== hashRef.current) {
        hashRef.current = h;
        setData(result);
        if (keyRef.current != null) _swrCache.set(keyRef.current, result);
      }
      setError(null);
    } catch (e) {
      if (e?.name === 'AbortError') return;
      if (myId !== reqIdRef.current) return;
      setError(e);
    } finally {
      if (myId === reqIdRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    // Re-resolve the key for current deps/fetcher, then seed from cache instantly
    // if we have prior data — only show the loading state on a true cold start.
    keyRef.current = cacheKey ?? fetcherRef.current?.cacheKey ?? (deps.length ? JSON.stringify(deps) : null);
    hashRef.current = null;
    const k = keyRef.current;
    if (k != null && _swrCache.has(k)) {
      setData(_swrCache.get(k));
      setLoading(false);
    } else {
      setLoading(true);
    }
    run();
    const t = setInterval(run, intervalMs);
    const onVis = () => { if (!document.hidden) run(); };
    document.addEventListener('visibilitychange', onVis);
    return () => {
      clearInterval(t);
      document.removeEventListener('visibilitychange', onVis);
      if (abortRef.current) abortRef.current.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { data, error, loading, refresh: run };
}
