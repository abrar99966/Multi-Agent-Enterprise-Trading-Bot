/**
 * chartKit — internal geometry + state helpers for the portfolio module's
 * hand-rolled SVG charts.
 *
 * WHY this exists: no charting library may be installed, so every chart draws
 * its own axes, scales and hit-testing. Keeping that arithmetic in one place
 * stops four charts from each inventing a slightly different tick algorithm
 * (which is how axis labels drift out of alignment between stacked panels).
 *
 * Nothing here fetches or formats domain data — it is pure geometry plus two
 * tiny React helpers.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { EmptyState, Skeleton, cx } from '../../ui';

/* ---- measurement --------------------------------------------------------- */

/**
 * useMeasure — [ref, {w,h}] for the element the ref is attached to.
 *
 * WHY ResizeObserver and not a window resize listener: workspace panels resize
 * when a *sibling* panel collapses or the shell rail toggles, neither of which
 * fires a window event. The size is rounded and identity-stable so a resize
 * that changes nothing does not re-render the chart.
 */
export function useMeasure() {
  const [size, setSize] = useState({ w: 0, h: 0 });
  const roRef = useRef(null);

  /* A CALLBACK ref, not an object ref.
   *
   * WHY: every chart here is wrapped in PanelState, which renders a skeleton
   * while data loads and swaps in the real content afterwards. With an object
   * ref + `[]`-deps effect, the effect ran once at mount — when the skeleton was
   * showing and ref.current was still null — bailed out, and never re-ran when
   * the measured node finally appeared. Width stayed 0 forever, every chart fell
   * back to its minimum viewBox (160px), and the SVG then scaled that tiny
   * viewBox to fit and CENTRED it: the charts rendered as a narrow band floating
   * in the middle of an otherwise empty panel.
   *
   * A callback ref fires on every attach and detach, so the observer follows the
   * node wherever it goes. */
  const ref = useCallback((node) => {
    if (roRef.current) {
      roRef.current.disconnect();
      roRef.current = null;
    }
    if (!node) return;

    if (typeof ResizeObserver === 'undefined') {
      setSize({ w: node.clientWidth, h: node.clientHeight });
      return;
    }

    // Seed synchronously: the observer's first callback lands a frame later, and
    // one frame at the fallback size is a visible flash of a mis-sized chart.
    setSize((s) => {
      const w = Math.round(node.clientWidth);
      const h = Math.round(node.clientHeight);
      return s.w === w && s.h === h ? s : { w, h };
    });

    const ro = new ResizeObserver((entries) => {
      const r = entries[0] ? entries[0].contentRect : null;
      if (!r) return;
      const w = Math.round(r.width);
      const h = Math.round(r.height);
      setSize((s) => (s.w === w && s.h === h ? s : { w, h }));
    });
    ro.observe(node);
    roRef.current = ro;
  }, []);

  // Detach on unmount — the callback ref handles node swaps, not teardown.
  useEffect(() => () => {
    if (roRef.current) roRef.current.disconnect();
  }, []);

  return [ref, size];
}

/* ---- scales -------------------------------------------------------------- */

/**
 * Domain for a series, padded so the line never touches the plot edge.
 * `includeZero` matters for P&L charts: a curve that never goes negative still
 * needs the zero rule visible, otherwise "up 400" and "down 400" look identical.
 */
export function niceExtent(values, { padFrac = 0.08, includeZero = false } = {}) {
  let min = Infinity;
  let max = -Infinity;
  for (let i = 0; i < values.length; i += 1) {
    const v = Number(values[i]);
    if (!Number.isFinite(v)) continue;
    if (v < min) min = v;
    if (v > max) max = v;
  }
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [0, 1];
  if (includeZero) {
    min = Math.min(min, 0);
    max = Math.max(max, 0);
  }
  if (min === max) {
    const d = Math.abs(min) || 1;
    return [min - d * 0.5, max + d * 0.5];
  }
  const pad = (max - min) * padFrac;
  return [min - pad, max + pad];
}

/** Round tick values across a domain — 1/2/5 x 10^n stepping. */
export function ticks(min, max, count = 4) {
  const span = max - min;
  if (!(span > 0) || !Number.isFinite(span)) return [min];
  const raw = span / Math.max(1, count);
  const mag = 10 ** Math.floor(Math.log10(raw));
  const norm = raw / mag;
  const step = (norm >= 7.5 ? 10 : norm >= 3.5 ? 5 : norm >= 1.5 ? 2 : 1) * mag;
  const out = [];
  const start = Math.ceil(min / step) * step;
  // Guard: a degenerate step would spin forever.
  if (!(step > 0)) return [min];
  for (let t = start; t <= max + step * 1e-6 && out.length < 20; t += step) {
    out.push(Number(t.toPrecision(12)));
  }
  return out;
}

/** Linear scale factory. */
export function scale(d0, d1, r0, r1) {
  const span = d1 - d0;
  if (!span) return () => (r0 + r1) / 2;
  return (v) => r0 + ((v - d0) / span) * (r1 - r0);
}

/* ---- path building ------------------------------------------------------- */

/** [[x,y],...] → "M x y L x y ...". Coordinates are fixed to 2dp to keep the
    emitted DOM small on 500-point curves. */
export function linePath(pts) {
  if (!pts || !pts.length) return '';
  let d = '';
  for (let i = 0; i < pts.length; i += 1) {
    d += `${i ? 'L' : 'M'}${pts[i][0].toFixed(2)} ${pts[i][1].toFixed(2)}`;
    if (i < pts.length - 1) d += ' ';
  }
  return d;
}

/** Closed area between the line and a baseline y. */
export function areaPath(pts, baseY) {
  if (!pts || !pts.length) return '';
  const first = pts[0];
  const last = pts[pts.length - 1];
  return `${linePath(pts)} L${last[0].toFixed(2)} ${baseY.toFixed(2)} L${first[0].toFixed(2)} ${baseY.toFixed(
    2,
  )} Z`;
}

/** Index of the point whose x is nearest `px`. Points must be x-ascending. */
export function nearestIndex(pts, px) {
  if (!pts || !pts.length) return -1;
  let lo = 0;
  let hi = pts.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (pts[mid][0] < px) lo = mid;
    else hi = mid;
  }
  return Math.abs(pts[lo][0] - px) <= Math.abs(pts[hi][0] - px) ? lo : hi;
}

/**
 * Pointer x within an SVG element, in SVG user units.
 * WHY not e.nativeEvent.offsetX: that is unreliable when the pointer is over a
 * child <path>, which is most of a chart's surface area.
 */
export function pointerX(e) {
  const r = e.currentTarget.getBoundingClientRect();
  if (!r.width) return 0;
  const vb = e.currentTarget.viewBox && e.currentTarget.viewBox.baseVal;
  const unitW = vb && vb.width ? vb.width : r.width;
  return ((e.clientX - r.left) / r.width) * unitW;
}

/* ---- colour -------------------------------------------------------------- */

/**
 * "#22d3ee" → "rgba(34,211,238,a)".
 *
 * WHY inline colour is legitimate here (it looks like the concatenation trap
 * tokens.js warns about, but is not): these values land in a `style` attribute,
 * never in a className, so Tailwind's JIT scanner is not involved.
 */
export function rgba(hex, a = 1) {
  const h = String(hex || '').replace('#', '');
  if (h.length !== 6) return `rgba(125,136,153,${a})`;
  const n = parseInt(h, 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}

/* ---- shared panel states ------------------------------------------------- */

/** Chart-shaped shimmer — a wide block plus a baseline, so the loading state
    occupies the same footprint the chart will. */
export function ChartSkeleton({ height = 160 }) {
  return (
    <div className="flex flex-col justify-end gap-2 w-full" style={{ height }} aria-hidden="true">
      <Skeleton h={Math.max(24, height - 46)} className="w-full" rounded="rounded-md" />
      <div className="flex items-center justify-between gap-2">
        <Skeleton h={8} className="w-16" />
        <Skeleton h={8} className="w-16" />
        <Skeleton h={8} className="w-16" />
      </div>
    </div>
  );
}

/**
 * PanelState — the loading / error / empty gate every data-bound panel routes
 * through, so no panel can ever render a blank box (a rule of this workspace).
 * Returns `children` only once there is real data to draw.
 */
export function PanelState({
  loading,
  error,
  empty,
  onRetry,
  emptyTitle = 'No data',
  emptyHint,
  emptyVariant = 'default',
  height = 160,
  skeleton,
  children,
  className = '',
}) {
  if (loading) {
    return <div className={cx('w-full', className)}>{skeleton || <ChartSkeleton height={height} />}</div>;
  }
  if (error) {
    return (
      <EmptyState
        variant="error"
        title="Couldn't load"
        hint={String((error && error.message) || error)}
        action={onRetry ? { label: 'Retry', onClick: onRetry, icon: 'refresh' } : undefined}
        className={className}
      />
    );
  }
  if (empty) {
    return <EmptyState variant={emptyVariant} title={emptyTitle} hint={emptyHint} className={className} />;
  }
  return children;
}

/**
 * FloatingReadout — absolutely-positioned hover card used by the treemap and
 * the time-series charts.
 *
 * WHY not the shared <Tooltip>: that primitive positions inside a `relative`
 * wrapper and clips inside `overflow:auto` ancestors. Charts live in panel
 * bodies that scroll, and a readout that follows the cursor needs free x/y
 * placement anyway. Callers must render this inside a `relative` box that does
 * not scroll — every call site in this module sets `scroll={false}` on its
 * PanelBody for exactly that reason.
 */
export function FloatingReadout({ x, y, children, align = 'auto', width = 168 }) {
  // Flip to the left of the cursor near the right edge so the card never
  // overflows the panel.
  const flip = align === 'left' || (align === 'auto' && x > width);
  return (
    <div
      role="status"
      className="pointer-events-none absolute z-20 rounded-md border border-hx-border-strong bg-hx-bg-overlay px-2 py-1.5 shadow-hx-pop animate-hx-fade-in"
      style={{
        left: flip ? undefined : x + 10,
        right: flip ? `calc(100% - ${x - 10}px)` : undefined,
        top: Math.max(2, y - 8),
        minWidth: width,
      }}
    >
      {children}
    </div>
  );
}

export default {
  useMeasure,
  niceExtent,
  ticks,
  scale,
  linePath,
  areaPath,
  nearestIndex,
  pointerX,
  rgba,
  ChartSkeleton,
  PanelState,
  FloatingReadout,
};
