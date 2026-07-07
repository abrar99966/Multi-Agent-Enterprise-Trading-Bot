import React from 'react';
import Link from 'next/link';
import { useRouter } from 'next/router';
import InteractiveChart from '../../components/InteractiveChart';

const num = (v) => (v == null || v === '' || Number.isNaN(Number(v)) ? undefined : Number(v));

export default function ChartPage() {
  const router = useRouter();
  const { symbol, entry, target, stop, side } = router.query;
  const sym = Array.isArray(symbol) ? symbol[0] : symbol;
  const levels = { entry: num(entry), target: num(target), stop: num(stop) };
  const dir = (side || 'buy').toString();
  const accent = dir.toLowerCase() === 'buy' ? '#10d995' : '#f43f5e';

  return (
    <div className="min-h-screen text-white">
      <header className="sticky top-0 z-30 glass-blur">
        <div className="max-w-[1280px] mx-auto px-4 sm:px-6 py-3 flex items-center gap-4">
          <Link href="/" className="flex items-center gap-3 group min-w-0">
            <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-gold-400 to-gold-600 flex items-center justify-center shadow-glow shrink-0">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#0a0e1a" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M3 17l6-6 4 4 8-8" /><path d="M14 7h7v7" />
              </svg>
            </div>
            <div className="min-w-0">
              <div className="text-sm font-semibold tracking-tight text-white group-hover:text-gold-300 transition truncate">Helios Capital</div>
              <div className="text-[10px] uppercase tracking-[0.18em] text-white/45 truncate">Chart</div>
            </div>
          </Link>
          {sym && (
            <div className="flex items-center gap-2 ml-2">
              <span className="text-lg font-semibold tracking-tight text-white">{sym}</span>
              <span className="px-2 py-0.5 rounded-md text-[11px] font-semibold uppercase tracking-wider"
                style={{ background: `${accent}22`, color: accent, border: `1px solid ${accent}44` }}>{dir}</span>
            </div>
          )}
          <Link href="/" className="ml-auto px-3 py-1.5 rounded-lg text-xs text-white/60 hover:text-white hover:bg-white/5 transition">← Dashboard</Link>
        </div>
      </header>

      <main className="max-w-[1280px] mx-auto px-4 sm:px-6 py-6">
        {!sym ? (
          <div className="text-white/50 text-sm py-20 text-center">Loading chart…</div>
        ) : (
          <div className="glass-premium rounded-2xl p-5">
            {(levels.entry || levels.target || levels.stop) && (
              <div className="text-xs text-white/50 tabular mb-3">
                Entry {levels.entry ?? '—'} · Target {levels.target ?? '—'} · Stop {levels.stop ?? '—'}
              </div>
            )}
            <InteractiveChart symbol={sym} levels={levels} side={dir} height={Math.max(420, (typeof window !== 'undefined' ? window.innerHeight : 800) - 240)} />
          </div>
        )}
      </main>
    </div>
  );
}
