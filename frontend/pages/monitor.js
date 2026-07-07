import React from 'react';
import Link from 'next/link';
import { useLivePoll } from '../lib/useLivePoll';

const API = (typeof window !== 'undefined' && window.__API__) || 'http://127.0.0.1:8000';

const fmtTs = (iso) => (iso ? new Date(iso).toLocaleString() : '—');
const Dot = ({ ok }) => (
  <span className={`inline-block w-2.5 h-2.5 rounded-full ${ok ? 'bg-emerald-400 shadow-[0_0_10px_-1px_#10d995]' : 'bg-rose-400 shadow-[0_0_10px_-1px_#f43f5e]'}`} />
);
const latTone = (ms) => (ms == null ? 'text-white/40' : ms < 200 ? 'text-emerald-300' : ms < 1500 ? 'text-amber-200' : 'text-rose-300');

export default function MonitorPage() {
  const { data, error } = useLivePoll(
    (signal) => fetch(`${API}/api/v1/performance/health`, { signal }).then((r) => r.json()),
    15_000, ['health'], 'health',
  );
  const agents = data?.agents || [];
  const okCount = agents.filter((a) => a.ok).length;
  const allOk = data?.ok;

  return (
    <div className="min-h-screen text-white">
      <header className="sticky top-0 z-30 glass-blur">
        <div className="max-w-[1400px] mx-auto px-4 sm:px-6 py-3 flex items-center gap-4 sm:gap-6">
          <Link href="/" className="flex items-center gap-3 group min-w-0">
            <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-gold-400 to-gold-600 flex items-center justify-center shadow-glow shrink-0">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#0a0e1a" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M3 17l6-6 4 4 8-8" /><path d="M14 7h7v7" /></svg>
            </div>
            <div className="min-w-0">
              <div className="text-sm font-semibold tracking-tight text-white group-hover:text-gold-300 transition truncate">Helios Capital</div>
              <div className="text-[10px] uppercase tracking-[0.18em] text-white/45 truncate">System Monitor</div>
            </div>
          </Link>
          <nav className="hidden sm:flex items-center gap-1 text-xs">
            <Link href="/" className="px-3 py-1.5 rounded-lg text-white/60 hover:text-white hover:bg-white/5 transition">Dashboard</Link>
            <Link href="/training" className="px-3 py-1.5 rounded-lg text-white/60 hover:text-white hover:bg-white/5 transition">Training</Link>
            <Link href="/performance" className="px-3 py-1.5 rounded-lg text-white/60 hover:text-white hover:bg-white/5 transition">Performance</Link>
            <span className="px-3 py-1.5 rounded-lg text-white bg-white/5 border border-white/10">Monitor</span>
          </nav>
          <div className="ml-auto text-xs text-white/55 tabular">auto-refresh 15s</div>
        </div>
      </header>

      <main className="max-w-[1400px] mx-auto px-4 sm:px-6 py-8 space-y-6">
        {/* Overall status */}
        <section className={`glass-premium rounded-2xl p-5 flex items-center gap-4 ${allOk ? '' : 'border border-rose-400/30'}`}>
          <Dot ok={allOk} />
          <div>
            <div className="text-lg font-semibold text-white">
              {error ? 'Backend unreachable' : allOk ? 'All systems operational' : 'Degraded — check agents below'}
            </div>
            <div className="text-xs text-white/55 mt-0.5">
              {agents.length ? `${okCount}/${agents.length} agents healthy` : 'probing…'}
              {data?.data?.probe_symbol ? ` · probe ${data.data.probe_symbol}` : ''}
            </div>
          </div>
        </section>

        {/* Agents grid */}
        <section>
          <h2 className="text-sm font-semibold text-white/80 mb-3 px-1 uppercase tracking-wider">Agents</h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {agents.map((a) => (
              <div key={a.name} className="glass-premium accent-rail rounded-2xl p-4" style={{ '--rail': a.ok ? '#10d99599' : '#f43f5e99' }}>
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2"><Dot ok={a.ok} /><span className="font-semibold text-white">{a.name}</span></div>
                  <span className={`text-[11px] tabular ${latTone(a.latency_ms)}`}>{a.latency_ms != null ? `${a.latency_ms}ms` : '—'}</span>
                </div>
                <div className="mt-2 text-xs text-white/65 break-words min-h-[2.4em]">{a.detail || '—'}</div>
                {a.error && <div className="mt-1.5 text-[11px] text-rose-300/80 break-words">⚠ {a.error}</div>}
              </div>
            ))}
            {!agents.length && <div className="text-white/45 text-sm px-1">Loading agent health…</div>}
          </div>
        </section>

        {/* Data + Learning */}
        <section className="grid grid-cols-1 lg:grid-cols-2 gap-3">
          <div className="glass rounded-2xl p-5">
            <div className="text-sm font-semibold text-white mb-3">Market data</div>
            <Row k="Quote source" v={data?.data?.quote_source} />
            <Row k="Quote latency" v={data?.data?.quote_latency_ms != null ? `${data.data.quote_latency_ms}ms` : '—'} />
            <Row k="Stored symbols" v={data?.data?.coverage_symbols} />
            <Row k="Total bars" v={data?.data?.total_bars != null ? Number(data.data.total_bars).toLocaleString() : '—'} />
            {data?.data?.quote_error && <div className="text-[11px] text-rose-300/80 mt-2">⚠ {data.data.quote_error}</div>}
          </div>
          <div className="glass rounded-2xl p-5">
            <div className="text-sm font-semibold text-white mb-3">Learning loops</div>
            <Row k="Trained symbols" v={data?.learning?.trained_symbols} />
            <Row k="Trained at" v={fmtTs(data?.learning?.trained_at)} />
            <Row k="Interval · lookback" v={data?.learning ? `${data.learning.interval || '—'} · ${data.learning.lookback_days || '—'}d` : '—'} />
            <Row k="Calibration samples" v={data?.learning?.calibration_samples} />
            <Row k="RL learned states" v={data?.learning?.rl_states} />
            {data?.learning?.strategy_wins && (
              <div className="mt-2 text-[11px] text-white/50">
                strategy wins: {Object.entries(data.learning.strategy_wins).sort((a, b) => b[1] - a[1]).slice(0, 5).map(([k, v]) => `${k} ${v}`).join(' · ')}
              </div>
            )}
          </div>
        </section>

        <div className="text-[11px] text-white/35 px-1">
          Agents are probed live on {data?.data?.probe_symbol || 'a symbol'} each refresh. Latency colour: green &lt;200ms · amber &lt;1.5s · red slower (News/Macro fetch live, so they're slower).
        </div>
      </main>
    </div>
  );
}

function Row({ k, v }) {
  return (
    <div className="flex items-center justify-between py-1.5 border-t border-white/5 first:border-t-0 text-sm">
      <span className="text-white/55">{k}</span>
      <span className="text-white/85 tabular text-right">{v ?? '—'}</span>
    </div>
  );
}
