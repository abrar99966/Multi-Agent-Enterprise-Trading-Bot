import React, { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';

const API = (typeof window !== 'undefined' && window.__API__) || 'http://127.0.0.1:8000';

const fmtPct = (n, signed = false) => {
  if (n == null || Number.isNaN(n)) return '—';
  const sign = signed && n > 0 ? '+' : '';
  return `${sign}${Number(n).toFixed(2)}%`;
};
const fmtNum = (n, d = 2) => (n == null || Number.isNaN(n) ? '—' : Number(n).toFixed(d));
const fmtTs = (iso) => (iso ? new Date(iso).toLocaleString() : '—');

// Short labels for the strategies that compete in the tournament.
const STRAT_LABEL = {
  rsi_sma: 'RSI/SMA',
  ema_cross: 'EMA×',
  macd: 'MACD',
  bollinger: 'Bollinger',
  supertrend: 'Supertrend',
  breakout: 'Breakout',
  volume_breakout: 'Vol breakout',
  golden_cross: 'Golden cross',
  engulfing: 'Engulfing',
};
const stratLabel = (k) => STRAT_LABEL[k] || k || '—';

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

const SymbolRow = memo(function SymbolRow({ row }) {
  const improve = row.improvement_pp;
  const tone = improve > 1 ? 'pos' : improve < -1 ? 'neg' : 'neutral';
  return (
    <tr className="border-t border-white/5 hover:bg-white/[0.02]">
      <td className="px-4 py-3 text-white font-semibold">{row.symbol}</td>
      <td className="px-4 py-3"><Tag tone="info">{stratLabel(row.best_strategy)}</Tag></td>
      <td className="px-4 py-3 tabular text-white/80">{fmtPct(row.baseline_win_rate * 100)}</td>
      <td className="px-4 py-3 tabular text-white font-medium">{fmtPct(row.best_win_rate * 100)}</td>
      <td className="px-4 py-3"><Tag tone={tone}>{improve > 0 ? '+' : ''}{fmtNum(improve, 1)} pp</Tag></td>
      <td className="px-4 py-3 tabular text-white/80">{fmtNum(row.best_sharpe)}</td>
      <td className="px-4 py-3 tabular text-white/80">{fmtPct(row.best_return_pct, true)}</td>
      <td className="px-4 py-3 tabular text-white/55">{row.n_trades}</td>
    </tr>
  );
});

export default function TrainingPage() {
  const [status, setStatus] = useState(null);
  const [results, setResults] = useState(null);
  const [universes, setUniverses] = useState([]);
  const [error, setError] = useState(null);

  const [preset, setPreset] = useState('indexes_plus_nifty50');
  const [customSymbols, setCustomSymbols] = useState('');
  const [intervalStr, setIntervalStr] = useState('30minute');
  const [lookbackDays, setLookbackDays] = useState(90);
  const [maxSymbols, setMaxSymbols] = useState(200);

  const pollRef = useRef(null);

  // Initial load — universes + status + results
  useEffect(() => {
    let cancelled = false;
    Promise.all([
      fetch(`${API}/api/v1/learning/universes`).then((r) => r.json()),
      fetch(`${API}/api/v1/learning/status`).then((r) => r.json()),
    ]).then(([u, s]) => {
      if (cancelled) return;
      setUniverses(u.universes || []);
      setStatus(s);
      if (s?.tuned_on_disk) {
        fetch(`${API}/api/v1/learning/results`).then((r) => r.ok && r.json()).then((rr) => !cancelled && rr && setResults(rr));
      }
    }).catch(() => {});
    return () => { cancelled = true; };
  }, []);

  // Live progress polling — only while a training job is running
  const running = status?.running;
  useEffect(() => {
    if (!running) {
      if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
      return;
    }
    const tick = async () => {
      try {
        const s = await fetch(`${API}/api/v1/learning/status`).then((r) => r.json());
        setStatus(s);
        if (!s.running && s.tuned_on_disk) {
          const r = await fetch(`${API}/api/v1/learning/results`);
          if (r.ok) setResults(await r.json());
        }
      } catch {}
    };
    pollRef.current = setInterval(tick, 2000);
    return () => { if (pollRef.current) clearInterval(pollRef.current); pollRef.current = null; };
  }, [running]);

  const runTraining = async () => {
    setError(null);
    try {
      const body = {
        interval: intervalStr,
        lookback_days: Number(lookbackDays),
      };
      if (preset === 'custom') {
        body.preset = 'custom';
        body.symbols = customSymbols.split(',').map((s) => s.trim()).filter(Boolean);
      } else {
        body.preset = preset;
      }
      // Cap the universe size for the whole-market (dynamic) preset.
      if (selectedUniverse?.dynamic && Number(maxSymbols) > 0) {
        body.max_symbols = Number(maxSymbols);
      }
      const r = await fetch(`${API}/api/v1/learning/train`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const respBody = await r.json();
      if (!r.ok) throw new Error(respBody.detail || 'Training failed to start');
      // Immediately refresh status so the progress bar appears
      const s = await fetch(`${API}/api/v1/learning/status`).then((rr) => rr.json());
      setStatus(s);
    } catch (e) {
      setError(e.message);
    }
  };

  // Build a sortable rows list from results
  const rows = useMemo(() => {
    if (!results?.last_run?.per_symbol_metrics) return [];
    const m = results.last_run.per_symbol_metrics;
    return Object.entries(m).map(([sym, data]) => ({
      symbol: sym,
      best_strategy: data.best_strategy || data.best?.strategy,
      best_win_rate: data.best.win_rate,
      baseline_win_rate: data.baseline.win_rate,
      improvement_pp: data.improvement_pp,
      best_sharpe: data.best.sharpe,
      best_return_pct: data.best.total_return_pct,
      n_trades: data.best.n_trades,
      best_params: data.best.params,
      max_dd: data.best.max_drawdown_pct,
    })).sort((a, b) => b.improvement_pp - a.improvement_pp);
  }, [results]);

  const persisted = results?.persisted;
  const progress = status?.state?.progress;
  const selectedUniverse = universes.find((u) => u.key === preset);

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
            <span className="px-3 py-1.5 rounded-lg text-white bg-white/5 border border-white/10">Training</span>
            <Link href="/screener" className="px-3 py-1.5 rounded-lg text-white/60 hover:text-white hover:bg-white/5 transition">Screener</Link>
            <Link href="/monitor" className="px-3 py-1.5 rounded-lg text-white/60 hover:text-white hover:bg-white/5 transition">Monitor</Link>
          </nav>
          <div className="ml-auto text-xs text-white/55 tabular">
            {persisted ? `Last train: ${fmtTs(persisted.trained_at)}` : 'Never trained'}
          </div>
        </div>
      </header>

      <main className="max-w-[1400px] mx-auto px-4 sm:px-6 py-8 space-y-8">
        {/* Honesty banner */}
        <section className="rounded-2xl border border-amber-400/30 bg-amber-400/5 px-5 py-4">
          <div className="text-sm text-amber-100">
            <span className="font-semibold">Read this before believing the numbers.</span>{' '}
            Backtest results are <em>NOT</em> a guarantee of live performance. Real trading is degraded by
            slippage, latency, and unmodelled order-book impact. A 60% backtested win-rate often becomes
            ~52% live. Treat tuned params as a sensible starting point, not a magic improvement.
          </div>
        </section>

        {/* Controls */}
        <section className="glass rounded-2xl p-5 shadow-card space-y-4">
          <div className="flex items-end justify-between gap-3 flex-wrap">
            <div>
              <h1 className="text-xl font-semibold text-white">Train the agent</h1>
              <p className="text-sm text-white/55 mt-1">
                Pulls historical bars from Upstox if connected, otherwise falls back to Yahoo Finance.
                Runs a 6-strategy tournament per symbol and keeps each one's winner. Live <code className="text-gold-300">TechnicalAgent</code> reloads immediately on completion.
              </p>
            </div>
            <button onClick={runTraining} disabled={running}
              className="px-4 py-2 rounded-lg text-sm font-semibold text-ink-900 bg-gold-500 hover:bg-gold-400 shadow-glow disabled:opacity-50 transition">
              {running ? `Training… (${progress?.done || 0}/${progress?.total || 0})` : 'Train now'}
            </button>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-sm">
            <label className="block md:col-span-3">
              <span className="block text-[11px] uppercase tracking-wider text-white/55 mb-1">Symbol universe</span>
              <select value={preset} onChange={(e) => setPreset(e.target.value)} disabled={running}
                className="w-full bg-white/[0.04] border border-white/10 rounded-lg px-3 py-2 text-sm text-white outline-none focus:border-gold-500/60">
                {universes.map((u) => (
                  <option key={u.key} value={u.key}>{u.label}</option>
                ))}
                <option value="custom">Custom symbols (type below)</option>
              </select>
              {selectedUniverse && (
                <span className="block text-[11px] text-white/45 mt-1">{selectedUniverse.description}</span>
              )}
            </label>

            {preset === 'custom' && (
              <label className="block md:col-span-3">
                <span className="block text-[11px] uppercase tracking-wider text-white/55 mb-1">Custom symbols (comma-separated)</span>
                <input value={customSymbols} onChange={(e) => setCustomSymbols(e.target.value)} disabled={running}
                  placeholder="RELIANCE,INFY,TCS,..."
                  className="w-full bg-white/[0.04] border border-white/10 rounded-lg px-3 py-2 text-sm text-white outline-none focus:border-gold-500/60 font-mono" />
              </label>
            )}

            <label className="block">
              <span className="block text-[11px] uppercase tracking-wider text-white/55 mb-1">Bar interval</span>
              <select value={intervalStr} onChange={(e) => setIntervalStr(e.target.value)} disabled={running}
                className="w-full bg-white/[0.04] border border-white/10 rounded-lg px-3 py-2 text-sm text-white outline-none focus:border-gold-500/60">
                <option value="30minute">30 minute (recommended — Yahoo capped at 60 days)</option>
                <option value="day">Daily (Yahoo supports years)</option>
                <option value="week">Weekly</option>
                <option value="1minute">1 minute (last 7 days only)</option>
              </select>
            </label>
            <label className="block">
              <span className="block text-[11px] uppercase tracking-wider text-white/55 mb-1">Lookback days</span>
              <input type="number" min={14} max={730} value={lookbackDays}
                onChange={(e) => setLookbackDays(Number(e.target.value) || 90)} disabled={running}
                className="w-full bg-white/[0.04] border border-white/10 rounded-lg px-3 py-2 text-sm text-white outline-none focus:border-gold-500/60 font-mono tabular" />
            </label>

            {selectedUniverse?.dynamic && (
              <label className="block md:col-span-3">
                <span className="block text-[11px] uppercase tracking-wider text-white/55 mb-1">
                  Max symbols (cap the whole-market run — 0 / blank = all ~{selectedUniverse.count})
                </span>
                <input type="number" min={0} value={maxSymbols}
                  onChange={(e) => setMaxSymbols(Number(e.target.value) || 0)} disabled={running}
                  className="w-full bg-white/[0.04] border border-white/10 rounded-lg px-3 py-2 text-sm text-white outline-none focus:border-gold-500/60 font-mono tabular" />
                <span className="block text-[11px] text-amber-200/70 mt-1">
                  ⚠ Training the whole market is slow — bars are fetched per symbol, sequentially. ~{selectedUniverse.count} symbols on Yahoo can take 30–60+ min and may hit rate limits. Start with a cap (e.g. 200) on daily bars.
                </span>
              </label>
            )}
          </div>

          {error && (
            <div className="rounded-lg bg-rose-500/10 border border-rose-500/30 px-3 py-2 text-sm text-rose-200">
              {error}
            </div>
          )}

          {/* Progress bar — only while running */}
          {running && progress && (
            <div className="rounded-xl bg-ink-900/60 border border-sky-400/30 p-4 space-y-3 fade-in">
              <div className="flex items-baseline justify-between gap-3">
                <div className="text-sm text-white font-semibold">
                  Training {progress.done}/{progress.total} symbols ({progress.percent.toFixed(0)}%)
                </div>
                <div className="text-xs text-white/55 tabular">
                  Current: <span className="text-sky-300 font-mono">{progress.current_symbol || '—'}</span>
                </div>
              </div>
              <div className="w-full h-2 rounded-full bg-white/[0.05] overflow-hidden">
                <div className="h-full bg-gradient-to-r from-sky-400 to-emerald-400 transition-all duration-500 ease-out"
                  style={{ width: `${progress.percent}%` }} />
              </div>
              {progress.last_result && (
                <div className="text-xs text-white/55 tabular">
                  Last completed: <span className="text-white">{progress.last_result.symbol}</span> —
                  win rate <span className={progress.last_result.win_rate >= 0.5 ? 'text-emerald-300' : 'text-rose-300'}>
                    {fmtPct(progress.last_result.win_rate * 100)}
                  </span>
                  {' · '}sharpe {fmtNum(progress.last_result.sharpe)}
                  {' · '}{progress.last_result.n_trades} trades
                </div>
              )}
            </div>
          )}

          {status?.state?.last_error && !running && (
            <div className="rounded-lg bg-rose-500/10 border border-rose-500/30 px-3 py-2 text-sm text-rose-200">
              Previous training failed: {status.state.last_error}
            </div>
          )}
        </section>

        {/* Persisted summary */}
        {persisted && (
          <section className="glass rounded-2xl p-5 shadow-card">
            <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
              <h2 className="text-lg font-semibold text-white">Live params in production</h2>
              <Tag tone="info">{persisted.n_symbols} symbols tuned</Tag>
            </div>
            <div className="text-xs text-white/55">
              Trained on {fmtTs(persisted.trained_at)} · {persisted.interval} bars · {persisted.lookback_days} day lookback ·
              took {persisted.duration_seconds?.toFixed(1)}s
            </div>
            {persisted.strategy_wins && Object.keys(persisted.strategy_wins).length > 0 && (
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <span className="text-[10px] uppercase tracking-wider text-white/40">Winning strategies</span>
                {Object.entries(persisted.strategy_wins)
                  .sort((a, b) => b[1] - a[1])
                  .map(([k, n]) => (
                    <Tag key={k} tone="info">{stratLabel(k)} · {n}</Tag>
                  ))}
              </div>
            )}
            <details className="mt-3">
              <summary className="cursor-pointer text-xs text-white/55 hover:text-white">Show raw tuned_params.json</summary>
              <pre className="mt-2 text-xs bg-ink-900/60 border border-white/5 rounded-lg p-3 text-white/75 overflow-auto max-h-64 tabular">
{JSON.stringify(persisted.tuned_params, null, 2)}
              </pre>
            </details>
          </section>
        )}

        {/* Per-symbol results table */}
        {rows.length > 0 ? (
          <section className="glass rounded-2xl shadow-card overflow-hidden">
            <div className="px-5 py-3 border-b border-white/5 flex items-center justify-between">
              <div className="text-sm font-semibold text-white">Per-symbol training metrics</div>
              <span className="text-[11px] text-white/40">{rows.length} symbols · sorted by win-rate improvement</span>
            </div>
            <div className="overflow-x-auto max-h-[600px]">
              <table className="w-full text-sm">
                <thead className="text-[10px] uppercase tracking-wider text-white/45 sticky top-0 bg-ink-900/80 backdrop-blur">
                  <tr>
                    <th className="text-left px-4 py-2">Symbol</th>
                    <th className="text-left px-4 py-2">Strategy</th>
                    <th className="text-left px-4 py-2">Win rate (baseline)</th>
                    <th className="text-left px-4 py-2">Win rate (tuned)</th>
                    <th className="text-left px-4 py-2">Improvement</th>
                    <th className="text-left px-4 py-2">Sharpe</th>
                    <th className="text-left px-4 py-2">Return</th>
                    <th className="text-left px-4 py-2">Trades</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => <SymbolRow key={row.symbol} row={row} />)}
                </tbody>
              </table>
            </div>
          </section>
        ) : (
          !persisted && !running && (
            <section className="glass rounded-2xl px-6 py-12 text-center text-sm text-white/55">
              No training has been run yet. Pick a universe and click <strong>Train now</strong>.
              Yahoo Finance fallback means it works even without a broker connection.
            </section>
          )
        )}

        {/* What each column means */}
        <section className="glass rounded-2xl p-5 text-xs text-white/55 space-y-1.5">
          <div className="text-sm font-semibold text-white mb-2">What the metrics mean</div>
          <div><strong>Win rate</strong> — fraction of simulated trades that closed profitable after a 0.1% round-trip cost.</div>
          <div><strong>Sharpe</strong> — mean per-trade return / standard deviation, scaled by √trades. Above 1.0 is decent on a backtest; expect ~half that live.</div>
          <div><strong>Return</strong> — total compounded % over the full lookback window, net of simulated fees.</div>
          <div><strong>Trades</strong> — count of round-trips. Anything under 5 is too few to trust.</div>
          <div><strong>Strategy</strong> — the tournament winner for that symbol: each of 6 strategies (RSI/SMA, EMA×, MACD, Bollinger, Supertrend, Breakout) is backtested across its own grid and the best by composite score is picked.</div>
          <div className="text-amber-200/80 mt-2">
            If you see suspiciously perfect results (Sharpe &gt; 3, win-rate &gt; 70%), that's almost certainly overfitting,
            not skill. Each strategy's grid is kept deliberately small (55 combos total across all 6) to limit this.
          </div>
        </section>
      </main>
    </div>
  );
}
