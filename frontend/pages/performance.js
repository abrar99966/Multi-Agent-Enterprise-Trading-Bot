import React, { memo, useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';

const API = (typeof window !== 'undefined' && window.__API__) || 'http://127.0.0.1:8000';

const fmtPct = (n, signed = false) => {
  if (n == null || Number.isNaN(n)) return '—';
  const sign = signed && n > 0 ? '+' : '';
  return `${sign}${Number(n).toFixed(2)}%`;
};
const fmtINR = (n) => {
  if (n == null || Number.isNaN(n)) return '—';
  return '₹' + Number(n).toLocaleString('en-IN', { maximumFractionDigits: 0 });
};
const fmtTs = (iso) => (iso ? new Date(iso).toLocaleString() : '—');

const Tag = ({ children, tone = 'neutral' }) => {
  const cls = {
    neutral: 'bg-white/[0.04] border-white/10 text-white/70',
    pos: 'bg-emerald-400/10 border-emerald-400/30 text-emerald-300',
    neg: 'bg-rose-400/10 border-rose-400/30 text-rose-300',
    info: 'bg-sky-400/10 border-sky-400/30 text-sky-200',
    warn: 'bg-amber-400/10 border-amber-400/30 text-amber-200',
  }[tone];
  return <span className={`text-[10px] uppercase tracking-wider px-2 py-0.5 rounded-full border ${cls}`}>{children}</span>;
};

const Metric = ({ label, value, sub, tone = 'neutral' }) => (
  <div className={`rounded-xl border px-4 py-3 ${
    tone === 'pos' ? 'border-emerald-400/30 bg-emerald-400/5' :
    tone === 'neg' ? 'border-rose-400/30 bg-rose-400/5' :
    tone === 'warn' ? 'border-amber-400/30 bg-amber-400/5' :
    'border-white/10 bg-white/[0.02]'
  }`}>
    <div className="text-[10px] uppercase tracking-wider text-white/45">{label}</div>
    <div className={`mt-0.5 text-2xl font-semibold tabular ${
      tone === 'pos' ? 'text-emerald-300' :
      tone === 'neg' ? 'text-rose-300' :
      tone === 'warn' ? 'text-amber-200' : 'text-white'
    }`}>{value}</div>
    {sub && <div className="mt-0.5 text-[11px] text-white/55 tabular">{sub}</div>}
  </div>
);

const RecentRow = memo(function RecentRow({ r }) {
  const correct = r.correct_1h;
  const moveTone = correct === true ? 'pos' : correct === false ? 'neg' : 'neutral';
  return (
    <tr className="border-t border-white/5 hover:bg-white/[0.02]">
      <td className="px-4 py-2.5 text-white font-medium">{r.symbol}</td>
      <td className="px-4 py-2.5">
        <span className={`text-[10px] px-2 py-0.5 rounded-md font-bold uppercase tracking-wider ${
          r.side === 'buy' ? 'bg-emerald-500/15 text-emerald-300' : 'bg-rose-500/15 text-rose-300'
        }`}>{r.side}</span>
      </td>
      <td className="px-4 py-2.5 tabular text-white/80">{fmtINR(r.entry_price * 1)}</td>
      <td className="px-4 py-2.5 tabular text-white/80">{r.price_after_1h != null ? fmtINR(r.price_after_1h) : '—'}</td>
      <td className="px-4 py-2.5">
        <Tag tone={moveTone}>{fmtPct(r.actual_move_pct_1h, true)}</Tag>
      </td>
      <td className="px-4 py-2.5">
        <Tag tone={correct === true ? 'pos' : correct === false ? 'neg' : 'neutral'}>
          {correct === true ? '✓ correct' : correct === false ? '✗ wrong' : 'pending'}
        </Tag>
      </td>
      <td className="px-4 py-2.5 text-xs text-white/45">{fmtTs(r.created_at)}</td>
    </tr>
  );
});

export default function PerformancePage() {
  const [stats, setStats] = useState(null);
  const [limits, setLimits] = useState(null);
  const [calib, setCalib] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [windowDays, setWindowDays] = useState(7);

  // Editable risk-limit form fields
  const [perTrade, setPerTrade] = useState('');
  const [dailyLoss, setDailyLoss] = useState('');
  const [dailyTrades, setDailyTrades] = useState('');

  const load = useCallback(async () => {
    try {
      const [s, l, cal] = await Promise.all([
        fetch(`${API}/api/v1/performance/stats?days=${windowDays}`).then((r) => r.json()),
        fetch(`${API}/api/v1/risk/limits`).then((r) => r.json()),
        fetch(`${API}/api/v1/performance/calibration`).then((r) => (r.ok ? r.json() : null)).catch(() => null),
      ]);
      setStats(s);
      setLimits(l);
      setCalib(cal);
      setPerTrade(String(l.per_trade_max_inr));
      setDailyLoss(String(l.daily_max_loss_inr));
      setDailyTrades(String(l.daily_max_trades));
    } catch (e) { setError(e.message); }
  }, [windowDays]);

  useEffect(() => { load(); }, [load]);
  // Poll every 30s so the page stays live
  useEffect(() => {
    const t = setInterval(load, 30_000);
    return () => clearInterval(t);
  }, [load]);

  const gradeNow = async () => {
    setBusy(true); setError(null);
    try {
      await fetch(`${API}/api/v1/performance/grade-now`, { method: 'POST' });
      await load();
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };

  const saveLimits = async () => {
    setBusy(true); setError(null);
    try {
      const r = await fetch(`${API}/api/v1/risk/limits`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          per_trade_max_inr: Number(perTrade) || 0,
          daily_max_loss_inr: Number(dailyLoss) || 0,
          daily_max_trades: Number(dailyTrades) || 0,
        }),
      });
      const body = await r.json();
      if (!r.ok) throw new Error(body.detail || 'Update failed');
      setLimits(body);
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };

  const toggleKill = async () => {
    setBusy(true); setError(null);
    try {
      const path = limits?.kill_switch ? '/api/v1/risk/resume' : '/api/v1/risk/kill';
      const r = await fetch(`${API}${path}`, { method: 'POST' });
      const body = await r.json();
      if (!r.ok) throw new Error(body.detail || 'Toggle failed');
      setLimits(body);
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };

  // ---- Pre-live readiness checklist (computed from real data) ----
  const checklist = useMemo(() => {
    const graded = stats?.graded_count || 0;
    const hitRate = stats?.hit_rate_1h;
    const exp = stats?.expectancy_1h;
    return [
      { key: 'tracked', label: 'Outcome-tracked ≥ 100 signals', ok: graded >= 100, current: `${graded} graded` },
      { key: 'hit', label: 'Hit rate ≥ 55% on 1h window', ok: hitRate != null && hitRate >= 0.55, current: hitRate != null ? `${(hitRate*100).toFixed(1)}%` : 'no data' },
      { key: 'exp', label: 'Positive expectancy per signal', ok: exp != null && exp > 0, current: exp != null ? `${exp.toFixed(2)}%/signal` : 'no data' },
      { key: 'limits_set', label: 'Per-trade cap configured', ok: limits != null && limits.per_trade_max_inr > 0, current: limits ? fmtINR(limits.per_trade_max_inr) : '—' },
      { key: 'loss_set', label: 'Daily loss cap configured', ok: limits != null && limits.daily_max_loss_inr > 0, current: limits ? fmtINR(limits.daily_max_loss_inr) : '—' },
      { key: 'kill_off', label: 'Kill switch disengaged', ok: limits != null && !limits.kill_switch, current: limits?.kill_switch ? 'ENGAGED' : 'off' },
    ];
  }, [stats, limits]);

  const readyForLive = checklist.every((c) => c.ok);

  const hitRateTone = (h) => h == null ? 'neutral' : (h >= 0.55 ? 'pos' : h >= 0.48 ? 'warn' : 'neg');
  const expTone = (e) => e == null ? 'neutral' : (e > 0 ? 'pos' : 'neg');

  return (
    <div className="min-h-screen text-white">
      <header className="sticky top-0 z-30 glass-blur">
        <div className="max-w-[1400px] mx-auto px-4 sm:px-6 py-3 flex items-center gap-4 sm:gap-6">
          <Link href="/" className="flex items-center gap-3 group min-w-0">
            <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-gold-400 to-gold-600 flex items-center justify-center shadow-glow shrink-0">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#0a0e1a" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M3 17l6-6 4 4 8-8" /><path d="M14 7h7v7" />
              </svg>
            </div>
            <div className="min-w-0">
              <div className="text-sm font-semibold tracking-tight text-white group-hover:text-gold-300 transition truncate">Helios Capital</div>
              <div className="text-[10px] uppercase tracking-[0.18em] text-white/45 truncate">AI Trading Desk</div>
            </div>
          </Link>
          <nav className="hidden sm:flex items-center gap-1 text-xs">
            <Link href="/" className="px-3 py-1.5 rounded-lg text-white/60 hover:text-white hover:bg-white/5 transition">Dashboard</Link>
            <Link href="/brokers" className="px-3 py-1.5 rounded-lg text-white/60 hover:text-white hover:bg-white/5 transition">Brokers</Link>
            <Link href="/training" className="px-3 py-1.5 rounded-lg text-white/60 hover:text-white hover:bg-white/5 transition">Training</Link>
            <Link href="/screener" className="px-3 py-1.5 rounded-lg text-white/60 hover:text-white hover:bg-white/5 transition">Screener</Link>
            <span className="px-3 py-1.5 rounded-lg text-white bg-white/5 border border-white/10">Performance</span>
            <Link href="/monitor" className="px-3 py-1.5 rounded-lg text-white/60 hover:text-white hover:bg-white/5 transition">Monitor</Link>
          </nav>
          <div className="ml-auto flex items-center gap-2">
            <button onClick={toggleKill} disabled={busy}
              className={`text-xs font-semibold px-3 py-1.5 rounded-lg transition disabled:opacity-50 ${
                limits?.kill_switch
                  ? 'bg-emerald-500 hover:bg-emerald-400 text-white shadow-glow'
                  : 'bg-rose-500 hover:bg-rose-400 text-white shadow-glow'
              }`}>
              {limits?.kill_switch ? '▶ Resume Live Trading' : '⏹ KILL SWITCH'}
            </button>
          </div>
        </div>
      </header>

      <main className="max-w-[1400px] mx-auto px-4 sm:px-6 py-8 space-y-8">
        {/* Honesty banner */}
        <section className="rounded-2xl border border-amber-400/30 bg-amber-400/5 px-5 py-4">
          <div className="text-sm text-amber-100">
            <span className="font-semibold">Read this carefully if you're considering live trading.</span>{' '}
            The numbers below are <em>actual</em> signal accuracy measured against the next 1h/24h market move — not backtest fantasy.
            They start meaningless (zero samples) and only become trustworthy past ~100 graded signals. Anything under 100 is noise.
            <strong className="block mt-2 text-amber-200">
              Do not enable live trading until every checklist item below is green AND you have personally reviewed at least 20 recent recommendations and would have made similar calls.
            </strong>
          </div>
        </section>

        {/* Headline metrics */}
        <section>
          <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
            <h1 className="text-xl font-semibold text-white">Signal accuracy</h1>
            <div className="flex items-center gap-2">
              <select value={windowDays} onChange={(e) => setWindowDays(Number(e.target.value))}
                className="text-xs bg-white/[0.04] border border-white/10 rounded-lg px-3 py-1.5 text-white outline-none">
                <option value={1}>Last 24h</option>
                <option value={3}>Last 3 days</option>
                <option value={7}>Last 7 days</option>
                <option value={30}>Last 30 days</option>
                <option value={90}>Last 90 days</option>
              </select>
              <button onClick={gradeNow} disabled={busy}
                className="text-xs px-3 py-1.5 rounded-lg border border-white/10 bg-white/[0.03] hover:bg-white/[0.08] disabled:opacity-50 transition">
                Re-grade now
              </button>
            </div>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <Metric label="Graded signals" value={stats?.graded_count ?? '—'} sub={stats?.message ? 'awaiting data' : `${windowDays}-day window`} />
            <Metric label="Hit rate (1h)" value={stats?.hit_rate_1h != null ? fmtPct(stats.hit_rate_1h * 100) : '—'} sub="direction matched" tone={hitRateTone(stats?.hit_rate_1h)} />
            <Metric label="Expectancy / signal" value={stats?.expectancy_1h != null ? fmtPct(stats.expectancy_1h, true) : '—'} sub="(p_win × avg_win) − (p_loss × avg_loss)" tone={expTone(stats?.expectancy_1h)} />
            <Metric label="Avg correct move" value={stats?.avg_correct_move_pct_1h != null ? fmtPct(stats.avg_correct_move_pct_1h) : '—'} sub={`avg wrong: ${fmtPct(stats?.avg_wrong_move_pct_1h)}`} />
          </div>
          {stats?.message && (
            <div className="mt-3 text-xs text-white/55">{stats.message}</div>
          )}
        </section>

        {/* Pre-live readiness checklist */}
        <section className="glass rounded-2xl p-5 shadow-card">
          <div className="flex items-center justify-between flex-wrap gap-2 mb-3">
            <h2 className="text-lg font-semibold text-white">Pre-live readiness checklist</h2>
            <Tag tone={readyForLive ? 'pos' : 'warn'}>
              {readyForLive ? '✓ Ready for live (review one more time)' : `${checklist.filter((c) => c.ok).length}/${checklist.length} checks passing`}
            </Tag>
          </div>
          <ul className="space-y-1.5">
            {checklist.map((c) => (
              <li key={c.key} className="flex items-center justify-between text-sm py-1.5 px-2 rounded">
                <span className="flex items-center gap-2">
                  <span className={`w-4 h-4 rounded-full flex items-center justify-center text-[10px] font-bold ${
                    c.ok ? 'bg-emerald-500 text-white' : 'bg-white/10 text-white/50'
                  }`}>{c.ok ? '✓' : '·'}</span>
                  <span className={c.ok ? 'text-white' : 'text-white/70'}>{c.label}</span>
                </span>
                <span className="text-xs text-white/45 tabular">{c.current}</span>
              </li>
            ))}
          </ul>
          {!readyForLive && (
            <div className="mt-3 text-xs text-white/55 leading-relaxed">
              Each unticked item is a real reason to stay in paper mode. Skipping these is the most common way retail algo traders blow up accounts.
            </div>
          )}
        </section>

        {/* Risk limits — editable */}
        <section className="glass rounded-2xl p-5 shadow-card">
          <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
            <h2 className="text-lg font-semibold text-white">Live-trading risk limits</h2>
            <div className="text-xs text-white/55 tabular">
              Today: {limits?.today_trade_count ?? 0}/{limits?.daily_max_trades ?? 0} trades · P&L {fmtINR(limits?.today_realized_pnl_inr)} · buffer {fmtINR(limits?.today_remaining_loss_buffer_inr)}
            </div>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-sm">
            <label className="block">
              <span className="block text-[11px] uppercase tracking-wider text-white/55 mb-1">Per-trade max (₹)</span>
              <input type="number" value={perTrade} onChange={(e) => setPerTrade(e.target.value)} disabled={busy}
                className="w-full bg-white/[0.04] border border-white/10 rounded-lg px-3 py-2 text-sm text-white outline-none font-mono tabular focus:border-gold-500/60" />
              <span className="block text-[11px] text-white/40 mt-1">Single position cannot exceed this rupee value</span>
            </label>
            <label className="block">
              <span className="block text-[11px] uppercase tracking-wider text-white/55 mb-1">Daily loss cap (₹)</span>
              <input type="number" value={dailyLoss} onChange={(e) => setDailyLoss(e.target.value)} disabled={busy}
                className="w-full bg-white/[0.04] border border-white/10 rounded-lg px-3 py-2 text-sm text-white outline-none font-mono tabular focus:border-gold-500/60" />
              <span className="block text-[11px] text-white/40 mt-1">Live trading halts for the day if loss reaches this</span>
            </label>
            <label className="block">
              <span className="block text-[11px] uppercase tracking-wider text-white/55 mb-1">Daily trade count cap</span>
              <input type="number" value={dailyTrades} onChange={(e) => setDailyTrades(e.target.value)} disabled={busy}
                className="w-full bg-white/[0.04] border border-white/10 rounded-lg px-3 py-2 text-sm text-white outline-none font-mono tabular focus:border-gold-500/60" />
              <span className="block text-[11px] text-white/40 mt-1">Max live orders per IST day (resets 06:00 IST)</span>
            </label>
          </div>
          <div className="mt-4 flex justify-end">
            <button onClick={saveLimits} disabled={busy}
              className="px-4 py-2 rounded-lg text-sm font-semibold text-ink-900 bg-gold-500 hover:bg-gold-400 disabled:opacity-50 transition">
              Save limits
            </button>
          </div>
          {limits?.kill_switch && (
            <div className="mt-3 rounded-lg bg-rose-500/10 border border-rose-500/30 px-3 py-2 text-sm text-rose-200">
              ⏹ Kill switch is ENGAGED — all live orders are blocked. Paper trades still go through.
            </div>
          )}
        </section>

        {/* Per-horizon accuracy — closed-loop, fills in as recs mature */}
        <section className="glass rounded-2xl shadow-card overflow-hidden">
          <div className="px-5 py-3 border-b border-white/5 flex items-center justify-between gap-3 flex-wrap">
            <div className="text-sm font-semibold text-white">Per-horizon accuracy (closed-loop)</div>
            <div className="text-[11px] text-white/45">Real hit rate of matured 1M/3M/6M/1Y calls — backtest expects ~OOS win rate</div>
          </div>
          {stats?.by_horizon && Object.keys(stats.by_horizon).length > 0 ? (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-[10px] uppercase tracking-wider text-white/45">
                  <tr>
                    <th className="text-left px-4 py-2">Horizon</th>
                    <th className="text-left px-4 py-2">Graded</th>
                    <th className="text-left px-4 py-2">Hit rate</th>
                    <th className="text-left px-4 py-2">Avg move</th>
                  </tr>
                </thead>
                <tbody>
                  {['1M', '3M', '6M', '1Y', 'SW'].filter((h) => stats.by_horizon[h]).map((h) => {
                    const v = stats.by_horizon[h];
                    return (
                      <tr key={h} className="border-t border-white/5 hover:bg-white/[0.02]">
                        <td className="px-4 py-2.5 text-white font-medium">{h}</td>
                        <td className="px-4 py-2.5 tabular text-white/80">{v.graded}</td>
                        <td className="px-4 py-2.5">{v.hit_rate != null ? <Tag tone={hitRateTone(v.hit_rate)}>{fmtPct(v.hit_rate * 100)}</Tag> : <span className="text-white/40">—</span>}</td>
                        <td className="px-4 py-2.5 tabular text-white/80">{v.avg_move_pct != null ? fmtPct(v.avg_move_pct, true) : '—'}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="px-5 py-8 text-center text-sm text-white/50">
              No horizon calls have matured yet. Each 1M/3M/6M/1Y recommendation is graded automatically once its
              window passes — real accuracy will appear here over time.
              <div className="text-[11px] text-white/35 mt-1">Until then, each card shows its <span className="text-gold-300">walk-forward</span> (out-of-sample) backtest win rate.</div>
            </div>
          )}
        </section>

        {/* Confidence calibration — does stated confidence match realized hit rate? */}
        <section className="glass rounded-2xl shadow-card overflow-hidden">
          <div className="px-5 py-3 border-b border-white/5 flex items-center justify-between gap-3 flex-wrap">
            <div className="text-sm font-semibold text-white">Confidence calibration</div>
            <div className="text-[11px] text-white/45">
              {calib?.samples ? `${calib.samples} graded · base rate ${calib.base_rate != null ? fmtPct(calib.base_rate * 100) : '—'}` : 'awaiting graded outcomes'}
            </div>
          </div>
          {calib?.table ? (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-[10px] uppercase tracking-wider text-white/45">
                  <tr>
                    <th className="text-left px-4 py-2">Stated confidence</th>
                    <th className="text-left px-4 py-2">Calls</th>
                    <th className="text-left px-4 py-2">Observed hit rate</th>
                    <th className="text-left px-4 py-2">Calibrated (used)</th>
                  </tr>
                </thead>
                <tbody>
                  {calib.table.filter((b) => b.n > 0).length === 0 ? (
                    <tr><td colSpan={4} className="px-4 py-6 text-center text-white/45 text-sm">
                      No calls have been graded yet — confidence is shown raw until outcomes mature, then auto-calibrates here.
                    </td></tr>
                  ) : calib.table.filter((b) => b.n > 0).map((b) => (
                    <tr key={b.bucket} className="border-t border-white/5 hover:bg-white/[0.02]">
                      <td className="px-4 py-2.5 text-white/85 tabular">{b.bucket}</td>
                      <td className="px-4 py-2.5 tabular text-white/70">{b.n}</td>
                      <td className="px-4 py-2.5"><Tag tone={hitRateTone(b.observed)}>{b.observed != null ? fmtPct(b.observed * 100) : '—'}</Tag></td>
                      <td className="px-4 py-2.5 tabular text-gold-200">{fmtPct(b.realized * 100)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="px-4 py-2.5 text-[11px] text-white/40 border-t border-white/5">
                Each rec's confidence is mapped to its bucket's realized rate (shrunk toward identity until ~{Math.round(calib.shrink)} samples) — this calibrated number drives display + position sizing.
              </div>
            </div>
          ) : (
            <div className="px-5 py-8 text-center text-sm text-white/50">Calibration unavailable.</div>
          )}
        </section>

        {/* Per-symbol breakdown */}
        {stats?.per_symbol && Object.keys(stats.per_symbol).length > 0 && (
          <section className="glass rounded-2xl shadow-card overflow-hidden">
            <div className="px-5 py-3 border-b border-white/5">
              <div className="text-sm font-semibold text-white">Per-symbol accuracy</div>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-[10px] uppercase tracking-wider text-white/45">
                  <tr>
                    <th className="text-left px-4 py-2">Symbol</th>
                    <th className="text-left px-4 py-2">Signals</th>
                    <th className="text-left px-4 py-2">Hit rate (1h)</th>
                    <th className="text-left px-4 py-2">Avg move</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(stats.per_symbol).map(([sym, v]) => (
                    <tr key={sym} className="border-t border-white/5 hover:bg-white/[0.02]">
                      <td className="px-4 py-2.5 text-white font-medium">{sym}</td>
                      <td className="px-4 py-2.5 tabular text-white/80">{v.total}</td>
                      <td className="px-4 py-2.5"><Tag tone={hitRateTone(v.hit_rate_1h)}>{fmtPct(v.hit_rate_1h * 100)}</Tag></td>
                      <td className="px-4 py-2.5 tabular text-white/80">{fmtPct(v.avg_move_pct, true)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}

        {/* Recent graded signals */}
        {stats?.recent && stats.recent.length > 0 && (
          <section className="glass rounded-2xl shadow-card overflow-hidden">
            <div className="px-5 py-3 border-b border-white/5">
              <div className="text-sm font-semibold text-white">Recent graded signals (last 20)</div>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-[10px] uppercase tracking-wider text-white/45">
                  <tr>
                    <th className="text-left px-4 py-2">Symbol</th>
                    <th className="text-left px-4 py-2">Side</th>
                    <th className="text-left px-4 py-2">Entry</th>
                    <th className="text-left px-4 py-2">+1h price</th>
                    <th className="text-left px-4 py-2">Actual move</th>
                    <th className="text-left px-4 py-2">Verdict</th>
                    <th className="text-left px-4 py-2">When</th>
                  </tr>
                </thead>
                <tbody>
                  {stats.recent.map((r) => <RecentRow key={r.id} r={r} />)}
                </tbody>
              </table>
            </div>
          </section>
        )}

        {error && (
          <div className="rounded-lg bg-rose-500/10 border border-rose-500/30 px-3 py-2 text-sm text-rose-200">
            {error}
          </div>
        )}
      </main>
    </div>
  );
}
