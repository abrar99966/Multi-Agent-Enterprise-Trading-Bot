import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useCandles } from '../lib/useCandles';

const UP = '#10d995';
const DOWN = '#f43f5e';
const GOLD = '#e6c181';

// Range → Yahoo interval. Daily intervals (3M+) route to Yahoo server-side so
// zoom-out returns months of real candles even with a broker connected.
const RANGES = [
  { key: '1d', label: '1D', interval: '5m' },
  { key: '5d', label: '5D', interval: '15m' },
  { key: '1mo', label: '1M', interval: '30m' },
  { key: '3mo', label: '3M', interval: '1d' },
  { key: '6mo', label: '6M', interval: '1d' },
  { key: '1y', label: '1Y', interval: '1d' },
];

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const fmt = (n, d = 2) => (n == null || Number.isNaN(n) ? '—' : Number(n).toLocaleString(undefined, { maximumFractionDigits: d }));
const fmtTime = (t, intraday) => {
  const dt = new Date(Number(t) * 1000);
  return intraday
    ? dt.toLocaleString([], { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })
    : dt.toLocaleDateString([], { day: '2-digit', month: 'short', year: '2-digit' });
};

export default function InteractiveChart({
  symbol, levels = {}, side = 'buy',
  height = 440, initialRange = '1d', initialStyle = 'candle',
}) {
  const [rangeKey, setRangeKey] = useState(initialRange);
  const [chartStyle, setChartStyle] = useState(initialStyle);
  const range = RANGES.find((r) => r.key === rangeKey) || RANGES[0];
  const intraday = range.interval.endsWith('m') || range.interval.endsWith('h');
  const { loading, series, quote, source, error, reload } = useCandles(symbol, { range: range.key, interval: range.interval });

  const wrapRef = useRef(null);
  const [width, setWidth] = useState(820);
  useEffect(() => {
    if (!wrapRef.current) return;
    const ro = new ResizeObserver((entries) => { for (const e of entries) setWidth(e.contentRect.width); });
    ro.observe(wrapRef.current);
    return () => ro.disconnect();
  }, []);

  const n = series.length;
  // Visible index window for zoom/pan; null = full series.
  const [view, setView] = useState(null);
  useEffect(() => { setView(null); }, [rangeKey, symbol]);
  const [cursor, setCursor] = useState(null);   // {x,y,idx} idx is into the visible slice
  const drag = useRef(null);

  const vStart = view ? clamp(view.start, 0, Math.max(0, n - 1)) : 0;
  const vEnd = view ? clamp(view.end, vStart + 1, n) : n;
  const visible = useMemo(() => series.slice(vStart, vEnd), [series, vStart, vEnd]);
  const m = visible.length;

  // ---- layout ----
  const H = height;
  const pad = { l: 8, r: 66, t: 16, b: 58 };
  const volH = 34;
  const priceH = H - pad.t - pad.b - volH - 6;
  const innerW = Math.max(60, width - pad.l - pad.r);
  const slot = innerW / Math.max(m, 1);
  const bodyW = Math.max(1.4, Math.min(9, slot * 0.62));

  // y-range over the visible candles + overlay levels
  const { lo, hi, rng } = useMemo(() => {
    if (!m) return { lo: 0, hi: 1, rng: 1 };
    let l = Math.min(...visible.map((d) => d.l ?? d.c));
    let h = Math.max(...visible.map((d) => d.h ?? d.c));
    [levels.entry, levels.target, levels.stop].forEach((v) => {
      if (v != null && !Number.isNaN(v)) { l = Math.min(l, v); h = Math.max(h, v); }
    });
    const s = (h - l) || 1;
    l -= s * 0.06; h += s * 0.06;
    return { lo: l, hi: h, rng: h - l };
  }, [visible, m, levels.entry, levels.target, levels.stop]);

  const x = (i) => pad.l + i * slot + slot / 2;
  const y = (v) => pad.t + (1 - (v - lo) / rng) * priceH;
  const idxFromX = (px) => clamp(Math.floor((px - pad.l) / slot), 0, Math.max(0, m - 1));

  const maxVol = useMemo(() => Math.max(1, ...visible.map((d) => d.v || 0)), [visible]);
  const volBase = pad.t + priceH + 6 + volH;
  const vy = (vv) => volBase - (vv / maxVol) * volH;

  // ---- interactions ----
  const zoomAt = useCallback((px, factor) => {
    setView((prev) => {
      const cur = prev || { start: 0, end: n };
      const len = cur.end - cur.start;
      const frac = clamp((px - pad.l) / innerW, 0, 1);
      const anchor = cur.start + frac * len;
      const newLen = clamp(Math.round(len * factor), 12, n);
      let start = Math.round(anchor - frac * newLen);
      start = clamp(start, 0, n - newLen);
      return { start, end: start + newLen };
    });
  }, [n, innerW]);

  const onWheel = useCallback((e) => {
    if (!n) return;
    e.preventDefault();
    const rect = wrapRef.current.getBoundingClientRect();
    zoomAt(e.clientX - rect.left, e.deltaY > 0 ? 1.25 : 0.8);
  }, [n, zoomAt]);

  const onDown = (e) => { drag.current = { x: e.clientX, start: vStart, end: vEnd }; };
  const onMove = (e) => {
    const rect = wrapRef.current.getBoundingClientRect();
    const px = e.clientX - rect.left, py = e.clientY - rect.top;
    setCursor({ x: px, y: py, idx: idxFromX(px) });
    if (drag.current && n) {
      const dCandles = Math.round(-(e.clientX - drag.current.x) / slot);
      const len = drag.current.end - drag.current.start;
      const start = clamp(drag.current.start + dCandles, 0, n - len);
      setView({ start, end: start + len });
    }
  };
  const endDrag = () => { drag.current = null; };
  const onLeave = () => { setCursor(null); drag.current = null; };

  // ---- derived render data ----
  const last = m ? visible[m - 1].c : (quote?.current_price ?? null);
  const first = m ? (visible[0].o ?? visible[0].c) : null;
  const dayUp = last != null && first != null ? last >= first : true;
  const liveChg = quote?.change_pct;

  const areaPath = useMemo(() => {
    if (chartStyle !== 'area' || !m) return null;
    const p = visible.map((d, i) => `${i === 0 ? 'M' : 'L'} ${x(i).toFixed(1)} ${y(d.c).toFixed(1)}`).join(' ');
    return { line: p, fill: `${p} L ${x(m - 1).toFixed(1)} ${pad.t + priceH} L ${x(0).toFixed(1)} ${pad.t + priceH} Z` };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible, chartStyle, m, lo, hi, slot, innerW]);

  const gridVals = useMemo(() => [0, 0.25, 0.5, 0.75, 1].map((t) => lo + t * rng), [lo, rng]);
  const xTicks = useMemo(() => {
    if (!m) return [];
    const count = Math.min(6, m);
    return Array.from({ length: count }, (_, k) => Math.round((k / (count - 1 || 1)) * (m - 1)));
  }, [m]);

  const hovered = cursor && m ? visible[cursor.idx] : null;

  const Level = ({ v, color, label, dashed = true }) => {
    if (v == null || Number.isNaN(v) || v < lo || v > hi) return null;
    const yy = y(v);
    return (
      <g>
        <line x1={pad.l} x2={width - pad.r} y1={yy} y2={yy} stroke={color} strokeOpacity="0.85" strokeWidth="1" strokeDasharray={dashed ? '5 3' : ''} />
        <rect x={width - pad.r + 2} y={yy - 8} width={pad.r - 4} height={16} rx="3" fill={color} fillOpacity="0.18" />
        <text x={width - pad.r + 6} y={yy + 3.5} fontSize="10" fill={color} className="tabular">{label}{fmt(v)}</text>
      </g>
    );
  };

  return (
    <div className="w-full select-none">
      {/* Controls */}
      <div className="flex items-center justify-between gap-3 flex-wrap mb-2">
        <div className="flex items-baseline gap-2">
          <span className="text-lg font-semibold text-white tabular">{fmt(last)}</span>
          {liveChg != null && (
            <span className={`text-sm tabular ${liveChg >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
              {liveChg >= 0 ? '+' : ''}{fmt(liveChg)}%
            </span>
          )}
          {source && <span className="text-[10px] uppercase tracking-wider text-white/35 px-1.5 py-0.5 rounded bg-white/5 border border-white/10">{source}</span>}
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <div className="flex rounded-lg border border-white/10 overflow-hidden">
            {RANGES.map((r) => (
              <button key={r.key} onClick={() => setRangeKey(r.key)}
                className={`px-2.5 py-1 text-xs transition ${rangeKey === r.key ? 'bg-gold-500/20 text-gold-200' : 'text-white/55 hover:bg-white/5'}`}>
                {r.label}
              </button>
            ))}
          </div>
          <div className="flex rounded-lg border border-white/10 overflow-hidden">
            {[['candle', 'Candles'], ['area', 'Area']].map(([k, lbl]) => (
              <button key={k} onClick={() => setChartStyle(k)}
                className={`px-2.5 py-1 text-xs transition ${chartStyle === k ? 'bg-sky-400/15 text-sky-200' : 'text-white/55 hover:bg-white/5'}`}>
                {lbl}
              </button>
            ))}
          </div>
          <div className="flex rounded-lg border border-white/10 overflow-hidden text-white/70">
            <button title="Zoom in" onClick={() => zoomAt(pad.l + innerW / 2, 0.8)} className="px-2.5 py-1 text-xs hover:bg-white/5">＋</button>
            <button title="Zoom out" onClick={() => zoomAt(pad.l + innerW / 2, 1.25)} className="px-2.5 py-1 text-xs hover:bg-white/5">－</button>
            <button title="Reset zoom" onClick={() => setView(null)} className="px-2.5 py-1 text-xs hover:bg-white/5">⤢</button>
          </div>
        </div>
      </div>

      {/* Chart surface */}
      <div ref={wrapRef} className="relative rounded-xl border border-white/5 bg-ink-950/50 overflow-hidden"
        style={{ height: H, cursor: drag.current ? 'grabbing' : 'crosshair' }}
        onWheel={onWheel} onMouseDown={onDown} onMouseMove={onMove} onMouseUp={endDrag} onMouseLeave={onLeave}>
        {loading ? (
          <div className="absolute inset-0 flex items-center justify-center">
            <div className="absolute inset-0 shimmer" />
            <span className="relative text-xs text-white/50">Loading {symbol} · {range.label}…</span>
          </div>
        ) : (error && error !== 'empty') || (!m && error) ? (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 text-center px-4">
            <div className="text-sm text-white/55">
              {error === 'timeout'
                ? 'Data fetch timed out — the server may be busy (bulk ingest running).'
                : 'Couldn’t load chart data.'}
            </div>
            <button onClick={reload}
              className="px-3 py-1.5 rounded-lg text-xs font-semibold text-ink-900 bg-gold-500 hover:bg-gold-400 transition">
              Retry
            </button>
          </div>
        ) : !m ? (
          <div className="absolute inset-0 flex items-center justify-center text-white/35 text-sm">No data for {symbol} · {range.label}</div>
        ) : (
          <svg width={width} height={H} className="block">
            <defs>
              <linearGradient id="ic-area" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={dayUp ? UP : DOWN} stopOpacity="0.28" />
                <stop offset="100%" stopColor={dayUp ? UP : DOWN} stopOpacity="0" />
              </linearGradient>
            </defs>

            {/* grid + y labels */}
            {gridVals.map((v, i) => (
              <g key={i}>
                <line x1={pad.l} x2={width - pad.r} y1={y(v)} y2={y(v)} stroke="rgba(255,255,255,0.045)" />
                <text x={width - pad.r + 6} y={y(v) + 3.5} fontSize="10" fill="rgba(255,255,255,0.4)" className="tabular">{fmt(v)}</text>
              </g>
            ))}

            {/* volume */}
            {visible.map((d, i) => (
              <rect key={`v${i}`} x={x(i) - bodyW / 2} y={vy(d.v || 0)} width={bodyW} height={Math.max(0, volBase - vy(d.v || 0))}
                fill={(d.c >= (d.o ?? d.c)) ? UP : DOWN} fillOpacity="0.22" />
            ))}

            {/* price: candles or area */}
            {chartStyle === 'area' && areaPath ? (
              <>
                <path d={areaPath.fill} fill="url(#ic-area)" />
                <path d={areaPath.line} fill="none" stroke={dayUp ? UP : DOWN} strokeWidth="1.6" strokeLinejoin="round" />
              </>
            ) : (
              visible.map((d, i) => {
                const o = d.o ?? d.c, c = d.c, h = d.h ?? Math.max(o, c), l = d.l ?? Math.min(o, c);
                const col = c >= o ? UP : DOWN;
                const top = Math.min(y(o), y(c));
                const bh = Math.max(1, Math.abs(y(c) - y(o)));
                return (
                  <g key={i}>
                    <line x1={x(i)} x2={x(i)} y1={y(h)} y2={y(l)} stroke={col} strokeOpacity="0.6" strokeWidth="1" />
                    <rect x={x(i) - bodyW / 2} y={top} width={bodyW} height={bh} fill={col} rx="0.6" />
                  </g>
                );
              })
            )}

            {/* overlays */}
            <Level v={levels.target} color={UP} label="T " />
            <Level v={levels.entry} color={GOLD} label="E " dashed={false} />
            <Level v={levels.stop} color={DOWN} label="S " />

            {/* x ticks */}
            {xTicks.map((i) => (
              <text key={`x${i}`} x={x(i)} y={H - 6} fontSize="9.5" textAnchor="middle" fill="rgba(255,255,255,0.38)" className="tabular">
                {fmtTime(visible[i].t, intraday)}
              </text>
            ))}

            {/* crosshair */}
            {cursor && hovered && (
              <g>
                <line x1={x(cursor.idx)} x2={x(cursor.idx)} y1={pad.t} y2={pad.t + priceH} stroke="rgba(255,255,255,0.25)" strokeDasharray="3 3" />
                <line x1={pad.l} x2={width - pad.r} y1={clamp(cursor.y, pad.t, pad.t + priceH)} y2={clamp(cursor.y, pad.t, pad.t + priceH)} stroke="rgba(255,255,255,0.18)" strokeDasharray="3 3" />
                <circle cx={x(cursor.idx)} cy={y(hovered.c)} r="2.5" fill={dayUp ? UP : DOWN} />
              </g>
            )}
          </svg>
        )}

        {/* OHLC tooltip */}
        {cursor && hovered && (
          <div className="absolute pointer-events-none rounded-lg border border-white/10 bg-ink-900/95 px-2.5 py-1.5 text-[11px] tabular shadow-xl"
            style={{ left: clamp(cursor.x + 12, 4, width - 180), top: 8 }}>
            <div className="text-white/50">{fmtTime(hovered.t, intraday)}</div>
            <div className="flex gap-2 mt-0.5">
              <span className="text-white/45">O <span className="text-white/80">{fmt(hovered.o)}</span></span>
              <span className="text-white/45">H <span className="text-emerald-300">{fmt(hovered.h)}</span></span>
              <span className="text-white/45">L <span className="text-rose-300">{fmt(hovered.l)}</span></span>
              <span className="text-white/45">C <span className="text-white">{fmt(hovered.c)}</span></span>
            </div>
            {hovered.v != null && <div className="text-white/40 mt-0.5">Vol {fmt(hovered.v, 0)}</div>}
          </div>
        )}
      </div>

      {/* legend / hint */}
      <div className="mt-2 flex items-center justify-between text-[10px] text-white/40 flex-wrap gap-2">
        <div className="flex items-center gap-3">
          <span className="inline-flex items-center gap-1"><i className="w-2.5 h-0.5 inline-block rounded" style={{ background: GOLD }} />Entry</span>
          <span className="inline-flex items-center gap-1"><i className="w-2.5 h-0.5 inline-block rounded" style={{ background: UP }} />Target</span>
          <span className="inline-flex items-center gap-1"><i className="w-2.5 h-0.5 inline-block rounded" style={{ background: DOWN }} />Stop</span>
        </div>
        <span>scroll to zoom · drag to pan · {m} of {n} bars</span>
      </div>
    </div>
  );
}
