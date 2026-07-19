/**
 * Workspace data layer — the single fetch contract for components/ws/**.
 *
 * This is the file components/ws/modules/ops/opsApi.js documented itself as a
 * stand-in for. The signatures are identical by construction, so ops modules can
 * be repointed here without touching a call site; they are left importing their
 * local copy for now because re-exporting through two paths would give the SWR
 * cache two module instances and therefore two caches.
 */

/** Resolved lazily — window.__API__ may be injected after this module evaluates. */
export function apiBase() {
  return (typeof window !== 'undefined' && window.__API__) || 'http://127.0.0.1:8000';
}

/** Fetcher tagged with a cacheKey so useLivePoll's SWR cache keys off the URL. */
export const jget = (url) => {
  const f = (signal) =>
    fetch(url, { signal }).then((r) => {
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    });
  f.cacheKey = url;
  return f;
};

/**
 * Every failure path on this backend returns FastAPI's `{detail}` and nothing
 * machine-readable, so error handling everywhere is "surface the prose string".
 * 422 returns detail as an array of validation objects — flattened here.
 */
async function send(method, path, body, signal) {
  const res = await fetch(`${apiBase()}${path}`, {
    method,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  });
  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }
  if (!res.ok) {
    const d = data && data.detail;
    const msg =
      typeof d === 'string'
        ? d
        : Array.isArray(d)
          ? d.map((x) => x?.msg || x?.detail || String(x)).join('; ')
          : `HTTP ${res.status}`;
    throw new Error(msg);
  }
  return data;
}

export const jpost = (path, body, signal) => send('POST', path, body, signal);
export const jdel = (path, signal) => send('DELETE', path, undefined, signal);

/**
 * Poll cadences, in ms. Centralised so a slow backend can be throttled in one
 * place rather than by hunting literals across a dozen modules.
 */
export const CADENCE = {
  watchlist: 20000,
  intraday: 20000,
  recommendations: 60000,
  history: 60000,
  accounts: 60000,
  risk: 60000,
  allocator: 60000,
  surveillance: 60000,
  sor: 60000,
  health: 60000,
  performance: 120000,
  calibration: 120000,
  training: 2000,
};

/** ISO strings from this backend are naive UTC with no 'Z' — JS reads those as
 *  local time, which silently shifts every timestamp by the viewer's offset. */
export function parseUtc(s) {
  if (!s) return null;
  if (typeof s !== 'string') return new Date(s);
  const hasZone = /[zZ]$|[+-]\d{2}:?\d{2}$/.test(s);
  const d = new Date(hasZone ? s : `${s}Z`);
  return Number.isNaN(d.getTime()) ? null : d;
}
