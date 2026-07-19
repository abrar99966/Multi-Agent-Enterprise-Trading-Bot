/**
 * Data layer for the ops modules (Analytics / Logs / Replay / Settings).
 *
 * WHY this lives inside the module directory instead of lib/ws/api.js: the shell
 * owns lib/ws/, and it does not exist yet. The jget contract below is byte-for-byte
 * the documented one, so when the shell lands this file can be deleted and the
 * imports repointed with no call-site changes.
 */
import { useCallback, useState } from 'react';

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
    const msg = typeof d === 'string'
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

/* ---- polling cadences ----------------------------------------------------
   Journal/TCA payloads are file-backed projections, not ticking market data, so
   they sit at the slow end of the documented ladder. `eventsFollow` is the one
   fast lane (tailing a live journal) and still respects the 20s floor. */
export const OPS_CADENCE = {
  eventsFollow: 20000,
  events: 60000,
  journals: 60000,
  tca: 120000,
  enrichment: 60000,
  risk: 60000,
  accounts: 60000,
  about: 120000,
};

/* ---- time ----------------------------------------------------------------
   Two incompatible conventions ship from this backend and mixing them silently
   yields dates in 1970 or 2255:
     journal + TCA timestamps  -> integer NANOSECONDS
     broker/risk ISO strings   -> naive UTC with NO 'Z' (JS parses as LOCAL) */

/** Nanoseconds → milliseconds. Returns null for absent/garbage input. */
export function nsToMs(ns) {
  const n = Number(ns);
  return Number.isFinite(n) ? n / 1e6 : null;
}

/** Parse a naive-UTC ISO string correctly by forcing a zone when none is present. */
export function parseUtc(s) {
  if (!s) return null;
  const str = String(s);
  const zoned = /([zZ]|[+-]\d{2}:?\d{2})$/.test(str);
  const d = new Date(zoned ? str : `${str}Z`);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** Seconds → "3h 12m" / "48s". Used for token-expiry countdowns. */
export function fmtCountdown(totalSeconds) {
  const s = Math.max(0, Math.floor(Number(totalSeconds) || 0));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ${m % 60}m`;
  return `${Math.floor(h / 24)}d ${h % 24}h`;
}

/** Bytes → "12.4 MB". Journal sizes span kB to GB. */
export function fmtBytes(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return '--';
  if (n < 1024) return `${n} B`;
  const units = ['kB', 'MB', 'GB', 'TB'];
  let x = n / 1024;
  let i = 0;
  while (x >= 1024 && i < units.length - 1) {
    x /= 1024;
    i += 1;
  }
  return `${x.toFixed(x >= 100 ? 0 : 1)} ${units[i]}`;
}

/* ---- store bridge --------------------------------------------------------
   The workspace store is not built yet. Every module therefore takes its shared
   selection as an optional controlled prop and falls back to local state, so the
   modules work standalone today and become store-driven the moment the shell
   passes value + onChange. No module reaches into a global.  */
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

/* ---- journal event semantics --------------------------------------------
   Journal events carry no severity field — severity is a UI concept derived from
   stream + payload. Kept here (not in a component) so Logs and Replay classify
   identically. */

export const STREAM_LABELS = {
  'md.bars': 'Bars',
  'md.ticks': 'Ticks',
  'signal.intents': 'Intents',
  'risk.verdicts': 'Risk verdicts',
  'exec.orders': 'Orders',
  'exec.order_updates': 'Order updates',
  'exec.fills': 'Fills',
  'oms.positions': 'Positions',
  'ctl.params': 'Params',
  'ctl.param_proposals': 'Param proposals',
  'ctl.kill': 'Kill switch',
  'ctl.approval_requests': 'Approval requests',
  'ctl.approval_decisions': 'Approval decisions',
};

export function eventSeverity(ev) {
  if (!ev) return 'ok';
  const p = ev.payload || {};
  if (ev.stream === 'ctl.kill') return p.engaged ? 'critical' : 'elevated';
  if (ev.stream === 'risk.verdicts') return p.approved === false ? 'high' : 'ok';
  if (ev.stream === 'exec.order_updates') {
    const s = String(p.status ?? '').toUpperCase();
    if (s.includes('REJECT')) return 'high';
    if (s.includes('CANCEL')) return 'elevated';
    return 'ok';
  }
  if (ev.stream === 'ctl.approval_requests') return 'elevated';
  if (ev.stream === 'ctl.param_proposals' || ev.stream === 'ctl.params') return 'elevated';
  return 'ok';
}

/**
 * One-line human summary per event. Written defensively with optional access on
 * every field: `payload` is an untyped passthrough and older journals predate
 * some keys, so a missing field must degrade to a shorter line, never a crash.
 */
export function eventSummary(ev) {
  if (!ev) return '';
  const p = ev.payload || {};
  const n = (v, dp = 2) => (Number.isFinite(Number(v)) ? Number(v).toFixed(dp) : '--');
  switch (ev.type) {
    case 'Bar':
      return `${p.symbol ?? '?'} C ${n(p.close)}${p.volume != null ? ` vol ${p.volume}` : ''}`;
    case 'Tick':
      return `${p.symbol ?? '?'} ${n(p.price)}`;
    case 'OrderIntent':
      return `${p.side ?? '?'} ${p.qty ?? '?'} ${p.symbol ?? '?'}${p.strategy_id ? ` · ${p.strategy_id}` : ''}`;
    case 'RiskVerdict':
      return p.approved === false
        ? `REJECTED${p.reject_reason ? ` · ${p.reject_reason}` : ''}`
        : `approved · tier ${p.tier ?? '?'}`;
    case 'Order':
    case 'OrderUpdate':
      return `${p.side ?? ''} ${p.qty ?? ''} ${p.symbol ?? '?'}${p.status ? ` · ${p.status}` : ''}`.trim();
    case 'Fill':
      return `${p.side ?? '?'} ${p.qty ?? '?'} ${p.symbol ?? '?'} @ ${n(p.price)}`;
    case 'PositionSnapshot':
      return `${p.symbol ?? '?'} qty ${p.qty ?? '?'}`;
    case 'ParameterChange':
      return `${p.parameter ?? '?'}: ${p.old_value ?? '?'} → ${p.new_value ?? '?'}`;
    case 'ParameterChangeProposal':
      return `${p.parameter ?? '?'} → ${p.proposed_value ?? '?'}${p.source ? ` · ${p.source}` : ''}`;
    case 'KillSwitch':
      return `${p.engaged ? 'ENGAGED' : 'released'} L${p.level ?? '?'} ${p.scope ?? '*'}${p.reason ? ` · ${p.reason}` : ''}`;
    case 'ApprovalRequest':
    case 'ApprovalDecision':
      return `${p.symbol ?? p.intent_id ?? ''} ${p.decision ?? p.status ?? ''}`.trim() || ev.type;
    default: {
      const keys = Object.keys(p).slice(0, 3);
      return keys.length ? keys.map((k) => `${k}=${p[k]}`).join(' · ') : ev.type;
    }
  }
}

/** Stable pretty-print for payload inspection and replay diffing. */
export function prettyJson(v) {
  try {
    return JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
}
