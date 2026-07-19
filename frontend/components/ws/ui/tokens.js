/**
 * Design tokens as JS constants.
 *
 * WHY this file exists: Tailwind class strings must be statically analysable, so
 * they can never be built by concatenation (`text-hx-${tone}-400` compiles to
 * nothing). Every tone→class decision therefore resolves through a literal map
 * here, and logic files import the map instead of hardcoding colour classes.
 * Change a tone's colour once here and the whole workspace follows.
 */

/** Canonical tone vocabulary. Every component's `tone` prop accepts these. */
export const TONES = ['neutral', 'accent', 'pos', 'neg', 'warn', 'info'];

/** Foreground text colour per tone. */
export const TONE_TEXT = {
  neutral: 'text-hx-text-mid',
  accent: 'text-hx-accent-400',
  pos: 'text-hx-pos-400',
  neg: 'text-hx-neg-400',
  warn: 'text-hx-warn-400',
  info: 'text-hx-info-400',
};

/** Tinted fill — used behind badges/chips. Kept at low alpha to stay flat. */
export const TONE_BG = {
  neutral: 'bg-white/[0.05]',
  accent: 'bg-hx-accent-500/[0.12]',
  pos: 'bg-hx-pos-500/[0.12]',
  neg: 'bg-hx-neg-500/[0.12]',
  warn: 'bg-hx-warn-500/[0.12]',
  info: 'bg-hx-info-500/[0.12]',
};

/** Hairline border per tone. */
export const TONE_BORDER = {
  neutral: 'border-hx-border-subtle',
  accent: 'border-hx-accent-500/30',
  pos: 'border-hx-pos-500/30',
  neg: 'border-hx-neg-500/30',
  warn: 'border-hx-warn-500/30',
  info: 'border-hx-info-500/30',
};

/** Solid fill for dots, gauge fills, timeline markers. */
export const TONE_SOLID = {
  neutral: 'bg-hx-text-dim',
  accent: 'bg-hx-accent-400',
  pos: 'bg-hx-pos-400',
  neg: 'bg-hx-neg-400',
  warn: 'bg-hx-warn-400',
  info: 'bg-hx-info-400',
};

/** SVG stroke/fill (Sparkline, Icon). */
export const TONE_STROKE = {
  neutral: 'stroke-hx-text-lo',
  accent: 'stroke-hx-accent-400',
  pos: 'stroke-hx-pos-400',
  neg: 'stroke-hx-neg-400',
  warn: 'stroke-hx-warn-400',
  info: 'stroke-hx-info-400',
};

/** Raw hex, for SVG gradients and canvas where classes don't reach. */
export const TONE_HEX = {
  neutral: '#7d8899',
  accent: '#22d3ee',
  pos: '#34d399',
  neg: '#f87171',
  warn: '#fbbf24',
  info: '#60a5fa',
};

/* ---- Severity ------------------------------------------------------------
   Risk/alert severity is a domain concept; tone is a visual one. Keep them
   separate so a severity can be re-skinned without touching call sites. */

export const SEVERITIES = ['ok', 'elevated', 'high', 'critical'];

export const SEVERITY_TONE = {
  ok: 'pos',
  elevated: 'warn',
  high: 'warn',
  critical: 'neg',
  // aliases seen in backend payloads
  info: 'info',
  low: 'pos',
  medium: 'warn',
  moderate: 'warn',
  severe: 'neg',
  error: 'neg',
  warning: 'warn',
};

/** Human label + icon name. Never signal severity with colour alone (WCAG 1.4.1). */
export const SEVERITY_META = {
  ok: { label: 'OK', icon: 'check', rank: 0 },
  elevated: { label: 'Elevated', icon: 'info', rank: 1 },
  high: { label: 'High', icon: 'alert', rank: 2 },
  critical: { label: 'Critical', icon: 'kill', rank: 3 },
};

/** Normalise any backend severity string to a canonical one. */
export function toSeverity(v) {
  const k = String(v ?? '').toLowerCase();
  if (SEVERITY_META[k]) return k;
  if (k === 'low') return 'ok';
  if (k === 'medium' || k === 'moderate' || k === 'warning') return 'elevated';
  if (k === 'severe' || k === 'error') return 'critical';
  return 'ok';
}

/** Map a severity (or raw string) to a tone key. */
export function severityTone(v) {
  return SEVERITY_TONE[String(v ?? '').toLowerCase()] || SEVERITY_TONE[toSeverity(v)] || 'neutral';
}

/** Sign of a number → tone. `zeroTone` lets callers decide how flat reads. */
export function deltaTone(n, zeroTone = 'neutral') {
  const x = Number(n);
  if (!Number.isFinite(x) || x === 0) return zeroTone;
  return x > 0 ? 'pos' : 'neg';
}

/* ---- Formatters ----------------------------------------------------------
   All return '--' for null/undefined/NaN so grids never render "NaN" or an
   empty cell that looks like a layout bug. Output is tabular-nums friendly:
   fixed decimal counts, no variable-width symbols mid-string. */

export const EMPTY = '--';

const isNil = (v) => v === null || v === undefined || v === '' || (typeof v === 'number' && !Number.isFinite(v));

/** 1234.5 → "1,234.50". Set `compact` for 1.2M style on wide-range columns. */
export function fmtNum(v, { dp = 2, compact = false, signed = false } = {}) {
  if (isNil(v)) return EMPTY;
  const n = Number(v);
  if (!Number.isFinite(n)) return EMPTY;
  let s;
  if (compact && Math.abs(n) >= 1000) {
    const units = [
      [1e12, 'T'],
      [1e9, 'B'],
      [1e6, 'M'],
      [1e3, 'K'],
    ];
    const [div, suf] = units.find(([d]) => Math.abs(n) >= d);
    s = (n / div).toFixed(Math.abs(n / div) >= 100 ? 0 : 1) + suf;
  } else {
    s = n.toLocaleString('en-US', { minimumFractionDigits: dp, maximumFractionDigits: dp });
  }
  return signed && n > 0 ? `+${s}` : s;
}

/** 0.0432 → "+4.32%". Pass `asRatio:false` when the value is already 4.32. */
export function fmtPct(v, { dp = 2, asRatio = true, signed = true } = {}) {
  if (isNil(v)) return EMPTY;
  const n = Number(v) * (asRatio ? 100 : 1);
  if (!Number.isFinite(n)) return EMPTY;
  const s = `${Math.abs(n).toFixed(dp)}%`;
  if (!signed) return s;
  return n > 0 ? `+${s}` : n < 0 ? `-${s}` : s;
}

/** 1234.5 → "$1,234.50". Symbol stays outside the sign for column alignment. */
export function fmtCur(v, { ccy = 'USD', dp = 2, compact = false, signed = false } = {}) {
  if (isNil(v)) return EMPTY;
  const n = Number(v);
  if (!Number.isFinite(n)) return EMPTY;
  const sym = { USD: '$', EUR: '€', GBP: '£', JPY: '¥', INR: '₹' }[ccy] || '';
  const body = fmtNum(Math.abs(n), { dp: ccy === 'JPY' ? 0 : dp, compact });
  const sign = n < 0 ? '-' : signed && n > 0 ? '+' : '';
  return `${sign}${sym}${body}`;
}

/** Share/contract counts — integers, compacted past 1e6. */
export function fmtQty(v, { signed = false } = {}) {
  if (isNil(v)) return EMPTY;
  const n = Number(v);
  if (!Number.isFinite(n)) return EMPTY;
  return fmtNum(n, { dp: Number.isInteger(n) ? 0 : 2, compact: Math.abs(n) >= 1e6, signed });
}

/**
 * Timestamp → "14:32:07". Accepts epoch ms, epoch seconds, ISO string, or Date.
 * `mode`: 'time' | 'hm' | 'date' | 'datetime' | 'rel'.
 */
export function fmtTime(v, { mode = 'time' } = {}) {
  if (isNil(v)) return EMPTY;
  let d;
  if (v instanceof Date) d = v;
  else if (typeof v === 'number') d = new Date(v < 1e11 ? v * 1000 : v); // seconds vs ms
  else d = new Date(v);
  if (Number.isNaN(d.getTime())) return EMPTY;

  const p2 = (x) => String(x).padStart(2, '0');
  switch (mode) {
    case 'hm':
      return `${p2(d.getHours())}:${p2(d.getMinutes())}`;
    case 'date':
      return `${d.getFullYear()}-${p2(d.getMonth() + 1)}-${p2(d.getDate())}`;
    case 'datetime':
      return `${fmtTime(d, { mode: 'date' })} ${fmtTime(d, { mode: 'time' })}`;
    case 'rel': {
      const s = Math.round((Date.now() - d.getTime()) / 1000);
      const a = Math.abs(s);
      const suf = s >= 0 ? 'ago' : 'ahead';
      if (a < 5) return 'now';
      if (a < 60) return `${a}s ${suf}`;
      if (a < 3600) return `${Math.floor(a / 60)}m ${suf}`;
      if (a < 86400) return `${Math.floor(a / 3600)}h ${suf}`;
      return `${Math.floor(a / 86400)}d ${suf}`;
    }
    default:
      return `${p2(d.getHours())}:${p2(d.getMinutes())}:${p2(d.getSeconds())}`;
  }
}

/** Milliseconds → "842ms" / "1.24s" / "310µs". Width-stable for latency columns. */
export function fmtLatency(ms) {
  if (isNil(ms)) return EMPTY;
  const n = Number(ms);
  if (!Number.isFinite(n)) return EMPTY;
  if (n < 1) return `${Math.round(n * 1000)}µs`;
  if (n < 1000) return `${n < 10 ? n.toFixed(1) : Math.round(n)}ms`;
  if (n < 60000) return `${(n / 1000).toFixed(2)}s`;
  return `${Math.floor(n / 60000)}m ${Math.round((n % 60000) / 1000)}s`;
}

/** Arrow glyph for a delta — the non-colour channel that makes deltas readable
    for colour-blind users and in greyscale print. */
export function deltaArrow(n) {
  const x = Number(n);
  if (!Number.isFinite(x) || x === 0) return '→';
  return x > 0 ? '▲' : '▼';
}

/** Tiny classnames joiner. Falsy entries drop out. */
export function cx(...parts) {
  return parts.filter(Boolean).join(' ');
}
