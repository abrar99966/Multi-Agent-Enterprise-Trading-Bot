/**
 * DrawdownChart — underwater plot: distance below the running peak of the
 * realised P&L curve.
 *
 * Units are CURRENCY, not percent. WHY: the underlying series is a P&L curve
 * that starts at zero and can go negative, so a percentage drawdown has no
 * stable denominator — dividing by a peak of 0 (or a negative peak) produces
 * nonsense. Absolute drawdown is the honest reading of this data source; if a
 * true net-liquidation series ever becomes available, switch to percent then.
 *
 * The series is <= 0 everywhere by construction, so the plot hangs from a zero
 * rule at the top. Max drawdown is marked in place, not just quoted in a tile.
 */
import React, { useMemo, useState } from 'react';
import { TONE_HEX, cx, fmtCur, fmtTime } from '../../ui';
import {
  PanelState,
  areaPath,
  linePath,
  nearestIndex,
  niceExtent,
  pointerX,
  rgba,
  scale,
  ticks,
  useMeasure,
} from './chartKit';

const PAD = { l: 58, r: 10, t: 10, b: 20 };

export function DrawdownChart({
  points = [],
  currency = 'INR',
  loading = false,
  error = null,
  onRetry,
  height = 150,
  className = '',
}) {
  const [wrapRef, { w: measuredW }] = useMeasure();
  const [hoverIdx, setHoverIdx] = useState(null);

  const W = Math.max(160, measuredW || 0);
  const H = height;
  const plotW = Math.max(1, W - PAD.l - PAD.r);
  const plotH = Math.max(1, H - PAD.t - PAD.b);

  const model = useMemo(() => {
    const pts = points.filter((p) => p && Number.isFinite(p.v) && Number.isFinite(p.t));
    if (pts.length < 2) return null;

    const t0 = pts[0].t;
    const t1 = pts[pts.length - 1].t;
    // Domain is pinned to 0 at the top — an underwater chart that floats its
    // upper bound hides how much of the time the book was at a new high.
    const [lo] = niceExtent(pts.map((p) => p.v), { includeZero: true, padFrac: 0.12 });
    const y0 = Math.min(lo, 0);
    const y1 = 0;

    const sx = scale(t0, t1 || t0 + 1, PAD.l, PAD.l + plotW);
    const sy = scale(y0, y1, PAD.t + plotH, PAD.t);
    const xy = pts.map((p) => [sx(p.t), sy(p.v)]);

    let troughIdx = 0;
    for (let i = 1; i < pts.length; i += 1) if (pts[i].v < pts[troughIdx].v) troughIdx = i;

    return {
      pts,
      xy,
      sx,
      sy,
      y0,
      y1,
      t0,
      t1,
      troughIdx,
      zeroY: sy(0),
      yTicks: ticks(y0, y1, 3),
    };
  }, [points, plotW, plotH]);

  const active = model ? model.pts[hoverIdx != null ? hoverIdx : model.troughIdx] : null;
  const maxDD = model ? model.pts[model.troughIdx] : null;

  return (
    <PanelState
      loading={loading}
      error={error}
      empty={!model}
      onRetry={onRetry}
      emptyTitle="No drawdown history"
      emptyHint="Underwater depth is computed from the realised P&L curve once it has two or more points."
      height={height}
      className={className}
    >
      {model && (
        <div className="flex flex-col gap-1 min-w-0">
          {/* readout strip */}
          <div className="flex items-baseline justify-between gap-3 px-0.5">
            <div className="flex items-baseline gap-2 min-w-0">
              <span
                className={cx(
                  'hx-mono text-hx-14 font-semibold',
                  active.v < 0 ? 'text-hx-neg-400' : 'text-hx-pos-400',
                )}
              >
                {active.v < 0 ? fmtCur(active.v, { ccy: currency }) : 'At high-water mark'}
              </span>
              <span className="text-hx-10 uppercase tracking-wider text-hx-text-lo">
                {hoverIdx != null ? 'at cursor' : 'max drawdown'}
              </span>
            </div>
            <span className="hx-mono text-hx-10 text-hx-text-dim shrink-0">
              {fmtTime(active.t, { mode: 'datetime' })}
            </span>
          </div>

          <div ref={wrapRef} className="w-full" style={{ height: H }}>
            <svg
              width="100%"
              height={H}
              viewBox={`0 0 ${W} ${H}`}
              role="img"
              aria-label={`Underwater drawdown chart, maximum drawdown ${fmtCur(maxDD.v, {
                ccy: currency,
              })}`}
              onMouseMove={(e) => setHoverIdx(nearestIndex(model.xy, pointerX(e)))}
              onMouseLeave={() => setHoverIdx(null)}
            >
              {model.yTicks.map((t) => {
                const y = model.sy(t);
                return (
                  <g key={t}>
                    <line
                      x1={PAD.l}
                      x2={PAD.l + plotW}
                      y1={y}
                      y2={y}
                      stroke="rgba(255,255,255,0.045)"
                      strokeWidth={1}
                    />
                    <text
                      x={PAD.l - 6}
                      y={y + 3}
                      textAnchor="end"
                      className="fill-hx-text-dim"
                      style={{ fontSize: 9, fontVariantNumeric: 'tabular-nums' }}
                    >
                      {fmtCur(t, { ccy: currency, compact: true })}
                    </text>
                  </g>
                );
              })}

              {/* high-water mark */}
              <line
                x1={PAD.l}
                x2={PAD.l + plotW}
                y1={model.zeroY}
                y2={model.zeroY}
                stroke="rgba(255,255,255,0.24)"
                strokeWidth={1}
              />

              <path d={areaPath(model.xy, model.zeroY)} fill={rgba(TONE_HEX.neg, 0.16)} />
              <path
                d={linePath(model.xy)}
                fill="none"
                stroke={TONE_HEX.neg}
                strokeWidth={1.3}
                strokeLinejoin="round"
                strokeLinecap="round"
              />

              {/* trough marker — labelled, so the worst point is legible without
                  hovering and without relying on the red fill alone */}
              {maxDD.v < 0 && (
                <g pointerEvents="none">
                  <circle
                    cx={model.xy[model.troughIdx][0]}
                    cy={model.xy[model.troughIdx][1]}
                    r={3}
                    fill={TONE_HEX.neg}
                    stroke="#070a12"
                    strokeWidth={1.5}
                  />
                  <text
                    x={Math.min(model.xy[model.troughIdx][0] + 6, PAD.l + plotW - 4)}
                    y={Math.max(PAD.t + 9, model.xy[model.troughIdx][1] - 5)}
                    textAnchor={model.xy[model.troughIdx][0] > PAD.l + plotW * 0.7 ? 'end' : 'start'}
                    className="fill-hx-neg-300"
                    style={{ fontSize: 9, fontVariantNumeric: 'tabular-nums' }}
                  >
                    {`max ${fmtCur(maxDD.v, { ccy: currency, compact: true })}`}
                  </text>
                </g>
              )}

              <text
                x={PAD.l}
                y={H - 6}
                className="fill-hx-text-dim"
                style={{ fontSize: 9, fontVariantNumeric: 'tabular-nums' }}
              >
                {fmtTime(model.t0, { mode: 'date' })}
              </text>
              <text
                x={PAD.l + plotW}
                y={H - 6}
                textAnchor="end"
                className="fill-hx-text-dim"
                style={{ fontSize: 9, fontVariantNumeric: 'tabular-nums' }}
              >
                {fmtTime(model.t1, { mode: 'date' })}
              </text>

              {hoverIdx != null && model.xy[hoverIdx] && (
                <g pointerEvents="none">
                  <line
                    x1={model.xy[hoverIdx][0]}
                    x2={model.xy[hoverIdx][0]}
                    y1={PAD.t}
                    y2={PAD.t + plotH}
                    stroke="rgba(255,255,255,0.28)"
                    strokeWidth={1}
                  />
                  <circle
                    cx={model.xy[hoverIdx][0]}
                    cy={model.xy[hoverIdx][1]}
                    r={3}
                    fill={TONE_HEX.warn}
                    stroke="#070a12"
                    strokeWidth={1.5}
                  />
                </g>
              )}
            </svg>
          </div>
        </div>
      )}
    </PanelState>
  );
}

export default DrawdownChart;
