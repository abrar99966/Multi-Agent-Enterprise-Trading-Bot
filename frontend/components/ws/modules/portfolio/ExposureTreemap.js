/**
 * ExposureTreemap — gross exposure by symbol, optionally nested under sector.
 *
 * Layout is a real squarified treemap (Bruls, Huizing & van Wijk 2000): rows are
 * accumulated only while doing so *improves* the worst aspect ratio in the row,
 * which is what keeps tiles near-square instead of the slivers a naive
 * slice-and-dice produces. Slivers matter here — an unreadable 3px-wide tile is
 * a tile the trader cannot click.
 *
 * Encoding: AREA is exposure, FILL is the day's move. Fill is a luminance ramp
 * (stronger tint = larger move) and every tile that has room prints the signed
 * percentage with an arrow, so direction survives greyscale and colour-blindness.
 */
import React, { useMemo, useState } from 'react';
import { TONE_HEX, cx, deltaArrow, fmtCur, fmtPct } from '../../ui';
import { FloatingReadout, PanelState, rgba, useMeasure } from './chartKit';

/* ---- squarified treemap -------------------------------------------------- */

/** Worst aspect ratio in a row of areas laid along a side of length `len`. */
function worstRatio(areas, len) {
  if (!areas.length || len <= 0) return Infinity;
  let sum = 0;
  let min = Infinity;
  let max = -Infinity;
  for (const a of areas) {
    sum += a;
    if (a < min) min = a;
    if (a > max) max = a;
  }
  if (sum <= 0 || min <= 0) return Infinity;
  const s2 = sum * sum;
  const l2 = len * len;
  return Math.max((l2 * max) / s2, s2 / (l2 * min));
}

/** Place a completed row and return the rectangle that remains. */
function layoutRow(row, rect, out) {
  const rowArea = row.reduce((s, i) => s + i.area, 0);
  if (rowArea <= 0) return rect;

  if (rect.w >= rect.h) {
    // Vertical strip down the left edge.
    const stripW = rowArea / rect.h;
    let y = rect.y;
    for (const item of row) {
      const h = item.area / stripW;
      out.push({ ...item, x: rect.x, y, w: stripW, h });
      y += h;
    }
    return { x: rect.x + stripW, y: rect.y, w: rect.w - stripW, h: rect.h };
  }

  // Horizontal strip across the top edge.
  const stripH = rowArea / rect.w;
  let x = rect.x;
  for (const item of row) {
    const w = item.area / stripH;
    out.push({ ...item, x, y: rect.y, w, h: stripH });
    x += w;
  }
  return { x: rect.x, y: rect.y + stripH, w: rect.w, h: rect.h - stripH };
}

/**
 * items: [{ key, value, ...payload }] — value must be > 0.
 * rect:  { x, y, w, h } in SVG user units.
 */
export function squarify(items, rect) {
  const positive = items.filter((i) => Number(i.value) > 0);
  const total = positive.reduce((s, i) => s + Number(i.value), 0);
  if (!positive.length || total <= 0 || rect.w <= 0 || rect.h <= 0) return [];

  // Scale values into pixel area so `worstRatio` compares like with like.
  const px = (rect.w * rect.h) / total;
  const queue = [...positive]
    .sort((a, b) => b.value - a.value)
    .map((i) => ({ ...i, area: Number(i.value) * px }));

  const out = [];
  let remaining = { ...rect };
  let row = [];

  while (queue.length) {
    const next = queue[0];
    const len = Math.min(remaining.w, remaining.h);
    const areas = row.map((i) => i.area);

    if (!row.length || worstRatio(areas, len) >= worstRatio([...areas, next.area], len)) {
      row.push(next);
      queue.shift();
    } else {
      remaining = layoutRow(row, remaining, out);
      row = [];
    }
  }
  if (row.length) layoutRow(row, remaining, out);
  return out;
}

/* ---- rendering ----------------------------------------------------------- */

const VIEW_H = 260;
const HEADER = 14; // sector caption band
const GAP = 2;

function tileFill(changePct, selected) {
  if (selected) return rgba(TONE_HEX.accent, 0.3);
  if (changePct == null) return 'rgba(255,255,255,0.055)';
  // 3% is treated as a "full strength" daily move for the ramp.
  const t = Math.min(1, Math.abs(changePct) / 3);
  const hex = changePct > 0 ? TONE_HEX.pos : changePct < 0 ? TONE_HEX.neg : TONE_HEX.neutral;
  return rgba(hex, 0.1 + 0.45 * t);
}

export function ExposureTreemap({
  items = [],
  groupBy = 'sector', // 'sector' | 'symbol'
  currency = 'INR',
  selectedSymbol = null,
  onSelectSymbol,
  loading = false,
  error = null,
  onRetry,
  height = VIEW_H,
  className = '',
}) {
  const [wrapRef, { w: measuredW }] = useMeasure();
  const [hover, setHover] = useState(null); // { tile, x, y }

  const W = Math.max(120, measuredW || 0);
  const H = height;

  const tiles = useMemo(() => {
    const clean = items.filter((i) => Number(i.value) > 0);
    if (!clean.length || !W) return [];

    if (groupBy !== 'sector') {
      return squarify(clean.map((i) => ({ ...i, key: i.symbol })), { x: 0, y: 0, w: W, h: H });
    }

    // Two-level: sectors first, then symbols inside each sector rectangle.
    const bySector = new Map();
    for (const i of clean) {
      const s = i.sector || 'Unclassified';
      const cur = bySector.get(s) || { key: s, sector: s, value: 0, children: [] };
      cur.value += Number(i.value);
      cur.children.push(i);
      bySector.set(s, cur);
    }

    const groups = squarify([...bySector.values()], { x: 0, y: 0, w: W, h: H });
    const out = [];
    for (const g of groups) {
      // Reserve a caption band; if the group is too short to caption, skip it
      // and let the child tiles use the full height.
      const captioned = g.h > HEADER + 10;
      const inner = {
        x: g.x + GAP / 2,
        y: g.y + (captioned ? HEADER : 0) + GAP / 2,
        w: Math.max(0, g.w - GAP),
        h: Math.max(0, g.h - (captioned ? HEADER : 0) - GAP),
      };
      out.push({ ...g, isGroup: true, captioned });
      for (const c of squarify(g.children.map((c) => ({ ...c, key: c.symbol })), inner)) out.push(c);
    }
    return out;
  }, [items, groupBy, W, H]);

  const leaves = tiles.filter((t) => !t.isGroup);
  const groups = tiles.filter((t) => t.isGroup);
  const total = items.reduce((s, i) => s + (Number(i.value) || 0), 0);

  return (
    <PanelState
      loading={loading}
      error={error}
      empty={!items.length}
      onRetry={onRetry}
      emptyTitle="No exposure"
      emptyHint="Open positions will appear here sized by notional exposure."
      height={height}
      className={className}
    >
      {/* `relative` + non-scrolling: FloatingReadout is positioned against this
          box, so the panel body wrapping it must not scroll (see chartKit). */}
      <div ref={wrapRef} className="relative w-full" style={{ height }}>
        <svg
          width="100%"
          height={H}
          viewBox={`0 0 ${W} ${H}`}
          role="img"
          aria-label={`Exposure treemap, ${leaves.length} positions totalling ${fmtCur(total, {
            ccy: currency,
            compact: true,
          })}`}
          onMouseLeave={() => setHover(null)}
        >
          {/* Sector frames sit behind the leaves. */}
          {groups.map((g) => (
            <g key={`g:${g.key}`}>
              <rect
                x={g.x}
                y={g.y}
                width={Math.max(0, g.w - GAP)}
                height={Math.max(0, g.h - GAP)}
                fill="rgba(255,255,255,0.018)"
                stroke="rgba(255,255,255,0.09)"
                strokeWidth={1}
                rx={3}
              />
              {g.captioned && g.w > 46 && (
                <text
                  x={g.x + 5}
                  y={g.y + 10}
                  className="fill-hx-text-lo"
                  style={{ fontSize: 9, letterSpacing: '0.04em', textTransform: 'uppercase' }}
                >
                  {String(g.sector).length * 6 > g.w - 10
                    ? `${String(g.sector).slice(0, Math.max(1, Math.floor((g.w - 12) / 6)))}…`
                    : g.sector}
                </text>
              )}
            </g>
          ))}

          {leaves.map((t) => {
            const selected = selectedSymbol && t.symbol === selectedSymbol;
            const wide = t.w > 44;
            const tall = t.h > 26;
            return (
              <g
                key={`t:${t.symbol}`}
                role={onSelectSymbol ? 'button' : undefined}
                tabIndex={onSelectSymbol ? 0 : undefined}
                aria-label={`${t.symbol}, exposure ${fmtCur(t.value, { ccy: currency })}, ${
                  t.changePct == null ? 'no quote' : fmtPct(t.changePct, { asRatio: false })
                }`}
                className={cx(onSelectSymbol && 'cursor-pointer hx-focus')}
                onClick={onSelectSymbol ? () => onSelectSymbol(t.symbol) : undefined}
                onKeyDown={
                  onSelectSymbol
                    ? (e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault();
                          onSelectSymbol(t.symbol);
                        }
                      }
                    : undefined
                }
                onMouseMove={(e) => {
                  const r = e.currentTarget.ownerSVGElement.getBoundingClientRect();
                  setHover({ tile: t, x: e.clientX - r.left, y: e.clientY - r.top });
                }}
              >
                <rect
                  x={t.x + GAP / 2}
                  y={t.y + GAP / 2}
                  width={Math.max(0, t.w - GAP)}
                  height={Math.max(0, t.h - GAP)}
                  rx={2}
                  fill={tileFill(t.changePct, selected)}
                  stroke={
                    selected
                      ? TONE_HEX.accent
                      : hover && hover.tile.symbol === t.symbol
                        ? 'rgba(255,255,255,0.34)'
                        : 'rgba(255,255,255,0.08)'
                  }
                  strokeWidth={selected ? 1.5 : 1}
                />
                {wide && tall && (
                  <>
                    <text
                      x={t.x + 5}
                      y={t.y + 14}
                      className="fill-hx-text-hi"
                      style={{ fontSize: 10, fontWeight: 600 }}
                    >
                      {t.symbol}
                    </text>
                    {t.h > 38 && (
                      <text
                        x={t.x + 5}
                        y={t.y + 26}
                        className="fill-hx-text-mid"
                        style={{ fontSize: 9, fontVariantNumeric: 'tabular-nums' }}
                      >
                        {t.changePct == null
                          ? '--'
                          : `${deltaArrow(t.changePct)} ${fmtPct(t.changePct, { asRatio: false, dp: 1 })}`}
                      </text>
                    )}
                  </>
                )}
              </g>
            );
          })}
        </svg>

        {hover && (
          <FloatingReadout x={hover.x} y={hover.y}>
            <div className="text-hx-11 font-semibold text-hx-text-hi">{hover.tile.symbol}</div>
            <div className="text-hx-10 text-hx-text-dim mb-1">{hover.tile.sector || 'Unclassified'}</div>
            <dl className="space-y-0.5">
              <Row label="Exposure" value={fmtCur(hover.tile.value, { ccy: currency })} />
              <Row
                label="Weight"
                value={total > 0 ? fmtPct(hover.tile.value / total, { signed: false, dp: 1 }) : '--'}
              />
              <Row
                label="Day"
                value={
                  hover.tile.changePct == null
                    ? '--'
                    : `${deltaArrow(hover.tile.changePct)} ${fmtPct(hover.tile.changePct, {
                        asRatio: false,
                      })}`
                }
              />
            </dl>
          </FloatingReadout>
        )}
      </div>
    </PanelState>
  );
}

function Row({ label, value }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-hx-10 text-hx-text-lo">{label}</dt>
      <dd className="hx-mono text-hx-11 text-hx-text-hi">{value}</dd>
    </div>
  );
}

export default ExposureTreemap;
