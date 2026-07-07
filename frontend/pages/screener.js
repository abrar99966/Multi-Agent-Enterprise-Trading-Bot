import React, { useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';

const API = (typeof window !== 'undefined' && window.__API__) || 'http://127.0.0.1:8000';

const fmtPctFrac = (n) => (n == null || Number.isNaN(n) ? '—' : `${(Number(n) * 100).toFixed(1)}%`);
const fmtPct = (n, signed = false) => {
  if (n == null || Number.isNaN(n)) return '—';
  const s = signed && n > 0 ? '+' : '';
  return `${s}${Number(n).toFixed(2)}%`;
};
const fmtNum = (n, d = 2) => (n == null || Number.isNaN(n) ? '—' : Number(n).toFixed(d));
const fmtInt = (n) => (n == null || Number.isNaN(n) ? '—' : Number(n).toLocaleString());
const fmtTs = (t) => (t ? new Date(Number(t) * 1000).toLocaleDateString() : '—');

const STRAT_LABEL = {
  rsi_sma: 'RSI/SMA', ema_cross: 'EMA×', macd: 'MACD', bollinger: 'Bollinger',
  supertrend: 'Supertrend', breakout: 'Breakout', volume_breakout: 'Vol breakout',
  golden_cross: 'Golden cross', engulfing: 'Engulfing',
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

const sigTone = (s) => (s === 'bullish' ? 'pos' : s === 'bearish' ? 'neg' : 'neutral');

export default function Screener() {
  const [universes, setUniverses] = useState([]);
  const [strategies, setStrategies] = useState([]);
  const [extSources, setExtSources] = useState([]);

  // 'internal' or an external source key (tradingview / chartink / screener_in)
  const [source, setSource] = useState('internal');

  // internal controls
  const [preset, setPreset] = useState('stored');
  const [customSymbols, setCustomSymbols] = useState('');
  const [strategy, setStrategy] = useState('');
  const [signal, setSignal] = useState('any');
  const [intervalStr, setIntervalStr] = useState('day');
  const [minWinRate, setMinWinRate] = useState(0);
  const [maxSymbols, setMaxSymbols] = useState(0);

  // external controls
  const [extScan, setExtScan] = useState('');
  const [annotate, setAnnotate] = useState(true);

  const [limit, setLimit] = useState(100);
  const [scanning, setScanning] = useState(false);
  const [result, setResult] = useState(null);   // { kind: 'internal'|'external', ...payload }
  const [error, setError] = useState(null);

  useEffect(() => {
    (async () => {
      try {
        const [u, s, x] = await Promise.all([
          fetch(`${API}/api/v1/learning/universes`).then((r) => r.json()),
          fetch(`${API}/api/v1/learning/strategies`).then((r) => r.json()),
          fetch(`${API}/api/v1/learning/screen/external/sources`).then((r) => r.json()),
        ]);
        setUniverses(u.universes || []);
        setStrategies(s.strategies || []);
        setExtSources(x.sources || []);
      } catch (e) {
        setError('Failed to load presets — is the backend running on :8000?');
      }
    })();
  }, []);

  const isExternal = source !== 'internal';
  const activeSource = extSources.find((s) => s.key === source);

  // Default the external scan to the source's first preset when switching sources.
  useEffect(() => {
    if (isExternal && activeSource) {
      setExtScan(activeSource.presets?.[0]?.key || '');
    }
  }, [source]); // eslint-disable-line react-hooks/exhaustive-deps

  const runScan = useCallback(async () => {
    setScanning(true);
    setError(null);
    try {
      if (!isExternal) {
        const qs = new URLSearchParams();
        qs.set('preset', preset);
        if (preset === 'custom') qs.set('symbols', customSymbols);
        if (strategy) qs.set('strategy', strategy);
        qs.set('signal', signal);
        qs.set('interval', intervalStr);
        if (minWinRate > 0) qs.set('min_win_rate', String(minWinRate));
        if (maxSymbols > 0) qs.set('max_symbols', String(maxSymbols));
        qs.set('limit', String(limit));
        const res = await fetch(`${API}/api/v1/learning/screen?${qs.toString()}`);
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Scan failed');
        setResult({ kind: 'internal', ...data });
      } else {
        const qs = new URLSearchParams();
        qs.set('source', source);
        qs.set('scan', extScan);
        qs.set('limit', String(limit));
        qs.set('annotate', String(annotate));
        qs.set('interval', intervalStr);
        const res = await fetch(`${API}/api/v1/learning/screen/external?${qs.toString()}`);
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'External scan failed');
        setResult({ kind: 'external', ...data });
      }
    } catch (e) {
      setError(String(e.message || e));
      setResult(null);
    } finally {
      setScanning(false);
    }
  }, [isExternal, source, preset, customSymbols, strategy, signal, intervalStr, minWinRate, maxSymbols, extScan, annotate, limit]);

  const selectedUniverse = universes.find((u) => u.key === preset);
  const hits = result?.hits || [];

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
            <span className="px-3 py-1.5 rounded-lg text-white bg-white/5 border border-white/10">Screener</span>
            <Link href="/monitor" className="px-3 py-1.5 rounded-lg text-white/60 hover:text-white hover:bg-white/5 transition">Monitor</Link>
          </nav>
          <div className="ml-auto text-xs text-white/55 tabular">
            {result ? `${result.count ?? result.total} hits` : 'Not scanned'}
          </div>
        </div>
      </header>

      <main className="max-w-[1400px] mx-auto px-4 sm:px-6 py-8 space-y-8">
        {/* Source tabs */}
        <section className="glass rounded-2xl p-5 shadow-card space-y-4">
          <div>
            <span className="block text-[11px] uppercase tracking-wider text-white/55 mb-2">Data source</span>
            <div className="flex flex-wrap gap-2">
              {[{ key: 'internal', label: 'Internal (our engine)' },
                ...extSources.map((s) => ({ key: s.key, label: s.label }))].map((s) => (
                <button key={s.key} onClick={() => setSource(s.key)} disabled={scanning}
                  className={`px-3 py-1.5 rounded-lg text-sm border transition disabled:opacity-50 ${
                    source === s.key
                      ? 'bg-gold-500/15 border-gold-500/50 text-gold-200'
                      : 'bg-white/[0.03] border-white/10 text-white/60 hover:text-white hover:bg-white/5'
                  }`}>
                  {s.label}
                </button>
              ))}
            </div>
            {isExternal && (
              <p className="text-[11px] text-amber-200/70 mt-2">
                ⚠ {activeSource?.note} Unofficial endpoint — personal-research use only; may break or rate-limit.
              </p>
            )}
          </div>

          {/* Controls */}
          {!isExternal ? (
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-sm">
              <label className="block md:col-span-3">
                <span className="block text-[11px] uppercase tracking-wider text-white/55 mb-1">Symbol universe</span>
                <select value={preset} onChange={(e) => setPreset(e.target.value)} disabled={scanning}
                  className="w-full bg-white/[0.04] border border-white/10 rounded-lg px-3 py-2 text-sm text-white outline-none focus:border-gold-500/60">
                  {universes.map((u) => (
                    <option key={u.key} value={u.key}>{u.label}{u.count != null ? ` (${u.count})` : ''}</option>
                  ))}
                  <option value="custom">Custom symbols (type below)</option>
                </select>
                {selectedUniverse && <span className="block text-[11px] text-white/45 mt-1">{selectedUniverse.description}</span>}
              </label>
              {preset === 'custom' && (
                <label className="block md:col-span-3">
                  <span className="block text-[11px] uppercase tracking-wider text-white/55 mb-1">Custom symbols (comma-separated)</span>
                  <input value={customSymbols} onChange={(e) => setCustomSymbols(e.target.value)} disabled={scanning}
                    placeholder="RELIANCE,INFY,TCS,..."
                    className="w-full bg-white/[0.04] border border-white/10 rounded-lg px-3 py-2 text-sm text-white outline-none focus:border-gold-500/60 font-mono" />
                </label>
              )}
              <label className="block">
                <span className="block text-[11px] uppercase tracking-wider text-white/55 mb-1">Strategy</span>
                <select value={strategy} onChange={(e) => setStrategy(e.target.value)} disabled={scanning}
                  className="w-full bg-white/[0.04] border border-white/10 rounded-lg px-3 py-2 text-sm text-white outline-none focus:border-gold-500/60">
                  <option value="">Each symbol's tournament winner</option>
                  {strategies.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
                </select>
              </label>
              <label className="block">
                <span className="block text-[11px] uppercase tracking-wider text-white/55 mb-1">Signal</span>
                <select value={signal} onChange={(e) => setSignal(e.target.value)} disabled={scanning}
                  className="w-full bg-white/[0.04] border border-white/10 rounded-lg px-3 py-2 text-sm text-white outline-none focus:border-gold-500/60">
                  <option value="any">Any</option>
                  <option value="bullish">Bullish only</option>
                  <option value="bearish">Bearish only</option>
                </select>
              </label>
              <label className="block">
                <span className="block text-[11px] uppercase tracking-wider text-white/55 mb-1">Bar interval (from store)</span>
                <select value={intervalStr} onChange={(e) => setIntervalStr(e.target.value)} disabled={scanning}
                  className="w-full bg-white/[0.04] border border-white/10 rounded-lg px-3 py-2 text-sm text-white outline-none focus:border-gold-500/60">
                  <option value="day">Daily</option>
                  <option value="30minute">30 minute</option>
                  <option value="week">Weekly</option>
                </select>
              </label>
              <label className="block">
                <span className="block text-[11px] uppercase tracking-wider text-white/55 mb-1">Min backtested win rate (%)</span>
                <input type="number" min={0} max={100} value={minWinRate}
                  onChange={(e) => setMinWinRate(Number(e.target.value) || 0)} disabled={scanning}
                  className="w-full bg-white/[0.04] border border-white/10 rounded-lg px-3 py-2 text-sm text-white outline-none focus:border-gold-500/60 font-mono tabular" />
              </label>
              <label className="block">
                <span className="block text-[11px] uppercase tracking-wider text-white/55 mb-1">Max symbols (0 = all)</span>
                <input type="number" min={0} value={maxSymbols}
                  onChange={(e) => setMaxSymbols(Number(e.target.value) || 0)} disabled={scanning}
                  className="w-full bg-white/[0.04] border border-white/10 rounded-lg px-3 py-2 text-sm text-white outline-none focus:border-gold-500/60 font-mono tabular" />
              </label>
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-sm">
              <label className="block md:col-span-2">
                <span className="block text-[11px] uppercase tracking-wider text-white/55 mb-1">
                  {source === 'screener_in' ? 'Screen (preset, or paste a /screens/<id>/ URL)' : 'Scan preset'}
                </span>
                {source === 'screener_in' ? (
                  <input value={extScan} onChange={(e) => setExtScan(e.target.value)} disabled={scanning}
                    placeholder="357649/low-pe  or  https://www.screener.in/screens/357649/low-pe/"
                    className="w-full bg-white/[0.04] border border-white/10 rounded-lg px-3 py-2 text-sm text-white outline-none focus:border-gold-500/60 font-mono" />
                ) : (
                  <select value={extScan} onChange={(e) => setExtScan(e.target.value)} disabled={scanning}
                    className="w-full bg-white/[0.04] border border-white/10 rounded-lg px-3 py-2 text-sm text-white outline-none focus:border-gold-500/60">
                    {(activeSource?.presets || []).map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
                  </select>
                )}
              </label>
              <label className="block">
                <span className="block text-[11px] uppercase tracking-wider text-white/55 mb-1">Annotation interval</span>
                <select value={intervalStr} onChange={(e) => setIntervalStr(e.target.value)} disabled={scanning}
                  className="w-full bg-white/[0.04] border border-white/10 rounded-lg px-3 py-2 text-sm text-white outline-none focus:border-gold-500/60">
                  <option value="day">Daily</option>
                  <option value="30minute">30 minute</option>
                  <option value="week">Weekly</option>
                </select>
              </label>
              <label className="flex items-center gap-2 md:col-span-3 text-sm text-white/70 select-none">
                <input type="checkbox" checked={annotate} onChange={(e) => setAnnotate(e.target.checked)} disabled={scanning}
                  className="accent-gold-500" />
                Cross-check each hit with our backtested edge + live signal
              </label>
            </div>
          )}

          <div className="flex items-center justify-between gap-3 flex-wrap pt-1">
            <label className="flex items-center gap-2 text-sm text-white/60">
              <span className="text-[11px] uppercase tracking-wider">Max hits</span>
              <input type="number" min={1} max={500} value={limit}
                onChange={(e) => setLimit(Number(e.target.value) || 100)} disabled={scanning}
                className="w-24 bg-white/[0.04] border border-white/10 rounded-lg px-3 py-2 text-sm text-white outline-none focus:border-gold-500/60 font-mono tabular" />
            </label>
            <button onClick={runScan} disabled={scanning || (isExternal && !extScan)}
              className="px-4 py-2 rounded-lg text-sm font-semibold text-ink-900 bg-gold-500 hover:bg-gold-400 shadow-glow disabled:opacity-50 transition">
              {scanning ? 'Scanning…' : 'Scan now'}
            </button>
          </div>

          {error && (
            <div className="rounded-lg bg-rose-500/10 border border-rose-500/30 px-3 py-2 text-sm text-rose-200">{error}</div>
          )}
        </section>

        {/* Results */}
        {result && result.kind === 'internal' && (
          <ResultsInternal result={result} hits={hits} />
        )}
        {result && result.kind === 'external' && (
          <ResultsExternal result={result} hits={hits} />
        )}
      </main>
    </div>
  );
}

function ResultsInternal({ result, hits }) {
  const counts = useMemo(() => {
    const bull = hits.filter((h) => h.signal === 'bullish').length;
    return { bull, bear: hits.length - bull };
  }, [hits]);
  return (
    <section className="glass rounded-2xl p-5 shadow-card space-y-4 fade-in">
      <div className="flex items-center gap-3 flex-wrap">
        <h2 className="text-lg font-semibold text-white">Hits</h2>
        <Tag tone="info">{result.total} total</Tag>
        <Tag tone="pos">{counts.bull} bullish</Tag>
        <Tag tone="neg">{counts.bear} bearish</Tag>
        <span className="text-xs text-white/45">
          {result.strategy === 'tournament_winner' ? 'per-symbol winner' : stratLabel(result.strategy)} · {result.interval} · {result.n_universe} scanned
        </span>
      </div>
      {hits.length === 0 ? (
        <div className="text-sm text-white/55 py-6 text-center">
          No symbols matched. Try signal = Any, lower the min win rate, or ingest more data
          (<Link href="/training" className="text-gold-300 hover:underline">Training</Link> → data store).
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-[11px] uppercase tracking-wider text-white/45 text-left">
                <th className="py-2 pr-3">#</th><th className="py-2 pr-3">Symbol</th><th className="py-2 pr-3">Signal</th>
                <th className="py-2 pr-3">Strategy</th><th className="py-2 pr-3 text-right">Win rate</th>
                <th className="py-2 pr-3 text-right">Sharpe</th><th className="py-2 pr-3 text-right">Score</th>
                <th className="py-2 pr-3 text-right">Trades</th><th className="py-2 pr-3 text-right">Price</th>
                <th className="py-2 pr-3 text-right">As of</th>
              </tr>
            </thead>
            <tbody>
              {hits.map((h, i) => (
                <tr key={h.symbol} className="border-t border-white/5 hover:bg-white/[0.02]">
                  <td className="py-2 pr-3 text-white/40 tabular">{i + 1}</td>
                  <td className="py-2 pr-3 font-semibold text-white">{h.symbol}</td>
                  <td className="py-2 pr-3"><Tag tone={sigTone(h.signal)}>{h.signal}</Tag></td>
                  <td className="py-2 pr-3 text-white/75">{stratLabel(h.strategy)}{!h.trained && <span className="ml-1.5 text-[10px] text-amber-200/70">untrained</span>}</td>
                  <td className="py-2 pr-3 text-right tabular text-white/85">{fmtPctFrac(h.win_rate)}</td>
                  <td className="py-2 pr-3 text-right tabular text-white/70">{fmtNum(h.sharpe)}</td>
                  <td className="py-2 pr-3 text-right tabular text-white/70">{fmtNum(h.score)}</td>
                  <td className="py-2 pr-3 text-right tabular text-white/55">{h.n_trades ?? '—'}</td>
                  <td className="py-2 pr-3 text-right tabular text-white/85">{h.price ?? '—'}</td>
                  <td className="py-2 pr-3 text-right tabular text-white/45">{fmtTs(h.bar_t)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function ResultsExternal({ result, hits }) {
  // How many external hits our own backtest also gives a proven edge + agreeing signal.
  const confirmed = useMemo(
    () => hits.filter((h) => h.edge?.trained && h.edge?.our_signal && h.edge.our_signal !== 'neutral').length,
    [hits]
  );
  return (
    <section className="glass rounded-2xl p-5 shadow-card space-y-4 fade-in">
      <div className="flex items-center gap-3 flex-wrap">
        <h2 className="text-lg font-semibold text-white">{result.label}</h2>
        <Tag tone="info">{result.count} from {result.source}</Tag>
        {result.annotated && <Tag tone="pos">{confirmed} with our edge</Tag>}
        <span className="text-xs text-white/45">external scan{result.annotated ? ' · cross-checked vs our backtests' : ''}</span>
      </div>
      {hits.length === 0 ? (
        <div className="text-sm text-white/55 py-6 text-center">No symbols returned by this scan.</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-[11px] uppercase tracking-wider text-white/45 text-left">
                <th className="py-2 pr-3">#</th><th className="py-2 pr-3">Symbol</th><th className="py-2 pr-3">Name</th>
                <th className="py-2 pr-3 text-right">Price</th><th className="py-2 pr-3 text-right">Chg%</th>
                <th className="py-2 pr-3 text-right">Volume</th>
                {result.annotated && <>
                  <th className="py-2 pr-3">Our signal</th><th className="py-2 pr-3">Our strategy</th>
                  <th className="py-2 pr-3 text-right">Win rate</th>
                </>}
              </tr>
            </thead>
            <tbody>
              {hits.map((h, i) => {
                const e = h.edge || {};
                return (
                  <tr key={`${h.symbol}-${i}`} className="border-t border-white/5 hover:bg-white/[0.02]">
                    <td className="py-2 pr-3 text-white/40 tabular">{i + 1}</td>
                    <td className="py-2 pr-3 font-semibold text-white">
                      {h.symbol}
                      {result.annotated && !e.in_store && <span className="ml-1.5 text-[10px] text-white/35">not in store</span>}
                    </td>
                    <td className="py-2 pr-3 text-white/60 max-w-[220px] truncate">{h.name || '—'}</td>
                    <td className="py-2 pr-3 text-right tabular text-white/85">{h.price ?? '—'}</td>
                    <td className={`py-2 pr-3 text-right tabular ${h.change_pct > 0 ? 'text-emerald-300' : h.change_pct < 0 ? 'text-rose-300' : 'text-white/60'}`}>
                      {h.change_pct == null ? '—' : fmtPct(h.change_pct, true)}
                    </td>
                    <td className="py-2 pr-3 text-right tabular text-white/55">{fmtInt(h.volume)}</td>
                    {result.annotated && <>
                      <td className="py-2 pr-3">{e.our_signal ? <Tag tone={sigTone(e.our_signal)}>{e.our_signal}</Tag> : <span className="text-white/30 text-xs">—</span>}</td>
                      <td className="py-2 pr-3 text-white/70">{e.trained ? stratLabel(e.our_strategy) : <span className="text-white/30 text-xs">untrained</span>}</td>
                      <td className="py-2 pr-3 text-right tabular text-white/85">{fmtPctFrac(e.win_rate)}</td>
                    </>}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
