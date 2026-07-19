/**
 * PerformanceChart — cumulative realised P&L over the journal's life.
 *
 * WHAT THIS IS NOT: it is not a net-liquidation curve. The source
 * (/dash/journal/{name}/trading → equity_curve) emits one point per
 * oms.positions event carrying a running sum of REALISED P&L only, so open
 * positions are absent by construction. The panel title says "realised" for
 * that reason — mislabelling it "equity" would overstate a flat book.
 *
 * Hover readout is rendered in the chart's own header strip rather than a
 * floating tooltip: it cannot clip inside a scrolling panel, it never occludes
 * the curve, and the numbers stay in one fixed place as the cursor sweeps —
 * which is what makes values comparable while scrubbing.
 */
import React, { useMemo, useState } from 'react';
import { TONE_HEX, TONE_TEXT, cx, deltaArrow, deltaTone, fmtCur, fmtTime } from '../../ui';
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

export function PerformanceChart({
  points = [],
  currency = 'INR',
  loading = false,
  error = null,
  onRetry,
  height = 200,
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
    // includeZero: a P&L curve must show the break-even rule, otherwise a
    // curve that is entirely negative looks like a winning one.
    const [y0, y1] = niceExtent(pts.map((p) => p.v), { includeZero: true });

    const sx = scale(t0, t1 || t0 + 1, PAD.l, PAD.l + plotW);
    const sy = scale(y0, y1, PAD.t + plotH, PAD.t);
    const xy = pts.map((p) => [sx(p.t), sy(p.v)]);

    return { pts, xy, sx, sy, y0, y1, t0, t1, zeroY: sy(0), yTicks: ticks(y0, y1, 4) };
  }, [points, plotW, plotH]);

  const last = model ? model.pts[model.pts.length - 1] : null;
  const active = model && hoverIdx != null ? model.pts[hoverIdx] : last;
  const tone = deltaTone(active ? active.v : 0);

  return (
    <PanelState
      loading={loading}
      error={error}
      empty={!model}
      onRetry={onRetry}
      emptyTitle="No equity curve"
      emptyHint="The selected journal has no position events yet — the curve fills in as fills are recorded."
      height={height}
      className={className}
    >
      {model && (
        <div className="flex flex-col gap-1 min-w-0">
          {/* ---- readout strip: fixed position, follows the cursor's value ---- */}
          <div className="flex items-baseline justify-between gap-3 px-0.5">
            <div className="flex items-baseline gap-2 min-w-0">
              <span className={cx('hx-mono text-hx-14 font-semibold', TONE_TEXT[tone])}>
                <span aria-hidden="true">{deltaArrow(active.v)}</span>{' '}
                {fmtCur(active.v, { ccy: currency, signed: true })}
              </span>
              <span className="text-hx-10 uppercase tracking-wider text-hx-text-lo">
                {hoverIdx != null ? 'at cursor' : 'realised, cumulative'}
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
              aria-label={`Cumulative realised profit and loss, ${model.pts.length} points, latest ${fmtCur(
                last.v,
                { ccy: currency, signed: true },
              )}`}
              onMouseMove={(e) => setHoverIdx(nearestIndex(model.xy, pointerX(e)))}
              onMouseLeave={() => setHoverIdx(null)}
            >
              {/* horizontal gridlines + y labels */}
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

              {/* break-even rule — brighter than the grid, it is a real datum */}
              {model.y0 <= 0 && model.y1 >= 0 && (
                <line
                  x1={PAD.l}
                  x2={PAD.l + plotW}
                  y1={model.zeroY}
                  y2={model.zeroY}
                  stroke="rgba(255,255,255,0.2)"
                  strokeWidth={1}
                  strokeDasharray="3 3"
                />
              )}

              {/* area + line */}
              <path
                d={areaPath(model.xy, Math.min(PAD.t + plotH, Math.max(PAD.t, model.zeroY)))}
                fill={rgba(TONE_HEX[tone === 'neutral' ? 'accent' : tone], 0.14)}
              />
              <path
                d={linePath(model.xy)}
                fill="none"
                stroke={TONE_HEX[tone === 'neutral' ? 'accent' : tone]}
                strokeWidth={1.4}
                strokeLinejoin="round"
                strokeLinecap="round"
              />

              {/* x labels: first / last only — dense ticks fight the readout */}
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

              {/* crosshair */}
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
                    fill={TONE_HEX[tone === 'neutral' ? 'accent' : tone]}
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

export default PerformanceChart;
