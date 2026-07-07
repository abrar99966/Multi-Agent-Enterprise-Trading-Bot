import React, { useEffect } from 'react';
import InteractiveChart from './InteractiveChart';

// Build the standalone-page URL carrying the symbol + trade levels so the
// "open in new tab" view renders identically and is shareable.
export function chartHref({ symbol, entry, target, stop, side } = {}) {
  const q = new URLSearchParams();
  if (entry != null) q.set('entry', entry);
  if (target != null) q.set('target', target);
  if (stop != null) q.set('stop', stop);
  if (side) q.set('side', side);
  return `/chart/${encodeURIComponent(symbol)}?${q.toString()}`;
}

export default function ChartModal({ open, onClose, symbol, levels = {}, side = 'buy' }) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { window.removeEventListener('keydown', onKey); document.body.style.overflow = prev; };
  }, [open, onClose]);

  if (!open) return null;
  const accent = (side || '').toLowerCase() === 'buy' ? '#10d995' : '#f43f5e';
  const href = chartHref({ symbol, entry: levels.entry, target: levels.target, stop: levels.stop, side });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 sm:p-8" role="dialog" aria-modal="true">
      <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" onClick={onClose} />
      <div className="relative w-full max-w-[1100px] glass-premium rounded-2xl p-5 fade-in">
        <div className="flex items-center justify-between gap-3 mb-3 flex-wrap">
          <div className="flex items-center gap-3">
            <span className="text-xl font-semibold tracking-tight text-white">{symbol}</span>
            <span className="px-2 py-0.5 rounded-md text-[11px] font-semibold uppercase tracking-wider"
              style={{ background: `${accent}22`, color: accent, border: `1px solid ${accent}44` }}>
              {side || '—'}
            </span>
            <span className="text-xs text-white/50 tabular">
              E {levels.entry ?? '—'} · T {levels.target ?? '—'} · S {levels.stop ?? '—'}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <a href={href} target="_blank" rel="noopener noreferrer"
              className="px-3 py-1.5 rounded-lg text-xs font-medium text-white/75 border border-white/10 hover:bg-white/5 transition inline-flex items-center gap-1.5">
              Open in new tab
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" /><path d="M15 3h6v6" /><path d="M10 14L21 3" />
              </svg>
            </a>
            <button onClick={onClose} aria-label="Close"
              className="w-8 h-8 rounded-lg border border-white/10 text-white/70 hover:bg-white/5 transition inline-flex items-center justify-center">✕</button>
          </div>
        </div>
        <InteractiveChart symbol={symbol} levels={levels} side={side} height={460} />
      </div>
    </div>
  );
}
