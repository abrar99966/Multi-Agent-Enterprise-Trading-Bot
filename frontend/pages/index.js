import React, { useState, useEffect, useRef, useMemo, useCallback, memo } from 'react';
import Link from 'next/link';
import { useLivePoll } from '../lib/useLivePoll';
import { useCandles } from '../lib/useCandles';
import ChartModal, { chartHref } from '../components/ChartModal';

const API = (typeof window !== 'undefined' && window.__API__) || 'http://127.0.0.1:8000';

const DEFAULT_WATCHLIST = 'NIFTY,BANKNIFTY,SENSEX,RELIANCE,INFY,TCS,HDFCBANK,AAPL,MSFT,NVDA';

// ---------- helpers ----------
const fmtNum = (n, opts = {}) => {
  if (n === null || n === undefined || Number.isNaN(n)) return '--';
  return Number(n).toLocaleString(undefined, { maximumFractionDigits: 2, ...opts });
};
const fmtPct = (n) => {
  if (n === null || n === undefined || Number.isNaN(n)) return '--';
  const sign = n >= 0 ? '+' : '';
  return `${sign}${Number(n).toFixed(2)}%`;
};
const fmtTime = (d) => new Date(d).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });

// Pause animations + polling when the tab is hidden. Set on <html> for CSS to hook into.
function useHiddenMarker() {
  useEffect(() => {
    const set = () => document.documentElement.dataset.hidden = String(document.hidden);
    set();
    document.addEventListener('visibilitychange', set);
    return () => document.removeEventListener('visibilitychange', set);
  }, []);
}

// Build a session-length history of a single live value so the KPI sparklines
// actually mean something. Persists in sessionStorage so a tab-refresh doesn't
// wipe the chart.
//
// NOTE: starts EMPTY on first render (both server and client) to avoid React
// hydration mismatch — the persisted history is loaded inside useEffect, after
// hydration is complete.
function useSessionRingBuffer(key, value, capacity = 32) {
  const [buf, setBuf] = useState([]);

  // Load persisted history once, after mount only (never on server)
  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(`kpi:${key}`);
      const arr = raw ? JSON.parse(raw) : null;
      if (Array.isArray(arr) && arr.length) setBuf(arr);
    } catch {}
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  // Append on every new value
  useEffect(() => {
    if (value == null || Number.isNaN(value)) return;
    setBuf((prev) => {
      if (prev.length && prev[prev.length - 1] === value) return prev;
      const next = [...prev, value].slice(-capacity);
      try { sessionStorage.setItem(`kpi:${key}`, JSON.stringify(next)); } catch {}
      return next;
    });
  }, [key, value, capacity]);

  return buf;
}

// INR formatting (Indian lakh/crore numbering)
const fmtINR = (n, { fraction = 0 } = {}) => {
  if (n == null || Number.isNaN(n)) return '—';
  return new Intl.NumberFormat('en-IN', {
    style: 'currency', currency: 'INR', maximumFractionDigits: fraction,
  }).format(n);
};
const fmtSignedINR = (n) => {
  if (n == null || Number.isNaN(n)) return '—';
  const sign = n > 0 ? '+' : '';
  return `${sign}${fmtINR(n)}`;
};

// ---------- Sparkline (memoised — props are referentially stable) ----------
const Sparkline = memo(function Sparkline({ data = [], width = 180, height = 44, color = '#10d995', fill = true }) {
  if (!data.length) return <svg width={width} height={height} />;
  const min = Math.min(...data); const max = Math.max(...data);
  const range = max - min || 1;
  const step = width / Math.max(data.length - 1, 1);
  const points = data.map((v, i) => `${i * step},${height - ((v - min) / range) * height}`).join(' ');
  const area = `0,${height} ${points} ${width},${height}`;
  const gid = `g-${color.replace('#', '')}`;
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none">
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.4" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      {fill && <polygon points={area} fill={`url(#${gid})`} />}
      <polyline points={points} fill="none" stroke={color} strokeWidth="1.6" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
});

// ---------- Confidence ring ----------
const ConfidenceRing = memo(function ConfidenceRing({ value = 0, size = 64 }) {
  const v = Math.max(0, Math.min(1, value));
  const r = size / 2 - 6;
  const c = 2 * Math.PI * r;
  const off = c * (1 - v);
  const color = v > 0.7 ? '#10d995' : v > 0.45 ? '#e6c181' : '#f43f5e';
  return (
    <svg width={size} height={size} className="-rotate-90">
      <circle cx={size / 2} cy={size / 2} r={r} stroke="rgba(255,255,255,0.06)" strokeWidth="6" fill="none" />
      <circle cx={size / 2} cy={size / 2} r={r} stroke={color} strokeWidth="6" fill="none"
        strokeDasharray={c} strokeDashoffset={off} strokeLinecap="round" />
      <text x="50%" y="50%" dominantBaseline="middle" textAnchor="middle" transform={`rotate(90 ${size/2} ${size/2})`}
        className="fill-white tabular" fontSize="13" fontWeight="600">{Math.round(v * 100)}%</text>
    </svg>
  );
});

// ---------- Clock (isolated → only this leaf re-renders on tick) ----------
// SSR-safe: render a stable placeholder during hydration, then start ticking.
const Clock = memo(function Clock() {
  const [now, setNow] = useState(null);
  useEffect(() => {
    setNow(new Date());
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);
  return (
    <span className="text-white/85 tabular" suppressHydrationWarning>
      {now ? fmtTime(now) : '--:--:--'}
    </span>
  );
});

// ---------- Date label (refreshes hourly — does NOT need to tick) ----------
const TodayLabel = memo(function TodayLabel() {
  const [d, setD] = useState(null);
  useEffect(() => {
    setD(new Date());
    const t = setInterval(() => setD(new Date()), 60_000);
    return () => clearInterval(t);
  }, []);
  return (
    <span className="text-white/60 tabular" suppressHydrationWarning>
      {d ? d.toLocaleDateString(undefined, { weekday: 'short', day: '2-digit', month: 'short' }) : '—'}
    </span>
  );
});

// ---------- Market status (Asia/Kolkata — TZ-correct via Intl) ----------
const MarketStatus = memo(function MarketStatus() {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const compute = () => {
      const parts = new Intl.DateTimeFormat('en-GB', {
        timeZone: 'Asia/Kolkata',
        weekday: 'short', hour: '2-digit', minute: '2-digit', hour12: false,
      }).formatToParts(new Date());
      const get = (t) => parts.find((p) => p.type === t)?.value;
      const weekday = get('weekday');
      const h = Number(get('hour'));
      const m = Number(get('minute'));
      const mins = h * 60 + m;
      const isWeekday = !['Sat', 'Sun'].includes(weekday);
      setOpen(isWeekday && mins >= 9 * 60 + 15 && mins <= 15 * 60 + 30);
    };
    compute();
    const t = setInterval(compute, 30_000);
    return () => clearInterval(t);
  }, []);
  return (
    <div className="flex items-center gap-2 text-xs tabular">
      <span className={`pulse-dot w-2 h-2 rounded-full ${open ? 'bg-emerald-400 text-emerald-400' : 'bg-rose-400 text-rose-400'}`} />
      <span className="text-white/70">NSE</span>
      <span className={open ? 'text-emerald-300' : 'text-rose-300'}>{open ? 'OPEN' : 'CLOSED'}</span>
    </div>
  );
});

// ---------- Data-source badge ----------
const SOURCE_META = {
  dhan:     { label: 'Dhan LIVE',     cls: 'bg-emerald-400/10 border-emerald-400/30 text-emerald-300', dot: 'bg-emerald-400 pulse-dot text-emerald-400' },
  alpaca:   { label: 'Alpaca LIVE',   cls: 'bg-emerald-400/10 border-emerald-400/30 text-emerald-300', dot: 'bg-emerald-400 pulse-dot text-emerald-400' },
  zerodha:  { label: 'Kite LIVE',     cls: 'bg-emerald-400/10 border-emerald-400/30 text-emerald-300', dot: 'bg-emerald-400 pulse-dot text-emerald-400' },
  upstox:   { label: 'Upstox LIVE',   cls: 'bg-emerald-400/10 border-emerald-400/30 text-emerald-300', dot: 'bg-emerald-400 pulse-dot text-emerald-400' },
  yahoo:    { label: 'Yahoo · 15m delayed', cls: 'bg-amber-400/10 border-amber-400/30 text-amber-200',  dot: 'bg-amber-300' },
};
const SourceBadge = memo(function SourceBadge({ source, size = 'sm' }) {
  const meta = SOURCE_META[source] || SOURCE_META.yahoo;
  const pad = size === 'sm' ? 'text-[10px] px-2 py-0.5' : 'text-xs px-2.5 py-1';
  return (
    <span className={`inline-flex items-center gap-1.5 ${pad} rounded-full border tabular ${meta.cls}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${meta.dot}`} />
      {meta.label}
    </span>
  );
});

// ---------- Live ticker bar ----------
const LiveTicker = memo(function LiveTicker({ quotes }) {
  if (!quotes || !quotes.length) return null;
  const items = [...quotes, ...quotes];
  return (
    <div className="ticker-host overflow-hidden border-y border-white/5 bg-ink-900/40">
      <div className="ticker-track flex gap-8 whitespace-nowrap py-2.5">
        {items.map((q, i) => (
          <div key={`${q.symbol}-${i}`} className="flex items-center gap-3 px-2 text-sm tabular">
            <span className="font-medium text-white/85">{q.symbol}</span>
            <span className="text-white/60">{fmtNum(q.current_price)}</span>
            <span className={q.change >= 0 ? 'text-emerald-400' : 'text-rose-400'}>
              {q.change >= 0 ? '▲' : '▼'} {fmtPct(q.change_pct)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
});

// ---------- KPI card ----------
const KPICard = memo(function KPICard({ label, value, sub, accent = '#e6c181', spark = [] }) {
  return (
    <div className="glass-premium accent-rail lift rounded-2xl px-5 py-4" style={{ '--rail': `${accent}99` }}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-[11px] uppercase tracking-wider text-white/50">{label}</div>
          <div className="mt-1 text-2xl font-semibold tabular text-white truncate">{value}</div>
          {sub && <div className="mt-0.5 text-xs text-white/55 tabular truncate">{sub}</div>}
        </div>
        <div className="opacity-90 shrink-0"><Sparkline data={spark} color={accent} width={88} height={36} /></div>
      </div>
    </div>
  );
});

// ---------- Recommendation card ----------
const RecCard = memo(function RecCard({ rec, onApprove, onReject, busy }) {
  const side = (rec.side || '').toString().toLowerCase();
  const rationale = rec.agent_outputs?.rationale;
  // `action` is the real call (buy/sell/hold); `side` is just storage bias.
  const action = (rationale?.action || side).toString().toLowerCase();
  const horizonTag = rationale?.horizon;
  const isBuy = action === 'buy';
  const isHold = action === 'hold';
  const accent = isHold ? '#e6c181' : (isBuy ? '#10d995' : '#f43f5e');
  const tech = rec.agent_outputs?.TechnicalAnalysis?.indicators || {};
  const trust = rationale?.trust;
  const why = rationale?.why || [];
  const bt = trust?.backtest;
  const stratLabel = trust?.strategy_label || rec.agent_outputs?.TechnicalAnalysis?.strategy_label;
  const trustTone = {
    high: 'bg-emerald-400/10 border-emerald-400/30 text-emerald-300',
    moderate: 'bg-sky-400/10 border-sky-400/30 text-sky-200',
    low: 'bg-amber-400/10 border-amber-400/30 text-amber-200',
    untested: 'bg-white/[0.04] border-white/15 text-white/55',
  }[trust?.level] || 'bg-white/[0.04] border-white/15 text-white/55';
  const noId = !rec.id;
  // Per-card local clock so only this card re-renders for the countdown
  const [, setTick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setTick((x) => x + 1), 30_000);
    return () => clearInterval(t);
  }, []);
  const expiresIn = useMemo(() => {
    if (!rec.expires_at) return null;
    return Math.max(0, Math.round((new Date(rec.expires_at).getTime() - Date.now()) / 1000));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rec.expires_at]);
  const expiresLabel = expiresIn == null
    ? null
    : expiresIn > 3600 ? `${Math.floor(expiresIn/3600)}h ${Math.floor((expiresIn%3600)/60)}m`
    : expiresIn > 60 ? `${Math.floor(expiresIn/60)}m`
    : `${expiresIn}s`;

  const candles = useCandles(rec.symbol);
  const levels = { entry: rec.entry_price, target: rec.target_price, stop: rec.stop_loss };
  const livePrice = candles.quote?.current_price ?? rec.entry_price;
  const liveChg = candles.quote?.change_pct;
  const [chartOpen, setChartOpen] = useState(false);
  const newTabHref = chartHref({ symbol: rec.symbol, entry: rec.entry_price, target: rec.target_price, stop: rec.stop_loss, side });

  return (
    <div className="glass-premium accent-rail lift rounded-2xl p-5 fade-in" style={{ '--rail': `${accent}cc` }}>
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div className="flex items-start gap-4">
          <ConfidenceRing value={rec.confidence_score || 0} />
          <div>
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-lg font-semibold tracking-tight text-white">{rec.symbol}</span>
              <span className="px-2 py-0.5 rounded-md text-[11px] font-semibold uppercase tracking-wider"
                style={{ background: `${accent}22`, color: accent, border: `1px solid ${accent}44` }}>
                {action || '—'}
              </span>
              {horizonTag && (
                <span className="text-[10px] px-2 py-0.5 rounded-full bg-gold-500/15 border border-gold-500/40 text-gold-200 font-semibold">
                  {horizonTag}
                </span>
              )}
              {stratLabel && (
                <span className="text-[10px] px-2 py-0.5 rounded-full bg-sky-400/10 border border-sky-400/30 text-sky-200">
                  {stratLabel}
                </span>
              )}
              {rationale?.no_edge_hold && (
                <span className="text-[10px] px-2 py-0.5 rounded-full bg-amber-400/10 border border-amber-400/40 text-amber-200" title="No positive out-of-sample edge — the strategy doesn't beat the market on held-out data, so this is a HOLD, not a trade.">
                  no proven edge
                </span>
              )}
              {rationale?.profit_factor != null && rationale?.action !== 'hold' && (
                <span className="text-[10px] px-2 py-0.5 rounded-full bg-emerald-400/10 border border-emerald-400/30 text-emerald-300 tabular" title="Profit factor (gross win / gross loss) and expectancy per trade — OOS. >1 = profitable edge.">
                  PF {Number(rationale.profit_factor).toFixed(2)} · exp {Number(rationale.expectancy_pct).toFixed(2)}%
                </span>
              )}
              {expiresLabel && (
                <span className="text-[10px] px-2 py-0.5 rounded-full bg-white/5 border border-white/10 text-white/55 tabular">
                  expires {expiresLabel}
                </span>
              )}
            </div>
            <div className="mt-1 text-xs text-white/55 tabular">
              R:R {fmtNum(rec.risk_reward_ratio)} · Qty {fmtNum(rec.quantity, { maximumFractionDigits: 0 })}
              {tech.rsi != null && <> · RSI {tech.rsi}</>}
              {tech.trend && <> · {tech.trend}</>}
            </div>
          </div>
        </div>
        <div className="flex gap-2 ml-auto">
          <button disabled={busy || noId} onClick={() => onReject(rec)} title={noId ? 'Recommendation not persisted yet — wait for refresh' : ''}
            className="px-3 py-1.5 rounded-lg text-xs font-medium text-white/70 border border-white/10 hover:bg-white/5 disabled:opacity-40 disabled:cursor-not-allowed transition">
            Reject
          </button>
          <button disabled={busy || noId} onClick={() => onApprove(rec)} title={noId ? 'Recommendation not persisted yet — wait for refresh' : ''}
            className="px-3 py-1.5 rounded-lg text-xs font-semibold text-ink-900 bg-gold-500 hover:bg-gold-400 shadow-glow disabled:opacity-40 disabled:cursor-not-allowed transition">
            Approve & Execute
          </button>
        </div>
      </div>

      <div className="mt-4 grid lg:grid-cols-5 gap-4">
        {/* Left: levels + reasoning */}
        <div className="lg:col-span-2 space-y-3">
          <div className="grid grid-cols-3 gap-2 text-sm tabular">
            <div className="rounded-lg bg-white/[0.03] border border-white/5 px-3 py-2">
              <div className="text-[10px] uppercase tracking-wider text-white/45">Entry</div>
              <div className="text-white">{fmtNum(rec.entry_price)}</div>
            </div>
            <div className="rounded-lg bg-emerald-400/5 border border-emerald-400/15 px-3 py-2">
              <div className="text-[10px] uppercase tracking-wider text-emerald-300/70">Target</div>
              <div className="text-emerald-300">{fmtNum(rec.target_price)}</div>
            </div>
            <div className="rounded-lg bg-rose-400/5 border border-rose-400/15 px-3 py-2">
              <div className="text-[10px] uppercase tracking-wider text-rose-300/70">Stop</div>
              <div className="text-rose-300">{fmtNum(rec.stop_loss)}</div>
            </div>
          </div>
          {rec.reasoning && (
            <div className="text-xs leading-relaxed text-white/60 border-l-2 border-gold-500/40 pl-3 italic">
              {rec.reasoning}
            </div>
          )}
        </div>

        {/* Right: TradingView-style candle preview with entry/target/stop overlays */}
        <div className="lg:col-span-3 rounded-xl border border-white/5 bg-ink-950/40 p-3">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-baseline gap-2">
              <span className="text-sm font-semibold text-white tabular">{fmtNum(livePrice)}</span>
              {liveChg != null && (
                <span className={`text-xs tabular ${liveChg >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>{fmtPct(liveChg)}</span>
              )}
            </div>
            <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-white/35">
              <span className="hidden sm:inline">1D · 5m</span>
              {candles.source && <span className="px-1.5 py-0.5 rounded bg-white/5 border border-white/10">{candles.source}</span>}
              <button type="button" title="Expand & zoom" onClick={() => setChartOpen(true)}
                className="w-6 h-6 rounded-md border border-white/10 text-white/55 hover:text-white hover:bg-white/5 transition inline-flex items-center justify-center">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M15 3h6v6" /><path d="M9 21H3v-6" /><path d="M21 3l-7 7" /><path d="M3 21l7-7" />
                </svg>
              </button>
              <a href={newTabHref} target="_blank" rel="noopener noreferrer" title="Open in new tab"
                className="w-6 h-6 rounded-md border border-white/10 text-white/55 hover:text-white hover:bg-white/5 transition inline-flex items-center justify-center">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" /><path d="M15 3h6v6" /><path d="M10 14L21 3" />
                </svg>
              </a>
            </div>
          </div>
          <button type="button" onClick={() => setChartOpen(true)} title="Click to expand & zoom"
            className="block w-full rounded-lg overflow-hidden ring-0 hover:ring-1 hover:ring-white/10 transition">
            <CandleChart data={candles.series} levels={levels} dir={side} loading={candles.loading} />
          </button>
          <div className="mt-2 flex items-center justify-between gap-2 text-[10px] text-white/40">
            <div className="flex items-center gap-3">
              <span className="inline-flex items-center gap-1"><i className="w-2.5 h-0.5 bg-[#e6c181] inline-block rounded" />Entry</span>
              <span className="inline-flex items-center gap-1"><i className="w-2.5 h-0.5 bg-[#10d995] inline-block rounded" />Target</span>
              <span className="inline-flex items-center gap-1"><i className="w-2.5 h-0.5 bg-[#f43f5e] inline-block rounded" />Stop</span>
            </div>
            <span className="text-white/30">click to zoom</span>
          </div>
        </div>
      </div>

      <ChartModal open={chartOpen} onClose={() => setChartOpen(false)} symbol={rec.symbol} levels={levels} side={side} />

      {(why.length > 0 || trust) && (
        <details className="mt-3">
          <summary className="cursor-pointer text-[11px] uppercase tracking-wider text-white/45 hover:text-white/75 select-none">
            Why &amp; how to trust this
          </summary>
          <div className="mt-2 space-y-3">
            {why.length > 0 && (
              <ul className="space-y-1">
                {why.map((w, i) => (
                  <li key={i} className="text-xs text-white/65 flex gap-2">
                    <span className={`shrink-0 font-semibold ${w.primary ? 'text-gold-400' : 'text-white/40'}`}>{w.factor}</span>
                    <span>{w.detail}</span>
                  </li>
                ))}
              </ul>
            )}
            {trust && (
              <div className="rounded-lg bg-white/[0.03] border border-white/5 p-3">
                <div className="flex items-center gap-2 mb-2 flex-wrap">
                  <span className="text-[10px] uppercase tracking-wider text-white/45">Trust</span>
                  <span className={`text-[10px] px-2 py-0.5 rounded-full border uppercase tracking-wider ${trustTone}`}>
                    {trust.level}
                  </span>
                  {trust.strategy_label && <span className="text-[11px] text-white/55">via {trust.strategy_label}</span>}
                  {bt?.validation && (
                    <span className={`text-[9px] px-1.5 py-0.5 rounded-full border uppercase tracking-wider ${String(bt.validation).startsWith('walk-forward') ? 'bg-emerald-400/10 border-emerald-400/30 text-emerald-300' : 'bg-amber-400/10 border-amber-400/30 text-amber-200'}`}
                      title="walk-forward = win rate measured out-of-sample across folds; honest, not overfit">
                      {bt.validation}
                    </span>
                  )}
                </div>
                {bt ? (
                  <>
                    <div className="grid grid-cols-4 gap-2 text-center tabular">
                      {[['Win', `${Math.round((bt.win_rate || 0) * 100)}%`],
                        ['Sharpe', fmtNum(bt.sharpe)],
                        ['Trades', fmtNum(bt.n_trades, { maximumFractionDigits: 0 })],
                        ['Max DD', `${fmtNum(bt.max_drawdown_pct)}%`]].map(([k, v]) => (
                        <div key={k} className="rounded-md bg-white/[0.03] border border-white/5 px-2 py-1.5">
                          <div className="text-[9px] uppercase tracking-wider text-white/40">{k}</div>
                          <div className="text-xs text-white/85">{v}</div>
                        </div>
                      ))}
                    </div>
                    <div className="mt-2 text-[11px] text-white/45">
                      {bt.horizon
                        ? `Backtested on the ${bt.horizon} horizon`
                        : `Backtested over ${bt.lookback_days}d of ${bt.interval} bars`}
                      {String(bt.validation || '').startsWith('walk-forward') && bt.train_win_rate != null && (
                        <> · in-sample {Math.round(bt.train_win_rate * 100)}% → OOS {Math.round((bt.win_rate || 0) * 100)}%</>
                      )}
                      {bt.baseline_win_rate != null && <> · baseline win {Math.round(bt.baseline_win_rate * 100)}%</>}
                      {bt.improvement_pp != null && <> · {bt.improvement_pp > 0 ? '+' : ''}{fmtNum(bt.improvement_pp, { maximumFractionDigits: 1 })}pp vs default</>}
                    </div>
                  </>
                ) : (
                  <div className="text-xs text-white/55">
                    No backtest track record for {rec.symbol} yet — train it on the Learning page to earn trust metrics.
                  </div>
                )}
                {Array.isArray(trust.caveats) && trust.caveats.length > 0 && (
                  <ul className="mt-2 space-y-0.5">
                    {trust.caveats.map((c, i) => (
                      <li key={i} className="text-[11px] text-amber-200/70 flex gap-1.5">
                        <span aria-hidden>⚠</span><span>{c}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </div>
        </details>
      )}
    </div>
  );
});

// ---------- Order-confirmation modal (preview then place) ----------
function OrderConfirmModal({ rec, onClose, onPlaced }) {
  const [preview, setPreview] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [adjustedQty, setAdjustedQty] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true); setError(null);
    fetch(`${API}/api/v1/trades/${rec.id}/preview`)
      .then((r) => r.json().then((b) => ({ ok: r.ok, body: b })))
      .then(({ ok, body }) => {
        if (cancelled) return;
        if (!ok) setError(body.detail || 'Preview failed');
        else { setPreview(body); setAdjustedQty(body.order.quantity); }
      })
      .catch((e) => !cancelled && setError(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => { cancelled = true; };
  }, [rec.id]);

  const confirm = async () => {
    setSubmitting(true); setError(null);
    try {
      const r = await fetch(`${API}/api/v1/trades/${rec.id}/approve`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ confirmed: true, adjusted_quantity: adjustedQty }),
      });
      const body = await r.json();
      if (!r.ok) throw new Error(body.detail || 'Order placement failed');
      onPlaced(body);
    } catch (e) {
      setError(e.message);
    } finally {
      setSubmitting(false);
    }
  };

  const isPaper = preview?.is_paper;
  const o = preview?.order;
  const cost = o ? (Number(o.price) || 0) * (Number(adjustedQty) || 0) : 0;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm fade-in">
      <div className="glass rounded-2xl shadow-card w-full max-w-md overflow-hidden">
        <header className="px-6 py-5 border-b border-white/5 flex items-center gap-3">
          <div className={`w-9 h-9 rounded-xl flex items-center justify-center font-bold ${
            rec.side === 'buy' ? 'bg-emerald-500/20 text-emerald-300' : 'bg-rose-500/20 text-rose-300'
          }`}>{(rec.side || '').toUpperCase().slice(0,1)}</div>
          <div className="flex-1 min-w-0">
            <div className="text-base font-semibold text-white">Confirm order — {rec.symbol}</div>
            <div className="text-xs text-white/55">{rec.side?.toUpperCase()} signal · review before sending to broker</div>
          </div>
          <button onClick={onClose} className="text-white/40 hover:text-white text-xl leading-none px-2">×</button>
        </header>

        <div className="px-6 py-5 space-y-4">
          {loading && <div className="text-sm text-white/60">Loading order preview…</div>}
          {error && !preview && (
            <div className="rounded-lg bg-rose-500/10 border border-rose-500/30 px-3 py-3 text-sm text-rose-200">
              {error}
            </div>
          )}

          {preview && (
            <>
              <div className={`rounded-xl border px-4 py-3 ${
                isPaper
                  ? 'bg-blue-400/10 border-blue-400/30 text-blue-200'
                  : 'bg-rose-500/10 border-rose-400/40 text-rose-100'
              }`}>
                <div className="text-[11px] uppercase tracking-wider font-semibold mb-1">
                  {isPaper ? 'PAPER MODE' : '⚠ LIVE TRADING'}
                </div>
                <div className="text-xs leading-relaxed">{preview.warning}</div>
              </div>

              <div className="grid grid-cols-2 gap-3 text-sm">
                <Detail label="Broker" value={preview.broker_label} />
                <Detail label="Side" value={o.side} accent={o.side === 'BUY' ? 'pos' : 'neg'} />
                <Detail label="Symbol" value={o.symbol} mono />
                <Detail label="Order Type" value={o.order_type} />
                <Detail label="Product" value={o.product} hint={o.product === 'MIS' ? 'Intraday' : o.product === 'CNC' ? 'Delivery' : 'Carry'} />
                <Detail label="Price" value={`₹${(Number(o.price) || 0).toLocaleString('en-IN', {maximumFractionDigits: 2})}`} mono />
              </div>

              <label className="block">
                <span className="block text-[11px] uppercase tracking-wider text-white/55 mb-1">Quantity (editable)</span>
                <input type="number" min={1} value={adjustedQty || ''}
                  onChange={(e) => setAdjustedQty(Math.max(1, Number(e.target.value) || 1))}
                  className="w-full bg-white/[0.04] border border-white/10 rounded-lg px-3 py-2 text-sm text-white outline-none focus:border-gold-500/60 font-mono tabular" />
              </label>

              <div className="rounded-lg bg-white/[0.03] border border-white/5 px-3 py-2 flex justify-between text-sm">
                <span className="text-white/55">Estimated cost</span>
                <span className="text-white tabular font-semibold">
                  ₹{cost.toLocaleString('en-IN', { maximumFractionDigits: 2 })}
                </span>
              </div>

              {error && (
                <div className="rounded-lg bg-rose-500/10 border border-rose-500/30 px-3 py-2 text-sm text-rose-200">
                  {error}
                </div>
              )}

              <div className="flex justify-end gap-2 pt-1">
                <button onClick={onClose} disabled={submitting}
                  className="px-4 py-2 rounded-lg text-sm text-white/70 border border-white/10 hover:bg-white/5 transition">
                  Cancel
                </button>
                <button onClick={confirm} disabled={submitting || !preview}
                  className={`px-4 py-2 rounded-lg text-sm font-semibold transition disabled:opacity-50 ${
                    isPaper
                      ? 'bg-blue-500 hover:bg-blue-400 text-white'
                      : 'bg-rose-500 hover:bg-rose-400 text-white shadow-glow'
                  }`}>
                  {submitting ? 'Placing…' : isPaper ? 'Simulate Order' : `Place LIVE order`}
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

const Detail = memo(function Detail({ label, value, mono, hint, accent }) {
  return (
    <div className="rounded-lg bg-white/[0.03] border border-white/5 px-3 py-2">
      <div className="text-[10px] uppercase tracking-wider text-white/45">{label}</div>
      <div className={`${mono ? 'font-mono' : ''} ${
        accent === 'pos' ? 'text-emerald-300' :
        accent === 'neg' ? 'text-rose-300' : 'text-white'
      } font-semibold`}>{value}</div>
      {hint && <div className="text-[10px] text-white/35 mt-0.5">{hint}</div>}
    </div>
  );
});

// ---------- Order history widget ----------
const OrderHistory = memo(function OrderHistory({ trades }) {
  if (!trades || !trades.length) {
    return (
      <div className="glass rounded-2xl px-5 py-4 text-xs text-white/50">
        No orders placed yet. Approve a recommendation to send your first order.
      </div>
    );
  }
  return (
    <div className="glass rounded-2xl shadow-card overflow-hidden">
      <div className="px-5 py-3 border-b border-white/5 flex items-center justify-between">
        <div className="text-sm font-semibold text-white">Recent orders</div>
        <span className="text-[11px] text-white/40">{trades.length} placed</span>
      </div>
      <div className="divide-y divide-white/5">
        {trades.slice(0, 8).map((t) => (
          <div key={t.id} className="px-5 py-2.5 flex items-center gap-3 text-sm">
            <span className={`px-2 py-0.5 rounded-md text-[10px] font-bold uppercase tracking-wider ${
              t.side === 'BUY' ? 'bg-emerald-500/15 text-emerald-300' : 'bg-rose-500/15 text-rose-300'
            }`}>{t.side}</span>
            <span className="text-white font-medium tabular">{t.symbol}</span>
            <span className="text-white/45 tabular">×{t.quantity}</span>
            <span className="text-white/60 tabular">₹{(t.placed_price || 0).toLocaleString('en-IN', {maximumFractionDigits: 2})}</span>
            <span className="text-[10px] uppercase tracking-wider text-white/40 ml-auto">{t.broker_name}</span>
            <span className={`text-[10px] uppercase tracking-wider px-2 py-0.5 rounded-full border ${
              t.status === 'COMPLETE' ? 'bg-emerald-400/10 border-emerald-400/30 text-emerald-300' :
              t.status === 'PLACED' || t.status === 'OPEN' ? 'bg-sky-400/10 border-sky-400/30 text-sky-300' :
              t.status === 'SIMULATED' ? 'bg-blue-400/10 border-blue-400/30 text-blue-300' :
              'bg-rose-400/10 border-rose-400/30 text-rose-300'
            }`}>
              {t.is_paper ? 'PAPER · ' : ''}{t.status}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
});

// ---------- Big chart (memoised — re-renders only when data or color changes) ----------
const PriceChart = memo(function PriceChart({ data = [], color = '#e6c181', height = 220 }) {
  if (!data.length) return <div style={{ height }} className="flex items-center justify-center text-white/40 text-sm">No chart data</div>;
  const w = 720, h = height;
  const pad = { l: 36, r: 8, t: 8, b: 22 };
  const closes = data.map(d => d.c);
  const min = Math.min(...closes), max = Math.max(...closes);
  const range = max - min || 1;
  const xs = (i) => pad.l + (i / Math.max(data.length - 1, 1)) * (w - pad.l - pad.r);
  const ys = (v) => pad.t + (1 - (v - min) / range) * (h - pad.t - pad.b);
  const path = data.map((d, i) => `${i === 0 ? 'M' : 'L'} ${xs(i)} ${ys(d.c)}`).join(' ');
  const area = `${path} L ${xs(data.length - 1)} ${h - pad.b} L ${pad.l} ${h - pad.b} Z`;
  const gid = `bg-${color.replace('#', '')}`;
  const yTicks = [0, 0.25, 0.5, 0.75, 1].map(t => min + t * range);
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="w-full" preserveAspectRatio="none">
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.35" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      {yTicks.map((v, i) => (
        <g key={i}>
          <line x1={pad.l} x2={w - pad.r} y1={ys(v)} y2={ys(v)} stroke="rgba(255,255,255,0.05)" />
          <text x={6} y={ys(v) + 4} fontSize="10" fill="rgba(255,255,255,0.4)" className="tabular">{v.toFixed(2)}</text>
        </g>
      ))}
      <path d={area} fill={`url(#${gid})`} />
      <path d={path} fill="none" stroke={color} strokeWidth="1.8" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
});

// Per-symbol candles hook lives in ../lib/useCandles (shared with the modal +
// standalone chart page; includes a timeout so a busy backend never hangs the UI).

// ---------- Candlestick preview (TradingView-style) ----------
// Pure SVG OHLC candles with entry / target / stop overlays + a live price tag.
const CandleChart = memo(function CandleChart({
  data = [], levels = {}, dir = 'buy', height = 188, loading = false,
}) {
  if (loading) {
    return <div style={{ height }} className="rounded-xl shimmer border border-white/5" aria-busy="true" />;
  }
  if (!data.length) {
    return (
      <div style={{ height }} className="rounded-xl border border-white/5 bg-white/[0.02] flex items-center justify-center text-white/35 text-xs">
        No intraday data
      </div>
    );
  }
  const W = 560, H = height;
  const pad = { l: 8, r: 58, t: 12, b: 16 };
  const up = '#10d995', down = '#f43f5e';

  // y-range spans candle extremes AND the overlay levels so the lines are visible.
  let lo = Math.min(...data.map((d) => d.l ?? d.c));
  let hi = Math.max(...data.map((d) => d.h ?? d.c));
  [levels.entry, levels.target, levels.stop].forEach((v) => {
    if (v != null && !Number.isNaN(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
  });
  const span = (hi - lo) || 1;
  lo -= span * 0.06; hi += span * 0.06;
  const rng = hi - lo;

  const innerW = W - pad.l - pad.r, innerH = H - pad.t - pad.b;
  const n = data.length;
  const slot = innerW / n;
  const bodyW = Math.max(1.4, Math.min(7, slot * 0.62));
  const x = (i) => pad.l + i * slot + slot / 2;
  const y = (v) => pad.t + (1 - (v - lo) / rng) * innerH;

  const last = data[data.length - 1].c;
  const first = data[0].o ?? data[0].c;
  const dayUp = last >= first;

  const gridVals = [0, 0.25, 0.5, 0.75, 1].map((t) => lo + t * rng);

  const Level = ({ v, color, label, dashed = true }) => {
    if (v == null || Number.isNaN(v)) return null;
    const yy = y(v);
    return (
      <g>
        <line x1={pad.l} x2={W - pad.r} y1={yy} y2={yy} stroke={color} strokeOpacity="0.8"
          strokeWidth="1" strokeDasharray={dashed ? '4 3' : ''} />
        <rect x={W - pad.r + 2} y={yy - 8} width={pad.r - 4} height={16} rx="3" fill={color} fillOpacity="0.16" />
        <text x={W - pad.r + 6} y={yy + 3.5} fontSize="9.5" fill={color} className="tabular">{label}</text>
      </g>
    );
  };

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" preserveAspectRatio="none" style={{ height }}>
      <defs>
        <linearGradient id="candle-bg" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={dayUp ? up : down} stopOpacity="0.06" />
          <stop offset="100%" stopColor={dayUp ? up : down} stopOpacity="0" />
        </linearGradient>
      </defs>
      <rect x={pad.l} y={pad.t} width={innerW} height={innerH} fill="url(#candle-bg)" />
      {gridVals.map((v, i) => (
        <line key={i} x1={pad.l} x2={W - pad.r} y1={y(v)} y2={y(v)} stroke="rgba(255,255,255,0.04)" />
      ))}

      {/* candles */}
      {data.map((d, i) => {
        const o = d.o ?? d.c, c = d.c, h = d.h ?? Math.max(o, c), l = d.l ?? Math.min(o, c);
        const col = c >= o ? up : down;
        const yo = y(o), yc = y(c);
        const top = Math.min(yo, yc);
        const bh = Math.max(1, Math.abs(yc - yo));
        return (
          <g key={i}>
            <line x1={x(i)} x2={x(i)} y1={y(h)} y2={y(l)} stroke={col} strokeOpacity="0.55" strokeWidth="1" />
            <rect x={x(i) - bodyW / 2} y={top} width={bodyW} height={bh} fill={col} rx="0.6" />
          </g>
        );
      })}

      {/* overlays */}
      <Level v={levels.target} color="#10d995" label={fmtNum(levels.target)} />
      <Level v={levels.entry} color="#e6c181" label={fmtNum(levels.entry)} dashed={false} />
      <Level v={levels.stop} color="#f43f5e" label={fmtNum(levels.stop)} />

      {/* last price tag */}
      <g>
        <line x1={pad.l} x2={W - pad.r} y1={y(last)} y2={y(last)} stroke={dayUp ? up : down} strokeOpacity="0.35" strokeWidth="1" />
        <rect x={W - pad.r + 2} y={y(last) - 8.5} width={pad.r - 4} height={17} rx="3" fill={dayUp ? up : down} />
        <text x={W - pad.r + 6} y={y(last) + 3.5} fontSize="9.5" fill="#06140f" fontWeight="700" className="tabular">{fmtNum(last)}</text>
      </g>
    </svg>
  );
});

// ---------- Chat sidebar ----------
function ChatPanel({ onSelectSymbol, open, onClose }) {
  const [messages, setMessages] = useState([
    { role: 'assistant', content: "I am your AI trading desk. Ask about a ticker, request a trade recommendation, or get a macro read.", suggestions: [
      "What's RELIANCE trading at right now?",
      "Should I buy INFY?",
      "Show me NIFTY news",
      "What's the macro picture?",
    ] }
  ]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const scrollRef = useRef(null);
  const abortRef = useRef(null);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [messages, sending]);

  useEffect(() => () => { if (abortRef.current) abortRef.current.abort(); }, []);

  const send = async (text) => {
    const msg = (text || input).trim();
    if (!msg || sending) return;
    setMessages(m => [...m, { role: 'user', content: msg }]);
    setInput('');
    setSending(true);
    if (abortRef.current) abortRef.current.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    try {
      const res = await fetch(`${API}/api/v1/chat/`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: msg }), signal: ctrl.signal,
      });
      const data = await res.json();
      setMessages(m => [...m, { role: 'assistant', content: data.reply, intent: data.intent, data: data.data, suggestions: data.suggestions || [] }]);
      if (data?.data?.quote?.symbol && onSelectSymbol) onSelectSymbol(data.data.quote.symbol);
      if (data?.data?.recommendation?.symbol && onSelectSymbol) onSelectSymbol(data.data.recommendation.symbol);
    } catch (e) {
      if (e.name === 'AbortError') return;
      setMessages(m => [...m, { role: 'assistant', content: `Connection error: ${e.message}`, intent: 'error', suggestions: [] }]);
    } finally {
      setSending(false);
    }
  };

  return (
    <>
      {/* Mobile backdrop */}
      <div onClick={onClose}
        className={`xl:hidden fixed inset-0 bg-black/50 z-30 transition-opacity ${open ? 'opacity-100' : 'opacity-0 pointer-events-none'}`} />
      <aside className={`glass rounded-2xl shadow-card flex flex-col
        xl:static xl:translate-x-0 xl:opacity-100 xl:h-[calc(100vh-140px)]
        fixed inset-x-3 bottom-3 top-20 z-40 transition-transform
        ${open ? 'translate-y-0' : 'translate-y-[110%]'} xl:translate-y-0`}>
        <header className="px-5 py-4 border-b border-white/5 flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-gold-500 to-gold-600 flex items-center justify-center text-ink-900 font-bold">AI</div>
          <div className="min-w-0">
            <div className="text-sm font-semibold text-white">Trading Desk</div>
            <div className="text-[11px] text-white/45 truncate">Technical · News · Macro · Risk</div>
          </div>
          <span className="pulse-dot w-2 h-2 rounded-full bg-emerald-400 text-emerald-400 ml-auto" />
          <button onClick={onClose}
            className="xl:hidden text-white/50 hover:text-white text-xl leading-none px-1">×</button>
        </header>

        <div ref={scrollRef} className="flex-1 overflow-y-auto px-5 py-4 space-y-3">
          {messages.map((m, i) => (
            <div key={i} className={`fade-in ${m.role === 'user' ? 'text-right' : ''}`}>
              <div className={`inline-block max-w-[88%] text-sm leading-relaxed rounded-2xl px-3.5 py-2.5 whitespace-pre-wrap ${
                m.role === 'user'
                  ? 'bg-gold-500 text-ink-900 font-medium'
                  : 'bg-white/[0.04] border border-white/5 text-white/85'
              }`}>{m.content}</div>
              {m.role === 'assistant' && m.data?.quote && (
                <div className="mt-2 inline-flex flex-wrap gap-2 text-xs tabular">
                  <span className="px-2 py-1 rounded-md bg-white/5 border border-white/5">{m.data.quote.symbol}</span>
                  <span className="px-2 py-1 rounded-md bg-white/5 border border-white/5">{fmtNum(m.data.quote.current_price)}</span>
                  <span className={`px-2 py-1 rounded-md border ${m.data.quote.change >= 0 ? 'bg-emerald-400/10 border-emerald-400/30 text-emerald-300' : 'bg-rose-400/10 border-rose-400/30 text-rose-300'}`}>
                    {fmtPct(m.data.quote.change_pct)}
                  </span>
                </div>
              )}
              {m.role === 'assistant' && m.suggestions && m.suggestions.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {m.suggestions.map((s, j) => (
                    <button key={j} onClick={() => send(s)}
                      className="text-[11px] px-2.5 py-1 rounded-full bg-white/[0.03] hover:bg-white/[0.08] border border-white/10 text-white/70 transition">
                      {s}
                    </button>
                  ))}
                </div>
              )}
            </div>
          ))}
          {sending && (
            <div className="flex items-center gap-1.5 text-white/40 text-xs px-1">
              <span className="w-1.5 h-1.5 rounded-full bg-white/40 animate-pulse" />
              <span className="w-1.5 h-1.5 rounded-full bg-white/40 animate-pulse" style={{animationDelay:'0.15s'}} />
              <span className="w-1.5 h-1.5 rounded-full bg-white/40 animate-pulse" style={{animationDelay:'0.3s'}} />
            </div>
          )}
        </div>

        <form onSubmit={(e) => { e.preventDefault(); send(); }} className="p-3 border-t border-white/5 flex gap-2">
          <input value={input} onChange={(e) => setInput(e.target.value)}
            placeholder="Ask about a ticker, request a trade idea..."
            className="flex-1 min-w-0 bg-white/[0.04] border border-white/10 rounded-xl px-4 py-2.5 text-sm text-white placeholder:text-white/30 outline-none focus:border-gold-500/60 transition"
            disabled={sending} />
          <button type="submit" disabled={sending || !input.trim()}
            className="px-4 py-2.5 rounded-xl bg-gold-500 hover:bg-gold-400 disabled:opacity-40 text-ink-900 font-semibold text-sm transition shrink-0">
            Send
          </button>
        </form>
      </aside>
    </>
  );
}

// ---------- json fetcher used by all polls ----------
const jget = (url) => {
  const fetcher = (signal) =>
    fetch(url, { signal })
      .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); });
  // Tag the URL so useLivePoll caches by resource across route changes — returning
  // to the dashboard shows last data instantly instead of a full blank reload.
  fetcher.cacheKey = url;
  return fetcher;
};

// ---------- Data store panel (historical OHLC coverage + bulk ingest) ----------
const DS_PRESETS = [
  ['all_nse', 'Whole NSE market (~2,900)'],
  ['indexes_plus_nifty50', 'Indexes + NIFTY 50 (66)'],
  ['nifty50', 'NIFTY 50 (50)'],
  ['indexes', 'Major indexes (16)'],
];

function DSStat({ label, value }) {
  return (
    <div className="rounded-lg bg-white/[0.03] border border-white/5 px-3 py-2">
      <div className="text-[10px] uppercase tracking-wider text-white/45">{label}</div>
      <div className="text-sm text-white tabular mt-0.5">{value}</div>
    </div>
  );
}

function DataStorePanel() {
  const [status, setStatus] = useState(null);
  const [preset, setPreset] = useState('all_nse');
  const [interval, setIntervalStr] = useState('day');
  const [maxSymbols, setMaxSymbols] = useState(500);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const pollRef = useRef(null);

  const load = useCallback(async () => {
    try {
      const s = await fetch(`${API}/api/v1/learning/data/status`).then((r) => r.json());
      setStatus(s);
    } catch {}
  }, []);

  useEffect(() => { load(); }, [load]);

  const running = status?.running;
  useEffect(() => {
    if (!running) { if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; } return; }
    pollRef.current = setInterval(load, 3000);
    return () => { if (pollRef.current) clearInterval(pollRef.current); pollRef.current = null; };
  }, [running, load]);

  const startIngest = async () => {
    setErr(null); setBusy(true);
    try {
      const body = { preset, interval, throttle: 1.0 };
      if (Number(maxSymbols) > 0) body.max_symbols = Number(maxSymbols);
      const r = await fetch(`${API}/api/v1/learning/data/ingest`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || 'Failed to start ingest');
      await load();
    } catch (e) { setErr(e.message); }
    finally { setBusy(false); }
  };

  const cov = status?.coverage || {};
  const prog = status?.state?.progress;
  const last = status?.state?.last_stats;
  const byIv = cov.by_interval || [];

  return (
    <section className="glass rounded-2xl shadow-card p-5">
      <div className="flex items-center justify-between gap-3 flex-wrap mb-3">
        <div className="min-w-0">
          <h2 className="text-lg font-semibold text-white tracking-tight">Historical data store</h2>
          <div className="text-xs text-white/50">Download once → backtest every strategy offline. Then run the tournament on the Training page.</div>
        </div>
        <Link href="/training"
          className="text-xs px-3 py-1.5 rounded-lg font-semibold text-ink-900 bg-gold-500 hover:bg-gold-400 shadow-glow transition shrink-0">
          Run tournament →
        </Link>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5 mb-4">
        <DSStat label="Symbols stored" value={cov.symbols ?? '—'} />
        <DSStat label="Total bars" value={cov.total_bars != null ? cov.total_bars.toLocaleString() : '—'} />
        {byIv.map((x) => (
          <DSStat key={x.interval} label={`${x.interval}`} value={`${x.symbols} sym · ${x.bars.toLocaleString()}`} />
        ))}
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 items-end">
        <label className="block">
          <span className="block text-[10px] uppercase tracking-wider text-white/45 mb-1">Universe</span>
          <select value={preset} onChange={(e) => setPreset(e.target.value)} disabled={running}
            className="w-full bg-white/[0.04] border border-white/10 rounded-lg px-2 py-2 text-xs text-white outline-none focus:border-gold-500/60">
            {DS_PRESETS.map(([k, lbl]) => <option key={k} value={k}>{lbl}</option>)}
          </select>
        </label>
        <label className="block">
          <span className="block text-[10px] uppercase tracking-wider text-white/45 mb-1">Interval</span>
          <select value={interval} onChange={(e) => setIntervalStr(e.target.value)} disabled={running}
            className="w-full bg-white/[0.04] border border-white/10 rounded-lg px-2 py-2 text-xs text-white outline-none focus:border-gold-500/60">
            <option value="day">Daily (years)</option>
            <option value="30minute">30 min (60d)</option>
          </select>
        </label>
        <label className="block">
          <span className="block text-[10px] uppercase tracking-wider text-white/45 mb-1">Max symbols (0=all)</span>
          <input type="number" min={0} value={maxSymbols} onChange={(e) => setMaxSymbols(Number(e.target.value) || 0)} disabled={running}
            className="w-full bg-white/[0.04] border border-white/10 rounded-lg px-2 py-2 text-xs text-white outline-none focus:border-gold-500/60 font-mono tabular" />
        </label>
        <button onClick={startIngest} disabled={busy || running}
          className="px-3 py-2 rounded-lg text-xs font-semibold text-ink-900 bg-gold-500 hover:bg-gold-400 shadow-glow disabled:opacity-50 transition">
          {running ? 'Ingesting…' : busy ? 'Starting…' : 'Ingest data'}
        </button>
      </div>

      {err && <div className="mt-3 rounded-lg bg-rose-500/10 border border-rose-500/30 px-3 py-2 text-xs text-rose-200">{err}</div>}

      {running && prog && (
        <div className="mt-3 rounded-xl bg-ink-900/60 border border-sky-400/30 p-3 space-y-2">
          <div className="flex items-center justify-between text-xs">
            <span className="text-white">Ingesting {prog.done}/{prog.total} ({(prog.percent || 0).toFixed(0)}%)</span>
            <span className="text-white/55 tabular">{prog.current_symbol || '—'}</span>
          </div>
          <div className="w-full h-1.5 rounded-full bg-white/[0.05] overflow-hidden">
            <div className="h-full bg-gradient-to-r from-sky-400 to-emerald-400 transition-all duration-500" style={{ width: `${prog.percent || 0}%` }} />
          </div>
          <div className="text-[11px] text-white/50 tabular">
            stored {prog.ingested} · skipped {prog.skipped} · failed {prog.failed} · {(prog.bars_added || 0).toLocaleString()} bars added
          </div>
        </div>
      )}

      {!running && last && (
        <div className="mt-3 text-[11px] text-white/50 tabular">
          Last run: {last.ingested} ingested · {last.skipped} skipped · {last.failed} failed · {(last.bars_added || 0).toLocaleString()} bars
          {last.failed > 0 && <span className="text-amber-200/70"> — re-run to fill the {last.failed} gaps (Yahoo 429s are resumable)</span>}
        </div>
      )}
    </section>
  );
}

// ---------- main page ----------
export default function Dashboard() {
  useHiddenMarker();
  const [selected, setSelected] = useState('RELIANCE');
  const [busyId, setBusyId] = useState(null);
  const [chatOpen, setChatOpen] = useState(false);
  const [confirmRec, setConfirmRec] = useState(null);   // recommendation pending confirmation
  const [orderToast, setOrderToast] = useState(null);   // ephemeral success/failure banner

  // Health (one-shot)
  const [healthy, setHealthy] = useState(null);
  useEffect(() => {
    let cancelled = false;
    const ctrl = new AbortController();
    fetch(`${API}/health`, { signal: ctrl.signal })
      .then((r) => r.json())
      .then((d) => { if (!cancelled) setHealthy(d.status === 'healthy'); })
      .catch(() => { if (!cancelled) setHealthy(false); });
    return () => { cancelled = true; ctrl.abort(); };
  }, []);

  // Watchlist — abortable, pauses when hidden
  const { data: wlData } = useLivePoll(jget(`${API}/api/v1/market-data/watchlist?symbols=${DEFAULT_WATCHLIST}`), 20_000);
  const watchlist = wlData?.quotes || [];
  const [lastTick, setLastTick] = useState(null);
  useEffect(() => { if (wlData) setLastTick(new Date()); }, [wlData]);

  // Investment horizon for recommendations (1M/3M/6M/1Y/SW)
  const [horizon, setHorizon] = useState('1M');
  const [horizons, setHorizons] = useState([]);
  useEffect(() => {
    fetch(`${API}/api/v1/trades/horizons`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (d?.horizons) setHorizons(d.horizons); })
      .catch(() => {});
  }, []);

  // Recommendations (low-frequency), scoped to the chosen horizon.
  const { data: recsData, refresh: refreshRecs } = useLivePoll(
    jget(`${API}/api/v1/trades/recommendations?horizon=${horizon}`), 60_000, [horizon],
  );
  const recs = Array.isArray(recsData) ? recsData : [];

  // Force-regenerate recommendations (hits ?refresh=true so the agent re-runs
  // for every symbol instead of returning cached ones).
  const [forceBusy, setForceBusy] = useState(false);
  const forceRefreshRecs = useCallback(async () => {
    setForceBusy(true);
    try {
      // Bust the server-side 30-min dedup, then refresh the polled cache
      await fetch(`${API}/api/v1/trades/recommendations?horizon=${horizon}&refresh=true`, { method: 'GET' });
      await refreshRecs();
    } catch (e) {
      // soft-fail; the next regular poll will catch up
    } finally {
      setForceBusy(false);
    }
  }, [refreshRecs, horizon]);

  // Broker count (low-frequency — only changes when user toggles a broker)
  const { data: brokerData } = useLivePoll(jget(`${API}/api/v1/brokers/accounts`), 60_000);
  const brokerCount = brokerData ? (brokerData.accounts || []).filter((a) => a.status === 'connected').length : null;

  // Chart for selected symbol
  const chartPoll = useLivePoll(
    (signal) => Promise.all([
      fetch(`${API}/api/v1/market-data/intraday/${encodeURIComponent(selected)}?range=1d&interval=5m`, { signal }).then((r) => r.ok ? r.json() : { series: [] }),
      fetch(`${API}/api/v1/market-data/quotes/${encodeURIComponent(selected)}`, { signal }).then((r) => r.ok ? r.json() : null),
    ]).then(([i, q]) => ({ series: i?.series || [], quote: q, intradaySource: i?.source })),
    20_000,
    [selected],
  );
  const chartData = chartPoll.data?.series || [];
  const chartQuote = chartPoll.data?.quote || null;
  const chartSource = chartPoll.data?.intradaySource || chartQuote?.source || 'yahoo';

  // Active data providers (low-freq — only changes when broker connects/disconnects)
  const { data: providersData } = useLivePoll(jget(`${API}/api/v1/market-data/providers`), 120_000);
  const activeProviders = providersData?.active || [];
  const blockedProviders = providersData?.blocked || [];
  const blockedNote = providersData?.blocked_note;

  // Honest performance & risk-limit state for header badges
  const { data: perfData } = useLivePoll(jget(`${API}/api/v1/performance/stats?days=7&grade_now=false`), 120_000);
  const { data: limitsData } = useLivePoll(jget(`${API}/api/v1/risk/limits`), 60_000);

  // Trade history — declared before KPI computation since KPIs need open-trade count
  const { data: historyData, refresh: refreshHistory } = useLivePoll(
    (signal) => fetch(`${API}/api/v1/trades/history`, { signal }).then((r) => r.json()),
    60_000,
  );
  const trades = historyData?.trades || [];

  // Real KPI values — derived from connected brokers + actual trade history
  const accounts = brokerData?.accounts || [];
  // Sum INR-only for now; multi-currency aggregation would need FX rates.
  const inrAccounts = accounts.filter((a) => (a.currency || 'INR').toUpperCase() === 'INR' && a.status === 'connected');
  const totalCapital = inrAccounts.reduce((s, a) => s + (Number(a.balance) || 0), 0);
  const totalEquity = inrAccounts.reduce((s, a) => s + (Number(a.equity) || 0), 0);
  const totalMargin = inrAccounts.reduce((s, a) => s + (Number(a.margin_available) || 0), 0);
  const dayPnl = totalEquity - totalCapital;
  const dayPnlPct = totalCapital > 0 ? (dayPnl / totalCapital) * 100 : 0;

  const openTrades = trades.filter((t) => t.status === 'PLACED' || t.status === 'OPEN');
  const openCount = openTrades.length;
  const openExposure = openTrades.reduce((s, t) => s + (Number(t.placed_price) || 0) * (Number(t.quantity) || 0), 0);
  const allPaper = openTrades.length > 0 && openTrades.every((t) => t.is_paper);

  const breadth = useMemo(() => {
    const gainers = watchlist.filter((q) => q.change >= 0).length;
    return { gainers, losers: watchlist.length - gainers };
  }, [watchlist]);

  // Per-card session ring buffers — sparklines reflect what actually moved this session
  const sparkCapital  = useSessionRingBuffer('capital', totalCapital);
  const sparkPnL      = useSessionRingBuffer('pnl', dayPnl);
  const sparkPositions = useSessionRingBuffer('positions', openCount);
  const sparkBreadth  = useSessionRingBuffer('breadth', breadth.gainers);

  // Approve → opens the confirm modal (real money — never one-click)
  const approve = useCallback((rec) => {
    if (!rec.id) return;
    setConfirmRec(rec);
  }, []);

  const onOrderPlaced = useCallback((result) => {
    setConfirmRec(null);
    setOrderToast({
      kind: result.paper ? 'sim' : 'live',
      msg: result.paper
        ? `Simulated order ${result.order_id} via ${result.broker}`
        : `LIVE order placed: ${result.order_id} via ${result.broker}`,
    });
    setTimeout(() => setOrderToast(null), 6000);
    refreshRecs();
  }, [refreshRecs]);

  const reject = useCallback(async (rec) => {
    if (!rec.id) return;
    setBusyId(rec.id);
    try {
      await fetch(`${API}/api/v1/trades/${rec.id}/reject`, { method: 'POST' });
      refreshRecs();
    } finally { setBusyId(null); }
  }, [refreshRecs]);

  // Refresh history immediately after a successful order
  const placedTrigger = orderToast?.kind;
  useEffect(() => { if (placedTrigger) refreshHistory(); }, [placedTrigger, refreshHistory]);

  const chartColor = chartQuote && chartQuote.change >= 0 ? '#10d995' : '#f43f5e';

  return (
    <div className="min-h-screen text-white">
      {/* Top nav */}
      <header className="sticky top-0 z-30 glass-blur">
        <div className="max-w-[1600px] mx-auto px-4 sm:px-6 py-3 flex items-center gap-4 sm:gap-6">
          <div className="flex items-center gap-3 min-w-0">
            <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-gold-400 to-gold-600 flex items-center justify-center shadow-glow shrink-0">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#0a0e1a" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M3 17l6-6 4 4 8-8" /><path d="M14 7h7v7" />
              </svg>
            </div>
            <div className="min-w-0">
              <div className="text-sm font-semibold tracking-tight text-white truncate">Helios Capital</div>
              <div className="text-[10px] uppercase tracking-[0.18em] text-white/45 truncate">AI Trading Desk</div>
            </div>
          </div>
          <div className="hidden lg:flex items-center gap-4 text-xs tabular text-white/55">
            <MarketStatus />
            <span className="text-white/20">·</span>
            <TodayLabel />
            <Clock />
            <span className="text-white/20">·</span>
            <Link href="/training" className="text-white/55 hover:text-gold-300 transition">Training</Link>
            <Link href="/screener" className="text-white/55 hover:text-gold-300 transition">Screener</Link>
            <Link href="/performance" className="text-white/55 hover:text-gold-300 transition">Performance</Link>
            <Link href="/monitor" className="text-white/55 hover:text-gold-300 transition">Monitor</Link>
          </div>
          <div className="ml-auto flex items-center gap-2 text-xs">
            {limitsData?.kill_switch && (
              <Link href="/performance" className="hidden sm:inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border border-rose-400/40 text-rose-100 bg-rose-500/20 text-xs font-semibold animate-pulse">
                ⏹ KILL SWITCH ENGAGED
              </Link>
            )}
            {perfData?.graded_count > 0 && (
              <Link href="/performance" className={`hidden md:inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border text-xs transition ${
                perfData.hit_rate_1h >= 0.55
                  ? 'border-emerald-400/30 text-emerald-300 bg-emerald-400/5 hover:bg-emerald-400/10'
                  : perfData.hit_rate_1h >= 0.48
                  ? 'border-amber-400/30 text-amber-200 bg-amber-400/5 hover:bg-amber-400/10'
                  : 'border-rose-400/30 text-rose-300 bg-rose-400/5 hover:bg-rose-400/10'
              }`} title="Real signal hit rate, last 7 days">
                Hit rate: {(perfData.hit_rate_1h * 100).toFixed(0)}% · {perfData.graded_count} signals
              </Link>
            )}
            {activeProviders.length > 0 ? (
              <span className="hidden sm:inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border border-emerald-400/30 text-emerald-300 bg-emerald-400/5 text-xs">
                <span className="pulse-dot w-1.5 h-1.5 rounded-full bg-emerald-400 text-emerald-400" />
                Data: {activeProviders.map((p) => p.spec_name).join(' + ')} LIVE
              </span>
            ) : blockedProviders.length > 0 ? (
              <Link href="/brokers" className="hidden sm:inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border border-amber-400/40 text-amber-200 bg-amber-400/10 hover:bg-amber-400/15 text-xs transition" title={blockedNote}>
                <span className="w-1.5 h-1.5 rounded-full bg-amber-300" />
                Data: {blockedProviders[0].spec_name} Data API not subscribed — using Yahoo
              </Link>
            ) : (
              <span className="hidden sm:inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border border-amber-400/30 text-amber-200 bg-amber-400/5 text-xs">
                <span className="w-1.5 h-1.5 rounded-full bg-amber-300" />
                Data: Yahoo (delayed)
              </span>
            )}
            <Link href="/brokers" className={`hidden sm:inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border transition ${
              brokerCount > 0
                ? 'border-emerald-400/30 text-emerald-300 bg-emerald-400/5 hover:bg-emerald-400/10'
                : 'border-gold-500/40 text-gold-300 bg-gold-500/5 hover:bg-gold-500/10'
            }`}>
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 12V7a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-1" />
                <path d="M16 12h6" /><path d="M19 9l3 3-3 3" />
              </svg>
              {brokerCount === null ? 'Brokers' : brokerCount > 0 ? `${brokerCount} connected` : 'Connect a broker'}
            </Link>
            <span className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full border ${
              healthy === null ? 'border-white/10 text-white/50' : healthy ? 'border-emerald-400/30 text-emerald-300 bg-emerald-400/5' : 'border-rose-400/30 text-rose-300 bg-rose-400/5'
            }`}>
              <span className={`w-1.5 h-1.5 rounded-full ${healthy ? 'bg-emerald-400' : 'bg-rose-400'}`} />
              <span className="hidden sm:inline">{healthy === null ? 'Connecting' : healthy ? 'API' : 'Offline'}</span>
            </span>
            {lastTick && <span className="hidden md:inline text-white/40 tabular">tick {fmtTime(lastTick)}</span>}
            <button onClick={() => setChatOpen(true)}
              className="xl:hidden px-2.5 py-1 rounded-full border border-white/10 text-white/70 hover:bg-white/5">Chat</button>
          </div>
        </div>
        <LiveTicker quotes={watchlist} />
      </header>

      <main className="max-w-[1600px] mx-auto px-4 sm:px-6 py-6 grid grid-cols-1 xl:grid-cols-[1fr_380px] gap-6">
        {/* LEFT: dashboard */}
        <div className="space-y-6 min-w-0">
          {/* KPI grid — all values derived from real broker accounts + trade history */}
          <section className="grid grid-cols-2 lg:grid-cols-4 gap-3 sm:gap-4">
            <KPICard
              label="Deployable Capital"
              value={brokerCount ? fmtINR(totalCapital) : '—'}
              sub={
                brokerCount
                  ? `${fmtINR(totalMargin)} usable · ${brokerCount} broker${brokerCount === 1 ? '' : 's'}`
                  : 'connect a broker on /brokers'
              }
              accent="#e6c181"
              spark={sparkCapital}
            />
            <KPICard
              label="Account P&L"
              value={brokerCount && totalCapital > 0 ? fmtSignedINR(dayPnl) : '—'}
              sub={
                brokerCount && totalCapital > 0
                  ? `${dayPnl >= 0 ? '+' : ''}${dayPnlPct.toFixed(2)}% (equity − balance)`
                  : 'awaiting broker data'
              }
              accent={dayPnl >= 0 ? '#10d995' : '#f43f5e'}
              spark={sparkPnL}
            />
            <KPICard
              label="Open Positions"
              value={String(openCount)}
              sub={
                openCount === 0
                  ? 'no live exposure'
                  : `${fmtINR(openExposure)} exposure${allPaper ? ' · paper' : ''}`
              }
              accent="#60a5fa"
              spark={sparkPositions}
            />
            <KPICard
              label="Watchlist Breadth"
              value={watchlist.length > 0 ? `${breadth.gainers} / ${breadth.losers}` : '—'}
              sub={
                watchlist.length > 0
                  ? `${breadth.gainers} advancing · ${breadth.losers} declining`
                  : 'awaiting market data'
              }
              accent="#a78bfa"
              spark={sparkBreadth}
            />
          </section>

          {/* Chart */}
          <section className="glass rounded-2xl shadow-card overflow-hidden gpu-layer">
            <div className="flex flex-wrap items-end justify-between gap-4 px-5 pt-5">
              <div className="min-w-0">
                <div className="flex items-center gap-3 flex-wrap">
                  <h2 className="text-lg font-semibold text-white tracking-tight truncate">{chartQuote?.name || selected}</h2>
                  <span className="text-xs text-white/40">{chartQuote?.exchange}</span>
                </div>
                <div className="mt-1 flex items-baseline gap-3 tabular flex-wrap">
                  <span className="text-2xl sm:text-3xl font-semibold text-white">{fmtNum(chartQuote?.current_price)}</span>
                  <span className={`text-sm font-medium ${chartQuote?.change >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                    {chartQuote ? (chartQuote.change >= 0 ? '▲' : '▼') : ''} {fmtNum(chartQuote?.change)} ({fmtPct(chartQuote?.change_pct)})
                  </span>
                  <span className="text-xs text-white/40">intraday · 5m</span>
                  <SourceBadge source={chartSource} />
                </div>
              </div>
              <div className="flex gap-1.5 flex-wrap">
                {['RELIANCE','INFY','HDFCBANK','TCS','NIFTY','AAPL','NVDA'].map(s => (
                  <button key={s} onClick={() => setSelected(s)}
                    className={`text-xs px-3 py-1.5 rounded-lg border transition tabular ${
                      selected === s
                        ? 'bg-gold-500 text-ink-900 border-gold-500'
                        : 'bg-white/[0.03] text-white/65 border-white/10 hover:bg-white/[0.08]'
                    }`}>
                    {s}
                  </button>
                ))}
              </div>
            </div>
            <div className="px-2 pb-4 pt-2">
              <PriceChart data={chartData} color={chartColor} height={260} />
            </div>
          </section>

          {/* Recommendations */}
          <section>
            <div className="flex items-center justify-between mb-3 px-1 gap-3 flex-wrap">
              <div className="min-w-0">
                <h2 className="text-lg font-semibold text-white tracking-tight">AI Recommendations</h2>
                <div className="text-xs text-white/50 truncate">
                  Backtested on the chosen horizon · direction, targets &amp; validity scale to the period
                </div>
              </div>
              <div className="flex items-center gap-2 shrink-0 flex-wrap">
                {/* Investment-horizon selector — backtest + levels match the period */}
                <div className="flex rounded-lg border border-white/10 overflow-hidden">
                  {(horizons.length ? horizons : [{ key: '1M', label: '1M' }, { key: '3M', label: '3M' }, { key: '6M', label: '6M' }, { key: '1Y', label: '1Y' }, { key: 'SW', label: 'Swing' }]).map((h) => (
                    <button key={h.key} onClick={() => setHorizon(h.key)} disabled={forceBusy}
                      title={`${h.label} investment view`}
                      className={`px-2.5 py-1.5 text-xs transition disabled:opacity-50 ${horizon === h.key ? 'bg-gold-500/20 text-gold-200' : 'text-white/55 hover:bg-white/5'}`}>
                      {h.key}
                    </button>
                  ))}
                </div>
                <button onClick={refreshRecs} disabled={forceBusy} title="Re-fetch the current list"
                  className="text-xs px-3 py-1.5 rounded-lg border border-white/10 bg-white/[0.03] hover:bg-white/[0.08] disabled:opacity-50 transition">
                  Reload
                </button>
                <button onClick={forceRefreshRecs} disabled={forceBusy} title="Force the AI to regenerate signals for every symbol now"
                  className="text-xs px-3 py-1.5 rounded-lg font-semibold text-ink-900 bg-gold-500 hover:bg-gold-400 shadow-glow disabled:opacity-50 transition">
                  {forceBusy ? 'Regenerating…' : 'Regenerate'}
                </button>
              </div>
            </div>
            <div className="space-y-3">
              {recs.length === 0 && (
                <div className="glass rounded-2xl px-6 py-10 text-center">
                  <div className="text-white/55 text-sm">No active recommendations.</div>
                  <div className="text-white/35 text-xs mt-1">The desk is monitoring the watchlist; signals will appear here as they qualify.</div>
                </div>
              )}
              {recs.map((rec, i) => (
                <RecCard key={rec.id || i} rec={rec} onApprove={approve} onReject={reject} busy={busyId === rec.id} />
              ))}
            </div>
          </section>

          {/* Historical data store */}
          <DataStorePanel />

          {/* Order history */}
          <section>
            <div className="flex items-center justify-between mb-3 px-1 gap-3">
              <div>
                <h2 className="text-lg font-semibold text-white tracking-tight">Order history</h2>
                <div className="text-xs text-white/50">Real orders placed via connected broker(s)</div>
              </div>
              <button onClick={refreshHistory}
                className="text-xs px-3 py-1.5 rounded-lg border border-white/10 bg-white/[0.03] hover:bg-white/[0.08] transition shrink-0">
                Refresh
              </button>
            </div>
            <OrderHistory trades={trades} />
          </section>
        </div>

        {/* RIGHT: chat (drawer on mobile, fixed column on xl) */}
        <ChatPanel onSelectSymbol={(s) => setSelected(s.toUpperCase())} open={chatOpen} onClose={() => setChatOpen(false)} />
      </main>

      {confirmRec && (
        <OrderConfirmModal rec={confirmRec} onClose={() => setConfirmRec(null)} onPlaced={onOrderPlaced} />
      )}

      {orderToast && (
        <div className={`fixed bottom-5 right-5 z-50 max-w-sm rounded-xl px-4 py-3 shadow-card fade-in border ${
          orderToast.kind === 'live'
            ? 'bg-rose-500/15 border-rose-400/40 text-rose-100'
            : 'bg-blue-500/15 border-blue-400/40 text-blue-100'
        }`}>
          <div className="text-[10px] uppercase tracking-wider font-semibold mb-0.5">
            {orderToast.kind === 'live' ? 'LIVE ORDER' : 'SIMULATED'}
          </div>
          <div className="text-sm">{orderToast.msg}</div>
        </div>
      )}

      <footer className="max-w-[1600px] mx-auto px-4 sm:px-6 py-6 text-[11px] text-white/30 flex justify-between items-center gap-3">
        <span className="truncate">Helios Capital · AI-assisted recommendations. Human approval required before execution.</span>
        <span className="tabular shrink-0">v0.2.0</span>
      </footer>
    </div>
  );
}
