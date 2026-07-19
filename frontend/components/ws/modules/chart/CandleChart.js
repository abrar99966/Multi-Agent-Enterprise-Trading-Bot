/**
 * CandleChart — canvas OHLC + volume with crosshair, overlays and markers.
 *
 * WHY canvas and not SVG: a 1D/1m series is several hundred candles, each 3
 * nodes in SVG. Canvas draws the whole frame in one pass and keeps pan/zoom at
 * pointer speed. WHY no charting library: the brief forbids new dependencies,
 * and the drawing surface here is deliberately small.
 *
 * Data contract — the backend's intraday shape verbatim:
 *   series: Array<{ t:number, o:number|null, h:number|null, l:number|null, c:number, v:number|null }>
 * `c` is the only field guaranteed present, so every derived value falls back to
 * the close rather than dropping the bar.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { fmtNum, fmtTime } from '../../ui';

const PAD = { l: 8, r: 62, t: 8, b: 20 };
const VOL_FRAC = 0.18; // share of plot height given to the volume pane
const COLORS = {
  up: '#34d399',
  down: '#f87171',
  upFill: 'rgba(52,211,153,0.75)',
  downFill: 'rgba(248,113,113,0.75)',
  grid: 'rgba(255,255,255,0.05)',
  axis: '#565f70',
  text: '#a8b3c7',
  cross: 'rgba(34,211,238,0.55)',
  ema9: '#fbbf24',
  ema21: '#22d3ee',
  vwap: '#a78bfa',
};

/** Exponential moving average over closes; nulls carry the previous value. */
function ema(values, period) {
  const k = 2 / (period + 1);
  let prev = null;
  return values.map((v) => {
    if (v == null) return prev;
    prev = prev == null ? v : v * k + prev * (1 - k);
    return prev;
  });
}

/** Session VWAP — resets never, because one payload is one session. */
function vwapSeries(bars) {
  let pv = 0;
  let vol = 0;
  return bars.map((b) => {
    const typical = ((b.h ?? b.c) + (b.l ?? b.c) + b.c) / 3;
    const v = b.v || 0;
    pv += typical * v;
    vol += v;
    return vol > 0 ? pv / vol : null;
  });
}

export function CandleChart({
  series = [],
  markers = [],
  overlays = { ema9: true, ema21: true, vwap: true },
  height = 320,
  onHover,
  onMarkerClick,
}) {
  const wrapRef = useRef(null);
  const canvasRef = useRef(null);
  const [size, setSize] = useState({ w: 0, h: height });
  const [hover, setHover] = useState(null); // { x, y, index }
  // View window over the series: [start, end). Panning/zooming mutates this.
  const [view, setView] = useState(null);

  const bars = useMemo(() => (Array.isArray(series) ? series.filter((b) => b && b.c != null) : []), [series]);

  // Reset the view whenever the underlying series identity changes length —
  // a symbol/timeframe switch must not keep a stale window.
  useEffect(() => {
    setView(null);
  }, [bars.length]);

  const win = useMemo(() => {
    const n = bars.length;
    if (!n) return { s: 0, e: 0 };
    if (!view) return { s: 0, e: n };
    const s = Math.max(0, Math.min(view.s, n - 10));
    const e = Math.min(n, Math.max(s + 10, view.e));
    return { s, e };
  }, [view, bars.length]);

  const visible = useMemo(() => bars.slice(win.s, win.e), [bars, win]);

  const ind = useMemo(() => {
    const closes = bars.map((b) => b.c);
    return {
      ema9: overlays.ema9 ? ema(closes, 9) : null,
      ema21: overlays.ema21 ? ema(closes, 21) : null,
      vwap: overlays.vwap ? vwapSeries(bars) : null,
    };
  }, [bars, overlays.ema9, overlays.ema21, overlays.vwap]);

  /* ---- responsive sizing (ResizeObserver, torn down on unmount) ---- */
  useEffect(() => {
    const el = wrapRef.current;
    if (!el || typeof ResizeObserver === 'undefined') return undefined;
    const ro = new ResizeObserver((entries) => {
      const r = entries[0]?.contentRect;
      if (r) setSize({ w: Math.floor(r.width), h: Math.floor(r.height) });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  /* ---- scales ---- */
  const scale = useMemo(() => {
    const w = Math.max(0, size.w - PAD.l - PAD.r);
    const totalH = Math.max(0, size.h - PAD.t - PAD.b);
    const volH = Math.round(totalH * VOL_FRAC);
    const priceH = totalH - volH - 6;
    let lo = Infinity;
    let hi = -Infinity;
    let vmax = 0;
    for (const b of visible) {
      lo = Math.min(lo, b.l ?? b.c);
      hi = Math.max(hi, b.h ?? b.c);
      vmax = Math.max(vmax, b.v || 0);
    }
    if (!Number.isFinite(lo)) {
      lo = 0;
      hi = 1;
    }
    if (hi === lo) {
      hi += 1;
      lo -= 1;
    }
    const padY = (hi - lo) * 0.06;
    lo -= padY;
    hi += padY;
    const n = visible.length || 1;
    const step = w / n;
    return {
      w, priceH, volH, lo, hi, step, n,
      x: (i) => PAD.l + i * step + step / 2,
      y: (p) => PAD.t + priceH - ((p - lo) / (hi - lo)) * priceH,
      vy: (v) => PAD.t + priceH + 6 + volH - (vmax > 0 ? (v / vmax) * volH : 0),
      volTop: PAD.t + priceH + 6,
    };
  }, [size, visible]);

  /* ---- paint ---- */
  useEffect(() => {
    const cvs = canvasRef.current;
    if (!cvs || !size.w || !size.h) return;
    const dpr = (typeof window !== 'undefined' && window.devicePixelRatio) || 1;
    cvs.width = Math.floor(size.w * dpr);
    cvs.height = Math.floor(size.h * dpr);
    cvs.style.width = `${size.w}px`;
    cvs.style.height = `${size.h}px`;
    const g = cvs.getContext('2d');
    if (!g) return;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, size.w, size.h);
    g.font = '10px ui-monospace, JetBrains Mono, monospace';
    g.textBaseline = 'middle';

    const { lo, hi, priceH, step, volTop, volH } = scale;

    // horizontal grid + right price axis
    const TICKS = 5;
    g.strokeStyle = COLORS.grid;
    g.fillStyle = COLORS.axis;
    g.lineWidth = 1;
    g.textAlign = 'left';
    for (let i = 0; i <= TICKS; i += 1) {
      const p = lo + ((hi - lo) * i) / TICKS;
      const y = Math.round(scale.y(p)) + 0.5;
      g.beginPath();
      g.moveTo(PAD.l, y);
      g.lineTo(PAD.l + scale.w, y);
      g.stroke();
      g.fillText(fmtNum(p), PAD.l + scale.w + 6, y);
    }

    // volume pane baseline
    g.strokeStyle = COLORS.grid;
    g.beginPath();
    g.moveTo(PAD.l, volTop + volH + 0.5);
    g.lineTo(PAD.l + scale.w, volTop + volH + 0.5);
    g.stroke();

    // candles + volume
    const bodyW = Math.max(1, Math.min(9, step * 0.62));
    visible.forEach((b, i) => {
      const up = b.c >= (b.o ?? b.c);
      const x = scale.x(i);
      const o = b.o ?? b.c;
      const h = b.h ?? b.c;
      const l = b.l ?? b.c;

      if (b.v) {
        g.fillStyle = up ? 'rgba(52,211,153,0.22)' : 'rgba(248,113,113,0.22)';
        const vy = scale.vy(b.v);
        g.fillRect(x - bodyW / 2, vy, bodyW, volTop + volH - vy);
      }

      g.strokeStyle = up ? COLORS.up : COLORS.down;
      g.beginPath();
      g.moveTo(Math.round(x) + 0.5, scale.y(h));
      g.lineTo(Math.round(x) + 0.5, scale.y(l));
      g.stroke();

      g.fillStyle = up ? COLORS.upFill : COLORS.downFill;
      const yO = scale.y(o);
      const yC = scale.y(b.c);
      const top = Math.min(yO, yC);
      const hgt = Math.max(1, Math.abs(yC - yO));
      g.fillRect(x - bodyW / 2, top, bodyW, hgt);
    });

    // overlays — drawn from the absolute series so they don't restart at the
    // window edge, then clipped to the visible slice.
    const line = (vals, color) => {
      if (!vals) return;
      g.strokeStyle = color;
      g.lineWidth = 1.25;
      g.beginPath();
      let started = false;
      for (let i = 0; i < visible.length; i += 1) {
        const v = vals[win.s + i];
        if (v == null) continue;
        const x = scale.x(i);
        const y = scale.y(v);
        if (!started) {
          g.moveTo(x, y);
          started = true;
        } else g.lineTo(x, y);
      }
      g.stroke();
      g.lineWidth = 1;
    };
    line(ind.vwap, COLORS.vwap);
    line(ind.ema21, COLORS.ema21);
    line(ind.ema9, COLORS.ema9);

    // last price tag
    const last = visible[visible.length - 1];
    if (last) {
      const y = scale.y(last.c);
      const up = last.c >= (last.o ?? last.c);
      g.fillStyle = up ? COLORS.up : COLORS.down;
      g.fillRect(PAD.l + scale.w + 2, y - 8, PAD.r - 6, 16);
      g.fillStyle = '#04060c';
      g.textAlign = 'left';
      g.fillText(fmtNum(last.c), PAD.l + scale.w + 6, y);
    }

    // markers — order/strategy/risk/AI annotations pinned to a bar index
    markers.forEach((m) => {
      const idx = m.index - win.s;
      if (idx < 0 || idx >= visible.length) return;
      const x = scale.x(idx);
      const y = m.price != null ? scale.y(m.price) : scale.y(visible[idx].c);
      const tone =
        m.tone === 'pos' ? COLORS.up : m.tone === 'neg' ? COLORS.down : m.tone === 'warn' ? '#fbbf24' : '#22d3ee';
      g.fillStyle = tone;
      g.beginPath();
      const s = 5;
      if (m.dir === 'down') {
        g.moveTo(x, y + s);
        g.lineTo(x - s, y - s);
        g.lineTo(x + s, y - s);
      } else {
        g.moveTo(x, y - s);
        g.lineTo(x - s, y + s);
        g.lineTo(x + s, y + s);
      }
      g.closePath();
      g.fill();
    });

    // crosshair
    if (hover && hover.index >= 0 && hover.index < visible.length) {
      const x = scale.x(hover.index);
      g.strokeStyle = COLORS.cross;
      g.setLineDash([3, 3]);
      g.beginPath();
      g.moveTo(Math.round(x) + 0.5, PAD.t);
      g.lineTo(Math.round(x) + 0.5, volTop + volH);
      g.moveTo(PAD.l, Math.round(hover.y) + 0.5);
      g.lineTo(PAD.l + scale.w, Math.round(hover.y) + 0.5);
      g.stroke();
      g.setLineDash([]);

      const p = lo + ((PAD.t + priceH - hover.y) / priceH) * (hi - lo);
      g.fillStyle = '#121926';
      g.fillRect(PAD.l + scale.w + 2, hover.y - 8, PAD.r - 6, 16);
      g.fillStyle = COLORS.text;
      g.textAlign = 'left';
      g.fillText(fmtNum(p), PAD.l + scale.w + 6, hover.y);
    }
  }, [size, scale, visible, win.s, ind, markers, hover]);

  /* ---- interaction ---- */
  const indexFromEvent = useCallback(
    (e) => {
      const rect = canvasRef.current?.getBoundingClientRect();
      if (!rect) return null;
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      const i = Math.floor((x - PAD.l) / (scale.step || 1));
      return { x, y, index: Math.max(0, Math.min(visible.length - 1, i)) };
    },
    [scale.step, visible.length],
  );

  const onMove = useCallback(
    (e) => {
      const h = indexFromEvent(e);
      if (!h) return;
      setHover(h);
      if (onHover) onHover(visible[h.index] || null);
    },
    [indexFromEvent, onHover, visible],
  );

  const onLeave = useCallback(() => {
    setHover(null);
    if (onHover) onHover(null);
  }, [onHover]);

  const onWheel = useCallback(
    (e) => {
      if (!bars.length) return;
      e.preventDefault();
      const cur = view || { s: 0, e: bars.length };
      const span = cur.e - cur.s;
      const factor = e.deltaY > 0 ? 1.15 : 0.87;
      const nextSpan = Math.max(10, Math.min(bars.length, Math.round(span * factor)));
      const h = indexFromEvent(e);
      const anchor = cur.s + (h ? h.index : Math.floor(span / 2));
      let s = Math.round(anchor - (nextSpan * (anchor - cur.s)) / span);
      s = Math.max(0, Math.min(bars.length - nextSpan, s));
      setView({ s, e: s + nextSpan });
    },
    [bars.length, view, indexFromEvent],
  );

  const drag = useRef(null);
  const onPointerDown = useCallback(
    (e) => {
      drag.current = { x: e.clientX, view: view || { s: 0, e: bars.length } };
      e.currentTarget.setPointerCapture(e.pointerId);
    },
    [view, bars.length],
  );
  const onPointerMove = useCallback(
    (e) => {
      onMove(e);
      const d = drag.current;
      if (!d) return;
      const shifted = Math.round((d.x - e.clientX) / (scale.step || 1));
      const span = d.view.e - d.view.s;
      let s = Math.max(0, Math.min(bars.length - span, d.view.s + shifted));
      setView({ s, e: s + span });
    },
    [onMove, scale.step, bars.length],
  );
  const onPointerUp = useCallback((e) => {
    drag.current = null;
    try {
      e.currentTarget.releasePointerCapture(e.pointerId);
    } catch {
      /* pointer already released */
    }
  }, []);

  const onClick = useCallback(
    (e) => {
      if (!onMarkerClick) return;
      const h = indexFromEvent(e);
      if (!h) return;
      const abs = win.s + h.index;
      const hit = markers.find((m) => Math.abs(m.index - abs) <= 1);
      if (hit) onMarkerClick(hit);
    },
    [indexFromEvent, markers, onMarkerClick, win.s],
  );

  const hoverBar = hover ? visible[hover.index] : null;

  return (
    <div ref={wrapRef} className="relative h-full w-full" style={{ minHeight: height }}>
      <canvas
        ref={canvasRef}
        onPointerMove={onPointerMove}
        onPointerDown={onPointerDown}
        onPointerUp={onPointerUp}
        onPointerLeave={onLeave}
        onWheel={onWheel}
        onClick={onClick}
        className="block h-full w-full cursor-crosshair touch-none"
        role="img"
        aria-label={`Price chart, ${bars.length} bars`}
      />

      {/* OHLC legend — the readout replaces a tooltip so the eye never leaves
          the price action */}
      <div className="pointer-events-none absolute left-2 top-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 font-hx-mono text-hx-10">
        {hoverBar ? (
          <>
            <span className="text-hx-text-dim">{fmtTime(hoverBar.t)}</span>
            <span className="text-hx-text-lo">
              O <span className="text-hx-text-mid">{fmtNum(hoverBar.o ?? hoverBar.c)}</span>
            </span>
            <span className="text-hx-text-lo">
              H <span className="text-hx-text-mid">{fmtNum(hoverBar.h ?? hoverBar.c)}</span>
            </span>
            <span className="text-hx-text-lo">
              L <span className="text-hx-text-mid">{fmtNum(hoverBar.l ?? hoverBar.c)}</span>
            </span>
            <span className="text-hx-text-lo">
              C <span className="text-hx-text-hi">{fmtNum(hoverBar.c)}</span>
            </span>
            {hoverBar.v ? (
              <span className="text-hx-text-lo">
                V <span className="text-hx-text-mid">{fmtNum(hoverBar.v, { compact: true })}</span>
              </span>
            ) : null}
          </>
        ) : (
          <span className="text-hx-text-dim">Scroll to zoom · drag to pan</span>
        )}
        {overlays.ema9 && <span style={{ color: COLORS.ema9 }}>EMA9</span>}
        {overlays.ema21 && <span style={{ color: COLORS.ema21 }}>EMA21</span>}
        {overlays.vwap && <span style={{ color: COLORS.vwap }}>VWAP</span>}
      </div>
    </div>
  );
}

export default CandleChart;
